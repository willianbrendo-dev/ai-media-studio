#!/usr/bin/env python3
"""True 2.5D Lip-Sync & Facial Motion Cascade Engine (v2.8).

Zero-Idle-RAM animation module converting any closed-mouth portrait (Flux 1080x1920 or
custom portrait) + 48kHz dialogue WAV into an expressive 30fps talking or reacting take:

  Tier 1 (Cloud / Colab GPU T4 fp16):
    Routes through active Cloudflare Colab tunnel (`COLAB_GPU_URL`):
      - POST /animate_turn (LivePortrait Stage 1 + LatentSync 1.5 / MuseTalk 1.5 Stage 2)
      - POST /animate-take (v2.6/v2.7 backward-compatible route)
  Tier 2 (Local CPU MediaPipe + 2.5D Phonetic Viseme Puppets Engine):
    Runs MediaPipe FaceLandmarker (478 landmarks) with OpenCV Haar & anatomical
    fallbacks, classifies frame-by-frame phonetic visemes from RMS + spectral centroid +
    zero-crossing rate (`SILENCE/M-B-P`, `ROUND O/U`, `OPEN A/E`, `FRICATIVE S/Z/F/V/I`),
    and drives vectorized 2D displacement warping via `cv2.remap`:
      - Genuine horizontal lip pucker/stretch + vertical jaw/lip articulation
      - Pure `cv2.remap` glasses-safe upper-eyelid blinks (ZERO opaque painted ellipse!)
      - `lead_in_pause` (0.45s breath/eye-close before comeback speech)
      - `is_silent_reaction` (Cut 4 sealed trembling mouth + furrowed brows + shoulder slump)
"""

from __future__ import annotations

import base64
import json
import math
import os
import shutil
import subprocess
import time
import urllib.request
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np


FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"
FACE_LANDMARKER_MODEL = Path(
    os.environ.get(
        "FACE_LANDMARKER_MODEL",
        "/opt/ai-media-studio/tools/hyperframes/models/face_landmarker.task",
    )
)
HAAR_DIR = Path("/usr/share/opencv4/haarcascades")


def _probe_duration(media_path: Path) -> float:
    out = subprocess.check_output(
        [
            FFPROBE_BIN,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        text=True,
        timeout=20,
    ).strip()
    return max(0.3, float(out or "1.0"))


def analyze_audio_visemes(audio_wav: Path, fps: int = 30) -> Dict[str, np.ndarray]:
    """Classify per-frame (33.3ms) phonetic visemes: M/B/P press, O/U round pucker, A/E open jaw, S/Z/F/I stretch."""
    with wave.open(str(audio_wav), "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        nframes = wf.getnframes()
        raw = wf.readframes(nframes)

    pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    if nch > 1:
        pcm = pcm.reshape(-1, nch).mean(axis=1)

    total_dur = max(0.3, len(pcm) / float(max(8000, sr)))
    num_video_frames = max(9, int(round(total_dur * fps)))
    hop = max(1, int(sr / fps))
    win_len = max(256, int(hop * 1.5))
    window = np.hanning(win_len).astype(np.float32)
    freqs = np.fft.rfftfreq(win_len, d=1.0 / sr)

    round_mask = (freqs >= 180) & (freqs <= 580)   # O/U F1 + M/B/P voiced closure
    open_mask = (freqs >= 580) & (freqs <= 1450)   # A/E open vowel formants
    sib_mask = (freqs >= 2200) & (freqs <= 6400)   # S/Z/F/V/I fricative energy
    band_mask = (freqs >= 120) & (freqs <= 6500)

    rms_arr = np.zeros(num_video_frames, dtype=np.float32)
    round_arr = np.zeros(num_video_frames, dtype=np.float32)
    open_arr = np.zeros(num_video_frames, dtype=np.float32)
    sib_arr = np.zeros(num_video_frames, dtype=np.float32)
    centroid_arr = np.zeros(num_video_frames, dtype=np.float32)
    zcr_arr = np.zeros(num_video_frames, dtype=np.float32)

    for idx in range(num_video_frames):
        center = int(round(idx * (len(pcm) / float(num_video_frames))))
        start = max(0, center - win_len // 2)
        seg = pcm[start : start + win_len]
        if len(seg) < win_len:
            seg = np.pad(seg, (0, win_len - len(seg)))
        rms = float(np.sqrt(np.mean(seg * seg) + 1e-10))
        rms_arr[idx] = rms
        zcr_arr[idx] = float(np.mean((seg[:-1] * seg[1:]) < 0.0)) if len(seg) > 1 else 0.0
        spec = np.abs(np.fft.rfft(seg * window))
        round_arr[idx] = float(np.mean(spec[round_mask])) if np.any(round_mask) else 0.0
        open_arr[idx] = float(np.mean(spec[open_mask])) if np.any(open_mask) else rms
        sib_arr[idx] = float(np.mean(spec[sib_mask])) if np.any(sib_mask) else 0.0
        b_spec = spec[band_mask]
        b_freqs = freqs[band_mask]
        b_sum = float(np.sum(b_spec)) + 1e-9
        centroid_arr[idx] = float(np.sum(b_freqs * b_spec) / b_sum)

    p92_rms = max(float(np.percentile(rms_arr, 92)), 1e-4)
    p92_rnd = max(float(np.percentile(round_arr, 92)), 1e-4)
    p92_opn = max(float(np.percentile(open_arr, 92)), 1e-4)
    p92_sib = max(float(np.percentile(sib_arr, 92)), 1e-4)

    norm_rms = np.clip(rms_arr / p92_rms, 0.0, 1.35)
    norm_rnd = np.clip(round_arr / p92_rnd, 0.0, 1.35)
    norm_opn = np.clip(open_arr / p92_opn, 0.0, 1.35)
    norm_sib = np.clip(sib_arr / p92_sib, 0.0, 1.35)

    open_env = np.zeros(num_video_frames, dtype=np.float32)
    wide_env = np.zeros(num_video_frames, dtype=np.float32)
    round_env = np.zeros(num_video_frames, dtype=np.float32)
    press_env = np.zeros(num_video_frames, dtype=np.float32)

    cur_o = cur_w = cur_r = cur_p = 0.0
    for i in range(num_video_frames):
        r_lvl = float(norm_rms[i])
        if r_lvl < 0.08:
            # True silence: mouth snaps gently shut
            tgt_o, tgt_w, tgt_r, tgt_p = 0.0, 0.0, 0.0, 0.25
        else:
            cent = float(centroid_arr[i])
            zcr = float(zcr_arr[i])
            is_fricative = (zcr > 0.14 or cent > 1950.0) and norm_sib[i] > 0.28 * max(0.15, norm_opn[i])
            is_bilabial = (r_lvl < 0.24 and cent < 720.0 and norm_opn[i] < 0.22)
            is_round = (not is_fricative and not is_bilabial) and (
                cent < 1050.0 or norm_rnd[i] > 1.12 * max(0.12, norm_opn[i])
            )

            if is_bilabial:
                tgt_o, tgt_w, tgt_r, tgt_p = 0.02, 0.0, 0.10, 0.85
            elif is_fricative:
                tgt_o = float(np.clip(0.24 * r_lvl + 0.16 * norm_opn[i], 0.08, 0.42))
                tgt_w = float(np.clip(0.55 + 0.45 * norm_sib[i], 0.0, 1.0))
                tgt_r, tgt_p = 0.0, 0.0
            elif is_round:
                tgt_o = float(np.clip(0.32 * r_lvl + 0.36 * norm_rnd[i], 0.18, 0.72))
                tgt_r = float(np.clip(0.58 + 0.42 * norm_rnd[i] - 0.20 * norm_sib[i], 0.0, 1.0))
                tgt_w, tgt_p = 0.0, 0.0
            else:
                # Open vowel A/E
                tgt_o = float(np.clip(0.45 * r_lvl + 0.58 * norm_opn[i], 0.25, 1.0))
                tgt_w = float(np.clip(0.22 * norm_opn[i] + 0.12 * norm_sib[i], 0.0, 0.45))
                tgt_r, tgt_p = 0.0, 0.0

        a_o = 0.68 if tgt_o > cur_o else 0.52
        cur_o = cur_o + a_o * (tgt_o - cur_o)
        cur_w = cur_w + 0.56 * (tgt_w - cur_w)
        cur_r = cur_r + 0.56 * (tgt_r - cur_r)
        cur_p = cur_p + 0.58 * (tgt_p - cur_p)

        open_env[i] = 0.0 if (r_lvl < 0.08 and cur_o < 0.07) else cur_o
        wide_env[i] = cur_w
        round_env[i] = cur_r
        press_env[i] = cur_p

    kernel = np.hanning(9)
    kernel /= kernel.sum()
    prosody_env = np.convolve(np.pad(open_env, (4, 4), mode="edge"), kernel, mode="valid")

    return {
        "rms": norm_rms,
        "mouth_open": np.clip(open_env, 0.0, 1.0),
        "mouth_wide": np.clip(wide_env, 0.0, 1.0),
        "mouth_round": np.clip(round_env, 0.0, 1.0),
        "mouth_press": np.clip(press_env, 0.0, 1.0),
        "prosody": np.clip(prosody_env, 0.0, 1.0),
        "num_frames": np.array([num_video_frames], dtype=np.int32),
        "duration": np.array([total_dur], dtype=np.float32),
    }


def _load_bgr_portrait(image_path: Path, width: int = 1080, height: int = 1920) -> np.ndarray:
    """Load JPG/PNG/SVG portrait scaled cleanly to exact target dimensions."""
    src = image_path
    tmp_jpg: Optional[Path] = None
    if image_path.suffix.lower() == ".svg":
        tmp_jpg = image_path.with_suffix(".anim_tmp.jpg")
        subprocess.run(
            ["rsvg-convert", "-w", str(width), "-h", str(height), str(image_path), "-o", str(tmp_jpg.with_suffix(".png"))],
            check=False,
            timeout=20,
        )
        png_cand = tmp_jpg.with_suffix(".png")
        img = cv2.imread(str(png_cand)) if png_cand.is_file() else None
        png_cand.unlink(missing_ok=True)
        if img is None:
            subprocess.run(
                [FFMPEG_BIN, "-y", "-i", str(image_path), "-vf", f"scale={width}:{height}", str(tmp_jpg)],
                check=True,
                timeout=20,
            )
            img = cv2.imread(str(tmp_jpg))
            tmp_jpg.unlink(missing_ok=True)
    else:
        img = cv2.imread(str(src))

    if img is None:
        raise RuntimeError(f"Não foi possível carregar retrato {image_path}")
    if img.shape[1] != width or img.shape[0] != height:
        # Center-crop scale to exact 1080x1920
        ih, iw = img.shape[:2]
        scale = max(width / float(iw), height / float(ih))
        nw, nh = int(round(iw * scale)), int(round(ih * scale))
        resized = cv2.resize(img, (nw, nh), interpolation=cv2.INTER_LANCZOS4)
        x0 = max(0, (nw - width) // 2)
        y0 = max(0, (nh - height) // 2)
        img = resized[y0 : y0 + height, x0 : x0 + width]
    return img


def detect_face_rig(img_bgr: np.ndarray) -> Dict[str, Any]:
    """Detect facial mouth, chin, eye, eyebrow, and head rig coordinates (MediaPipe -> Haar -> Anatomical)."""
    h, w = img_bgr.shape[:2]
    rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)

    # 1) MediaPipe FaceLandmarker (478 landmarks) — works on both 35mm realistic & 3D stylized CG
    if FACE_LANDMARKER_MODEL.is_file():
        try:
            import mediapipe as mp
            from mediapipe.tasks import python as mp_python
            from mediapipe.tasks.python import vision as mp_vision

            opts = mp_vision.FaceLandmarkerOptions(
                base_options=mp_python.BaseOptions(model_asset_path=str(FACE_LANDMARKER_MODEL)),
                num_faces=1,
                min_face_detection_confidence=0.22,
                min_face_presence_confidence=0.22,
            )
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            with mp_vision.FaceLandmarker.create_from_options(opts) as detector:
                res = detector.detect(mp_img)
            if res.face_landmarks:
                lm = res.face_landmarks[0]

                def pt(idx: int) -> Tuple[float, float]:
                    return (float(lm[idx].x * w), float(lm[idx].y * h))

                xs = [p.x * w for p in lm]
                ys = [p.y * h for p in lm]
                fx0, fx1 = float(min(xs)), float(max(xs))
                fy0, fy1 = float(min(ys)), float(max(ys))
                fw, fh = max(40.0, fx1 - fx0), max(40.0, fy1 - fy0)

                l_corn = pt(61)
                r_corn = pt(291)
                up_out = pt(0)
                lo_out = pt(17)
                up_in = pt(13)
                lo_in = pt(14)
                chin = pt(152)

                le_pts = [pt(i) for i in (33, 133, 159, 145, 158, 153)]
                re_pts = [pt(i) for i in (362, 263, 386, 374, 385, 380)]
                le_cx = float(np.mean([p[0] for p in le_pts]))
                le_cy = float(np.mean([p[1] for p in le_pts]))
                re_cx = float(np.mean([p[0] for p in re_pts]))
                re_cy = float(np.mean([p[1] for p in re_pts]))
                le_w = max(14.0, abs(pt(133)[0] - pt(33)[0]))
                le_h = max(8.0, abs(pt(145)[1] - pt(159)[1]))
                re_w = max(14.0, abs(pt(263)[0] - pt(362)[0]))
                re_h = max(8.0, abs(pt(374)[1] - pt(386)[1]))

                lb_cx = float(np.mean([pt(i)[0] for i in (70, 63, 105, 66)]))
                lb_cy = float(np.mean([pt(i)[1] for i in (70, 63, 105, 66)]))
                rb_cx = float(np.mean([pt(i)[0] for i in (336, 296, 334, 293)]))
                rb_cy = float(np.mean([pt(i)[1] for i in (336, 296, 334, 293)]))

                return {
                    "detector": "mediapipe_facelandmarker",
                    "face_box": (fx0, fy0, fw, fh),
                    "head_center": ((fx0 + fx1) * 0.5, (fy0 + fy1) * 0.5),
                    "mouth_center": ((l_corn[0] + r_corn[0]) * 0.5, (up_in[1] + lo_in[1]) * 0.5),
                    "mouth_left": l_corn,
                    "mouth_right": r_corn,
                    "mouth_upper_y": up_out[1],
                    "mouth_lower_y": lo_out[1],
                    "mouth_width": max(30.0, abs(r_corn[0] - l_corn[0])),
                    "chin_y": chin[1],
                    "left_eye": (le_cx, le_cy, le_w, le_h),
                    "right_eye": (re_cx, re_cy, re_w, re_h),
                    "left_brow": (lb_cx, lb_cy),
                    "right_brow": (rb_cx, rb_cy),
                }
        except Exception:
            pass

    # 2) Fallback: OpenCV Haar Cascade Face + Eyes
    gray = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    for cname in ("haarcascade_frontalface_alt2.xml", "haarcascade_frontalface_default.xml"):
        cpath = HAAR_DIR / cname
        if not cpath.is_file():
            continue
        clf = cv2.CascadeClassifier(str(cpath))
        faces = clf.detectMultiScale(gray, scaleFactor=1.08, minNeighbors=3, minSize=(140, 140))
        if len(faces) > 0:
            fx, fy, fw, fh = [float(v) for v in max(faces, key=lambda r: r[2] * r[3])]
            mcx, mcy = fx + 0.50 * fw, fy + 0.75 * fh
            mw = max(36.0, 0.36 * fw)
            return {
                "detector": "opencv_haar_face",
                "face_box": (fx, fy, fw, fh),
                "head_center": (fx + 0.50 * fw, fy + 0.48 * fh),
                "mouth_center": (mcx, mcy),
                "mouth_left": (mcx - mw * 0.5, mcy),
                "mouth_right": (mcx + mw * 0.5, mcy),
                "mouth_upper_y": mcy - 0.05 * fh,
                "mouth_lower_y": mcy + 0.06 * fh,
                "mouth_width": mw,
                "chin_y": fy + 0.98 * fh,
                "left_eye": (fx + 0.33 * fw, fy + 0.38 * fh, 0.16 * fw, 0.07 * fh),
                "right_eye": (fx + 0.67 * fw, fy + 0.38 * fh, 0.16 * fw, 0.07 * fh),
                "left_brow": (fx + 0.33 * fw, fy + 0.30 * fh),
                "right_brow": (fx + 0.67 * fw, fy + 0.30 * fh),
            }

    # 3) Fallback: Calibrated Portrait Golden Thirds (for stylized cartoon angles / vector rigs)
    fx, fy, fw, fh = w * 0.30, h * 0.16, w * 0.40, h * 0.25
    mcx, mcy = fx + 0.50 * fw, fy + 0.74 * fh
    mw = 0.34 * fw
    return {
        "detector": "anatomical_portrait_rig",
        "face_box": (fx, fy, fw, fh),
        "head_center": (fx + 0.50 * fw, fy + 0.48 * fh),
        "mouth_center": (mcx, mcy),
        "mouth_left": (mcx - mw * 0.5, mcy),
        "mouth_right": (mcx + mw * 0.5, mcy),
        "mouth_upper_y": mcy - 0.05 * fh,
        "mouth_lower_y": mcy + 0.06 * fh,
        "mouth_width": mw,
        "chin_y": fy + 0.98 * fh,
        "left_eye": (fx + 0.34 * fw, fy + 0.38 * fh, 0.16 * fw, 0.07 * fh),
        "right_eye": (fx + 0.66 * fw, fy + 0.38 * fh, 0.16 * fw, 0.07 * fh),
        "left_brow": (fx + 0.34 * fw, fy + 0.30 * fh),
        "right_brow": (fx + 0.66 * fw, fy + 0.30 * fh),
    }


def _compute_blink_schedule(num_frames: int, fps: int = 30, seed_val: int = 7) -> np.ndarray:
    """Generate smooth natural eye-blink closures every 2.5s-3.5s."""
    rng = np.random.default_rng(seed_val)
    blinks = np.zeros(num_frames, dtype=np.float32)
    t_idx = int(fps * float(rng.uniform(0.65, 1.35)))
    while t_idx < num_frames - 4:
        # 5-frame natural blink curve
        curve = (0.45, 0.95, 1.0, 0.75, 0.25)
        for off, val in enumerate(curve):
            if 0 <= t_idx + off < num_frames:
                blinks[t_idx + off] = max(blinks[t_idx + off], val)
        t_idx += int(fps * float(rng.uniform(2.3, 3.4)))
    return blinks


def _precompute_roi_weights(
    roi_h: int,
    roi_w: int,
    local_rig: Dict[str, Any],
    roi_src: Optional[np.ndarray] = None,
) -> Dict[str, Any]:
    """Precompute vectorized Gaussian spatial influence maps inside the cropped head/torso ROI."""
    yy, xx = np.mgrid[0:roi_h, 0:roi_w].astype(np.float32)
    fx0, fy0, fw, fh = local_rig["face_box"]
    mcx, mcy = local_rig["mouth_center"]
    mw = max(24.0, local_rig["mouth_width"])
    chin_y = local_rig["chin_y"]
    hcx, hcy = local_rig["head_center"]

    # Lower lip + jaw drop weight (centered just below mouth_center down to chin)
    jaw_cy = mcy + 0.08 * fh
    w_jaw = np.exp(
        -(
            ((xx - mcx) ** 2) / (2.0 * (0.44 * fw) ** 2)
            + ((yy - jaw_cy) ** 2) / (2.0 * (0.19 * fh) ** 2)
        )
    ).astype(np.float32)
    w_jaw *= np.clip((yy - (mcy - 0.07 * fh)) / max(8.0, 0.12 * fh), 0.0, 1.0)

    # Upper lip subtle lift/compression weight
    ul_cy = local_rig["mouth_upper_y"] - 0.01 * fh
    w_upper_lip = np.exp(
        -(
            ((xx - mcx) ** 2) / (2.0 * (0.58 * mw) ** 2)
            + ((yy - ul_cy) ** 2) / (2.0 * (0.055 * fh) ** 2)
        )
    ).astype(np.float32)

    # Lip press compression field (pulls upper & lower lip margins gently together on M/B/P & silent tension)
    press_dir = -np.tanh((yy - mcy) / max(4.0, 0.03 * fh)) * np.exp(
        -(((xx - mcx) ** 2) / (2.0 * (0.62 * mw) ** 2) + ((yy - mcy) ** 2) / (2.0 * (0.065 * fh) ** 2))
    ).astype(np.float32)

    # Horizontal lip corner stretch/pucker vector field
    dx_norm = (xx - mcx) / max(12.0, mw * 0.52)
    w_corners = np.exp(
        -(
            ((xx - mcx) ** 2) / (2.0 * (0.82 * mw) ** 2)
            + ((yy - mcy) ** 2) / (2.0 * (0.10 * fh) ** 2)
        )
    ).astype(np.float32)
    corner_dir = np.tanh(dx_norm * 1.45) * w_corners

    # Glasses-safe Left & Right Eye upper-lid closure weights (strictly interior iris/lid only, ZERO rim distortion)
    luma_norm = None
    if roi_src is not None:
        gray = cv2.cvtColor(roi_src, cv2.COLOR_BGR2GRAY).astype(np.float32)
        face_y0 = max(0, int(fy0 + 0.1 * fh))
        face_y1 = min(roi_h, int(fy0 + 0.9 * fh))
        face_x0 = max(0, int(fx0 + 0.1 * fw))
        face_x1 = min(roi_w, int(fx0 + 0.9 * fw))
        med_skin = float(np.median(gray[face_y0:face_y1, face_x0:face_x1])) if (face_y1 > face_y0 and face_x1 > face_x0) else 128.0
        luma_norm = gray / max(40.0, med_skin)

    def eye_weights(eye_tuple: Tuple[float, float, float, float]) -> np.ndarray:
        ecx, ecy, ew, eh = eye_tuple
        lid_y = ecy - 0.30 * eh
        # Strictly confined inside inner eye opening radius so eyeglass rims around the orbit never bend
        rx_in = max(6.0, 0.38 * ew)
        ry_in = max(4.0, 0.62 * eh)
        w_lid = np.exp(
            -(
                ((xx - ecx) ** 2) / (2.0 * rx_in ** 2)
                + ((yy - lid_y) ** 2) / (2.0 * ry_in ** 2)
            )
        ).astype(np.float32)
        w_lid *= np.clip((ecy + 0.48 * eh - yy) / max(3.5, 0.65 * eh), 0.0, 1.0)
        w_lid *= np.clip(1.0 - (((xx - ecx) / max(8.0, 0.52 * ew)) ** 4), 0.0, 1.0)
        if luma_norm is not None:
            # Protect dark eyeglass frames at outer perimeter outside pupil radius
            r_outer = np.sqrt(((xx - ecx) / max(6.0, 0.36 * ew)) ** 2 + ((yy - ecy) / max(4.0, 0.45 * eh)) ** 2)
            dark_rim = (r_outer > 0.85) & (luma_norm < 0.52)
            w_lid = np.where(dark_rim, w_lid * 0.15, w_lid)
        return w_lid

    w_le = eye_weights(local_rig["left_eye"])
    w_re = eye_weights(local_rig["right_eye"])

    # Eyebrow expressive lift / knit
    lbx, lby = local_rig["left_brow"]
    rbx, rby = local_rig["right_brow"]
    w_brow = (
        np.exp(-(((xx - lbx) ** 2) / (2.0 * (0.20 * fw) ** 2) + ((yy - lby) ** 2) / (2.0 * (0.07 * fh) ** 2)))
        + np.exp(-(((xx - rbx) ** 2) / (2.0 * (0.20 * fw) ** 2) + ((yy - rby) ** 2) / (2.0 * (0.07 * fh) ** 2)))
    ).astype(np.float32)

    fg_cx, fg_cy = hcx, fy0 + 0.95 * fh
    fg_rx, fg_ry = max(fw * 1.15, roi_w * 0.40), max(fh * 1.45, roi_h * 0.46)
    d_fg = np.sqrt(((xx - fg_cx) / fg_rx) ** 2 + ((yy - fg_cy) / fg_ry) ** 2)
    fg_mask = np.clip((1.05 - d_fg) / 0.35, 0.0, 1.0).astype(np.float32)[..., None]

    return {
        "xx": xx,
        "yy": yy,
        "w_jaw": w_jaw,
        "w_upper_lip": w_upper_lip,
        "press_dir": press_dir,
        "corner_dir": corner_dir,
        "w_le": w_le,
        "w_re": w_re,
        "w_brow": w_brow,
        "fg_mask": fg_mask,
        "chin_y": chin_y,
    }


def _animate_local_viseme_take(
    *,
    image_path: Path,
    audio_wav: Path,
    out_mp4: Path,
    emotion: str = "warm",
    emotion_preset: str = "warm",
    camera_action: str = "zoom_in",
    lead_in_pause: float = 0.0,
    is_silent_reaction: bool = False,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Dict[str, Any]:
    """Render genuine 30fps 2.5D phonetic lip-sync + glasses-safe blink + head/reaction acting locally on CPU."""
    base_img = _load_bgr_portrait(image_path, width=width, height=height)
    rig = detect_face_rig(base_img)
    audio_feats = analyze_audio_visemes(audio_wav, fps=fps)

    num_frames = int(audio_feats["num_frames"][0])
    mouth_open = audio_feats["mouth_open"].copy()
    mouth_wide = audio_feats["mouth_wide"].copy()
    mouth_round = audio_feats["mouth_round"].copy()
    mouth_press = audio_feats["mouth_press"].copy()
    prosody = audio_feats["prosody"].copy()
    duration = float(audio_feats["duration"][0])

    if is_silent_reaction:
        mouth_open[:] = 0.0
        mouth_wide[:] = 0.0
        mouth_round[:] = 0.0
        mouth_press[:] = 0.72
        prosody[:] = 0.0

    blinks = _compute_blink_schedule(num_frames, fps=fps, seed_val=abs(hash(image_path.name)) % 10000)

    # Crop generous animated ROI around upper body + head for ultra-fast vectorized warp
    fx0, fy0, fw, fh = rig["face_box"]
    rx0 = max(0, int(fx0 - 0.70 * fw))
    ry0 = max(0, int(fy0 - 0.45 * fh))
    rx1 = min(width, int(fx0 + 1.70 * fw))
    ry1 = min(height, int(fy0 + 2.35 * fh))
    roi_w, roi_h = rx1 - rx0, ry1 - ry0
    roi_src = base_img[ry0:ry1, rx0:rx1].copy()

    local_rig = dict(rig)
    local_rig["face_box"] = (fx0 - rx0, fy0 - ry0, fw, fh)
    local_rig["head_center"] = (rig["head_center"][0] - rx0, rig["head_center"][1] - ry0)
    local_rig["mouth_center"] = (rig["mouth_center"][0] - rx0, rig["mouth_center"][1] - ry0)
    local_rig["mouth_left"] = (rig["mouth_left"][0] - rx0, rig["mouth_left"][1] - ry0)
    local_rig["mouth_right"] = (rig["mouth_right"][0] - rx0, rig["mouth_right"][1] - ry0)
    local_rig["mouth_upper_y"] = rig["mouth_upper_y"] - ry0
    local_rig["mouth_lower_y"] = rig["mouth_lower_y"] - ry0
    local_rig["chin_y"] = rig["chin_y"] - ry0
    local_rig["left_eye"] = (rig["left_eye"][0] - rx0, rig["left_eye"][1] - ry0, rig["left_eye"][2], rig["left_eye"][3])
    local_rig["right_eye"] = (rig["right_eye"][0] - rx0, rig["right_eye"][1] - ry0, rig["right_eye"][2], rig["right_eye"][3])
    local_rig["left_brow"] = (rig["left_brow"][0] - rx0, rig["left_brow"][1] - ry0)
    local_rig["right_brow"] = (rig["right_brow"][0] - rx0, rig["right_brow"][1] - ry0)

    weights = _precompute_roi_weights(roi_h, roi_w, local_rig, roi_src=roi_src)
    xx = weights["xx"]
    yy = weights["yy"]
    w_jaw = weights["w_jaw"]
    w_ul = weights["w_upper_lip"]
    press_dir = weights["press_dir"]
    corner_dir = weights["corner_dir"]
    w_le = weights["w_le"]
    w_re = weights["w_re"]
    w_brow = weights["w_brow"]
    fg_mask = weights["fg_mask"]

    mcx_l, mcy_l = local_rig["mouth_center"]
    mw_l = max(24.0, local_rig["mouth_width"])
    lex_l, ley_l, lew_l, leh_l = local_rig["left_eye"]
    rex_l, rey_l, rew_l, reh_l = local_rig["right_eye"]

    emo_amp = 1.15 if emotion in ("shocked", "laughing", "dramatic") else 1.0
    jaw_max_px = max(16.0, min(48.0, 0.105 * fh * emo_amp))
    ul_max_px = max(4.5, 0.025 * fh * emo_amp)
    corner_px = max(5.5, 0.135 * mw_l)
    press_px = max(3.0, 0.022 * fh)
    brow_px = max(5.5, 0.036 * fh * emo_amp)
    eye_close_l = max(7.0, 1.15 * leh_l)
    eye_close_r = max(7.0, 1.15 * reh_l)

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    ff_cmd = [
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        "-i", str(audio_wav),
        "-c:v", "libx264", "-preset", "fast", "-crf", "19", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(out_mp4),
    ]
    proc = subprocess.Popen(ff_cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert proc.stdin is not None

    try:
        for i in range(num_frames):
            t_sec = i / float(fps)
            mo = float(mouth_open[i])
            mw_v = float(mouth_wide[i])
            mr_v = float(mouth_round[i])
            mp_v = float(mouth_press[i])
            pr = float(prosody[i])
            blk = float(blinks[i])

            # v2.8 Dramatic beats: Cut 2 calm breath + eye-close before speech (`lead_in_pause >= 0.30`)
            if not is_silent_reaction and lead_in_pause > 0.15 and t_sec <= lead_in_pause:
                mo = mw_v = mr_v = 0.0
                mp_v = 0.55
                phase = t_sec / max(0.05, lead_in_pause)
                blk = max(blk, float(0.92 * math.sin(phase * math.pi)))
                pr = 0.42 * math.sin(phase * math.pi)
            elif is_silent_reaction:
                # Cut 4 Silent Shame Reaction: slow mortified blink/squint around 0.65s + tense quiver
                shame_blink = math.exp(-((t_sec - 0.65) / 0.20) ** 2)
                blk = max(blk, float(0.88 * shame_blink))

            # 1) Background subtle organic handheld camera motion
            cam_zoom = (
                1.0 + 0.055 * (i / float(max(1, num_frames - 1)))
                if is_silent_reaction
                else 1.0 + 0.035 * (i / float(max(1, num_frames - 1)))
            )
            if camera_action == "shake" and pr > 0.35 and not is_silent_reaction:
                bg_sx = int(round(6.0 * math.sin(t_sec * 18.0) * pr))
                bg_sy = int(round(4.0 * math.cos(t_sec * 14.0) * pr))
            else:
                bg_sx = int(round(4.5 * math.sin(t_sec * 1.4)))
                bg_sy = int(round(2.5 * math.cos(t_sec * 1.1)))

            bh, bw = int(round(height * cam_zoom)), int(round(width * cam_zoom))
            bg_scaled = cv2.resize(base_img, (bw, bh), interpolation=cv2.INTER_LINEAR)
            bx0 = int(np.clip((bw - width) // 2 + bg_sx, 0, max(0, bw - width)))
            by0 = int(np.clip((bh - height) // 2 + bg_sy, 0, max(0, bh - height)))
            frame = bg_scaled[by0 : by0 + height, bx0 : bx0 + width].copy()

            # 2) Compute 2.5D phonetic displacement field on ROI (inverse mapping!)
            if is_silent_reaction:
                swallow = 0.7 * math.sin(t_sec * 6.5)
                dy_jaw = (-0.04 * jaw_max_px + swallow * 0.25) * w_jaw
                dy_ul = (0.28 * ul_max_px) * w_ul
                dy_press = (press_px * 0.90) * press_dir
                dx_corn = (-corner_px * 0.18) * corner_dir
                # Knit/lower brows gently in shame
                dy_brow = (brow_px * 0.95 * min(1.0, t_sec / 0.55)) * w_brow
            else:
                dy_jaw = (jaw_max_px * (mo - 0.10 if mo > 0.05 else -0.08)) * w_jaw
                dy_ul = -(ul_max_px * max(0.0, mo - 0.25 * mr_v)) * w_ul
                dy_press = (press_px * mp_v) * press_dir
                # Clear horizontal contrast: fricative/wide pulls corners out (+), O/U pulls inward (-)
                dx_corn = (corner_px * (1.15 * mw_v - 1.25 * mr_v)) * corner_dir
                dy_brow = -(brow_px * pr) * w_brow

            # Pure cv2.remap eyelid blink inside inner eye contour (ZERO painted grey ellipse!)
            dy_eyes = (eye_close_l * blk) * w_le + (eye_close_r * blk) * w_re

            map_x = xx - dx_corn
            map_y = yy - (dy_jaw + dy_ul + dy_press + dy_eyes + dy_brow)

            warped_roi = cv2.remap(roi_src, map_x, map_y, cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

            # 3) Organic soft-feathered warm interior cavity shading ONLY when really articulating open (ZERO bright white bar!)
            if not is_silent_reaction and mo > 0.24:
                cav_rx = max(5, int(mw_l * (0.22 + 0.11 * mw_v - 0.09 * mr_v)))
                cav_ry = max(3, int(0.048 * fh * (mo - 0.14) * emo_amp))
                cav_cy = int(round(mcy_l + 0.42 * cav_ry))
                cav_cx = int(round(mcx_l))
                cav_mask = np.zeros((roi_h, roi_w), dtype=np.float32)
                cv2.ellipse(cav_mask, (cav_cx, cav_cy), (cav_rx, cav_ry), 0, 0, 360, 1.0, -1, cv2.LINE_AA)
                cav_mask = cv2.GaussianBlur(cav_mask, (13, 13), 0)
                cav_alpha = float(np.clip((mo - 0.24) / 0.48, 0.0, 0.60)) * cav_mask[..., None]
                cav_layer = np.full_like(warped_roi, (22, 13, 24), dtype=np.uint8)
                warped_roi = np.clip(
                    warped_roi.astype(np.float32) * (1.0 - cav_alpha) + cav_layer.astype(np.float32) * cav_alpha,
                    0, 255,
                ).astype(np.uint8)

            # 4) NO painted opaque cv2.ellipse over eyes/glasses! Blink motion is already crisp in step (2).

            # 5) Head sway / nod / tilt + torso slumped breathing
            if is_silent_reaction:
                slump = min(1.0, t_sec / 0.7)
                breath = math.sin(t_sec * 4.4)
                head_tilt = 2.6 * math.sin(t_sec * 2.8) - 1.2 * slump
                head_nod_y = (11.5 * slump) + (3.8 * breath)
                head_sway_x = 3.5 * math.sin(t_sec * 1.9)
                scale_f = 1.0 + 0.015 * breath
            elif not is_silent_reaction and lead_in_pause > 0.15 and t_sec <= lead_in_pause:
                phase = t_sec / max(0.05, lead_in_pause)
                head_tilt = -1.4 * math.sin(phase * math.pi)
                head_nod_y = -6.0 * math.sin(phase * math.pi)
                head_sway_x = 0.0
                scale_f = 1.0 + 0.014 * math.sin(phase * math.pi)
            else:
                head_tilt = (2.1 * pr * math.sin(t_sec * 4.2)) + (1.0 * math.sin(t_sec * 1.9))
                head_nod_y = (7.5 * pr * math.sin(t_sec * 6.0)) + (3.2 * math.sin(t_sec * 1.8))
                head_sway_x = 6.5 * math.sin(t_sec * 1.6) + (4.0 * pr * math.cos(t_sec * 3.1))
                scale_f = 1.0 + 0.012 * pr

            pivot = (float(local_rig["head_center"][0]), float(min(roi_h - 10, local_rig["chin_y"] + 0.55 * fh)))
            rot_m = cv2.getRotationMatrix2D(pivot, head_tilt, scale_f)
            rot_m[0, 2] += head_sway_x
            rot_m[1, 2] += head_nod_y
            fg_moved = cv2.warpAffine(warped_roi, rot_m, (roi_w, roi_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)
            mask_moved = cv2.warpAffine(fg_mask, rot_m, (roi_w, roi_h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT, borderValue=0)
            if mask_moved.ndim == 2:
                mask_moved = mask_moved[..., None]

            bg_roi = frame[ry0:ry1, rx0:rx1].astype(np.float32)
            blended_roi = fg_moved.astype(np.float32) * mask_moved + bg_roi * (1.0 - mask_moved)
            frame[ry0:ry1, rx0:rx1] = np.clip(blended_roi, 0, 255).astype(np.uint8)

            proc.stdin.write(frame.tobytes())

        proc.stdin.close()
        stderr_out = proc.stderr.read().decode("utf-8", errors="ignore") if proc.stderr else ""
        ret = proc.wait(timeout=120)
        if ret != 0 or not out_mp4.is_file():
            raise RuntimeError(f"FFmpeg lip-sync pipe falhou ({ret}): {stderr_out[-400:]}")
    finally:
        if proc.poll() is None:
            proc.kill()

    return {
        "ok": True,
        "animation_engine_used": f"local_viseme_v28_{rig['detector']}_30fps",
        "detector": rig["detector"],
        "mouth_center": [round(rig["mouth_center"][0], 1), round(rig["mouth_center"][1], 1)],
        "face_box": [round(v, 1) for v in rig["face_box"]],
        "num_frames": num_frames,
        "duration": round(duration, 2),
        "peak_mouth_open": round(float(np.max(mouth_open)), 3),
        "is_silent_reaction": bool(is_silent_reaction),
        "output_mp4": str(out_mp4),
    }


def _try_tier1_colab_animate(
    *,
    image_path: Path,
    audio_wav: Path,
    out_mp4: Path,
    prompt: str,
    colab_url: str,
    colab_token: str,
    colab_engine: str,
    emotion_preset: str,
    lead_in_pause: float,
    is_silent_reaction: bool,
    width: int,
    height: int,
) -> Optional[Dict[str, Any]]:
    clean_url = (colab_url or "").strip().rstrip("/")
    if not clean_url:
        return None
    try:
        headers = {"Accept": "application/json"}
        if colab_token:
            headers["X-Colab-Token"] = colab_token
        hreq = urllib.request.Request(f"{clean_url}/health", headers=headers, method="GET")
        with urllib.request.urlopen(hreq, timeout=2.5) as resp:
            if resp.status != 200:
                return None

        src_img = image_path
        tmp_jpg: Optional[Path] = None
        if image_path.suffix.lower() == ".svg":
            tmp_jpg = out_mp4.with_suffix(".colab_in.jpg")
            img_arr = _load_bgr_portrait(image_path, width=width, height=height)
            cv2.imwrite(str(tmp_jpg), img_arr, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            src_img = tmp_jpg
        try:
            payload = {
                "image_b64": base64.b64encode(src_img.read_bytes()).decode("ascii"),
                "audio_b64": base64.b64encode(audio_wav.read_bytes()).decode("ascii"),
                "prompt": prompt,
                "engine": colab_engine or "liveportrait_latentsync",
                "emotion_preset": emotion_preset or "warm",
                "lead_in_pause": float(lead_in_pause or 0.0),
                "is_silent_reaction": bool(is_silent_reaction),
                "width": width,
                "height": height,
            }
        finally:
            if tmp_jpg:
                tmp_jpg.unlink(missing_ok=True)

        data = None
        for route in ("/animate_turn", "/animate-take"):
            try:
                req = urllib.request.Request(
                    f"{clean_url}{route}",
                    data=json.dumps(payload).encode("utf-8"),
                    headers={**headers, "Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=240) as resp:
                    cand = json.loads(resp.read().decode("utf-8"))
                if cand.get("ok") and cand.get("video_b64"):
                    data = cand
                    break
            except Exception:
                continue
        if not data:
            return None

        raw_mp4 = out_mp4.with_suffix(".colab_raw.mp4")
        raw_mp4.write_bytes(base64.b64decode(data["video_b64"]))
        try:
            subprocess.run(
                [
                    FFMPEG_BIN, "-y", "-i", str(raw_mp4), "-i", str(audio_wav),
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-vf", f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},fps=30,format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "19",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-shortest", str(out_mp4),
                ],
                check=True,
                timeout=90,
            )
        finally:
            raw_mp4.unlink(missing_ok=True)

        return {
            "ok": True,
            "animation_engine_used": f"tier1_colab:{data.get('engine') or colab_engine}",
            "detector": "colab_gpu_worker",
            "duration": round(_probe_duration(out_mp4), 2),
            "is_silent_reaction": bool(is_silent_reaction),
            "output_mp4": str(out_mp4),
        }
    except Exception:
        return None


def animate_character_talking_take(
    image_path: Path,
    audio_wav: Path,
    out_mp4: Path,
    *,
    prompt: str = "",
    emotion: str = "warm",
    emotion_preset: str = "warm",
    camera_action: str = "zoom_in",
    lead_in_pause: float = 0.0,
    is_silent_reaction: bool = False,
    colab_url: str = "",
    colab_token: str = "",
    colab_engine: str = "liveportrait_latentsync",
    env_map: Optional[Dict[str, str]] = None,
    width: int = 1080,
    height: int = 1920,
    fps: int = 30,
) -> Dict[str, Any]:
    """Cascade Lip-Sync animator: Tier 1 Colab GPU v2.8 if reachable, else Tier 2 Local 2.5D Visemes."""
    eff_url = (colab_url or (env_map or {}).get("COLAB_GPU_URL") or "").strip()
    eff_tok = (colab_token or (env_map or {}).get("COLAB_GPU_TOKEN") or "").strip()
    if eff_url:
        t1 = _try_tier1_colab_animate(
            image_path=image_path,
            audio_wav=audio_wav,
            out_mp4=out_mp4,
            prompt=prompt,
            colab_url=eff_url,
            colab_token=eff_tok,
            colab_engine=colab_engine,
            emotion_preset=emotion_preset,
            lead_in_pause=lead_in_pause,
            is_silent_reaction=is_silent_reaction,
            width=width,
            height=height,
        )
        if t1 is not None:
            return t1

    return _animate_local_viseme_take(
        image_path=image_path,
        audio_wav=audio_wav,
        out_mp4=out_mp4,
        emotion=emotion,
        emotion_preset=emotion_preset,
        camera_action=camera_action,
        lead_in_pause=lead_in_pause,
        is_silent_reaction=is_silent_reaction,
        width=width,
        height=height,
        fps=fps,
    )
