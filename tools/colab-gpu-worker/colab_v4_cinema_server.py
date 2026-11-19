#!/usr/bin/env python3
"""Google Colab Pro L4 (24GB VRAM) Cinema Engine v4.0 Worker & Benchmark Server.

Zero-Drive, Clean-Slate BigTech Architecture:
1. Local high-speed NVMe runtime under /content/ai_studio_v4 (zero Google Drive FUSE slowdowns/popups).
2. Dual isolated uv Python 3.10 virtual environments:
   - /content/ai_studio_v4/envs/seedvc : isolated numpy==1.26.4 + transformers==4.46.3 Seed-VC CLI runtime.
   - /content/ai_studio_v4/envs/wangp  : isolated FastAPI + WanGP shared/api.py persistent session runtime.
3. Persistent in-process WanGPSession (no cold 14B-22B reloads between shots) with INT8/SDPA/Profile-4 L4 memory safety.
4. Honest /health telemetry (GPU name, real free/total VRAM, disk free GB, loaded model & readiness flags).
5. Async Job Queue (/jobs/animate_scene + /jobs/{id}) immune to Cloudflare 524 100s timeouts.
6. Clean stdout logging (zero noisy stderr red/pink boxes in Colab outputs).
"""

from __future__ import annotations

import base64
import gc
import hmac
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn


os.environ.setdefault("WAN2GP_DISABLE_AUDIO", "1")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

WORKSPACE_ROOT = Path(os.environ.get("AI_STUDIO_ROOT", "/content/ai_studio_v4"))
REPOS_DIR = WORKSPACE_ROOT / "repos"
HF_CACHE_DIR = Path(os.environ.get("HF_HOME", str(WORKSPACE_ROOT / "hf_cache")))
CONFIG_DIR = WORKSPACE_ROOT / "config"
OUTPUTS_DIR = WORKSPACE_ROOT / "outputs" / "wangp"

COLAB_AUTH_TOKEN = os.environ.get("COLAB_AUTH_TOKEN", "").strip()
DEFAULT_VIDEO_MODEL = os.environ.get("AI_STUDIO_VIDEO_MODEL", "ltx2_25_22B_distilled").strip()

SEED_VC_PYTHON = Path(os.environ.get("AI_STUDIO_SEEDVC_PY", str(WORKSPACE_ROOT / "envs" / "seedvc" / "bin" / "python")))
SEED_VC_DIR = Path(os.environ.get("AI_STUDIO_SEEDVC_DIR", str(REPOS_DIR / "seed-vc")))
WANGP_DIR = Path(os.environ.get("AI_STUDIO_WANGP_DIR", str(REPOS_DIR / "WanGP")))

GPU_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}
_WANGP_SESSION = None


BACKEND_MODEL_MAP: Dict[str, str] = {
    "auto": DEFAULT_VIDEO_MODEL,
    "wangp_wan22_s2v": DEFAULT_VIDEO_MODEL,
    "wan22_s2v_v4": DEFAULT_VIDEO_MODEL,
    "ltx25_v4": "ltx2_25_22B_distilled",
    "ltx25_distilled": "ltx2_25_22B_distilled",
    "ltx2_25_22b_distilled": "ltx2_25_22B_distilled",
    "longcat_avatar": "longcat_avatar_v1_5",
    "longcat_avatar_v1_5": "longcat_avatar_v1_5",
    "infinitetalk": "infinitetalk",
    "multitalk": "multitalk",
    "i2v_2_2": "i2v_2_2",
}


def _cleanup_vram() -> None:
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()
        try:
            torch.cuda.ipc_collect()
        except Exception:
            pass


def _gpu_info() -> Dict[str, Any]:
    if not torch.cuda.is_available():
        return {"gpu": "CPU", "vram_gb": 0.0, "vram_free_gb": 0.0, "cuda_ok": False}
    props = torch.cuda.get_device_properties(0)
    free_b, total_b = torch.cuda.mem_get_info(0)
    return {
        "gpu": torch.cuda.get_device_name(0),
        "vram_gb": round(total_b / (1024 ** 3), 1),
        "vram_free_gb": round(free_b / (1024 ** 3), 1),
        "cuda_ok": True,
        "torch_version": torch.__version__,
        "cuda_version": getattr(torch.version, "cuda", ""),
    }


def _smi_used_gb() -> float:
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            text=True,
            timeout=5,
        ).strip().splitlines()
        if out:
            return round(float(out[0].strip()) / 1024.0, 2)
    except Exception:
        pass
    if torch.cuda.is_available():
        return round(torch.cuda.max_memory_allocated() / (1024 ** 3), 2)
    return 0.0


def _require_token(token: str, require_configured: bool = False) -> None:
    if require_configured and not COLAB_AUTH_TOKEN:
        raise HTTPException(status_code=403, detail="Rota administrativa exige COLAB_AUTH_TOKEN configurado.")
    if COLAB_AUTH_TOKEN and not hmac.compare_digest(token or "", COLAB_AUTH_TOKEN):
        raise HTTPException(status_code=401, detail="X-Colab-Token inválido")


def _probe_dur(path: Path) -> float:
    out = subprocess.check_output(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        text=True,
    ).strip()
    return max(0.3, float(out))


def _mux_no_freeze(raw_mp4: Path, wav_path: Optional[Path], out_mp4: Path, width: int, height: int, fps: int = 24) -> None:
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,crop={width}:{height},setsar=1,fps={fps},format=yuv420p"
    vid_dur = _probe_dur(raw_mp4)
    if wav_path and wav_path.is_file():
        aud_dur = _probe_dur(wav_path)
        target_dur = min(vid_dur, aud_dur)
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_mp4), "-i", str(wav_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf, "-t", f"{target_dur:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(out_mp4),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_mp4),
            "-vf", vf, "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(out_mp4),
        ]
    subprocess.run(cmd, check=True, capture_output=True)


def _get_wangp_session():
    global _WANGP_SESSION
    if _WANGP_SESSION is not None:
        return _WANGP_SESSION
    if not (WANGP_DIR / "shared" / "api.py").is_file():
        raise RuntimeError(f"WanGP não instalado em {WANGP_DIR}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)
    if str(WANGP_DIR) not in sys.path:
        sys.path.insert(0, str(WANGP_DIR))
    from shared.api import init as wangp_init  # type: ignore

    _WANGP_SESSION = wangp_init(
        root=WANGP_DIR,
        config_path=CONFIG_DIR,
        output_dir=OUTPUTS_DIR,
        cli_args=["--attention", "sdpa", "--profile", "4"],
        console_output=False,
    )
    return _WANGP_SESSION


def _resolve_model_type(requested_backend: str, has_audio: bool) -> str:
    key = (requested_backend or "auto").strip().lower()
    if key == "seed_vc_only":
        raise RuntimeError("Este worker foi iniciado em modo seed_vc_only (sem modelo de vídeo).")
    resolved = BACKEND_MODEL_MAP.get(key) or DEFAULT_VIDEO_MODEL
    if resolved == "seed_vc_only":
        raise RuntimeError("Worker configurado apenas para Seed-VC (VIDEO_MODEL=seed_vc_only).")
    if not has_audio and resolved in ("infinitetalk", "multitalk", "longcat_avatar_v1_5"):
        return "i2v_2_2"
    return resolved


def _resolve_model_resolution(model_type: str, width: int, height: int) -> str:
    portrait = height >= width
    if model_type in ("infinitetalk", "multitalk"):
        return "480x832" if portrait else "832x480"
    if "ltx2" in model_type:
        w = max(256, (width // 32) * 32)
        h = max(256, (height // 32) * 32)
        return f"{w}x{h}"
    if "longcat" in model_type:
        return "480x832" if portrait else "832x480"
    return "480x832" if portrait else "832x480"


app = FastAPI(title="AI Media Studio Colab Cinema Engine v4.0", version="4.0.0-clean-l4")


class ExecReq(BaseModel):
    cmd: str
    timeout: int = 600


class VoiceConvertReq(BaseModel):
    guide_audio_b64: str
    target_timbre_b64: str
    character_id: str = "dona_socorro"
    diffusion_steps: int = 25
    semi_tone_shift: int = 0
    f0_condition: bool = False


class AnimateSceneReq(BaseModel):
    image_b64: str
    audio_b64: str = ""
    prompt: str
    negative_prompt: str = "static portrait, frozen posture, distorted hands, extra fingers, watermark, overlaid text"
    backend: str = "auto"
    steps: int = 8
    guidance_scale: float = 3.5
    duration_sec: float = 3.2
    width: int = 720
    height: int = 1280
    fps: int = 24
    seed: int = 42


@app.get("/health")
def health(x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=False)
    info = _gpu_info()
    disk_free_gb = 0.0
    try:
        st = shutil.disk_usage(str(WORKSPACE_ROOT if WORKSPACE_ROOT.exists() else Path("/content")))
        disk_free_gb = round(st.free / (1024 ** 3), 1)
    except Exception:
        pass
    wangp_ready = (WANGP_DIR / "wgp.py").is_file() and (WANGP_DIR / "shared" / "api.py").is_file()
    return {
        "ok": True,
        "ready": True,
        "version": "4.0.0-clean-l4",
        **info,
        "default_video_model": DEFAULT_VIDEO_MODEL,
        "workspace_root": str(WORKSPACE_ROOT),
        "cache_free_gb": disk_free_gb,
        "drive_mounted": False,
        "seed_vc_ready": SEED_VC_PYTHON.is_file() and (SEED_VC_DIR / "inference.py").is_file(),
        "wangp_ready": wangp_ready and DEFAULT_VIDEO_MODEL != "seed_vc_only",
        "ltx2_ready": wangp_ready and "ltx2" in DEFAULT_VIDEO_MODEL,
        "longcat_ready": wangp_ready and "longcat" in DEFAULT_VIDEO_MODEL,
        "wangp_session_warm": _WANGP_SESSION is not None,
        "active_jobs": sum(1 for j in JOBS.values() if j.get("status") == "processing"),
    }


@app.post("/admin/exec")
def admin_exec(req: ExecReq, x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=True)
    proc = subprocess.run(req.cmd, shell=True, capture_output=True, text=True, timeout=req.timeout)
    return {"rc": proc.returncode, "stdout": proc.stdout[-60000:], "stderr": proc.stderr[-25000:]}


@app.post("/voice-convert")
def voice_convert(req: VoiceConvertReq, x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=False)
    inf_py = SEED_VC_DIR / "inference.py"
    if not inf_py.is_file() or not SEED_VC_PYTHON.is_file():
        raise HTTPException(status_code=503, detail=f"Seed-VC não encontrado em {SEED_VC_DIR}")

    job_dir = WORKSPACE_ROOT / "tmp" / f"svc_v4_{uuid.uuid4().hex[:8]}"
    job_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    with GPU_LOCK:
        try:
            g_wav = job_dir / "source.wav"
            t_wav = job_dir / "target.wav"
            out_dir = job_dir / "out"
            out_dir.mkdir(exist_ok=True)
            out_wav = job_dir / "converted_48k.wav"
            g_wav.write_bytes(base64.b64decode(req.guide_audio_b64))
            t_wav.write_bytes(base64.b64decode(req.target_timbre_b64))

            cmd = [
                str(SEED_VC_PYTHON), str(inf_py),
                "--source", str(g_wav),
                "--target", str(t_wav),
                "--output", str(out_dir),
                "--diffusion-steps", str(int(req.diffusion_steps)),
                "--semi-tone-shift", str(int(req.semi_tone_shift)),
                "--f0-condition", "True" if req.f0_condition else "False",
                "--auto-f0-adjust", "True" if not req.f0_condition else "False",
                "--fp16", "True",
            ]
            env = {
                **os.environ,
                "HF_HOME": str(HF_CACHE_DIR),
                "PYTHONWARNINGS": "ignore",
                "HF_HUB_VERBOSITY": "error",
            }
            proc = subprocess.run(cmd, cwd=str(SEED_VC_DIR), env=env, capture_output=True, text=True, timeout=180)
            gen = next(iter(sorted(out_dir.glob("*.wav"))), None)
            if proc.returncode != 0 or not gen or not gen.is_file():
                raise RuntimeError(f"Seed-VC falhou (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}")

            subprocess.run(
                ["ffmpeg", "-y", "-i", str(gen), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)],
                check=True,
                capture_output=True,
            )
            return {
                "ok": True,
                "engine": "seed-vc-v4-real",
                "elapsed_s": round(time.time() - t0, 2),
                "duration": round(_probe_dur(out_wav), 2),
                "vram_used_gb": _smi_used_gb(),
                "character_id": req.character_id,
                "audio_b64": base64.b64encode(out_wav.read_bytes()).decode("ascii"),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            _cleanup_vram()


def _execute_scene_job(job_id: str, req: AnimateSceneReq) -> None:
    job_dir = WORKSPACE_ROOT / "tmp" / f"scene_v4_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "processing"
        img_p = job_dir / "keyframe.jpg"
        img_p.write_bytes(base64.b64decode(req.image_b64))
        wav_p: Optional[Path] = None
        dur_target = float(req.duration_sec or 3.2)
        if req.audio_b64:
            wav_p = job_dir / "dialogue.wav"
            wav_p.write_bytes(base64.b64decode(req.audio_b64))
            dur_target = max(1.0, min(10.0, _probe_dur(wav_p)))

        fps = int(req.fps or 24)
        final_mp4 = job_dir / "final_scene.mp4"
        resolved_model = _resolve_model_type(req.backend, has_audio=(wav_p is not None))

        with GPU_LOCK:
            session = _get_wangp_session()
            settings = session.get_default_settings(resolved_model)
            mdef = session.get_model_def(resolved_model) or {}
            schema = (session.get_model_schema(resolved_model) or {}).get("metadata", {})
            f_min = int(schema.get("frames_minimum") or mdef.get("frames_minimum") or 17)
            f_step = int(schema.get("frames_steps") or mdef.get("frames_steps") or 8)
            f_off = int(mdef.get("frames_offset") or 1)
            raw_frames = max(f_min, int(math.ceil(dur_target * fps)) + 4)
            steps_n = max(0, (raw_frames - f_off + f_step - 1) // f_step)
            aligned_frames = max(f_min, steps_n * f_step + f_off)

            settings["model_type"] = resolved_model
            settings["prompt"] = req.prompt
            settings["negative_prompt"] = req.negative_prompt
            settings["resolution"] = _resolve_model_resolution(resolved_model, int(req.width), int(req.height))
            settings["video_length"] = aligned_frames
            settings["force_fps"] = fps
            settings["seed"] = int(req.seed)
            settings["image_start"] = str(img_p)
            settings["image_prompt_type"] = "S"
            if wav_p is not None:
                settings["audio_guide"] = str(wav_p)
                settings["audio_prompt_type"] = "AS" if "ltx2" in resolved_model else "A"
            if "ltx2" in resolved_model and "distilled" in resolved_model:
                settings["num_inference_steps"] = 8
            elif "longcat" in resolved_model:
                settings["num_inference_steps"] = 8
                settings["guidance_scale"] = 1.0
                settings["audio_guidance_scale"] = 1.0
            else:
                settings["num_inference_steps"] = max(4, int(req.steps))
                settings["guidance_scale"] = float(req.guidance_scale)
            settings["overrides"] = "attention_mode='sdpa', memory_profile=4, transformer_quantization='int8'"

            job = session.submit_task(settings)
            result = job.result()
            if not result.success or not result.generated_files:
                errs = "; ".join(str(e) for e in (result.errors or [])) or "WanGP retornou 0 arquivos."
                raise RuntimeError(errs[:500])

            raw_cand = Path(result.generated_files[0])
            _mux_no_freeze(raw_cand, wav_p, final_mp4, int(req.width), int(req.height), fps=fps)
            peak_vram = _smi_used_gb()
            _cleanup_vram()

        vid_b64 = base64.b64encode(final_mp4.read_bytes()).decode("ascii")
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "completed",
                "ok": True,
                "backend": resolved_model,
                "elapsed_s": round(time.time() - t0, 2),
                "duration": round(_probe_dur(final_mp4), 2),
                "vram_peak_gb": peak_vram,
                "video_b64": vid_b64,
            })
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "error",
                "ok": False,
                "elapsed_s": round(time.time() - t0, 2),
                "error": str(exc)[:600],
            })
    finally:
        shutil.rmtree(job_dir, ignore_errors=True)
        _cleanup_vram()


@app.post("/jobs/animate_scene")
def submit_scene_job(req: AnimateSceneReq, x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=False)
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        if len(JOBS) > 40:
            for k in list(JOBS.keys())[:20]:
                JOBS.pop(k, None)
        JOBS[job_id] = {"ok": True, "job_id": job_id, "status": "queued", "created_at": time.time()}
    threading.Thread(target=_execute_scene_job, args=(job_id, req), daemon=True).start()
    return {"ok": True, "job_id": job_id, "status": "queued"}


@app.get("/jobs/{job_id}")
def get_scene_job(job_id: str, x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=False)
    with JOBS_LOCK:
        job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job não encontrado")
    return job


if __name__ == "__main__":
    log_cfg = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {"format": "%(asctime)s [%(levelname)s] %(message)s"},
        },
        "handlers": {
            "file": {
                "class": "logging.FileHandler",
                "filename": "/tmp/colab_v4_server.log",
                "formatter": "default",
            },
            "stdout": {
                "class": "logging.StreamHandler",
                "stream": "ext://sys.stdout",
                "formatter": "default",
            },
        },
        "loggers": {
            "uvicorn": {"handlers": ["file", "stdout"], "level": "WARNING", "propagate": False},
            "uvicorn.error": {"handlers": ["file", "stdout"], "level": "WARNING", "propagate": False},
            "uvicorn.access": {"handlers": ["file"], "level": "INFO", "propagate": False},
        },
    }
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8787")), log_config=log_cfg)
