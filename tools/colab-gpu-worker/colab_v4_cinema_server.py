#!/usr/bin/env python3
"""Google Colab Pro L4 (24GB VRAM) Cinema Engine v4.0 Worker & Benchmark Server.

Fixes all v2.9/v3.1 Colab notebook failures:
1. Honest /health reporting (zero fake stubs).
2. Persistent 5 TB Google Drive cache for HF weights & uv/pip caches.
3. Strict GPU serialization Lock + explicit torch.cuda.empty_cache() cleanup.
4. Async Job Queue (POST /jobs/animate_scene + GET /jobs/{id}) avoiding Cloudflare 524 timeouts.
5. Real Seed-VC speech conversion (--f0-condition False --auto-f0-adjust True --fp16 True) with
   explicit HTTP 500 failure instead of silent copy fallbacks.
6. Clean muxing WITHOUT tpad=stop_mode=clone frame freezing.
7. Token-protected /admin/exec (disabled unless COLAB_AUTH_TOKEN is explicitly configured).
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
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Optional

import torch
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn


COLAB_AUTH_TOKEN = os.environ.get("COLAB_AUTH_TOKEN", "").strip()
DRIVE_CACHE_ROOT = Path(os.environ.get("AI_STUDIO_CACHE_DIR", "/content/drive/MyDrive/AI_Media_Studio_v4_Cache"))
LOCAL_CACHE_ROOT = Path("/content/ai_studio_cache")
CACHE_ROOT = DRIVE_CACHE_ROOT if Path("/content/drive/MyDrive").exists() else LOCAL_CACHE_ROOT

REPOS_DIR = CACHE_ROOT / "repos"
MODELS_DIR = CACHE_ROOT / "models"
PY311_BIN = Path(os.environ.get("AI_STUDIO_PY311_BIN", "/content/venv_cinema311/bin/python"))
EXEC_PYTHON = str(PY311_BIN) if PY311_BIN.is_file() else "python3"

SEED_VC_DIR = REPOS_DIR / "seed-vc" if (REPOS_DIR / "seed-vc").exists() else Path("/content/seed-vc")
WANGP_DIR = REPOS_DIR / "WanGP" if (REPOS_DIR / "WanGP").exists() else Path("/content/WanGP")
LTX_DIR = REPOS_DIR / "LTX-2" if (REPOS_DIR / "LTX-2").exists() else Path("/content/LTX-2")
LONGCAT_DIR = REPOS_DIR / "LongCat-Video" if (REPOS_DIR / "LongCat-Video").exists() else Path("/content/LongCat-Video")

GPU_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}


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
        return {"gpu": "CPU", "vram_gb": 0.0, "cuda_ok": False}
    props = torch.cuda.get_device_properties(0)
    return {
        "gpu": torch.cuda.get_device_name(0),
        "vram_gb": round(props.total_memory / (1024 ** 3), 1),
        "cuda_ok": True,
    }


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


app = FastAPI(title="AI Media Studio Colab Cinema Engine v4.0", version="4.0.0-cinema-l4")


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
    negative_prompt: str = "static portrait, frozen posture, distorted hands, extra fingers, looking into camera lens, watermark"
    backend: str = "wangp_wan22_s2v"  # wangp_wan22_s2v | ltx25_distilled | longcat_avatar
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
        st = shutil.disk_usage(str(CACHE_ROOT))
        disk_free_gb = round(st.free / (1024 ** 3), 1)
    except Exception:
        pass
    return {
        "ok": True,
        "ready": True,
        "version": "4.0.0-cinema-l4",
        **info,
        "exec_python": EXEC_PYTHON,
        "cache_root": str(CACHE_ROOT),
        "cache_free_gb": disk_free_gb,
        "drive_mounted": Path("/content/drive/MyDrive").exists(),
        "seed_vc_ready": (SEED_VC_DIR / "inference.py").is_file(),
        "wangp_ready": (WANGP_DIR / "wgp.py").is_file(),
        "ltx2_ready": LTX_DIR.exists(),
        "longcat_ready": (LONGCAT_DIR / "run_demo_avatar_single_audio_to_video.py").is_file(),
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
    if not inf_py.is_file():
        raise HTTPException(status_code=503, detail=f"Seed-VC não encontrado em {inf_py}")

    job_dir = Path(f"/tmp/svc_v4_{uuid.uuid4().hex[:8]}")
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
                EXEC_PYTHON, str(inf_py),
                "--source", str(g_wav),
                "--target", str(t_wav),
                "--output", str(out_dir),
                "--diffusion-steps", str(int(req.diffusion_steps)),
                "--semi-tone-shift", str(int(req.semi_tone_shift)),
                "--f0-condition", "True" if req.f0_condition else "False",
                "--auto-f0-adjust", "True" if not req.f0_condition else "False",
                "--fp16", "True",
            ]
            env = {**os.environ, "HF_HOME": str(CACHE_ROOT / "hf_hub")}
            proc = subprocess.run(cmd, cwd=str(SEED_VC_DIR), env=env, capture_output=True, text=True, timeout=180)
            gen = next(iter(sorted(out_dir.glob("*.wav"))), None)
            if proc.returncode != 0 or not gen or not gen.is_file():
                raise RuntimeError(f"Seed-VC falhou (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}")

            subprocess.run(
                ["ffmpeg", "-y", "-i", str(gen), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)],
                check=True, capture_output=True
            )
            return {
                "ok": True,
                "engine": "seed-vc-v4-real",
                "elapsed_s": round(time.time() - t0, 2),
                "duration": round(_probe_dur(out_wav), 2),
                "character_id": req.character_id,
                "audio_b64": base64.b64encode(out_wav.read_bytes()).decode("ascii"),
            }
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:500])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            _cleanup_vram()


def _execute_scene_job(job_id: str, req: AnimateSceneReq) -> None:
    job_dir = Path(f"/tmp/scene_v4_{job_id}")
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
        # Wan / LTX require 4k+1 / 8k+1 frame alignment
        raw_frames = int(math.ceil(dur_target * fps)) + 4
        aligned_frames = ((raw_frames - 1 + 3) // 4) * 4 + 1
        out_dir = job_dir / "render_out"
        out_dir.mkdir(exist_ok=True)
        final_mp4 = job_dir / "final_scene.mp4"

        with GPU_LOCK:
            if torch.cuda.is_available():
                torch.cuda.reset_peak_memory_stats()
            env = {**os.environ, "HF_HOME": str(CACHE_ROOT / "hf_hub")}
            if req.backend == "wangp_wan22_s2v":
                wgp_py = WANGP_DIR / "wgp.py"
                if not wgp_py.is_file():
                    raise RuntimeError(f"WanGP não instalado em {WANGP_DIR}")
                settings_p = job_dir / "wangp_job.json"
                settings_p.write_text(json.dumps({
                    "model_type": "s2v_14B" if wav_p else "i2v_2_2",
                    "prompt": req.prompt,
                    "negative_prompt": req.negative_prompt,
                    "resolution": f"{req.width}x{req.height}",
                    "num_inference_steps": int(req.steps),
                    "guidance_scale": float(req.guidance_scale),
                    "video_length": aligned_frames,
                    "force_fps": fps,
                    "seed": int(req.seed),
                    "image_path": str(img_p),
                    "audio_path": str(wav_p) if wav_p else "",
                }, indent=2), encoding="utf-8")
                proc = subprocess.run(
                    [EXEC_PYTHON, str(wgp_py), "--process", str(settings_p), "--output-dir", str(out_dir)],
                    cwd=str(WANGP_DIR), env=env, capture_output=True, text=True, timeout=600,
                )
                cand = next(iter(sorted(out_dir.rglob("*.mp4"))), None)
                if proc.returncode != 0 or not cand:
                    raise RuntimeError(f"WanGP falhou (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-500:]}")
                _mux_no_freeze(cand, wav_p, final_mp4, req.width, req.height, fps=fps)
            else:
                raise RuntimeError(f"Backend {req.backend} ainda não habilitado neste worker.")

            peak_vram = round(torch.cuda.max_memory_allocated() / (1024 ** 3), 2) if torch.cuda.is_available() else 0.0
            _cleanup_vram()

        vid_b64 = base64.b64encode(final_mp4.read_bytes()).decode("ascii")
        with JOBS_LOCK:
            JOBS[job_id].update({
                "status": "completed",
                "ok": True,
                "backend": req.backend,
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
            oldest = list(JOBS.keys())[:20]
            for k in oldest:
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
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8787")), log_level="info")
