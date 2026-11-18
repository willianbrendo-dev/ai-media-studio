#!/usr/bin/env python3
"""Viral Reference Reverse-Engineering Analyzer for Shopee / Short-Form Videos.

Uses local faster-whisper INT8 (cpu_threads=2), FFmpeg scene-cut detection (`gt(scene,0.32)`),
audio energy detection (`volumedetect`), and VPS AI Cascade Gateway (`http://127.0.0.1:9000/v1`)
to generate `viral_blueprint.json` inside `/opt/ai-media-studio/campaigns/{campaign_id}/`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import tempfile
import time
import wave
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

for _env_k, _env_v in (("OMP_NUM_THREADS", "2"), ("MKL_NUM_THREADS", "2"), ("CT2_NUM_THREADS", "2")):
    os.environ.setdefault(_env_k, _env_v)

SUPOCLIP_HF_CACHE = Path("/opt/ai-media-studio/supoclip/.cache/huggingface")
if SUPOCLIP_HF_CACHE.exists() and not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(SUPOCLIP_HF_CACHE)

import httpx  # noqa: E402
import numpy as np  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

CAMPAIGNS_ROOT = Path(os.environ.get("STUDIO_CAMPAIGNS_DIR", "/opt/ai-media-studio/campaigns"))
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}
_MODEL_CACHE: Dict[str, WhisperModel] = {}


def _get_whisper_model(size: str = "base") -> WhisperModel:
    if size not in _MODEL_CACHE:
        _MODEL_CACHE[size] = WhisperModel(
            size,
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
            num_workers=1,
        )
    return _MODEL_CACHE[size]


def _probe_video(path: Path) -> Dict[str, Any]:
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(res.stdout or "{}")
    streams = data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0 and v_stream:
        duration = float(v_stream.get("duration") or 0.0)
    return {
        "duration_s": max(0.2, duration),
        "width": int(v_stream.get("width", 1080)) if v_stream else 1080,
        "height": int(v_stream.get("height", 1920)) if v_stream else 1920,
        "has_audio": a_stream is not None,
    }


def _detect_scene_cuts(path: Path, threshold: float = 0.32) -> List[float]:
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
        "-vf", f"select='gt(scene,{threshold:.2f})',showinfo",
        "-f", "null", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
    stderr = res.stderr or ""
    timestamps: List[float] = []
    for m in re.finditer(r"pts_time:([0-9]+(?:\.[0-9]+)?)", stderr):
        t = round(float(m.group(1)), 2)
        if not timestamps or (t - timestamps[-1]) >= 0.25:
            timestamps.append(t)
    return timestamps


def _detect_audio_energy(path: Path, has_audio: bool) -> Dict[str, Any]:
    if not has_audio:
        return {"mean_volume_db": -90.0, "max_volume_db": -90.0, "energy_score": 0, "energy_label": "mudo"}
    cmd = [
        "ffmpeg", "-hide_banner", "-v", "info", "-i", str(path),
        "-vn", "-af", "volumedetect", "-f", "null", "-",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    stderr = res.stderr or ""
    m_mean = re.search(r"mean_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB", stderr)
    m_max = re.search(r"max_volume:\s*(-?[0-9]+(?:\.[0-9]+)?)\s*dB", stderr)
    mean_db = float(m_mean.group(1)) if m_mean else -30.0
    max_db = float(m_max.group(1)) if m_max else -12.0
    score = int(max(0, min(100, round((mean_db + 45.0) * (100.0 / 35.0)))))
    label = "alta" if mean_db >= -18.0 else ("media" if mean_db >= -28.0 else "baixa")
    return {
        "mean_volume_db": round(mean_db, 1),
        "max_volume_db": round(max_db, 1),
        "energy_score": score,
        "energy_label": label,
    }


def _transcribe_video(path: Path, has_audio: bool, language: str = "pt") -> Dict[str, Any]:
    if not has_audio:
        return {"words": [], "full_text": "", "hook_0_3s": "", "language": language}
    words: List[Dict[str, Any]] = []
    full_parts: List[str] = []
    detected_lang = language
    try:
        with tempfile.TemporaryDirectory(prefix="viral_asr_") as tmp:
            wav = Path(tmp) / "audio16k.wav"
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(path),
                    "-vn", "-ac", "1", "-ar", "16000", "-threads", "2", str(wav),
                ],
                check=True,
                capture_output=True,
                timeout=90,
            )
            with wave.open(str(wav), "rb") as wf:
                pcm = wf.readframes(wf.getnframes())
            if pcm:
                audio_np = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                model = _get_whisper_model("base")
                lang_code = None if not language or language == "auto" else language.split("-")[0].split("_")[0]
                segs, info = model.transcribe(
                    audio_np,
                    language=lang_code,
                    beam_size=1,
                    vad_filter=True,
                    word_timestamps=True,
                )
                detected_lang = getattr(info, "language", detected_lang) or detected_lang
                for seg in segs:
                    st = (seg.text or "").strip()
                    if st:
                        full_parts.append(st)
                    for w in getattr(seg, "words", None) or []:
                        tok = (w.word or "").strip()
                        if not tok:
                            continue
                        words.append({
                            "text": tok,
                            "start": round(float(w.start), 2),
                            "end": round(float(w.end), 2),
                        })
    except Exception:
        pass

    full_text = re.sub(r"\s+", " ", " ".join(full_parts)).strip()
    if not full_text and words:
        full_text = " ".join(w["text"] for w in words)

    hook_words = [w["text"] for w in words if w["start"] <= 3.35]
    if not hook_words and words:
        hook_words = [w["text"] for w in words[:10]]
    hook_0_3s = re.sub(r"\s+", " ", " ".join(hook_words)).strip()
    if not hook_0_3s and full_text:
        hook_0_3s = " ".join(full_text.split()[:10])

    return {
        "words": words,
        "full_text": full_text,
        "hook_0_3s": hook_0_3s,
        "language": detected_lang,
    }


def _analyze_single_video(mp4: Path, language: str = "pt") -> Dict[str, Any]:
    meta = _probe_video(mp4)
    duration_s = meta["duration_s"]
    cuts = _detect_scene_cuts(mp4, threshold=0.32)
    pacing_s = round(duration_s / max(1, len(cuts) + 1), 2)
    energy = _detect_audio_energy(mp4, meta["has_audio"])
    asr = _transcribe_video(mp4, meta["has_audio"], language=language)
    wc = len(asr["words"]) if asr["words"] else len(asr["full_text"].split())
    wpm = round((wc / max(1.0, duration_s)) * 60.0, 1) if wc > 0 else 0.0
    return {
        "filename": mp4.name,
        "path": str(mp4),
        "duration_s": round(duration_s, 2),
        "resolution": f"{meta['width']}x{meta['height']}",
        "scene_cuts_count": len(cuts),
        "cut_timestamps": cuts[:40],
        "pacing_seconds": pacing_s,
        "audio_energy": energy,
        "word_count": wc,
        "wpm": wpm,
        "hook_0_3s": asr["hook_0_3s"] or "(Gancho visual silencioso sem fala detectada nos primeiros 3s)",
        "transcript": asr["full_text"],
        "language": asr["language"],
    }


def _heuristic_structure(refs: List[Dict[str, Any]], title: str, price: str, desc: str) -> Dict[str, Any]:
    avg_dur = sum(r["duration_s"] for r in refs) / max(1, len(refs))
    avg_pacing = sum(r["pacing_seconds"] for r in refs) / max(1, len(refs))
    spoken_wpms = [r["wpm"] for r in refs if r["wpm"] > 0]
    avg_wpm = round(sum(spoken_wpms) / len(spoken_wpms), 1) if spoken_wpms else 150.0
    avg_energy = round(sum(r["audio_energy"]["mean_volume_db"] for r in refs) / max(1, len(refs)), 1)

    first_hook = next(
        (r["hook_0_3s"] for r in refs if r["hook_0_3s"] and not r["hook_0_3s"].startswith("(Gancho")),
        f"Olha a diferença real antes e depois usando {title}!",
    )
    all_text = " ".join(r.get("transcript", "") for r in refs).strip()

    price_str = price.strip() or "R$ 69,99"
    rec_dur = round(max(14.0, min(20.0, avg_dur if avg_dur >= 12.0 else 17.0)), 1)

    hook_script = first_hook.rstrip(".!?")
    script = (
        f"{hook_script}! "
        f"Com {title[:60]}, você garante resultado visível já na primeira semana sem pesar. "
        f"Produto original com ótimo rendimento por apenas {price_str}. "
        f"Garanta já o seu com desconto!"
    )
    return {
        "hook_0_3s": first_hook,
        "pacing_seconds": round(max(1.2, avg_pacing), 2),
        "wpm": avg_wpm,
        "audio_energy_db": avg_energy,
        "argument_order": [
            "0s-3s: Hook direto de dor ou prova visual (sem logo/saudação)",
            "3s-8s: Demonstração dos benefícios práticos do produto",
            "8s-12s: Prova de fórmula / quebra de objeção de autenticidade",
            f"12s-{int(round(rec_dur))}s: Ancoragem de preço ({price_str}) + urgência",
        ],
        "objection_breaker": "Produto 100% original com alta durabilidade, rendimento real e proteção no uso diário.",
        "cta_trigger": f"Oferta por tempo limitado ({price_str}) com chamada visual para o Carrinho Laranja da Shopee.",
        "recommended_script": script,
        "recommended_duration": rec_dur,
        "cut_cadence": f"Cortes visuais a cada ~{round(max(1.5, avg_pacing), 1)}s acompanhando ritmo de {int(round(avg_wpm))} WPM.",
        "direction_notes": (
            "Manter todo texto e fotos dentro da zona anti-crop 20:9 (X: 96-884px, Y: 235-1203px); "
            "evitar saudação nos primeiros 3s; não repetir texto do CTA na legenda durante o badge final."
        ),
        "source_transcript_excerpt": all_text[:400],
    }


def _synthesize_with_cascade(
    refs: List[Dict[str, Any]],
    campaign_meta: Dict[str, Any],
    product_title: str,
    product_price: str,
    product_description: str,
) -> tuple[Dict[str, Any], bool, str]:
    base = _heuristic_structure(refs, product_title, product_price, product_description)
    pricing = campaign_meta.get("pricing") or {}
    assets_desc = [
        f"{k}: {(v.get('scene') or '')} - {(v.get('description') or '')}"
        for k, v in (campaign_meta.get("asset_roles") or {}).items()
    ]
    ref_summary = [
        {
            "file": r["filename"],
            "duration_s": r["duration_s"],
            "cuts": r["scene_cuts_count"],
            "pacing_seconds": r["pacing_seconds"],
            "wpm": r["wpm"],
            "audio_db": r["audio_energy"]["mean_volume_db"],
            "hook_0_3s": r["hook_0_3s"],
            "transcript": r["transcript"][:800],
        }
        for r in refs
    ]
    prompt = {
        "produto": product_title,
        "preco": product_price or pricing.get("kit_price", ""),
        "preco_unitario": pricing.get("unit_price", ""),
        "descricao": product_description or campaign_meta.get("name", ""),
        "fotos_disponiveis": assets_desc[:8],
        "telemetria_virais": ref_summary,
    }
    sys_msg = (
        "Você é um Diretor de Performance e Engenharia Reversa de Vídeos Virais da Shopee Video. "
        "Analise a telemetria e transcrições dos vídeos virais de referência e retorne ESTRITAMENTE um JSON válido "
        "(sem markdown) com as chaves: hook_0_3s (string curta), argument_order (array de strings), "
        "objection_breaker (string), cta_trigger (string), recommended_script (string falada em PT-BR natural com números por extenso, entre 35 e 46 palavras, direta para 16-18 segundos, sem repetir 'carrinho laranja' no final), "
        "recommended_duration (float entre 15 e 19), cut_cadence (string), direction_notes (string)."
    )
    try:
        with httpx.Client(timeout=45.0) as client:
            r = client.post(
                "http://127.0.0.1:9000/v1/chat/completions",
                json={
                    "model": "cascade-auto",
                    "messages": [
                        {"role": "system", "content": sys_msg},
                        {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
                    ],
                    "max_tokens": 2600,
                    "temperature": 0.35,
                },
            )
        if r.status_code == 200:
            raw = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
            model_used = str(r.json().get("model") or "cascade-auto")
            raw_clean = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
            m_obj = re.search(r"\{.*\}", raw_clean, flags=re.DOTALL)
            parsed = json.loads(m_obj.group(0) if m_obj else raw_clean)
            if isinstance(parsed, dict) and parsed.get("recommended_script"):
                merged = dict(base)
                for k in (
                    "hook_0_3s", "argument_order", "objection_breaker", "cta_trigger",
                    "recommended_script", "recommended_duration", "cut_cadence", "direction_notes",
                ):
                    val = parsed.get(k)
                    if val:
                        merged[k] = val
                return merged, True, model_used
    except Exception:
        pass
    return base, False, "deterministic-fallback"


def analyze_campaign_virals(
    campaign_id: str,
    product_title: str = "",
    product_price: str = "",
    product_description: str = "",
    viral_paths: Optional[List[str]] = None,
    output_path: Optional[Path] = None,
) -> Dict[str, Any]:
    t0 = time.monotonic()
    c_dir = (CAMPAIGNS_ROOT / campaign_id.strip()).resolve()
    if not c_dir.is_relative_to(CAMPAIGNS_ROOT.resolve()) or not c_dir.is_dir():
        raise FileNotFoundError(f"Campanha não encontrada: {campaign_id}")

    meta: Dict[str, Any] = {}
    meta_file = c_dir / "campaign.json"
    if meta_file.exists():
        try:
            meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            meta = {}

    eff_title = (product_title or "").strip() or meta.get("name") or campaign_id
    eff_price = (product_price or "").strip() or (meta.get("pricing") or {}).get("kit_price", "")
    eff_desc = (product_description or "").strip() or (meta.get("pricing") or {}).get("psychology_hook", "")

    targets: List[Path] = []
    if viral_paths:
        for vp in viral_paths:
            p = Path(str(vp).strip()).resolve()
            if p.is_file() and p.suffix.lower() in VIDEO_EXTS:
                targets.append(p)
    if not targets:
        v_dir = c_dir / "virals"
        if v_dir.is_dir():
            targets = sorted(
                p for p in v_dir.iterdir()
                if p.is_file() and p.suffix.lower() in VIDEO_EXTS
            )
    if not targets:
        raise FileNotFoundError(f"Nenhum vídeo .mp4 viral de referência encontrado em {c_dir / 'virals'}")

    refs = [_analyze_single_video(mp4) for mp4 in targets]
    blueprint_core, cascade_used, cascade_model = _synthesize_with_cascade(
        refs=refs,
        campaign_meta=meta,
        product_title=eff_title,
        product_price=eff_price,
        product_description=eff_desc,
    )

    out_file = output_path or (c_dir / "viral_blueprint.json")
    blueprint = {
        "ok": True,
        "campaign_id": campaign_id,
        "product_title": eff_title,
        "product_price": eff_price,
        "product_description": eff_desc,
        "analyzed_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "references_count": len(refs),
        "references": refs,
        "cascade_used": cascade_used,
        "cascade_model": cascade_model,
        "elapsed_seconds": round(time.monotonic() - t0, 2),
        **blueprint_core,
    }
    tmp_out = out_file.with_suffix(".tmp.json")
    tmp_out.write_text(json.dumps(blueprint, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_out.replace(out_file)
    return blueprint


def main() -> None:
    ap = argparse.ArgumentParser(description="Analyze viral Shopee reference MP4s and emit viral_blueprint.json")
    ap.add_argument("--campaign", required=True, help="Campaign slug under /opt/ai-media-studio/campaigns/")
    ap.add_argument("--viral", action="append", default=[], help="Optional viral reference MP4 path (repeatable)")
    ap.add_argument("--product-title", default="")
    ap.add_argument("--product-price", default="")
    ap.add_argument("--product-description", default="")
    ap.add_argument("-o", "--output", type=Path, default=None)
    args = ap.parse_args()

    res = analyze_campaign_virals(
        campaign_id=args.campaign,
        product_title=args.product_title,
        product_price=args.product_price,
        product_description=args.product_description,
        viral_paths=args.viral,
        output_path=args.output,
    )
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
