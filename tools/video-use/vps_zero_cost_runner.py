#!/usr/bin/env python3
"""Zero-cost Video Use Jump-Cut & Polish Pro runner for 2-vCPU VPS.

Uses local faster-whisper INT8 (word_timestamps=True, vad_filter=True, cpu_threads=2),
VPS AI Cascade Gateway (optional editorial polish), per-segment FFmpeg grading with
30ms audio fades + 48kHz Stereo SoXR resampling, and output-timeline ASS subtitles.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

for _env_k, _env_v in (("OMP_NUM_THREADS", "2"), ("MKL_NUM_THREADS", "2"), ("CT2_NUM_THREADS", "2")):
    os.environ.setdefault(_env_k, _env_v)

SUPOCLIP_HF_CACHE = Path("/opt/ai-media-studio/supoclip/.cache/huggingface")
if SUPOCLIP_HF_CACHE.exists() and not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(SUPOCLIP_HF_CACHE)

import httpx  # noqa: E402
import numpy as np  # noqa: E402
from faster_whisper import WhisperModel  # noqa: E402

GRADE_PRESETS: Dict[str, str] = {
    "neutral_clean": "eq=contrast=1.03:brightness=0.01:saturation=1.02",
    "Neutral Clean": "eq=contrast=1.03:brightness=0.01:saturation=1.02",
    "cinema_warm": "colorbalance=rs=0.05:gs=0.01:bs=-0.05:rm=0.03:bm=-0.04,eq=contrast=1.08:saturation=0.95",
    "Cinema Warm": "colorbalance=rs=0.05:gs=0.01:bs=-0.05:rm=0.03:bm=-0.04,eq=contrast=1.08:saturation=0.95",
    "ecommerce_vibrant": "eq=contrast=1.10:saturation=1.22:brightness=0.02",
    "E-Commerce Vibrant": "eq=contrast=1.10:saturation=1.22:brightness=0.02",
}

FILLER_TOKENS = {
    "uh", "um", "uhm", "er", "erm", "ah", "eh", "hã", "hm", "hmm",
    "éé", "ééé", "aham", "aam",
}

_MODEL_CACHE: Dict[str, WhisperModel] = {}


def _get_model(model_size: str = "base") -> WhisperModel:
    if model_size not in _MODEL_CACHE:
        _MODEL_CACHE[model_size] = WhisperModel(
            model_size,
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
            num_workers=1,
        )
    return _MODEL_CACHE[model_size]


def _probe_video(path: Path) -> Dict[str, Any]:
    cmd = ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)]
    res = subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
    data = json.loads(res.stdout)
    streams = data.get("streams", [])
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = float((data.get("format") or {}).get("duration") or 0.0)
    if duration <= 0 and v_stream:
        duration = float(v_stream.get("duration") or 0.0)
    w = int(v_stream.get("width", 1080)) if v_stream else 1080
    h = int(v_stream.get("height", 1920)) if v_stream else 1920
    return {
        "duration": max(0.1, duration),
        "width": w + (w % 2),
        "height": h + (h % 2),
        "has_audio": a_stream is not None,
    }


def _transcribe_words(
    video: Path,
    cache_dir: Path,
    model_size: str = "base",
    language: Optional[str] = None,
) -> Dict[str, Any]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    sig = hashlib.sha256(f"{video.resolve()}:{video.stat().st_mtime_ns}:{video.stat().st_size}".encode()).hexdigest()[:16]
    cache_file = cache_dir / f"{video.stem}.{sig}.transcript.json"
    if cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    with tempfile.TemporaryDirectory(prefix="videouse_asr_") as tmp:
        wav = Path(tmp) / "audio16k.wav"
        subprocess.run(
            [
                "ffmpeg", "-y", "-v", "error", "-i", str(video),
                "-vn", "-ac", "1", "-ar", "16000", "-threads", "2", str(wav),
            ],
            check=True,
            capture_output=True,
            timeout=180,
        )
        with wave.open(str(wav), "rb") as wf:
            pcm_bytes = wf.readframes(wf.getnframes())
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        model = _get_model(model_size)
        lang_code = None if not language or language == "auto" else language.split("-")[0].split("_")[0]
        segs_iter, info = model.transcribe(
            audio_np,
            language=lang_code,
            beam_size=1,
            vad_filter=True,
            word_timestamps=True,
        )
        words: List[Dict[str, Any]] = []
        full_texts: List[str] = []
        for seg in segs_iter:
            txt = (seg.text or "").strip()
            if txt:
                full_texts.append(txt)
            for w in getattr(seg, "words", None) or []:
                token = (w.word or "").strip()
                if not token:
                    continue
                ws = float(w.start)
                we = max(ws + 0.04, float(w.end))
                norm = re.sub(r"[^\wÀ-ÿ]", "", token.lower())
                words.append({
                    "text": token,
                    "start": round(ws, 3),
                    "end": round(we, 3),
                    "is_filler": norm in FILLER_TOKENS,
                })

        payload = {
            "language": getattr(info, "language", lang_code or "pt"),
            "text": " ".join(full_texts).strip(),
            "words": words,
        }
        cache_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload


def _detect_non_silent_ffmpeg(
    video: Path,
    duration: float,
    threshold_db: str = "-38dB",
    min_silence: float = 0.45,
) -> List[Tuple[float, float]]:
    res = subprocess.run(
        [
            "ffmpeg", "-v", "info", "-i", str(video),
            "-af", f"silencedetect=noise={threshold_db}:d={min_silence}",
            "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    silences: List[Tuple[float, float]] = []
    cur_start: Optional[float] = None
    for line in (res.stderr or "").splitlines():
        m_start = re.search(r"silence_start:\s*([0-9.]+)", line)
        if m_start:
            cur_start = float(m_start.group(1))
            continue
        m_end = re.search(r"silence_end:\s*([0-9.]+)", line)
        if m_end and cur_start is not None:
            silences.append((cur_start, float(m_end.group(1))))
            cur_start = None
    if cur_start is not None:
        silences.append((cur_start, duration))

    if not silences:
        return [(0.0, round(duration, 3))]

    kept: List[Tuple[float, float]] = []
    pos = 0.0
    pad = 0.06
    for s0, s1 in silences:
        seg_end = min(duration, s0 + pad)
        if seg_end - pos >= 0.25:
            kept.append((round(pos, 3), round(seg_end, 3)))
        pos = max(pos, s1 - pad)
    if duration - pos >= 0.25:
        kept.append((round(pos, 3), round(duration, 3)))
    return kept or [(0.0, round(duration, 3))]


def _build_jump_cut_segments(
    words: List[Dict[str, Any]],
    duration: float,
    video: Path,
    has_audio: bool,
    remove_silences: bool = True,
    max_gap_s: float = 0.45,
) -> List[Tuple[float, float]]:
    if not remove_silences:
        return [(0.0, round(duration, 3))]

    kept_words = [w for w in words if not w.get("is_filler")]
    if not kept_words:
        if has_audio:
            return _detect_non_silent_ffmpeg(video, duration, min_silence=max(0.30, max_gap_s))
        return [(0.0, round(duration, 3))]

    pad_in = 0.08
    pad_out = 0.08
    raw_segs: List[Tuple[float, float]] = []
    cur_s = max(0.0, float(kept_words[0]["start"]) - pad_in)
    cur_e = min(duration, float(kept_words[0]["end"]) + pad_out)

    for w in kept_words[1:]:
        ws = max(0.0, float(w["start"]) - pad_in)
        we = min(duration, float(w["end"]) + pad_out)
        if ws - cur_e <= max_gap_s:
            cur_e = max(cur_e, we)
        else:
            if cur_e - cur_s >= 0.18:
                raw_segs.append((round(cur_s, 3), round(cur_e, 3)))
            cur_s, cur_e = ws, we

    if cur_e - cur_s >= 0.18:
        raw_segs.append((round(cur_s, 3), round(cur_e, 3)))

    return raw_segs or [(0.0, round(duration, 3))]


def _optional_ai_review(words: List[Dict[str, Any]], gateway_url: str = "http://127.0.0.1:9000/v1/chat/completions") -> str:
    if len(words) < 10:
        return ""
    sample_text = " ".join(w["text"] for w in words[:80])
    try:
        with httpx.Client(timeout=6.0) as client:
            r = client.post(
                gateway_url,
                json={
                    "model": "cascade-auto",
                    "messages": [
                        {"role": "system", "content": "Você é um editor de vídeo conciso. Resuma em 1 frase curta em português o foco do clipe."},
                        {"role": "user", "content": sample_text},
                    ],
                    "max_tokens": 60,
                    "temperature": 0.2,
                },
            )
            if r.status_code == 200:
                return ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    except Exception:
        pass
    return ""


def _fmt_ass_time(seconds: float) -> str:
    cs = max(0, int(round(seconds * 100)))
    h = cs // 360_000
    m = (cs % 360_000) // 6000
    s = (cs % 6000) // 100
    rem = cs % 100
    return f"{h}:{m:02d}:{s:02d}.{rem:02d}"


def _build_output_ass(
    words: List[Dict[str, Any]],
    segments: List[Tuple[float, float]],
    ass_path: Path,
    width: int,
    height: int,
    platform_preset: str = "shopee_20_9",
) -> int:
    """Build clean ASS subtitles mapped onto post-cut output timeline (Hard Rule #5)."""
    mapped: List[Dict[str, Any]] = []
    offset = 0.0
    for seg_s, seg_e in segments:
        seg_dur = seg_e - seg_s
        for w in words:
            if w.get("is_filler"):
                continue
            mid = 0.5 * (w["start"] + w["end"])
            if seg_s <= mid <= seg_e:
                out_s = max(offset, offset + (w["start"] - seg_s))
                out_e = min(offset + seg_dur, max(out_s + 0.06, offset + (w["end"] - seg_s)))
                mapped.append({"text": w["text"], "start": out_s, "end": out_e})
        offset += seg_dur

    if not mapped:
        return 0

    chunks: List[Tuple[float, float, str]] = []
    idx = 0
    while idx < len(mapped):
        grp = mapped[idx:idx + 3]
        text = " ".join(x["text"] for x in grp).strip().upper().replace("{", "").replace("}", "")
        c_start = grp[0]["start"]
        c_end = grp[-1]["end"]
        if chunks and c_start < chunks[-1][1]:
            c_start = chunks[-1][1]
        if c_end <= c_start:
            c_end = c_start + 0.25
        chunks.append((c_start, c_end, text))
        idx += 3

    is_vertical = height > width
    preset = (platform_preset or "shopee_20_9").strip().lower()
    # Shopee 20:9 Anti-Crop Safe-Zone on 1080x1920: MarginL=96, MarginR=196, MarginV=826
    font_size = max(32, int(round(min(width, height) * (0.035 if is_vertical else 0.050))))
    if is_vertical:
        if preset in ("tiktok_reels", "yt_shorts"):
            margin_v = int(round(height * 0.30))
        else:
            margin_v = 826 if height == 1920 else int(round(height * 826 / 1920))
        margin_l = 96 if width == 1080 else max(48, int(round(width * 96 / 1080)))
        margin_r = 196 if width == 1080 else max(80, int(round(width * 196 / 1080)))
    else:
        margin_v = int(round(height * 0.10))
        margin_l = 48
        margin_r = 48
    header = "\n".join([
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {width}",
        f"PlayResY: {height}",
        "WrapStyle: 2",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,DejaVu Sans,{font_size},&H00FFFFFF,&H0000FFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,4,1,2,{margin_l},{margin_r},{margin_v},1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])
    events = [
        f"Dialogue: 0,{_fmt_ass_time(s0)},{_fmt_ass_time(s1)},Default,,0,0,0,,{{\\an2}}{txt}"
        for s0, s1, txt in chunks
    ]
    ass_path.write_text(header + "\n" + "\n".join(events) + "\n", encoding="utf-8")
    return len(chunks)


def process_video(
    input_path: Path,
    output_path: Path,
    grade_preset: str = "neutral_clean",
    remove_silences: bool = True,
    max_gap_s: float = 0.45,
    burn_subtitles: bool = True,
    language: Optional[str] = "pt",
    use_ai_director: bool = True,
    platform_preset: str = "shopee_20_9",
) -> Dict[str, Any]:
    if not input_path.exists():
        raise FileNotFoundError(f"Arquivo de entrada não encontrado: {input_path}")

    t0 = time.monotonic()
    info = _probe_video(input_path)
    duration_in = info["duration"]
    has_audio = info["has_audio"]
    width = info["width"]
    height = info["height"]

    grade_filter = GRADE_PRESETS.get(grade_preset, GRADE_PRESETS["neutral_clean"])
    cache_dir = Path("/opt/ai-media-studio/tools/video-use/.cache_transcripts")

    words: List[Dict[str, Any]] = []
    detected_lang = language or "pt"
    if has_audio:
        asr = _transcribe_words(input_path, cache_dir, model_size="base", language=language)
        words = asr.get("words", [])
        detected_lang = asr.get("language", detected_lang)

    segments = _build_jump_cut_segments(
        words=words,
        duration=duration_in,
        video=input_path,
        has_audio=has_audio,
        remove_silences=remove_silences,
        max_gap_s=max_gap_s,
    )
    ai_note = _optional_ai_review(words) if use_ai_director else ""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="videouse_render_") as tmpdir:
        tmp = Path(tmpdir)
        seg_files: List[Path] = []
        vf_base = (
            f"{grade_filter},scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
            if grade_filter
            else "scale=trunc(iw/2)*2:trunc(ih/2)*2,format=yuv420p"
        )

        for idx, (s0, s1) in enumerate(segments):
            seg_dur = max(0.15, s1 - s0)
            fade_d = min(0.03, seg_dur * 0.2)
            out_st = max(0.0, seg_dur - fade_d)
            seg_mp4 = tmp / f"seg_{idx:03d}.mp4"
            if has_audio:
                af_chain = (
                    f"afade=t=in:st=0:d={fade_d:.3f},"
                    f"afade=t=out:st={out_st:.3f}:d={fade_d:.3f},"
                    "aresample=resampler=soxr"
                )
                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", f"{s0:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(input_path),
                    "-vf", vf_base,
                    "-af", af_chain,
                    "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    str(seg_mp4),
                ]
            else:
                cmd = [
                    "ffmpeg", "-y", "-v", "error",
                    "-ss", f"{s0:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(input_path),
                    "-f", "lavfi", "-t", f"{seg_dur:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                    "-vf", vf_base,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest",
                    str(seg_mp4),
                ]
            subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=300)
            seg_files.append(seg_mp4)

        concat_txt = tmp / "concat.txt"
        concat_txt.write_text("\n".join(f"file '{p}'" for p in seg_files), encoding="utf-8")
        base_mp4 = tmp / "base.mp4"
        subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(concat_txt), "-c", "copy", str(base_mp4)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )

        ass_path = tmp / "master.ass"
        sub_count = _build_output_ass(words, segments, ass_path, width, height, platform_preset=platform_preset) if burn_subtitles else 0

        if burn_subtitles and sub_count > 0:
            esc_ass = str(ass_path).replace("\\", "/").replace(":", "\\:")
            subprocess.run(
                [
                    "ffmpeg", "-y", "-v", "error", "-i", str(base_mp4),
                    "-vf", f"ass=filename={esc_ass}",
                    "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-crf", "19", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(output_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=300,
            )
        else:
            subprocess.run(
                ["ffmpeg", "-y", "-v", "error", "-i", str(base_mp4), "-c", "copy", "-movflags", "+faststart", str(output_path)],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

    out_info = _probe_video(output_path)
    duration_out = out_info["duration"]
    elapsed_s = round(time.monotonic() - t0, 2)
    return {
        "ok": True,
        "input_file": input_path.name,
        "output_path": str(output_path),
        "filename": output_path.name,
        "size_bytes": output_path.stat().st_size,
        "duration_in": round(duration_in, 2),
        "duration_out": round(duration_out, 2),
        "saved_seconds": round(max(0.0, duration_in - duration_out), 2),
        "segments_kept": len(segments),
        "words_count": len(words),
        "subtitles_chunks": sub_count,
        "subtitles_burned": bool(burn_subtitles and sub_count > 0),
        "grade_preset": grade_preset,
        "platform_preset": platform_preset,
        "language": detected_lang,
        "ai_note": ai_note,
        "elapsed_seconds": elapsed_s,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Zero-cost Video Use Jump-Cut & Polish Pro runner")
    ap.add_argument("input", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--grade", type=str, default="neutral_clean")
    ap.add_argument("--no-silence-cut", action="store_true")
    ap.add_argument("--max-gap", type=float, default=0.45)
    ap.add_argument("--no-subtitles", action="store_true")
    ap.add_argument("--language", type=str, default="pt")
    ap.add_argument("--platform-preset", type=str, default="shopee_20_9")
    args = ap.parse_args()

    res = process_video(
        input_path=args.input,
        output_path=args.output,
        grade_preset=args.grade,
        remove_silences=not args.no_silence_cut,
        max_gap_s=args.max_gap,
        burn_subtitles=not args.no_subtitles,
        language=args.language,
        platform_preset=args.platform_preset,
    )
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
