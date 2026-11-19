#!/usr/bin/env python3
"""Cinematic Quality Assurance & Objective Video Auditor (v4.0).

Measures objective cinematography, acting, audio dynamics, gaze/eyeline (Yaw),
Over-the-Shoulder (OTS) / multi-character staging, burned subtitle intrusion,
static-crop reuse detection, and lip-sync metrics on vertical 9:16 micro-novelas.

Outputs structured JSON per video and checks acceptance criteria calibrated against
`docs/refs/espectativa.mp4`.
"""

from __future__ import annotations

import argparse
import gc
import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

for _k, _v in (("OMP_NUM_THREADS", "2"), ("MKL_NUM_THREADS", "2"), ("CT2_NUM_THREADS", "2")):
    os.environ.setdefault(_k, _v)
if Path("/opt/ai-media-studio/supoclip/.cache/huggingface").is_dir() and not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = "/opt/ai-media-studio/supoclip/.cache/huggingface"

FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"
_WHISPER_CACHE: Dict[str, Any] = {}


def _probe_stream_info(mp4_path: Path) -> Dict[str, Any]:
    out = subprocess.check_output(
        [
            FFPROBE_BIN,
            "-v",
            "error",
            "-show_format",
            "-show_streams",
            "-of",
            "json",
            str(mp4_path),
        ],
        text=True,
        timeout=30,
    )
    data = json.loads(out)
    streams = data.get("streams") or []
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}

    fps_raw = str(v_stream.get("avg_frame_rate") or v_stream.get("r_frame_rate") or "30/1")
    if "/" in fps_raw:
        num, den = fps_raw.split("/", 1)
        fps = float(num) / max(1.0, float(den))
    else:
        fps = float(fps_raw or 30.0)

    dur = float(fmt.get("duration") or v_stream.get("duration") or 0.0)
    return {
        "width": int(v_stream.get("width") or 0),
        "height": int(v_stream.get("height") or 0),
        "fps": round(fps, 3),
        "duration_sec": round(dur, 3),
        "size_bytes": int(fmt.get("size") or mp4_path.stat().st_size),
        "has_audio": a_stream is not None,
        "audio_sample_rate": int(a_stream.get("sample_rate") or 0) if a_stream else 0,
        "audio_channels": int(a_stream.get("channels") or 0) if a_stream else 0,
    }


def _measure_ebur128(mp4_path: Path) -> Dict[str, float]:
    proc = subprocess.run(
        [
            FFMPEG_BIN,
            "-nostats",
            "-i",
            str(mp4_path),
            "-filter_complex",
            "ebur128=peak=true",
            "-f",
            "null",
            "-",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=60,
    )
    stderr = proc.stderr
    i_matches = re.findall(r"I:\s*([+-]?[0-9.]+)\s*LUFS", stderr)
    lra_matches = re.findall(r"LRA:\s*([+-]?[0-9.]+)\s*LU", stderr)
    tp_matches = re.findall(r"Peak:\s*([+-]?[0-9.]+)\s*dBFS", stderr)
    return {
        "integrated_lufs": round(float(i_matches[-1]), 2) if i_matches else -70.0,
        "lra_lu": round(float(lra_matches[-1]), 2) if lra_matches else 0.0,
        "true_peak_dbtp": round(float(tp_matches[-1]), 2) if tp_matches else -70.0,
    }


def _extract_pcm_16k_and_48k(mp4_path: Path) -> Tuple[np.ndarray, np.ndarray]:
    with tempfile.NamedTemporaryFile(suffix="_16k.wav", delete=False) as tf16, tempfile.NamedTemporaryFile(
        suffix="_48k.wav", delete=False
    ) as tf48:
        p16 = Path(tf16.name)
        p48 = Path(tf48.name)
    try:
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-v",
                "error",
                "-i",
                str(mp4_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "16000",
                "-c:a",
                "pcm_s16le",
                str(p16),
            ],
            check=True,
            timeout=45,
        )
        subprocess.run(
            [
                FFMPEG_BIN,
                "-y",
                "-v",
                "error",
                "-i",
                str(mp4_path),
                "-vn",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-c:a",
                "pcm_s16le",
                str(p48),
            ],
            check=True,
            timeout=45,
        )
        with wave.open(str(p16), "rb") as wf16:
            pcm16 = np.frombuffer(wf16.readframes(wf16.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        with wave.open(str(p48), "rb") as wf48:
            pcm48 = np.frombuffer(wf48.readframes(wf48.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
        return pcm16, pcm48
    finally:
        p16.unlink(missing_ok=True)
        p48.unlink(missing_ok=True)


def _analyze_audio_windows(pcm48: np.ndarray, sr: int = 48000, win_sec: float = 0.25) -> Dict[str, Any]:
    win_samples = max(1, int(round(sr * win_sec)))
    n_wins = max(1, len(pcm48) // win_samples)
    win_dbfs: List[float] = []
    below_40 = 0
    below_45 = 0
    for i in range(n_wins):
        seg = pcm48[i * win_samples : (i + 1) * win_samples]
        rms = float(np.sqrt(np.mean(seg * seg) + 1e-12))
        db = 20.0 * math.log10(max(rms, 1e-7))
        win_dbfs.append(round(db, 2))
        if db < -40.0:
            below_40 += 1
        if db < -45.0:
            below_45 += 1
    return {
        "window_sec": win_sec,
        "total_windows": n_wins,
        "min_window_dbfs": round(min(win_dbfs), 2) if win_dbfs else -90.0,
        "max_window_dbfs": round(max(win_dbfs), 2) if win_dbfs else -90.0,
        "median_window_dbfs": round(float(np.median(win_dbfs)), 2) if win_dbfs else -90.0,
        "windows_below_minus_40db": below_40,
        "windows_below_minus_45db": below_45,
    }


def _transcribe_words_whisper(pcm16: np.ndarray) -> List[Dict[str, Any]]:
    try:
        from faster_whisper import WhisperModel

        if "base" not in _WHISPER_CACHE:
            _WHISPER_CACHE["base"] = WhisperModel(
                "base", device="cpu", compute_type="int8", cpu_threads=2, num_workers=1
            )
        model = _WHISPER_CACHE["base"]
        segments, _ = model.transcribe(
            pcm16,
            language="pt",
            word_timestamps=True,
            vad_filter=True,
            beam_size=2,
        )
        words: List[Dict[str, Any]] = []
        for seg in segments:
            if getattr(seg, "words", None):
                for w in seg.words:
                    tok = str(getattr(w, "word", "") or "").strip()
                    if tok:
                        words.append(
                            {
                                "word": tok,
                                "start": round(float(w.start), 3),
                                "end": round(float(w.end), 3),
                                "probability": round(float(getattr(w, "probability", 0.0)), 3),
                            }
                        )
            elif str(seg.text or "").strip():
                words.append(
                    {
                        "word": str(seg.text).strip(),
                        "start": round(float(seg.start), 3),
                        "end": round(float(seg.end), 3),
                        "probability": 0.5,
                    }
                )
        return words
    except Exception as exc:
        return [{"error": f"whisper_unavailable: {exc}"}]


def _resolve_landmarker_model() -> Optional[Path]:
    here = Path(__file__).resolve()
    for cand in (
        here.parent.parent / "tools/hyperframes/models/face_landmarker.task",
        Path("/opt/ai-media-studio/tools/hyperframes/models/face_landmarker.task"),
    ):
        if cand.is_file():
            return cand
    return None


def _init_face_analyzers():
    mp_face_landmarker = None
    mp_face_mesh = None
    mp_face_det = None
    try:
        import mediapipe as mp

        task_model = _resolve_landmarker_model()
        if task_model is not None:
            try:
                from mediapipe.tasks import python as mp_python
                from mediapipe.tasks.python import vision as mp_vision

                opts = mp_vision.FaceLandmarkerOptions(
                    base_options=mp_python.BaseOptions(model_asset_path=str(task_model)),
                    running_mode=mp_vision.RunningMode.IMAGE,
                    num_faces=4,
                    min_face_detection_confidence=0.25,
                    min_face_presence_confidence=0.25,
                    min_tracking_confidence=0.25,
                )
                mp_face_landmarker = mp_vision.FaceLandmarker.create_from_options(opts)
            except Exception:
                mp_face_landmarker = None

        if mp_face_landmarker is None and hasattr(mp, "solutions"):
            mp_face_mesh = mp.solutions.face_mesh.FaceMesh(
                static_image_mode=False,
                max_num_faces=4,
                refine_landmarks=True,
                min_detection_confidence=0.38,
                min_tracking_confidence=0.38,
            )
            mp_face_det = mp.solutions.face_detection.FaceDetection(
                model_selection=1,
                min_detection_confidence=0.38,
            )
    except Exception:
        pass
    haar_base = Path(cv2.data.haarcascades) if hasattr(cv2, "data") else Path("/usr/share/opencv4/haarcascades")
    haar_frontal_p = haar_base / "haarcascade_frontalface_default.xml"
    haar_profile_p = haar_base / "haarcascade_profileface.xml"
    haar_frontal = cv2.CascadeClassifier(str(haar_frontal_p)) if haar_frontal_p.is_file() else None
    haar_profile = cv2.CascadeClassifier(str(haar_profile_p)) if haar_profile_p.is_file() else None
    return mp_face_landmarker, mp_face_mesh, mp_face_det, haar_frontal, haar_profile


def _landmarks_to_face_dict(lm: Any, w: int, h: int, source: str) -> Dict[str, Any]:
    xs = [p.x * w for p in lm]
    ys = [p.y * h for p in lm]
    x0, x1 = max(0.0, min(xs)), min(float(w), max(xs))
    y0, y1 = max(0.0, min(ys)), min(float(h), max(ys))
    fw = max(1.0, x1 - x0)
    fh = max(1.0, y1 - y0)

    nose_x = lm[1].x * w
    l_edge_x = lm[234].x * w
    r_edge_x = lm[454].x * w
    d_left = max(1.0, nose_x - l_edge_x)
    d_right = max(1.0, r_edge_x - nose_x)
    asym = (d_left - d_right) / max(1.0, d_left + d_right)
    z_diff = (lm[454].z - lm[234].z) * 115.0
    yaw_2d = math.degrees(math.asin(max(-1.0, min(1.0, asym)))) * 1.45
    yaw_deg = 0.65 * yaw_2d + 0.35 * z_diff

    upper_lip_y = lm[13].y * h
    lower_lip_y = lm[14].y * h
    mouth_open_ratio = abs(lower_lip_y - upper_lip_y) / fh

    return {
        "bbox": [round(x0, 1), round(y0, 1), round(fw, 1), round(fh, 1)],
        "center_x_norm": round(0.5 * (x0 + x1) / w, 3),
        "center_y_norm": round(0.5 * (y0 + y1) / h, 3),
        "area_norm": round((fw * fh) / float(w * h), 4),
        "yaw_deg": round(yaw_deg, 2),
        "mouth_open_ratio": round(mouth_open_ratio, 4),
        "source": source,
    }


def _analyze_frame_faces(
    frame_bgr: np.ndarray,
    mp_face_landmarker: Any,
    mp_face_mesh: Any,
    mp_face_det: Any,
    haar_frontal: Any,
    haar_profile: Any,
) -> Dict[str, Any]:
    h, w = frame_bgr.shape[:2]
    rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
    faces: List[Dict[str, Any]] = []

    if mp_face_landmarker is not None:
        try:
            import mediapipe as mp

            # Normalized coordinates work identically on half-res; 4x faster on CPU
            if max(h, w) > 960:
                scale = 960.0 / float(max(h, w))
                rgb_det = cv2.resize(rgb, (int(round(w * scale)), int(round(h * scale))), interpolation=cv2.INTER_AREA)
            else:
                rgb_det = rgb
            mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb_det)
            res = mp_face_landmarker.detect(mp_img)
            for lm in getattr(res, "face_landmarks", None) or []:
                faces.append(_landmarks_to_face_dict(lm, w, h, "mediapipe_landmarker"))
        except Exception:
            pass

    if not faces and mp_face_mesh is not None:
        res = mp_face_mesh.process(rgb)
        if res and res.multi_face_landmarks:
            for fl in res.multi_face_landmarks:
                faces.append(_landmarks_to_face_dict(fl.landmark, w, h, "mediapipe_mesh"))

    # Also check MediaPipe FaceDetection to catch secondary/background/profile faces missed by mesh
    if mp_face_det is not None:
        dres = mp_face_det.process(rgb)
        if dres and dres.detections:
            for det in dres.detections:
                bb = det.location_data.relative_bounding_box
                x0 = max(0.0, bb.xmin * w)
                y0 = max(0.0, bb.ymin * h)
                fw = max(1.0, bb.width * w)
                fh = max(1.0, bb.height * h)
                cx = (x0 + 0.5 * fw) / w
                cy = (y0 + 0.5 * fh) / h
                # Check if already covered by mesh face
                already = any(
                    abs(f["center_x_norm"] - cx) < 0.10 and abs(f["center_y_norm"] - cy) < 0.10 for f in faces
                )
                if not already:
                    kps = det.location_data.relative_keypoints
                    # 0: right eye, 1: left eye, 2: nose tip, 4: right ear, 5: left ear
                    yaw_est = 0.0
                    if len(kps) >= 6:
                        d_r = abs(kps[2].x - kps[4].x)
                        d_l = abs(kps[5].x - kps[2].x)
                        asym = (d_l - d_r) / max(1e-4, d_l + d_r)
                        yaw_est = math.degrees(math.asin(max(-1.0, min(1.0, asym)))) * 1.4
                    faces.append(
                        {
                            "bbox": [round(x0, 1), round(y0, 1), round(fw, 1), round(fh, 1)],
                            "center_x_norm": round(cx, 3),
                            "center_y_norm": round(cy, 3),
                            "area_norm": round((fw * fh) / float(w * h), 4),
                            "yaw_deg": round(yaw_est, 2),
                            "mouth_open_ratio": 0.0,
                            "source": "mediapipe_det",
                        }
                    )

    if not faces and haar_frontal is not None:
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        rects = haar_frontal.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=4, minSize=(48, 48))
        for (x, y, fw, fh) in rects:
            faces.append(
                {
                    "bbox": [float(x), float(y), float(fw), float(fh)],
                    "center_x_norm": round((x + 0.5 * fw) / w, 3),
                    "center_y_norm": round((y + 0.5 * fh) / h, 3),
                    "area_norm": round((fw * fh) / float(w * h), 4),
                    "yaw_deg": 0.0,
                    "mouth_open_ratio": 0.0,
                    "source": "haar_frontal",
                }
            )
        if not faces and haar_profile is not None:
            prects = haar_profile.detectMultiScale(gray, scaleFactor=1.12, minNeighbors=4, minSize=(48, 48))
            for (x, y, fw, fh) in prects:
                faces.append(
                    {
                        "bbox": [float(x), float(y), float(fw), float(fh)],
                        "center_x_norm": round((x + 0.5 * fw) / w, 3),
                        "center_y_norm": round((y + 0.5 * fh) / h, 3),
                        "area_norm": round((fw * fh) / float(w * h), 4),
                        "yaw_deg": 25.0,
                        "mouth_open_ratio": 0.0,
                        "source": "haar_profile",
                    }
                )

    faces.sort(key=lambda f: f["area_norm"], reverse=True)
    return {"faces_count": len(faces), "faces": faces}


def _detect_ots_foreground_blur(frame_bgr: np.ndarray, primary_face: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Detect blurred over-the-shoulder (OTS) foreground mass on left or right frame edge."""
    h, w = frame_bgr.shape[:2]
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)

    if primary_face:
        bx, by, bw, bh = [int(round(v)) for v in primary_face["bbox"]]
        bx0, by0 = max(0, bx), max(0, by)
        bx1, by1 = min(w, bx + bw), min(h, by + bh)
        face_roi = gray[by0:by1, bx0:bx1]
        face_sharpness = float(cv2.Laplacian(face_roi, cv2.CV_64F).var()) if face_roi.size > 100 else 100.0
        cx_norm = float(primary_face["center_x_norm"])
        yaw = float(primary_face["yaw_deg"])
    else:
        face_sharpness = float(cv2.Laplacian(gray[int(0.2 * h) : int(0.6 * h), int(0.3 * w) : int(0.7 * w)], cv2.CV_64F).var())
        cx_norm = 0.5
        yaw = 0.0

    # Left and right foreground OTS zones (lower-mid vertical 35%..95%, outer 26% width)
    y0, y1 = int(0.35 * h), int(0.95 * h)
    left_zone = frame_bgr[y0:y1, 0 : int(0.26 * w)]
    right_zone = frame_bgr[y0:y1, int(0.74 * w) : w]
    left_gray = gray[y0:y1, 0 : int(0.26 * w)]
    right_gray = gray[y0:y1, int(0.74 * w) : w]

    # Upper background corners for comparison
    bg_left = frame_bgr[int(0.04 * h) : int(0.24 * h), 0 : int(0.24 * w)]
    bg_right = frame_bgr[int(0.04 * h) : int(0.24 * h), int(0.76 * w) : w]

    left_sharp = float(cv2.Laplacian(left_gray, cv2.CV_64F).var())
    right_sharp = float(cv2.Laplacian(right_gray, cv2.CV_64F).var())

    # Color/luminance mass difference between lower OTS zone and upper background corner
    left_color_diff = float(np.linalg.norm(np.mean(left_zone, axis=(0, 1)) - np.mean(bg_left, axis=(0, 1))))
    right_color_diff = float(np.linalg.norm(np.mean(right_zone, axis=(0, 1)) - np.mean(bg_right, axis=(0, 1))))

    # An OTS foreground shoulder/head is darker or distinctly colored compared to upper background,
    # heavily defocused relative to the subject's face, AND the subject has non-frontal eyeline or off-center framing
    blur_ratio_left = left_sharp / max(1.0, face_sharpness)
    blur_ratio_right = right_sharp / max(1.0, face_sharpness)

    ots_left = (
        blur_ratio_left < 0.45
        and left_color_diff > 26.0
        and (cx_norm > 0.46 or abs(yaw) > 8.0)
    )
    ots_right = (
        blur_ratio_right < 0.45
        and right_color_diff > 26.0
        and (cx_norm < 0.54 or abs(yaw) > 8.0)
    )
    return {
        "ots_detected": bool(ots_left or ots_right),
        "ots_side": "left" if ots_left else ("right" if ots_right else "none"),
        "face_sharpness": round(face_sharpness, 2),
        "blur_ratio_left": round(blur_ratio_left, 3),
        "blur_ratio_right": round(blur_ratio_right, 3),
    }


def _detect_burned_subtitles(frames_bgr: List[np.ndarray]) -> Dict[str, Any]:
    """Detect burned-in white/yellow text with black outline stroke in lower/mid frame."""
    if not frames_bgr:
        return {"has_burned_subtitles": False}

    h, w = frames_bgr[0].shape[:2]
    y_scan_0 = int(0.45 * h)
    y_scan_1 = int(0.94 * h)
    x_scan_0 = int(0.08 * w)
    x_scan_1 = int(0.92 * w)

    sub_rows_accum = np.zeros(y_scan_1 - y_scan_0, dtype=np.float32)
    frames_with_sub = 0
    step = max(1, len(frames_bgr) // 30)
    sampled = 0

    for idx in range(0, len(frames_bgr), step):
        sampled += 1
        roi = frames_bgr[idx][y_scan_0:y_scan_1, x_scan_0:x_scan_1]
        gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
        # Bright text pixels (white >= 230 or bright yellow) adjacent to very dark outline pixels (<= 32)
        bright = (gray >= 232).astype(np.uint8)
        dark = (gray <= 32).astype(np.uint8)
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (7, 7))
        dark_dilated = cv2.dilate(dark, kernel)
        stroke_text = cv2.bitwise_and(bright, dark_dilated)
        # Also require high horizontal gradient density characteristic of glyphs
        sobel_x = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
        high_edge = (sobel_x > 140.0).astype(np.uint8)
        glyph_mask = cv2.bitwise_and(stroke_text, high_edge)

        row_counts = np.sum(glyph_mask > 0, axis=1)
        active_rows = np.where(row_counts > (0.035 * (x_scan_1 - x_scan_0)))[0]
        if len(active_rows) >= 10:
            frames_with_sub += 1
            sub_rows_accum[active_rows] += 1.0

    sub_ratio = frames_with_sub / float(max(1, sampled))
    has_subs = sub_ratio >= 0.18
    if not has_subs:
        return {
            "has_burned_subtitles": False,
            "frames_with_subtitles_ratio": round(sub_ratio, 3),
            "overlaps_torso_zone": False,
        }

    persistent_rows = np.where(sub_rows_accum >= max(2.0, 0.20 * frames_with_sub))[0]
    if len(persistent_rows) > 0:
        y_top_px = int(y_scan_0 + persistent_rows[0])
        y_bot_px = int(y_scan_0 + persistent_rows[-1])
    else:
        y_top_px = int(0.70 * h)
        y_bot_px = int(0.78 * h)

    center_y_norm = 0.5 * (y_top_px + y_bot_px) / float(h)
    # Torso zone in medium close-up is roughly 0.55..0.80 of height; safe bottom zone is >= 0.78
    overlaps_torso = y_top_px < int(0.77 * h)
    return {
        "has_burned_subtitles": True,
        "frames_with_subtitles_ratio": round(sub_ratio, 3),
        "subtitle_y_top_px": y_top_px,
        "subtitle_y_bottom_px": y_bot_px,
        "subtitle_height_px": max(0, y_bot_px - y_top_px),
        "subtitle_center_y_norm": round(center_y_norm, 3),
        "overlaps_torso_zone": bool(overlaps_torso),
    }


def _detect_static_crop_reuse(shot_rep_frames: List[Tuple[int, np.ndarray]]) -> List[Dict[str, Any]]:
    """Check if any two shots reuse the exact same static base portrait (or +14% optical crop)."""
    reuses: List[Dict[str, Any]] = []
    for i in range(len(shot_rep_frames)):
        idx_a, fr_a = shot_rep_frames[i]
        ga = cv2.resize(cv2.cvtColor(fr_a, cv2.COLOR_BGR2GRAY), (270, 480))
        for j in range(i + 1, len(shot_rep_frames)):
            idx_b, fr_b = shot_rep_frames[j]
            gb = cv2.resize(cv2.cvtColor(fr_b, cv2.COLOR_BGR2GRAY), (270, 480))

            # Direct 1:1 upper background correlation (top 30% of frame avoids mouth motion)
            top_a = ga[0:140, :]
            top_b = gb[0:140, :]
            ncc_1to1 = float(cv2.matchTemplate(top_b, top_a, cv2.TM_CCOEFF_NORMED)[0, 0])

            # Check +14% crop scale (1232x2190 -> 1080x1920 is ~1.14x scale)
            ga_zoom = cv2.resize(ga, (308, 548), interpolation=cv2.INTER_LINEAR)
            res_zoom = cv2.matchTemplate(ga_zoom[0:200, :], top_b, cv2.TM_CCOEFF_NORMED)
            _, max_ncc_zoom, _, _ = cv2.minMaxLoc(res_zoom)

            best_ncc = max(ncc_1to1, float(max_ncc_zoom))
            if best_ncc >= 0.88:
                reuses.append(
                    {
                        "shot_a": idx_a,
                        "shot_b": idx_b,
                        "background_ncc": round(best_ncc, 4),
                        "reuse_type": "optical_crop_14pct" if max_ncc_zoom > ncc_1to1 + 0.02 else "identical_static_plate",
                    }
                )
    return reuses


def _extract_face_signature(frame_bgr: np.ndarray, face: Dict[str, Any]) -> Optional[np.ndarray]:
    h, w = frame_bgr.shape[:2]
    bx, by, bw, bh = [int(round(v)) for v in face["bbox"]]
    x0 = max(0, bx)
    y0 = max(0, by)
    x1 = min(w, bx + bw)
    y1 = min(h, by + bh)
    if x1 - x0 < 24 or y1 - y0 < 24:
        return None
    crop = cv2.resize(frame_bgr[y0:y1, x0:x1], (64, 64))
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [18, 16], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY).astype(np.float32)
    gray = (gray - np.mean(gray)) / (np.std(gray) + 1e-5)
    spatial = cv2.resize(gray, (16, 16)).flatten()
    spatial = spatial / (np.linalg.norm(spatial) + 1e-6)
    vec = np.concatenate([hist.flatten(), spatial])
    vec = vec / (np.linalg.norm(vec) + 1e-6)
    return vec


def evaluate_video(mp4_path: Path) -> Dict[str, Any]:
    if not mp4_path.is_file():
        raise FileNotFoundError(f"Video not found: {mp4_path}")

    stream_info = _probe_stream_info(mp4_path)
    ebur = _measure_ebur128(mp4_path) if stream_info["has_audio"] else {"integrated_lufs": -70.0, "lra_lu": 0.0, "true_peak_dbtp": -70.0}
    pcm16, pcm48 = _extract_pcm_16k_and_48k(mp4_path) if stream_info["has_audio"] else (np.zeros(16000, dtype=np.float32), np.zeros(48000, dtype=np.float32))
    audio_windows = _analyze_audio_windows(pcm48, sr=48000, win_sec=0.25)
    whisper_words = _transcribe_words_whisper(pcm16) if stream_info["has_audio"] else []

    cap = cv2.VideoCapture(str(mp4_path))
    if not cap.isOpened():
        raise RuntimeError(f"Failed to open video: {mp4_path}")

    fps = stream_info["fps"] or 30.0
    frames_small_gray: List[np.ndarray] = []

    while True:
        ok, fr = cap.read()
        if not ok:
            break
        g = cv2.cvtColor(fr, cv2.COLOR_BGR2GRAY)
        # 180x320 grayscale as specified in <dados_medidos_por_mim>
        frames_small_gray.append(cv2.resize(g, (180, 320), interpolation=cv2.INTER_AREA))
    cap.release()

    n_frames = len(frames_small_gray)
    if n_frames < 5:
        raise RuntimeError(f"Too few frames ({n_frames}) in {mp4_path}")

    # Frame-to-frame motion |Δ| on 180x320 grayscale
    diffs_all: List[float] = []
    upper_motion_all: List[float] = []
    lower_body_motion_all: List[float] = []
    outer_bg_motion_all: List[float] = []
    cut_indices: List[int] = [0]

    for i in range(1, n_frames):
        a = frames_small_gray[i - 1].astype(np.float32)
        b = frames_small_gray[i].astype(np.float32)
        d_map = np.abs(b - a)
        mad = float(np.mean(d_map))
        diffs_all.append(mad)

        # Detect scene cut: sharp jump in 180x320 MAD (> 18.5 and > 3.0x local rolling median)
        local_prev = float(np.median(diffs_all[max(0, len(diffs_all) - 8) : -1])) if len(diffs_all) > 2 else 1.0
        if mad > 18.5 and mad > max(18.5, 3.0 * local_prev) and (i - cut_indices[-1]) >= int(round(0.45 * fps)):
            cut_indices.append(i)
        else:
            # Intra-shot regional motion
            upper_motion_all.append(float(np.mean(d_map[40:160, 40:140])))
            lower_body_motion_all.append(float(np.mean(d_map[160:290, 25:155])))
            mask_bg = np.ones_like(d_map, dtype=bool)
            mask_bg[40:280, 30:150] = False
            outer_bg_motion_all.append(float(np.mean(d_map[mask_bg])))

    cut_indices.append(n_frames)
    cut_timestamps_sec = [round(idx / fps, 2) for idx in cut_indices[1:-1]]

    # Compute exact frame indices needed for full-res analysis (keeps peak RAM < 250 MB)
    needed_indices: set[int] = set()
    for s_idx in range(len(cut_indices) - 1):
        f0, f1 = cut_indices[s_idx], cut_indices[s_idx + 1]
        needed_indices.add((f0 + f1) // 2)
        sample_step = max(1, (f1 - f0) // 10)
        for fi in range(f0, f1, sample_step):
            needed_indices.add(fi)
    sub_step = max(1, n_frames // 30)
    sub_indices = list(range(0, n_frames, sub_step))
    needed_indices.update(sub_indices)

    sampled_bgr: Dict[int, np.ndarray] = {}
    cap2 = cv2.VideoCapture(str(mp4_path))
    fi_cur = 0
    while fi_cur < n_frames:
        if fi_cur in needed_indices:
            ok, fr = cap2.read()
            if not ok:
                break
            sampled_bgr[fi_cur] = fr
        else:
            if not cap2.grab():
                break
        fi_cur += 1
    cap2.release()

    # Per-frame audio RMS
    frame_rms = np.zeros(n_frames, dtype=np.float32)
    step_pcm = max(1.0, len(pcm16) / float(n_frames))
    win_pcm = max(256, int(step_pcm * 1.4))
    for i in range(n_frames):
        c = int(round(i * step_pcm))
        s = max(0, c - win_pcm // 2)
        seg = pcm16[s : s + win_pcm]
        if len(seg) > 0:
            frame_rms[i] = float(np.sqrt(np.mean(seg * seg) + 1e-12))
    speech_rms_thresh = max(0.012, float(np.percentile(frame_rms, 35)))

    mp_face_landmarker, mp_face_mesh, mp_face_det, haar_frontal, haar_profile = _init_face_analyzers()

    shots_report: List[Dict[str, Any]] = []
    shot_rep_frames: List[Tuple[int, np.ndarray]] = []
    shot_face_sigs: List[Tuple[int, np.ndarray]] = []
    total_speaking_face_frames = 0
    non_frontal_speaking_face_frames = 0
    multi_person_or_ots_shots = 0

    valid_words = [w for w in whisper_words if "word" in w]

    for s_idx in range(len(cut_indices) - 1):
        f0, f1 = cut_indices[s_idx], cut_indices[s_idx + 1]
        t0, t1 = round(f0 / fps, 2), round(f1 / fps, 2)
        dur_s = round((f1 - f0) / fps, 2)
        mid_f = (f0 + f1) // 2
        if mid_f in sampled_bgr:
            shot_rep_frames.append((s_idx + 1, sampled_bgr[mid_f]))

        shot_diffs = [
            float(np.mean(np.abs(frames_small_gray[k].astype(np.float32) - frames_small_gray[k - 1].astype(np.float32))))
            for k in range(f0 + 1, f1)
        ] or [0.0]

        # Audio in this shot
        s_pcm = pcm48[int(round(t0 * 48000)) : int(round(t1 * 48000))]
        s_rms = float(np.sqrt(np.mean(s_pcm * s_pcm) + 1e-12)) if len(s_pcm) > 0 else 1e-7
        s_dbfs = round(20.0 * math.log10(max(s_rms, 1e-7)), 2)
        s_words = [w["word"] for w in valid_words if (w["start"] < t1 - 0.08 and w["end"] > t0 + 0.08)]
        s_transcript = " ".join(s_words).strip()

        # Sample frames inside the shot for faces, yaw, OTS
        sample_step = max(1, (f1 - f0) // 10)
        yaws: List[float] = []
        max_faces_seen = 0
        ots_votes = 0
        sampled_cnt = 0
        mouth_apertures: List[float] = []
        best_face_for_sig: Optional[Tuple[np.ndarray, Dict[str, Any]]] = None

        for fi in range(f0, f1, sample_step):
            fr_cur = sampled_bgr.get(fi)
            if fr_cur is None:
                continue
            sampled_cnt += 1
            f_res = _analyze_frame_faces(
                fr_cur, mp_face_landmarker, mp_face_mesh, mp_face_det, haar_frontal, haar_profile
            )
            fc = f_res["faces_count"]
            max_faces_seen = max(max_faces_seen, fc)
            p_face = f_res["faces"][0] if fc > 0 else None

            if p_face is not None:
                yaw_val = float(p_face["yaw_deg"])
                yaws.append(yaw_val)
                mouth_apertures.append(float(p_face["mouth_open_ratio"]))
                if best_face_for_sig is None or p_face["area_norm"] > best_face_for_sig[1]["area_norm"]:
                    best_face_for_sig = (fr_cur, p_face)

                if frame_rms[fi] >= speech_rms_thresh:
                    total_speaking_face_frames += 1
                    if abs(yaw_val) > 8.0:
                        non_frontal_speaking_face_frames += 1

            ots_info = _detect_ots_foreground_blur(fr_cur, p_face)
            if ots_info["ots_detected"]:
                ots_votes += 1

        if best_face_for_sig is not None:
            sig = _extract_face_signature(best_face_for_sig[0], best_face_for_sig[1])
            if sig is not None:
                shot_face_sigs.append((s_idx + 1, sig))

        has_ots = (ots_votes / float(max(1, sampled_cnt))) >= 0.35
        has_multi_or_ots = (max_faces_seen >= 2) or has_ots
        if has_multi_or_ots:
            multi_person_or_ots_shots += 1

        mean_abs_yaw = round(float(np.mean(np.abs(yaws))), 2) if yaws else 0.0
        mean_yaw = round(float(np.mean(yaws)), 2) if yaws else 0.0
        non_frontal_shot_pct = round(100.0 * sum(1 for y in yaws if abs(y) > 8.0) / float(len(yaws)), 1) if yaws else 0.0

        shots_report.append(
            {
                "shot_index": s_idx + 1,
                "start_sec": t0,
                "end_sec": t1,
                "duration_sec": dur_s,
                "frames": [f0, f1],
                "motion_mean": round(float(np.mean(shot_diffs)), 3),
                "motion_median": round(float(np.median(shot_diffs)), 3),
                "audio_rms_dbfs": s_dbfs,
                "asr_words_count": len(s_words),
                "asr_transcript": s_transcript,
                "has_audible_speech": len(s_words) > 0,
                "max_faces_detected": max_faces_seen,
                "ots_foreground_detected": bool(has_ots),
                "multi_person_or_ots": bool(has_multi_or_ots),
                "mean_yaw_deg": mean_yaw,
                "mean_abs_yaw_deg": mean_abs_yaw,
                "non_frontal_frames_pct": non_frontal_shot_pct,
                "mouth_open_std": round(float(np.std(mouth_apertures)), 4) if len(mouth_apertures) > 1 else 0.0,
            }
        )

    if mp_face_landmarker is not None:
        mp_face_landmarker.close()
    if mp_face_mesh is not None:
        mp_face_mesh.close()
    if mp_face_det is not None:
        mp_face_det.close()

    static_reuses = _detect_static_crop_reuse(shot_rep_frames)
    sub_frames = [sampled_bgr[i] for i in sub_indices if i in sampled_bgr]
    subtitles_info = _detect_burned_subtitles(sub_frames)

    # Free frame buffers before verify_lipsync_motion loads video frames
    del sampled_bgr, shot_rep_frames, sub_frames, frames_small_gray
    gc.collect()

    # Compute cross-shot identity similarity for recurring characters
    id_sims: List[float] = []
    for i in range(len(shot_face_sigs)):
        for j in range(i + 1, len(shot_face_sigs)):
            sim = float(np.dot(shot_face_sigs[i][1], shot_face_sigs[j][1]))
            id_sims.append(sim)
    max_pair_identity_sim = round(max(id_sims), 3) if id_sims else 0.0
    mean_pair_identity_sim = round(float(np.mean(id_sims)), 3) if id_sims else 0.0

    # Also run verify_lipsync_motion audit if available
    lipsync_report: Dict[str, Any] = {}
    try:
        for vlm_dir in (
            Path(__file__).resolve().parent.parent / "tools/hyperframes",
            Path("/opt/ai-media-studio/tools/hyperframes"),
        ):
            if vlm_dir.is_dir() and str(vlm_dir) not in sys.path:
                sys.path.insert(0, str(vlm_dir))
        import verify_lipsync_motion as vlm

        lipsync_report = vlm.audit_lipsync_video(mp4_path, min_mouth_diff=3.0)
    except Exception as exc:
        lipsync_report = {"ok": False, "error": str(exc)}

    shot_durs = [s["duration_sec"] for s in shots_report]
    non_frontal_speaking_pct = (
        round(100.0 * non_frontal_speaking_face_frames / float(total_speaking_face_frames), 1)
        if total_speaking_face_frames > 0
        else 0.0
    )

    motion_mean = round(float(np.mean(diffs_all)), 2)
    motion_median = round(float(np.median(diffs_all)), 2)

    # Measure longest non-speech gap across dialogue (excluding trailing silent reaction shot)
    dialogue_end_limit = shots_report[-1]["start_sec"] if (len(shots_report) >= 2 and not shots_report[-1]["has_audible_speech"]) else stream_info["duration_sec"]
    dial_words = [w for w in valid_words if w["start"] < dialogue_end_limit]
    speech_gaps: List[float] = []
    prev_end = 0.0
    for w in dial_words:
        if w["start"] > prev_end:
            speech_gaps.append(w["start"] - prev_end)
        prev_end = max(prev_end, w["end"])
    if dialogue_end_limit > prev_end:
        speech_gaps.append(dialogue_end_limit - prev_end)
    max_dialogue_speech_gap_sec = round(max(speech_gaps), 2) if speech_gaps else round(dialogue_end_limit, 2)

    speaking_shots = [s for s in shots_report if s["has_audible_speech"]]
    landmark_lipsync_ok = bool(speaking_shots) and all(s["mouth_open_std"] >= 0.012 for s in speaking_shots)

    # Acceptance criteria calibrated against docs/refs/espectativa.mp4:
    # - espectativa.mp4 dips to -41.7 dBFS in 2 brief dramatic pauses, so dead-air gate is calibrated at -45 dBFS
    # - espectativa.mp4 has max_pair_identity_sim = 0.834 across angle/lighting shifts, so threshold >= 0.80
    # - legacy verify_lipsync_motion breaks on yaw != 0 / handheld motion, so landmark_lipsync_ok is primary
    criteria = {
        "motion_mean_ge_3_0": motion_mean >= 3.0,
        "motion_median_ge_2_5": motion_median >= 2.5,
        "shots_ge_4_and_varied": (
            len(shots_report) >= 4
            and min(shot_durs) <= 1.5
            and max(shot_durs) <= 5.0
        ),
        "non_frontal_gaze_ge_60pct": non_frontal_speaking_pct >= 60.0,
        "at_least_1_multi_person_or_ots": multi_person_or_ots_shots >= 1,
        "audio_lra_ge_5_0": ebur["lra_lu"] >= 5.0,
        "audio_lufs_in_target": -17.5 <= ebur["integrated_lufs"] <= -14.5,
        "audio_true_peak_le_minus_1_5": ebur["true_peak_dbtp"] <= -1.5,
        "audio_no_dead_window_below_45db": audio_windows["windows_below_minus_45db"] == 0,
        "zero_static_crop_reuse": len(static_reuses) == 0,
        "no_corrupted_mute_dialogue_shot": (
            all((s["has_audible_speech"] or s["shot_index"] == len(shots_report)) for s in shots_report)
            and max_dialogue_speech_gap_sec <= 2.0
        ),
        "identity_similarity_ge_0_80": max_pair_identity_sim >= 0.80,
        "landmark_lipsync_modulation_ok": landmark_lipsync_ok,
    }

    return {
        "video_path": str(mp4_path),
        "filename": mp4_path.name,
        "stream": stream_info,
        "motion_180x320": {
            "mean": motion_mean,
            "median": motion_median,
            "p10": round(float(np.percentile(diffs_all, 10)), 2),
            "p90": round(float(np.percentile(diffs_all, 90)), 2),
            "upper_head_zone_mean": round(float(np.mean(upper_motion_all)), 2) if upper_motion_all else 0.0,
            "lower_torso_hands_zone_mean": round(float(np.mean(lower_body_motion_all)), 2) if lower_body_motion_all else 0.0,
            "outer_background_zone_mean": round(float(np.mean(outer_bg_motion_all)), 2) if outer_bg_motion_all else 0.0,
        },
        "editing_and_pacing": {
            "cuts_count": len(cut_timestamps_sec),
            "cuts_timestamps_sec": cut_timestamps_sec,
            "shots_count": len(shots_report),
            "shot_durations_sec": shot_durs,
            "min_shot_sec": min(shot_durs) if shot_durs else 0.0,
            "max_shot_sec": max(shot_durs) if shot_durs else 0.0,
            "shot_duration_std_sec": round(float(np.std(shot_durs)), 2) if len(shot_durs) > 1 else 0.0,
            "static_crop_reuses_detected": static_reuses,
        },
        "cinematography_and_blocking": {
            "non_frontal_speaking_frames_pct": non_frontal_speaking_pct,
            "frontal_lens_stare_frames_pct": round(100.0 - non_frontal_speaking_pct, 1),
            "multi_person_or_ots_shots_count": multi_person_or_ots_shots,
            "max_faces_in_any_shot": max((s["max_faces_detected"] for s in shots_report), default=0),
            "identity_similarity_max_pair": max_pair_identity_sim,
            "identity_similarity_mean_pair": mean_pair_identity_sim,
        },
        "audio_and_dynamics": {
            **ebur,
            "windows_025s": audio_windows,
            "max_dialogue_speech_gap_sec": max_dialogue_speech_gap_sec,
            "whisper_full_transcript": " ".join(w["word"] for w in valid_words).strip(),
            "whisper_total_words": len(valid_words),
        },
        "subtitles": subtitles_info,
        "lipsync_audit_summary": {
            "ok": bool(lipsync_report.get("ok")),
            "avg_mouth_speech_vs_pause_diff": lipsync_report.get("avg_mouth_speech_vs_pause_diff", 0.0),
            "min_scene_mouth_peak_p95": lipsync_report.get("min_scene_mouth_peak_p95", 0.0),
        },
        "shots": shots_report,
        "acceptance_criteria": criteria,
        "all_criteria_passed": all(criteria.values()),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run Cinematic QA on one or more MP4 videos.")
    ap.add_argument("videos", nargs="+", help="Paths to MP4 videos")
    ap.add_argument("--out-dir", default="", help="Optional directory to save per-video JSON reports")
    args = ap.parse_args()

    out_dir = Path(args.out_dir) if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    results: Dict[str, Any] = {}
    for v_raw in args.videos:
        vp = Path(v_raw)
        rep = evaluate_video(vp)
        results[vp.stem] = rep
        if out_dir:
            out_json = out_dir / f"cinema_qa_{vp.stem}.json"
            out_json.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")

    print(json.dumps(results if len(results) > 1 else next(iter(results.values())), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
