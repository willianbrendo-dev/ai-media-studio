#!/usr/bin/env python3
"""Computer Vision Lip-Sync & Facial Motion Auditor (v2.7).

Audits any animated talking-head .mp4 (single take or multi-cut novela episode)
without relying on static/Ken-Burns zoom artifacts:
  1. Extracts audio RMS at exact video FPS (30fps) via FFmpeg.
  2. Segments shot cuts automatically (hist/MAD jump) and detects each character's
     facial rig (MediaPipe FaceLandmarker -> Haar -> Anatomical).
  3. Tracks and compensates head sway per frame using forehead/nose-bridge template
     matching (`cv2.matchTemplate`), isolating TRUE local articulation in:
       - Mouth & Jaw ROI (loud speech frames vs pause/minimum-RMS frames)
       - Eye / Eyelid ROI (blink transients across the segment)
  4. Asserts mathematically (`mouth_roi_diff >= min_mouth_diff`) that the mouth opens
     and closes in sync with audio amplitude and is not a frozen/zooming photograph.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Tuple

import cv2
import numpy as np

for candidate in (
    Path("/opt/ai-media-studio/tools/hyperframes"),
    Path(__file__).resolve().parent.parent / "tools/hyperframes",
):
    if candidate.is_dir() and str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

import novela_lipsync_animator as nla  # noqa: E402


def _extract_rms_per_frame(mp4_path: Path, fps: float, num_frames: int) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tf:
        wav_p = Path(tf.name)
    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(mp4_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le",
                str(wav_p),
            ],
            check=True,
            timeout=30,
        )
        with wave.open(str(wav_p), "rb") as wf:
            pcm = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    finally:
        wav_p.unlink(missing_ok=True)

    rms = np.zeros(num_frames, dtype=np.float32)
    step = max(1.0, len(pcm) / float(max(1, num_frames)))
    win = max(256, int(step * 1.4))
    for i in range(num_frames):
        c = int(round(i * step))
        s = max(0, c - win // 2)
        seg = pcm[s : s + win]
        if len(seg) > 0:
            rms[i] = float(np.sqrt(np.mean(seg * seg) + 1e-10))
    return rms


def _track_anchor_shift(
    ref_gray: np.ndarray,
    cur_gray: np.ndarray,
    ax: int,
    ay: int,
    aw: int,
    ah: int,
    pad: int = 28,
) -> Tuple[int, int]:
    h, w = ref_gray.shape
    x0, y0 = max(0, ax - aw // 2), max(0, ay - ah // 2)
    x1, y1 = min(w, x0 + aw), min(h, y0 + ah)
    tmpl = ref_gray[y0:y1, x0:x1]
    if tmpl.size < 64:
        return 0, 0
    sx0, sy0 = max(0, x0 - pad), max(0, y0 - pad)
    sx1, sy1 = min(w, x1 + pad), min(h, y1 + pad)
    search = cur_gray[sy0:sy1, sx0:sx1]
    if search.shape[0] <= tmpl.shape[0] or search.shape[1] <= tmpl.shape[1]:
        return 0, 0
    res = cv2.matchTemplate(search, tmpl, cv2.TM_CCOEFF_NORMED)
    _, _, _, max_loc = cv2.minMaxLoc(res)
    dx = (sx0 + max_loc[0]) - x0
    dy = (sy0 + max_loc[1]) - y0
    return int(dx), int(dy)


def audit_lipsync_video(mp4_path: Path, min_mouth_diff: float = 3.5) -> Dict[str, Any]:
    if not mp4_path.is_file():
        raise FileNotFoundError(f"Arquivo não encontrado: {mp4_path}")

    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        raise RuntimeError(f"Falha ao abrir vídeo {mp4_path}")

    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    frames_bgr: List[np.ndarray] = []
    frames_gray: List[np.ndarray] = []
    while True:
        ok, fr = cap.read()
        if not ok:
            break
        frames_bgr.append(fr)
        frames_gray.append(cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY))
    cap.release()

    n_frames = len(frames_bgr)
    if n_frames < 12:
        raise RuntimeError(f"Vídeo muito curto para auditoria ({n_frames} frames)")

    rms = _extract_rms_per_frame(mp4_path, fps, n_frames)

    # Detect hard scene cuts between takes
    cuts = [0]
    for i in range(1, n_frames):
        # Downsampled MAD detects shot-reverse-shot transitions cleanly
        small_a = cv2.resize(frames_gray[i - 1], (96, 160))
        small_b = cv2.resize(frames_gray[i], (96, 160))
        mad = float(np.mean(np.abs(small_a.astype(np.float32) - small_b.astype(np.float32))))
        if mad > 24.0 and (i - cuts[-1]) >= 12:
            cuts.append(i)
    cuts.append(n_frames)

    scene_reports: List[Dict[str, Any]] = []
    for s_idx in range(len(cuts) - 1):
        f_start, f_end = cuts[s_idx], cuts[s_idx + 1]
        if f_end - f_start < 10:
            continue
        seg_rms = rms[f_start:f_end]
        p20 = float(np.percentile(seg_rms, 18))
        p80 = float(np.percentile(seg_rms, 80))

        quiet_rel = int(np.argmin(seg_rms))
        ref_idx = f_start + quiet_rel
        ref_bgr = frames_bgr[ref_idx]
        ref_gray = frames_gray[ref_idx]
        rig = nla.detect_face_rig(ref_bgr)

        fx0, fy0, fw, fh = rig["face_box"]
        mcx, mcy = rig["mouth_center"]
        mw = max(32.0, rig["mouth_width"])

        # Forehead / nose bridge anchor above mouth for head-motion stabilization
        ax, ay = int(round(fx0 + 0.50 * fw)), int(round(fy0 + 0.42 * fh))
        aw, ah = max(36, int(round(0.32 * fw))), max(28, int(round(0.20 * fh)))

        m_half_w = max(22, int(round(0.62 * mw)))
        m_half_h = max(18, int(round(0.15 * fh)))
        mx0 = int(np.clip(mcx - m_half_w, 0, ref_gray.shape[1] - 2))
        my0 = int(np.clip(mcy - m_half_h * 0.65, 0, ref_gray.shape[0] - 2))
        mx1 = int(np.clip(mcx + m_half_w, mx0 + 8, ref_gray.shape[1]))
        my1 = int(np.clip(mcy + m_half_h * 1.45, my0 + 8, ref_gray.shape[0]))
        ref_mouth = ref_gray[my0:my1, mx0:mx1].astype(np.float32)

        lex, ley, lew, leh = rig["left_eye"]
        ex0 = int(np.clip(lex - 0.55 * lew, 0, ref_gray.shape[1] - 2))
        ey0 = int(np.clip(ley - 0.90 * leh, 0, ref_gray.shape[0] - 2))
        ex1 = int(np.clip(lex + 0.55 * lew, ex0 + 6, ref_gray.shape[1]))
        ey1 = int(np.clip(ley + 0.90 * leh, ey0 + 6, ref_gray.shape[0]))
        ref_eye = ref_gray[ey0:ey1, ex0:ex1].astype(np.float32)

        mouth_diffs: List[float] = []
        eye_diffs: List[float] = []
        loud_mouth_diffs: List[float] = []
        quiet_mouth_diffs: List[float] = []

        for fi in range(f_start, f_end):
            cur_g = frames_gray[fi]
            dx, dy = _track_anchor_shift(ref_gray, cur_g, ax, ay, aw, ah)
            cx0 = int(np.clip(mx0 + dx, 0, cur_g.shape[1] - (mx1 - mx0)))
            cy0 = int(np.clip(my0 + dy, 0, cur_g.shape[0] - (my1 - my0)))
            cur_mouth = cur_g[cy0 : cy0 + (my1 - my0), cx0 : cx0 + (mx1 - mx0)].astype(np.float32)
            mdiff = float(np.mean(np.abs(cur_mouth - ref_mouth)))
            mouth_diffs.append(mdiff)
            if rms[fi] >= p80:
                loud_mouth_diffs.append(mdiff)
            elif rms[fi] <= p20:
                quiet_mouth_diffs.append(mdiff)

            ecx0 = int(np.clip(ex0 + dx, 0, cur_g.shape[1] - (ex1 - ex0)))
            ecy0 = int(np.clip(ey0 + dy, 0, cur_g.shape[0] - (ey1 - ey0)))
            cur_eye = cur_g[ecy0 : ecy0 + (ey1 - ey0), ecx0 : ecx0 + (ex1 - ex0)].astype(np.float32)
            eye_diffs.append(float(np.mean(np.abs(cur_eye - ref_eye))))

        loud_mean = float(np.mean(loud_mouth_diffs)) if loud_mouth_diffs else float(np.max(mouth_diffs))
        quiet_mean = float(np.mean(quiet_mouth_diffs)) if quiet_mouth_diffs else 0.0
        mouth_peak = float(np.percentile(mouth_diffs, 95))
        mouth_std = float(np.std(mouth_diffs))
        eye_peak = float(np.max(eye_diffs)) if eye_diffs else 0.0

        corr = 0.0
        if np.std(seg_rms) > 1e-6 and np.std(mouth_diffs) > 1e-6:
            corr = float(np.corrcoef(seg_rms, np.array(mouth_diffs, dtype=np.float32))[0, 1])

        scene_reports.append({
            "scene": len(scene_reports) + 1,
            "frames": [f_start, f_end],
            "detector": rig["detector"],
            "mouth_roi_diff": round(loud_mean - quiet_mean, 3),
            "mouth_loud_mean": round(loud_mean, 3),
            "mouth_quiet_mean": round(quiet_mean, 3),
            "mouth_peak_p95": round(mouth_peak, 3),
            "mouth_temporal_std": round(mouth_std, 3),
            "eye_blink_peak_diff": round(eye_peak, 3),
            "audio_mouth_corr": round(corr, 3),
        })

    assert scene_reports, "Nenhuma cena detectada para auditoria"
    min_diff_seen = min(s["mouth_peak_p95"] for s in scene_reports)
    avg_speech_vs_pause = float(np.mean([s["mouth_loud_mean"] - s["mouth_quiet_mean"] for s in scene_reports]))
    max_eye_seen = max(s["eye_blink_peak_diff"] for s in scene_reports)

    passed = (
        avg_speech_vs_pause >= min_mouth_diff
        and min_diff_seen >= max(4.0, min_mouth_diff)
        and all(s["mouth_temporal_std"] >= 1.2 for s in scene_reports)
    )
    return {
        "ok": passed,
        "video": str(mp4_path),
        "fps": round(fps, 2),
        "total_frames": n_frames,
        "scenes_detected": len(scene_reports),
        "avg_mouth_speech_vs_pause_diff": round(avg_speech_vs_pause, 3),
        "min_scene_mouth_peak_p95": round(min_diff_seen, 3),
        "max_eye_blink_diff": round(max_eye_seen, 3),
        "threshold": min_mouth_diff,
        "scenes": scene_reports,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Verify true local mouth & facial motion synced with audio RMS")
    ap.add_argument("video", help="Path to MP4 video")
    ap.add_argument("--min-mouth-diff", type=float, default=3.5, help="Minimum stabilized speech-vs-pause mouth ROI diff")
    args = ap.parse_args()
    rep = audit_lipsync_video(Path(args.video), min_mouth_diff=args.min_mouth_diff)
    print(json.dumps(rep, ensure_ascii=False, indent=2))
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
