#!/usr/bin/env python3
"""Regional Accent & Voice Cloning Engine (v2.6).

Zero-Idle-RAM audio synthesis engine for authentic Brazilian Northeast ("Nordestino")
micro-series and regional comedy, supporting three engines cleanly:
  1. ``nordestino_raiz`` (Default): Gemini Native Audio TTS (``gemini-2.5-flash-preview-tts``)
     with authentic Sertão prosody director prompts and character voice mapping, converting
     24kHz ``s16le`` PCM output to 48kHz Stereo SoXR (loudnorm ``I=-16``). Transparently falls
     back to ``edge-tts`` + ``apply_nordestino_prosody()`` if Gemini quota fluctuates.
  2. ``f5_tts_clone``: Zero-Shot Voice Cloning via remote Colab GPU ``/tts-clone``
     (``F5-TTS-PT-BR``) using 6s-15s regional reference audio samples in
     ``/opt/ai-media-studio/voices/regional/``.
  3. ``seed_vc_guide``: Speech-to-Speech Voice Conversion via remote Colab GPU
     ``/voice-convert`` (``Seed-VC``) transferring the user's recorded microphone accent
     onto any target character timbre.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


STUDIO_BASE = Path(os.environ.get("STUDIO_BASE_DIR", "/opt/ai-media-studio"))
VOICES_REGIONAL_DIR = STUDIO_BASE / "voices/regional"
PROFILES_META_PATH = VOICES_REGIONAL_DIR / "profiles.json"
EDGE_TTS_BIN = os.environ.get(
    "EDGE_TTS_BIN",
    "/opt/ai-media-studio/tools/video-use/.venv/bin/edge-tts",
)
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"
GEMINI_TTS_MODEL = "gemini-3.1-flash-tts-preview"
GEMINI_TTS_MODELS: Tuple[str, ...] = (
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-flash-preview-tts",
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
)

NORDESTE_FAST_DIRECTOR_PROMPT = (
    "Atue em português brasileiro com sotaque nordestino autêntico do interior (Piauí/Ceará), "
    "ritmo RÁPIDO, vibrante, energético e espontâneo de comédia popular de calçada, "
    "fala ágil sem arrastar sílabas, entonação viva e natural."
)


# ---------------------------------------------------------------------------
# Authentic Character Regional Profiles & Gemini Voice Mappings
# ---------------------------------------------------------------------------
DEFAULT_REGIONAL_PROFILES: Dict[str, Dict[str, Any]] = {
    "dona_socorro": {
        "id": "dona_socorro",
        "label": "Dona Socorro",
        "region": "Interior do Ceará / Piauí",
        "role": "Matriarca dramática, desconfiada e expressiva",
        "gemini_voice": "Kore",
        "gemini_alt_voice": "Aoede",
        "edge_voice": "pt-BR-FranciscaNeural",
        "edge_rate": "+16%",
        "edge_pitch": "+5Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete Dona Socorro, senhora nordestina arretada, direta e afiada, com vogais abertas e reação imediata."
        ),
        "default_sample_text": "Oxente, minino! Tu veio botar boneco logo na minha calçada hoje?!",
    },
    "seu_bastiao": {
        "id": "seu_bastiao",
        "label": "Seu Bastião",
        "role": "Comerciante maduro, debochado e bom de prosa",
        "region": "Sertão do Piauí / Ceará / Pernambuco",
        "gemini_voice": "Charon",
        "gemini_alt_voice": "Fenrir",
        "edge_voice": "pt-BR-AntonioNeural",
        "edge_rate": "+14%",
        "edge_pitch": "-5Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete Seu Bastião, dono de bodega esperto, voz masculina madura, tirada rápida e riso debochado."
        ),
        "default_sample_text": "Hahaha! Deixe de cunversa, cabra leso! Aqui na bodega o negócio é arretado mesmo!",
    },
    "enzo": {
        "id": "enzo",
        "label": "Enzo",
        "role": "Sobrinho jovem confuso entre a capital e o sertão",
        "region": "Urbano / Nordestino Jovem",
        "gemini_voice": "Puck",
        "gemini_alt_voice": "Zephyr",
        "edge_voice": "pt-BR-AntonioNeural",
        "edge_rate": "+14%",
        "edge_pitch": "+6Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete Enzo, sobrinho jovem apressado e meio afobado tentando responder rápido com surpresa."
        ),
        "default_sample_text": "Tia Socorro, calma! Eu só tava pedindo um café sem frescura!",
    },
    "narrador_sertao": {
        "id": "narrador_sertao",
        "label": "Narrador do Sertão",
        "role": "Contador de causos musicais e irônicos",
        "region": "Cariri / Interior Nordestino",
        "gemini_voice": "Sulafat",
        "gemini_alt_voice": "Algenib",
        "edge_voice": "pt-BR-ThalitaMultilingualNeural",
        "edge_rate": "+14%",
        "edge_pitch": "-2Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete um narrador ágil de causos nordestinos com ritmo dinâmico de repente e entusiasmo."
        ),
        "default_sample_text": "Eita lá! Mais um dia agitado na bodega antes da presepada começar!",
    },
}

SPEAKER_KEY_ALIASES: Dict[str, str] = {
    "dona_socorro": "dona_socorro",
    "dona socorro": "dona_socorro",
    "socorro": "dona_socorro",
    "seu_bastiao": "seu_bastiao",
    "seu bastiao": "seu_bastiao",
    "seu bastião": "seu_bastiao",
    "bastiao": "seu_bastiao",
    "bastião": "seu_bastiao",
    "enzo": "enzo",
    "narrador": "narrador_sertao",
    "narradora": "narrador_sertao",
    "narrador_sertao": "narrador_sertao",
}

EMOTION_ACTING_CUES_PT: Dict[str, str] = {
    "shocked": "Reação imediata e rápida de espanto, voz firme e expressiva sem arrastar.",
    "confused": "Tom perplexo, resposta ágil de quem foi pego de surpresa.",
    "laughing": "Rindo de verdade com tirada rápida, alegre e debochada.",
    "proud": "Tom convencido e esperto, resposta na ponta da língua.",
    "dramatic": "Dramaticidade cômica acelerada de barraco bem-humorado.",
    "warm": "Tom animado, direto e caloroso de conversa rápida na calçada.",
}


def normalize_character_id(speaker_raw: str) -> str:
    clean = re.sub(r"\s+", " ", (speaker_raw or "").strip().lower())
    if clean in SPEAKER_KEY_ALIASES:
        return SPEAKER_KEY_ALIASES[clean]
    if "socorro" in clean:
        return "dona_socorro"
    if "bast" in clean:
        return "seu_bastiao"
    if "enzo" in clean:
        return "enzo"
    if "narr" in clean:
        return "narrador_sertao"
    return "narrador_sertao"


def sanitize_northeast_slang(text: str) -> str:
    """Enforce authentic Northeast (PI/CE/PE/BA) vocabulary and punchy non-dragged cadence.

    Blocks out-of-region slang ('trem', 'uai', 'sô', 'mano', 'meo', 'daora', 'guri', 'bah', 'tchê')
    and removes vowel-stretching spellings ('Oxêêênte' -> 'Oxente') that cause slow narration.
    """
    txt = (text or "").strip()
    if not txt:
        return ""

    # 1) Block out-of-region slang (Mineirês / Paulistês / Gauchês)
    replacements = [
        (r"\b(?:um|esse|aquele|o)\s+trem\b", lambda m: m.group(0).rsplit(None, 1)[0] + " negócio"),
        (r"\b(?:nessa|nesse|dessa|desse)\s+trem\b", "nesse negócio"),
        (r"\btrem\b", "negócio"),
        (r"\btrens\b", "negócios"),
        (r"\buai\b", "oxe"),
        (r"\bs[oô]\b", "visse"),
        (r"\bmano\b", "rapaz"),
        (r"\bmeo\b", "cabra"),
        (r"\bda\s*hora\b|\bdaora\b", "arretado"),
        (r"\bguri\b", "minino"),
        (r"\bguria\b", "minina"),
        (r"\bbah\b", "vixe"),
        (r"\btch[eê]\b", "oxente"),
    ]
    for pat, rep in replacements:
        txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    # 2) Collapse exaggerated repeated vowels (e.g. Oxêêênte -> Oxente, Viixe -> Vixe)
    # without breaking legitimate Portuguese double vowels like 'caatinga' or 'preencher'
    txt = re.sub(r"\b[oô]+x[eê]+n+t+e+\b", "Oxente", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bv[iî]+x[eê]+\b", "Vixe", txt, flags=re.IGNORECASE)
    txt = re.sub(r"([áéíóúâêîôûà])\1{1,}", r"\1", txt, flags=re.IGNORECASE)
    txt = re.sub(r"([aeiou])\1{2,}", r"\1", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\.{3,}", ",", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def apply_nordestino_prosody(
    text: str,
    character: str = "dona_socorro",
    emotion: str = "warm",
    mode: str = "gemini",
) -> str:
    """Apply authentic Brazilian Northeast prosody without dragging syllables.

    - ``mode='gemini'`` enforces cultural sanitation, crisp colloquial contractions,
      and tight punctuation so Gemini Native TTS speaks fast and punchily.
    - ``mode='phonetic'`` injects subtle open-vowel cues ONLY for neural fallback engines.
    """
    txt = sanitize_northeast_slang(text)
    if not txt:
        return "Oxente!"

    char_id = normalize_character_id(character)
    if char_id != "enzo":
        gerund_rules = [
            (r"\b(procur)ando\b", r"\1ano"),
            (r"\b(bot)ando\b", r"\1ano"),
            (r"\b(olh)ando\b", r"\1ano"),
            (r"\b(fal)ando\b", r"\1ano"),
            (r"\b(chor)ando\b", r"\1ano"),
            (r"\b(esper)ando\b", r"\1ano"),
            (r"\b(faz)endo\b", r"\1eno"),
            (r"\b(diz)endo\b", r"\1eno"),
            (r"\b(v)endo\b", r"\1eno"),
            (r"\b(r)indo\b", r"\1ino"),
            (r"\b(v)indo\b", r"\1ino"),
        ]
        for pat, rep in gerund_rules:
            txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    colloq = [
        (r"\bpara\s+eu\b", "pr'eu" if mode == "gemini" else "pru eu"),
        (r"\bpara\b", "pra"),
        (r"\bestou\b", "tô"),
        (r"\bestá\b", "tá"),
        (r"\bmeu filho\b", "meu fí"),
        (r"\bmenino\b", "minino"),
        (r"\bsenhora\b", "sinhóra"),
        (r"\bconversa\b", "cunversa"),
        (r"\bvocê\b", "ocê"),
    ]
    for pat, rep in colloq:
        txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    # Crisp interjections (NO elongated vowels that drag Gemini speech!)
    txt = re.sub(r"\b[oô]xente\b", "Oxente", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bvixe\s+maria\b", "Vixe Maria", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\beita\b", "Eita", txt, flags=re.IGNORECASE)

    if mode == "phonetic":
        open_vowels = [
            (r"\bboneco\b", "bonéco"),
            (r"\bpre[aá]\b", "Preá"),
            (r"\bcaf[eé]\b", "café"),
            (r"\bn[aã]o\b", "num"),
        ]
        for pat, rep in open_vowels:
            txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    txt = re.sub(r"([!?])\s*,", r"\1", txt)
    txt = re.sub(r"([!?])\1+", r"\1", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _load_secrets_env() -> Dict[str, str]:
    env_map = dict(os.environ)
    for p in (
        STUDIO_BASE / "media-control-panel/.env",
        STUDIO_BASE / ".env",
        Path("/opt/ai-media-studio/media-control-panel/.env"),
    ):
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if k and k not in env_map:
                    env_map[k] = v.strip().strip("\"'")
    for vf in (
        STUDIO_BASE / "media-control-panel/vault.json",
        STUDIO_BASE / "media-control-panel/data/vault.json",
    ):
        if vf.is_file():
            try:
                data = json.loads(vf.read_text(encoding="utf-8"))
                for k, val in data.items():
                    if isinstance(val, str) and val.strip() and not env_map.get(k):
                        env_map[k] = val.strip()
            except Exception:
                pass
    return env_map


def _run_checked(cmd: List[str], timeout: int = 120) -> str:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd[:6])}\n{proc.stderr[-800:]}")
    return proc.stdout


def _probe_duration(path: Path) -> float:
    out = _run_checked([
        FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], timeout=20).strip()
    try:
        return max(0.25, float(out))
    except ValueError:
        return 1.5


def _pcm_to_48k_stereo_wav(
    raw_pcm_bytes: bytes,
    src_rate: int,
    out_wav: Path,
    lead_pad: float = 0.02,
    tail_pad: float = 0.05,
    codec: str = "s16le",
    src_channels: int = 1,
    comedy_fast: bool = True,
) -> float:
    """Convert raw PCM (or RIFF WAV) stream into agile 48kHz Stereo SoXR WAV."""
    raw_pcm_bytes = raw_pcm_bytes[: len(raw_pcm_bytes) - (len(raw_pcm_bytes) % 2)]
    tmp_pcm = out_wav.with_suffix(".raw.pcm")
    tmp_pcm.write_bytes(raw_pcm_bytes)
    speed_chain = (
        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-38dB:"
        "stop_periods=-1:stop_duration=0.18:stop_threshold=-36dB,atempo=1.16,"
        if comedy_fast
        else ""
    )
    try:
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-t", f"{lead_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-f", codec, "-ar", str(src_rate), "-ac", str(max(1, int(src_channels))), "-i", str(tmp_pcm),
            "-f", "lavfi", "-t", f"{tail_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex",
            f"[1:a]{speed_chain}highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,"
            "aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo[sp];"
            "[0:a][sp][2:a]concat=n=3:v=0:a=1[outa]",
            "-map", "[outa]",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(out_wav),
        ], timeout=60)
    finally:
        tmp_pcm.unlink(missing_ok=True)
    return _probe_duration(out_wav)


def _normalize_media_to_48k_wav(
    src_audio: Path,
    out_wav: Path,
    lead_pad: float = 0.03,
    tail_pad: float = 0.09,
    pitch_shift_edge_fallback: bool = False,
) -> float:
    af_core = (
        "[1:a]highpass=f=75,acompressor=threshold=-16dB:ratio=2.5:attack=10:release=100,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        "aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo[sp]"
    )
    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-t", f"{lead_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-i", str(src_audio),
        "-f", "lavfi", "-t", f"{tail_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", f"{af_core};[0:a][sp][2:a]concat=n=3:v=0:a=1[outa]",
        "-map", "[outa]",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(out_wav),
    ], timeout=60)
    return _probe_duration(out_wav)


# ---------------------------------------------------------------------------
# Regional Voice Profile Bank (`/opt/ai-media-studio/voices/regional/`)
# ---------------------------------------------------------------------------

def ensure_regional_voice_bank() -> Dict[str, Any]:
    """Ensure regional voices directory and profiles.json exist on disk."""
    VOICES_REGIONAL_DIR.mkdir(parents=True, exist_ok=True)
    saved: Dict[str, Any] = {}
    if PROFILES_META_PATH.is_file():
        try:
            saved = json.loads(PROFILES_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            saved = {}

    changed = False
    for cid, defaults in DEFAULT_REGIONAL_PROFILES.items():
        cdir = VOICES_REGIONAL_DIR / cid
        cdir.mkdir(parents=True, exist_ok=True)
        if cid not in saved or not isinstance(saved[cid], dict):
            saved[cid] = dict(defaults)
            changed = True
        else:
            for k, v in defaults.items():
                if k in ("style_instruction", "edge_rate", "edge_pitch", "default_sample_text"):
                    if saved[cid].get(k) != v:
                        saved[cid][k] = v
                        changed = True
                else:
                    saved[cid].setdefault(k, v)
        # Validate reference path still exists
        ref_rel = saved[cid].get("reference_file") or ""
        if ref_rel and not (VOICES_REGIONAL_DIR / ref_rel).is_file():
            saved[cid]["reference_file"] = ""
            saved[cid]["has_custom_reference"] = False
            changed = True

    if changed or not PROFILES_META_PATH.is_file():
        PROFILES_META_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return saved


def list_regional_voice_profiles() -> List[Dict[str, Any]]:
    profiles = ensure_regional_voice_bank()
    out: List[Dict[str, Any]] = []
    for cid, p in profiles.items():
        ref_rel = str(p.get("reference_file") or "")
        ref_abs = (VOICES_REGIONAL_DIR / ref_rel) if ref_rel else None
        has_ref = bool(ref_abs and ref_abs.is_file())
        out.append({
            "id": cid,
            "label": p.get("label") or cid,
            "role": p.get("role") or "",
            "region": p.get("region") or "",
            "gemini_voice": p.get("gemini_voice") or "Kore",
            "edge_voice": p.get("edge_voice") or "pt-BR-FranciscaNeural",
            "style_instruction": p.get("style_instruction") or "",
            "default_sample_text": p.get("default_sample_text") or "",
            "has_reference": has_ref,
            "has_custom_reference": bool(p.get("has_custom_reference") and has_ref),
            "reference_file": ref_rel if has_ref else "",
            "reference_path": str(ref_abs) if has_ref else "",
            "reference_text": p.get("reference_text") or p.get("default_sample_text") or "",
            "reference_preview_url": (
                f"/api/voices/regional/sample/{cid}" if has_ref else ""
            ),
        })
    return out


def save_regional_voice_reference(
    character_id: str,
    raw_bytes: bytes,
    orig_filename: str,
    reference_text: str = "",
) -> Dict[str, Any]:
    """Save and normalize an uploaded regional reference voice sample (6s-15s clean WAV) for zero-shot cloning/VC."""
    cid = normalize_character_id(character_id) if character_id not in DEFAULT_REGIONAL_PROFILES else character_id
    profiles = ensure_regional_voice_bank()
    cdir = (VOICES_REGIONAL_DIR / cid).resolve()
    cdir.mkdir(parents=True, exist_ok=True)

    ext = Path(orig_filename or "sample.wav").suffix.lower()
    if ext not in (".wav", ".mp3", ".ogg", ".m4a", ".webm", ".flac"):
        ext = ".wav"
    tmp_in = cdir / f"upload_tmp_{uuid.uuid4().hex[:6]}{ext}"
    out_wav = cdir / f"reference_{cid}.wav"
    tmp_in.write_bytes(raw_bytes)
    try:
        dur_in = _probe_duration(tmp_in)
        if dur_in < 0.8:
            raise ValueError(f"Áudio de referência muito curto ({dur_in:.2f}s); envie entre 4s e 15s.")
        # Normalize to crisp 24kHz mono <= 15s (ideal for F5-TTS-PT-BR & Seed-VC)
        _run_checked([
            FFMPEG_BIN, "-y", "-i", str(tmp_in),
            "-t", "15",
            "-vn", "-af", "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=24000:resampler=soxr",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out_wav),
        ], timeout=45)
    finally:
        tmp_in.unlink(missing_ok=True)

    final_dur = _probe_duration(out_wav)
    profiles[cid]["reference_file"] = f"{cid}/{out_wav.name}"
    profiles[cid]["has_custom_reference"] = True
    if reference_text.strip():
        profiles[cid]["reference_text"] = reference_text.strip()[:300]
    profiles[cid]["reference_duration_sec"] = round(final_dur, 2)
    PROFILES_META_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "character_id": cid,
        "reference_path": str(out_wav),
        "reference_file": f"{cid}/{out_wav.name}",
        "duration_sec": round(final_dur, 2),
        "reference_text": profiles[cid].get("reference_text") or profiles[cid].get("default_sample_text") or "",
        "reference_preview_url": f"/api/voices/regional/sample/{cid}",
    }


def _ensure_character_seed_reference(character_id: str, env_map: Dict[str, str]) -> Tuple[Optional[Path], str]:
    """Ensure a clean target timbre sample exists on disk for F5-TTS / Seed-VC (uses Gemini TTS if absent)."""
    profiles = ensure_regional_voice_bank()
    prof = profiles.get(character_id) or profiles["dona_socorro"]
    ref_rel = prof.get("reference_file") or ""
    if ref_rel and (VOICES_REGIONAL_DIR / ref_rel).is_file():
        return VOICES_REGIONAL_DIR / ref_rel, prof.get("reference_text") or prof.get("default_sample_text") or ""

    seed_wav = VOICES_REGIONAL_DIR / character_id / f"reference_{character_id}_seed.wav"
    if seed_wav.is_file() and seed_wav.stat().st_size > 2000:
        return seed_wav, prof.get("default_sample_text") or ""

    sample_text = prof.get("default_sample_text") or "Ôxente, rapaz! Agora sim tem sotaque nordestino de verdade!"
    gem_key = env_map.get("GEMINI_API_KEY") or env_map.get("GOOGLE_API_KEY") or ""
    if gem_key:
        try:
            pcm_bytes, srate, codec = _call_gemini_native_tts(
                text=sample_text,
                voice_name=prof.get("gemini_voice") or "Kore",
                style_prompt=prof.get("style_instruction") or "",
                emotion="warm",
                api_key=gem_key,
            )
            tmp_48k = seed_wav.with_suffix(".tmp48.wav")
            _pcm_to_48k_stereo_wav(pcm_bytes, srate, tmp_48k, lead_pad=0.04, tail_pad=0.08, codec=codec)
            _run_checked([
                FFMPEG_BIN, "-y", "-i", str(tmp_48k),
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                str(seed_wav),
            ], timeout=30)
            tmp_48k.unlink(missing_ok=True)
            prof["reference_file"] = f"{character_id}/{seed_wav.name}"
            prof["reference_text"] = sample_text
            PROFILES_META_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
            return seed_wav, sample_text
        except Exception:
            pass
    return None, sample_text


# ---------------------------------------------------------------------------
# Engine Implementations: Gemini Native TTS / Edge Fallback / F5-TTS / Seed-VC
# ---------------------------------------------------------------------------

_EXHAUSTED_RPD_KEYS: set[Tuple[str, str]] = set()
_LAST_GEMINI_CALL_TS: float = 0.0


def _collect_gemini_keys(env_map: Dict[str, str]) -> List[str]:
    """Collect distinct Gemini API keys configured in env/vault in priority order."""
    out: List[str] = []
    raw_multi = str(env_map.get("GEMINI_API_KEYS") or "").strip()
    if raw_multi:
        for part in re.split(r"[,;\s]+", raw_multi):
            if part and part not in out:
                out.append(part)
    for kname in (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GOOGLE_API_KEY",
    ):
        val = str(env_map.get(kname) or "").strip()
        if val and val not in out:
            out.append(val)
    return out


def _extract_audio_from_gemini_resp(data: Dict[str, Any]) -> Tuple[bytes, int, str, int]:
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    for p in parts:
        inline = p.get("inlineData") or {}
        if inline.get("data"):
            mime = str(inline.get("mimeType") or "audio/L16;codec=pcm;rate=24000")
            rate_m = re.search(r"rate=(\d+)", mime)
            srate = int(rate_m.group(1)) if rate_m else 24000
            raw = base64.b64decode(inline["data"])
            if len(raw) < 1000:
                raise RuntimeError("Gemini TTS retornou áudio vazio")
            if raw[:4] == b"RIFF" or "wav" in mime.lower():
                with wave.open(io.BytesIO(raw), "rb") as wf:
                    pcm = wf.readframes(wf.getnframes())
                    return pcm, int(wf.getframerate() or srate), "s16le", int(wf.getnchannels() or 1)
            return raw, srate, "s16le", 1
    raise RuntimeError("Resposta do Gemini TTS sem inlineData de áudio")


def _post_gemini_tts_payload(
    *,
    payload: Dict[str, Any],
    api_key: str,
    model: str = GEMINI_TTS_MODEL,
) -> Tuple[bytes, int, str, int]:
    global _LAST_GEMINI_CALL_TS
    gap = time.time() - _LAST_GEMINI_CALL_TS
    if gap < 0.45:
        time.sleep(0.45 - gap)
    _LAST_GEMINI_CALL_TS = time.time()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _extract_audio_from_gemini_resp(data)


def _call_gemini_native_tts(
    *,
    text: str,
    voice_name: str,
    style_prompt: str,
    emotion: str,
    api_key: str,
    model: str = GEMINI_TTS_MODEL,
) -> Tuple[bytes, int, str, int]:
    """Call Gemini Native TTS and return (raw_pcm_bytes, sample_rate, ffmpeg_codec, channels)."""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não configurada")

    emo_cue = EMOTION_ACTING_CUES_PT.get(emotion, EMOTION_ACTING_CUES_PT["warm"])
    director_prompt = (
        f"{style_prompt} {emo_cue} "
        f"Leia exatamente a fala abaixo com cadência rápida, viva e naturalidade nordestina, sem arrastar palavras e sem comentários: "
        f"\"{text}\""
    )
    payload = {
        "contents": [{"parts": [{"text": director_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name or "Kore",
                    }
                }
            },
        },
    }
    return _post_gemini_tts_payload(payload=payload, api_key=api_key, model=model)


def _call_gemini_resilient(
    *,
    env_map: Dict[str, str],
    build_payload_fn: Any,
    models: Tuple[str, ...] = GEMINI_TTS_MODELS,
) -> Tuple[bytes, int, str, int, str]:
    """Invoke Gemini TTS across rotated keys & models with anti-429 exponential backoff (2s -> 4s -> 8s)."""
    keys = _collect_gemini_keys(env_map)
    if not keys:
        raise RuntimeError("Nenhuma GEMINI_API_KEY configurada")

    last_err: Optional[Exception] = None
    for model in models:
        for key in keys:
            pair = (key[-6:], model)
            if pair in _EXHAUSTED_RPD_KEYS:
                continue
            for wait_s in (2.0, 4.0, 8.0):
                try:
                    payload = build_payload_fn(model)
                    pcm, sr, codec, ch = _post_gemini_tts_payload(payload=payload, api_key=key, model=model)
                    return pcm, sr, codec, ch, model
                except urllib.error.HTTPError as exc:
                    err_body = ""
                    try:
                        err_body = exc.read().decode("utf-8", errors="ignore")
                    except Exception:
                        pass
                    last_err = RuntimeError(f"{model} HTTP {exc.code}: {err_body[:240]}")
                    low_err = err_body.lower()
                    # Daily quota exhausted or invalid multi-speaker schema on this model -> switch immediately
                    if exc.code == 429 and ("perday" in low_err or "requests per day" in low_err):
                        _EXHAUSTED_RPD_KEYS.add(pair)
                        break
                    if exc.code == 400:
                        break
                    if exc.code in (429, 500, 502, 503, 504):
                        delay_m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', err_body)
                        sleep_for = min(12.0, float(delay_m.group(1))) if delay_m else wait_s
                        time.sleep(sleep_for)
                        continue
                    break
                except Exception as exc:
                    last_err = exc
                    time.sleep(wait_s)
    raise RuntimeError(f"Todos os modelos/chaves Gemini TTS falharam: {last_err}")


def synthesize_gemini_multispeaker_dialogue(
    *,
    dialogue: List[Dict[str, Any]],
    out_wav: Path,
    lead_pad: float = 0.02,
    tail_pad: float = 0.05,
) -> Dict[str, Any]:
    """Synthesize a multi-character conversation in ONE Gemini Multi-Speaker Native TTS API call."""
    env_map = _load_secrets_env()
    profiles = ensure_regional_voice_bank()
    speaker_configs: List[Dict[str, Any]] = []
    seen_speakers: set[str] = set()
    script_lines: List[str] = []

    for row in dialogue[:8]:
        spk_raw = str(row.get("speaker") or "Dona Socorro").strip()
        cid = normalize_character_id(spk_raw)
        prof = profiles.get(cid) or profiles["dona_socorro"]
        spk_label = prof.get("label") or spk_raw
        txt = apply_nordestino_prosody(
            str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or ""),
            character=cid,
            emotion=str(row.get("emotion") or "warm"),
            mode="gemini",
        )
        if not txt:
            continue
        if spk_label not in seen_speakers:
            seen_speakers.add(spk_label)
            speaker_configs.append({
                "speaker": spk_label,
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": prof.get("gemini_voice") or "Kore"}},
            })
        script_lines.append(f"{spk_label}: {txt}")

    if not script_lines:
        raise ValueError("Diálogo vazio para síntese multi-speaker")

    prompt = f"{NORDESTE_FAST_DIRECTOR_PROMPT}\nInterpretando em ritmo rápido e cômico:\n" + "\n".join(script_lines)

    def _build_ms_payload(_model: str) -> Dict[str, Any]:
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "multiSpeakerVoiceConfig": {"speakerVoiceConfigs": speaker_configs}
                },
            },
        }

    # Note: 3+ speakers work natively on gemini-3.1-flash-tts-preview
    ms_models = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts") if len(speaker_configs) <= 2 else ("gemini-3.1-flash-tts-preview",)
    pcm, srate, codec, ch, used_model = _call_gemini_resilient(
        env_map=env_map,
        build_payload_fn=_build_ms_payload,
        models=ms_models,
    )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    dur = _pcm_to_48k_stereo_wav(
        pcm,
        srate,
        out_wav,
        lead_pad=lead_pad,
        tail_pad=tail_pad,
        codec=codec,
        src_channels=ch,
        comedy_fast=True,
    )
    return {
        "ok": True,
        "voice_engine_used": f"gemini_tts:multi_speaker:{used_model}",
        "gemini_model": used_model,
        "speakers": [s["speaker"] for s in speaker_configs],
        "duration": round(dur, 3),
        "wav_path": str(out_wav),
    }


def _synthesize_edge_regional_fallback(
    *,
    tts_text: str,
    character_id: str,
    emotion: str,
    profile: Dict[str, Any],
    out_wav: Path,
    lead_pad: float,
    tail_pad: float,
) -> float:
    """Apply aggressive phonetics + edge-tts when Gemini TTS is unreachable/rate-limited."""
    phonetic_text = apply_nordestino_prosody(tts_text, character=character_id, emotion=emotion, mode="phonetic")
    raw_mp3 = out_wav.with_suffix(".edge.mp3")
    voice = str(profile.get("edge_voice") or "pt-BR-FranciscaNeural")
    rate = str(profile.get("edge_rate") or "+10%")
    pitch = str(profile.get("edge_pitch") or "+0Hz")
    cmd = [
        EDGE_TTS_BIN,
        "--voice", voice,
        f"--rate={rate}",
        f"--pitch={pitch}",
        "--text", phonetic_text,
        "--write-media", str(raw_mp3),
    ]
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            _run_checked(cmd, timeout=45)
            if raw_mp3.exists() and raw_mp3.stat().st_size > 800:
                break
        except Exception as exc:
            last_err = exc
            time.sleep(0.6 * (attempt + 1))
    else:
        raise RuntimeError(f"edge-tts regional fallback falhou: {last_err}")

    try:
        return _normalize_media_to_48k_wav(raw_mp3, out_wav, lead_pad=lead_pad, tail_pad=tail_pad)
    finally:
        raw_mp3.unlink(missing_ok=True)


def _call_colab_voice_endpoint(
    *,
    endpoint_path: str,
    colab_url: str,
    colab_token: str,
    payload: Dict[str, Any],
    out_wav: Path,
    lead_pad: float,
    tail_pad: float,
) -> float:
    url = f"{colab_url.rstrip('/')}{endpoint_path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if colab_token:
        headers["X-Colab-Token"] = colab_token
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok") or not data.get("audio_b64"):
        raise RuntimeError(f"Colab {endpoint_path} falhou: {data.get('error') or 'sem audio_b64'}")
    tmp_raw = out_wav.with_suffix(".colab_raw.wav")
    tmp_raw.write_bytes(base64.b64decode(data["audio_b64"]))
    try:
        return _normalize_media_to_48k_wav(tmp_raw, out_wav, lead_pad=lead_pad, tail_pad=tail_pad)
    finally:
        tmp_raw.unlink(missing_ok=True)


def synthesize_regional_turn(
    *,
    tts_text: str,
    speaker: str,
    emotion: str = "warm",
    accent_engine: str = "nordestino_raiz",
    accent_preset: str = "nordestino_raiz",
    out_wav: Path,
    guide_audio_path: Optional[Path] = None,
    colab_url: str = "",
    colab_token: str = "",
    lead_pad: float = 0.03,
    tail_pad: float = 0.09,
    voice_override: str = "",
) -> Dict[str, Any]:
    """Synthesize one dialogue turn in authentic Northeast prosody as 48kHz Stereo SoXR WAV."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    env_map = _load_secrets_env()
    profiles = ensure_regional_voice_bank()
    cid = normalize_character_id(speaker)
    prof = profiles.get(cid) or profiles["dona_socorro"]

    eff_engine = (accent_engine or accent_preset or "nordestino_raiz").strip().lower()
    if eff_engine not in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide"):
        eff_engine = "nordestino_raiz"

    eff_colab_url = (colab_url or env_map.get("COLAB_GPU_URL") or "").strip()
    eff_colab_tok = (colab_token or env_map.get("COLAB_GPU_TOKEN") or "").strip()
    processed_text = apply_nordestino_prosody(tts_text, character=cid, emotion=emotion, mode="gemini")

    # 1) Direct / Seed-VC Speech-to-Speech when user recorded a guide clip for this line
    if eff_engine == "seed_vc_guide" and guide_audio_path and Path(guide_audio_path).is_file():
        g_path = Path(guide_audio_path)
        target_wav, _ = _ensure_character_seed_reference(cid, env_map)
        if eff_colab_url and target_wav and target_wav.is_file():
            try:
                dur = _call_colab_voice_endpoint(
                    endpoint_path="/voice-convert",
                    colab_url=eff_colab_url,
                    colab_token=eff_colab_tok,
                    payload={
                        "guide_audio_b64": base64.b64encode(g_path.read_bytes()).decode("ascii"),
                        "target_timbre_b64": base64.b64encode(target_wav.read_bytes()).decode("ascii"),
                        "character_id": cid,
                    },
                    out_wav=out_wav,
                    lead_pad=lead_pad,
                    tail_pad=tail_pad,
                )
                return {
                    "ok": True,
                    "character_id": cid,
                    "accent_engine_requested": eff_engine,
                    "voice_engine_used": "seed_vc_colab",
                    "processed_text": processed_text,
                    "duration": round(dur, 3),
                    "wav_path": str(out_wav),
                }
            except Exception:
                pass
        # Fallback: normalize the user's real regional voice recording cleanly!
        dur = _normalize_media_to_48k_wav(g_path, out_wav, lead_pad=lead_pad, tail_pad=tail_pad)
        return {
            "ok": True,
            "character_id": cid,
            "accent_engine_requested": eff_engine,
            "voice_engine_used": "direct_user_guide_audio",
            "processed_text": processed_text,
            "duration": round(dur, 3),
            "wav_path": str(out_wav),
        }

    # 2) Zero-Shot F5-TTS-PT-BR Cloning via Colab GPU when configured
    if eff_engine == "f5_tts_clone" and eff_colab_url:
        ref_wav, ref_text = _ensure_character_seed_reference(cid, env_map)
        if ref_wav and ref_wav.is_file():
            try:
                dur = _call_colab_voice_endpoint(
                    endpoint_path="/tts-clone",
                    colab_url=eff_colab_url,
                    colab_token=eff_colab_tok,
                    payload={
                        "text": processed_text,
                        "ref_audio_b64": base64.b64encode(ref_wav.read_bytes()).decode("ascii"),
                        "ref_text": ref_text,
                        "character_id": cid,
                    },
                    out_wav=out_wav,
                    lead_pad=lead_pad,
                    tail_pad=tail_pad,
                )
                return {
                    "ok": True,
                    "character_id": cid,
                    "accent_engine_requested": eff_engine,
                    "voice_engine_used": "f5_tts_pt_br_colab",
                    "processed_text": processed_text,
                    "duration": round(dur, 3),
                    "wav_path": str(out_wav),
                }
            except Exception:
                pass

    # 3) Primary Engine: 100% Gemini Native Audio TTS with Multi-Model + Key Rotation + Backoff
    gem_keys = _collect_gemini_keys(env_map)
    gem_voice = (voice_override or prof.get("gemini_voice") or "Kore").strip()
    if gem_keys:
        emo_cue = EMOTION_ACTING_CUES_PT.get(emotion, EMOTION_ACTING_CUES_PT["warm"])
        style_inst = prof.get("style_instruction") or NORDESTE_FAST_DIRECTOR_PROMPT
        director_prompt = (
            f"{style_inst} {emo_cue} "
            f"Leia exatamente a fala abaixo com cadência rápida, viva e naturalidade nordestina, sem arrastar palavras e sem comentários: "
            f"\"{processed_text}\""
        )

        def _build_turn_payload(model_name: str) -> Dict[str, Any]:
            # Prompt-capable 2.5/3.1 models take short acting direction; 3.8 raw TTS models take pure text ONLY!
            if "3.8" in (model_name or ""):
                prompt_text = processed_text
            else:
                prompt_text = (
                    f"{style_inst} {emo_cue} "
                    f"Fale somente o texto entre aspas com ritmo rápido nordestino, sem comentários: "
                    f"\"{processed_text}\""
                )
            return {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": gem_voice,
                            }
                        }
                    },
                },
            }

        try:
            pcm_bytes, srate, codec, src_ch, used_model = _call_gemini_resilient(
                env_map=env_map,
                build_payload_fn=_build_turn_payload,
                models=GEMINI_TTS_MODELS,
            )
            dur = _pcm_to_48k_stereo_wav(
                pcm_bytes,
                srate,
                out_wav,
                lead_pad=lead_pad,
                tail_pad=tail_pad,
                codec=codec,
                src_channels=src_ch,
                comedy_fast=True,
            )
            return {
                "ok": True,
                "character_id": cid,
                "accent_engine_requested": eff_engine,
                "voice_engine_used": f"gemini_tts:{gem_voice}",
                "gemini_model": used_model,
                "processed_text": processed_text,
                "duration": round(dur, 3),
                "wav_path": str(out_wav),
            }
        except Exception as gem_exc:
            # Log failure transparently before falling back ONLY when all Gemini keys/models failed
            print(f"[RVE WARNING] Gemini TTS falhou em todos os modelos para {cid}: {gem_exc}", flush=True)

    # 4) Offline / Unconfigured Key Edge-TTS Phonetic Fallback
    dur = _synthesize_edge_regional_fallback(
        tts_text=tts_text,
        character_id=cid,
        emotion=emotion,
        profile=prof,
        out_wav=out_wav,
        lead_pad=lead_pad,
        tail_pad=tail_pad,
    )
    return {
        "ok": True,
        "character_id": cid,
        "accent_engine_requested": eff_engine,
        "voice_engine_used": "edge_tts_regional_fallback",
        "processed_text": apply_nordestino_prosody(tts_text, character=cid, emotion=emotion, mode="phonetic"),
        "duration": round(dur, 3),
        "wav_path": str(out_wav),
    }


def synthesize_dialogue_preview(
    *,
    dialogue: List[Dict[str, Any]],
    accent_engine: str = "nordestino_raiz",
    out_mp3: Path,
    colab_url: str = "",
    colab_token: str = "",
) -> Dict[str, Any]:
    """Synthesize rapid multi-character or single-line MP3 audio preview (48kHz Stereo SoXR)."""
    if not dialogue:
        raise ValueError("Diálogo vazio para preview de voz.")
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(f"/tmp/reg_preview_{uuid.uuid4().hex[:8]}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    turn_metas: List[Dict[str, Any]] = []
    wavs: List[Path] = []
    try:
        for idx, row in enumerate(dialogue[:8]):
            txt = str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or "").strip()
            if not txt:
                continue
            wav_p = tmp_dir / f"prev_{idx:02d}.wav"
            g_raw = str(row.get("guide_audio_path") or "").strip()
            g_path = Path(g_raw) if g_raw and Path(g_raw).is_file() else None
            meta = synthesize_regional_turn(
                tts_text=txt,
                speaker=str(row.get("speaker") or "Dona Socorro"),
                emotion=str(row.get("emotion") or "warm").strip().lower(),
                accent_engine=accent_engine,
                out_wav=wav_p,
                guide_audio_path=g_path,
                colab_url=colab_url,
                colab_token=colab_token,
                lead_pad=0.02,
                tail_pad=0.07,
            )
            wavs.append(wav_p)
            turn_metas.append(meta)

        if not wavs:
            raise ValueError("Nenhuma fala válida para preview.")

        c_txt = tmp_dir / "concat.txt"
        c_txt.write_text("\n".join(f"file '{w}'" for w in wavs) + "\n", encoding="utf-8")
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(c_txt),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000:resampler=soxr",
            "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(out_mp3),
        ], timeout=60)
        total_dur = _probe_duration(out_mp3)
        return {
            "ok": True,
            "audio_path": str(out_mp3),
            "filename": out_mp3.name,
            "duration": round(total_dur, 2),
            "turns_count": len(turn_metas),
            "engines_used": sorted({m["voice_engine_used"] for m in turn_metas}),
            "turns": turn_metas,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
