#!/usr/bin/env python3
"""On-demand webcam vision tool for OpenJarvis (OpenCV headless + Gemini 2.5 Flash)."""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

for _tvar in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_tvar, "2")

import cv2
import httpx

try:
    cv2.setNumThreads(2)
except Exception:
    pass

VAULT_PATH = Path("/home/wbrendo/media-control-panel/vault.json")
ENV_PATH = Path("/home/wbrendo/media-control-panel/.env")
OPENJARVIS_ENV_PATH = Path("/home/wbrendo/.openjarvis/.env")
DEFAULT_DEVICE = "/dev/video0"
DEFAULT_MODEL = "gemini-2.5-flash"
DEFAULT_GATEWAY_URL = "https://187.77.202.176.sslip.io/v1/chat/completions"
_MODEL_COOLDOWN_UNTIL: Dict[str, float] = {}


def _read_openjarvis_env() -> Dict[str, str]:
    vals: Dict[str, str] = {}
    if OPENJARVIS_ENV_PATH.exists():
        try:
            for line in OPENJARVIS_ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip("\"'")
        except Exception:
            pass
    return vals


def _load_gemini_key() -> str:
    """Resolve GEMINI_API_KEY from environment, ~/.openjarvis/.env, or vault."""
    env_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if env_key:
        return env_key

    oj_vals = _read_openjarvis_env()
    if oj_vals.get("GEMINI_API_KEY"):
        return oj_vals["GEMINI_API_KEY"]

    if VAULT_PATH.exists():
        try:
            data = json.loads(VAULT_PATH.read_text(encoding="utf-8"))
            key = str(data.get("GEMINI_API_KEY", "")).strip()
            if key:
                return key
        except Exception:
            pass

    if ENV_PATH.exists():
        try:
            for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("GEMINI_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("\"'")
        except Exception:
            pass

    return ""


def capture_frame_base64(
    device: str = DEFAULT_DEVICE,
    *,
    warmup_frames: int = 3,
    jpeg_quality: int = 85,
) -> tuple[bool, str, Optional[str]]:
    """Capture a single frame from *device* and release the camera immediately.

    Returns ``(ok, base64_jpeg_or_error, error_code)``.
    """
    dev_path = Path(device)
    if device.startswith("/dev/") and not dev_path.exists():
        return (
            False,
            f"Dispositivo de câmera não encontrado em {device}. Conecte uma webcam USB e tente novamente.",
            "DEVICE_NOT_FOUND",
        )

    source: Any = device
    if device.startswith("/dev/video") and device[10:].isdigit():
        source = device

    cap = cv2.VideoCapture(source, cv2.CAP_V4L2)
    if not cap.isOpened() and isinstance(source, str) and source.startswith("/dev/video"):
        try:
            cap.release()
        except Exception:
            pass
        if source[10:].isdigit():
            cap = cv2.VideoCapture(int(source[10:]))

    try:
        if not cap.isOpened():
            return (
                False,
                f"Não foi possível abrir a câmera em {device}. Verifique se outro app está usando ou se há permissão de vídeo.",
                "CAMERA_OPEN_FAILED",
            )

        frame = None
        ok = False
        for _ in range(max(1, warmup_frames)):
            ok, frame = cap.read()
        if not ok or frame is None:
            return (
                False,
                f"A câmera {device} abriu, mas falhou ao capturar o frame.",
                "FRAME_CAPTURE_FAILED",
            )

        encoded_ok, buf = cv2.imencode(
            ".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality]
        )
        if not encoded_ok:
            return False, "Falha ao codificar frame JPEG.", "ENCODE_FAILED"

        b64 = base64.b64encode(buf.tobytes()).decode("ascii")
        return True, b64, None
    finally:
        # Release the webcam immediately so the hardware LED turns off right away.
        try:
            cap.release()
        except Exception:
            pass


def analyze_image_with_gemini(
    image_b64: str,
    prompt: str,
    *,
    mime_type: str = "image/jpeg",
    model: str = DEFAULT_MODEL,
    timeout: float = 25.0,
) -> Dict[str, Any]:
    """Send a base64 image to Gemini multimodal endpoint with automatic model & Gateway cascade fallback."""
    api_key = _load_gemini_key()
    errors: List[str] = []

    if api_key:
        candidates = [model, "gemini-2.5-flash-lite", "gemini-2.0-flash"]
        dedup_candidates: List[str] = []
        for c in candidates:
            c_clean = c.split("/")[-1].strip()
            if c_clean and c_clean not in dedup_candidates:
                dedup_candidates.append(c_clean)

        now = time.monotonic()
        active_candidates = [
            c for c in dedup_candidates if _MODEL_COOLDOWN_UNTIL.get(c, 0.0) <= now
        ] or dedup_candidates

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": mime_type, "data": image_b64}},
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.3,
                "maxOutputTokens": 600,
            },
        }

        with httpx.Client(timeout=timeout) as client:
            for candidate in active_candidates:
                url = (
                    f"https://generativelanguage.googleapis.com/v1beta/models/"
                    f"{candidate}:generateContent?key={api_key}"
                )
                try:
                    resp = client.post(url, json=payload)
                    if resp.status_code == 200:
                        data = resp.json()
                        cand_list = data.get("candidates", [])
                        if cand_list:
                            parts = cand_list[0].get("content", {}).get("parts", [])
                            text = "".join(p.get("text", "") for p in parts).strip()
                            if text:
                                return {
                                    "ok": True,
                                    "model": candidate,
                                    "description": text,
                                }
                        errors.append(f"{candidate}: resposta vazia")
                    else:
                        if resp.status_code in (429, 503):
                            _MODEL_COOLDOWN_UNTIL[candidate] = time.monotonic() + 180.0
                        errors.append(f"{candidate} HTTP {resp.status_code}")
                except Exception as exc:
                    errors.append(f"{candidate} erro: {exc}")

    # Fallback via Gateway Central na VPS
    try:
        oj_vals = _read_openjarvis_env()
        vps_base = (os.environ.get("VPS_PANEL_URL") or oj_vals.get("VPS_PANEL_URL") or "").rstrip("/")
        gw_url = f"{vps_base}/v1/chat/completions" if vps_base else DEFAULT_GATEWAY_URL
        vps_token = (os.environ.get("VPS_PANEL_TOKEN") or oj_vals.get("VPS_PANEL_TOKEN") or "").strip()
        gw_headers = {"Authorization": f"Bearer {vps_token}"} if vps_token else {}
        gw_payload = {
            "model": "gemini-2.5-flash-lite",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                        },
                    ],
                }
            ],
            "max_tokens": 500,
            "temperature": 0.3,
        }
        with httpx.Client(timeout=timeout, headers=gw_headers) as client:
            gw_resp = client.post(gw_url, json=gw_payload)
        if gw_resp.status_code == 200:
            gw_data = gw_resp.json()
            choices = gw_data.get("choices", [])
            if choices:
                text = (choices[0].get("message", {}).get("content") or "").strip()
                if text:
                    return {
                        "ok": True,
                        "model": gw_data.get("cascade_model") or gw_data.get("model", "gateway-cascade"),
                        "description": text,
                    }
        errors.append(f"Gateway VPS HTTP {gw_resp.status_code}")
    except Exception as exc:
        errors.append(f"Gateway VPS erro: {exc}")

    return {
        "ok": False,
        "error": "Falha ao analisar imagem na cascata multimodal: " + " | ".join(errors),
    }


def capture_and_describe(
    prompt: str = "Descreva em português o que aparece nesta imagem, destacando objetos ou produtos que estou segurando.",
    *,
    device: str = DEFAULT_DEVICE,
    model: str = DEFAULT_MODEL,
    image_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Capture 1 frame from webcam (or load test image) and describe it via Gemini."""
    if image_path:
        p = Path(image_path).expanduser()
        if not p.is_file():
            return {"ok": False, "error": f"Imagem não encontrada: {p}"}
        raw = p.read_bytes()
        b64 = base64.b64encode(raw).decode("ascii")
        ext = p.suffix.lower()
        mime = "image/png" if ext == ".png" else "image/jpeg"
        res = analyze_image_with_gemini(b64, prompt, mime_type=mime, model=model)
        res["source"] = str(p)
        return res

    ok, payload_or_err, err_code = capture_frame_base64(device=device)
    if not ok:
        return {
            "ok": False,
            "code": err_code,
            "device": device,
            "error": payload_or_err,
        }

    res = analyze_image_with_gemini(payload_or_err, prompt, mime_type="image/jpeg", model=model)
    res["device"] = device
    res["camera_released"] = True
    return res


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Visão Webcam Sob Demanda do OpenJarvis (OpenCV + Gemini 2.5 Flash)"
    )
    parser.add_argument(
        "--prompt",
        default="Descreva em português o que estou segurando ou o que aparece na câmera, com foco em identificar o objeto/produto.",
        help="Instrução para o modelo multimodal.",
    )
    parser.add_argument("--device", default=DEFAULT_DEVICE, help="Dispositivo V4L2 (padrão: /dev/video0)")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="Modelo Gemini (padrão: gemini-2.5-flash)")
    parser.add_argument("--image", default=None, help="Caminho opcional de imagem para teste sem webcam.")
    args = parser.parse_args()

    result = capture_and_describe(
        prompt=args.prompt,
        device=args.device,
        model=args.model,
        image_path=args.image,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
