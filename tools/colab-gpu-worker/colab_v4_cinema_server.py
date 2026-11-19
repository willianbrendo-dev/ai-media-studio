#!/usr/bin/env python3
"""Google Colab Pro L4 (24GB VRAM) Cinema Engine v4.0 Worker Server.

Zero-Drive, Clean-Slate Architecture (v4.0.2-clean-l4):
1. Hermetic environment sanitization at process bootstrap and every subprocess call
   (eliminates Jupyter MPLBACKEND/PYTHONPATH/JPY_* leaks while preserving CUDA/NVIDIA
   driver libs and COLAB_AUTH_TOKEN).
2. Dual isolated uv Python 3.10 virtual environments under /content/ai_studio_v4/envs/:
   - envs/seedvc : numpy==1.26.4 + transformers==4.46.3 + torchcodec + TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1
   - envs/wangp  : FastAPI + WanGP v20.00 shared/api.py persistent session (INT8/SDPA/Profile-4)
3. Model-aware WanGP settings verified against upstream handlers:
   - ltx2_25_22B_distilled : vae_block_size=64, image_start (S), audio_guide (AS), force_fps='24'
   - longcat_avatar_v1_6 / v1_5 : image_start (S) + anchor image_refs (KI), audio_guide (A), distill 8-step
   - infinitetalk : image_start (S), audio_guide (A), 480x832
   - i2v_2_2 : image_start (S), 480x832
4. Real Audio Quality Gate (Seed-VC) and Video Quality Gate (WanGP) — zero silent/static fallbacks.
5. Thread-safe async Job Queue (/jobs/animate_scene + /jobs/{job_id}) immune to Cloudflare 524.
6. Structured stdout + file logging (/tmp/colab_v4_server.log), zero stderr spam.
"""

from __future__ import annotations

import array
import base64
import gc
import hmac
import inspect
import json
import math
import os
import shutil
import subprocess
import sys
import threading
import time
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, Optional

# ---------------------------------------------------------------------------
# 1. Hermetic Environment Sanitizer (MUST run BEFORE importing torch / WanGP)
# ---------------------------------------------------------------------------

TOXIC_EXACT_VARS = {
    "MPLBACKEND",
    "PYTHONHOME",
    "PYTHONPATH",
    "PYTHONSTARTUP",
    "PYTHONUSERBASE",
    "PYTHONEXECUTABLE",
    "CONDA_PREFIX",
    "CONDA_DEFAULT_ENV",
    "CONDA_SHLVL",
    "PIP_CONSTRAINT",
    "PIP_REQUIRE_VIRTUALENV",
    "UV_PYTHON",
    "JPY_PARENT_PID",
    "JPY_SESSION_NAME",
    "JPY_API_TOKEN",
    "IPYTHONDIR",
    "DISPLAY",
    "LD_PRELOAD",
}

TOXIC_PREFIXES = (
    "JPY_",
    "JUPYTER_",
    "IPYTHON_",
    "COLAB_",
    "TBE_",
)

# Never strip application secrets that share the COLAB_ prefix!
PRESERVED_EXACT_VARS = {
    "COLAB_AUTH_TOKEN",
}

WORKSPACE_ROOT = Path(os.environ.get("AI_STUDIO_ROOT", "/content/ai_studio_v4"))
CACHE_DIR = WORKSPACE_ROOT / "cache"
HF_CACHE_DIR = Path(os.environ.get("HF_HOME", str(WORKSPACE_ROOT / "hf_cache")))


def build_clean_env(
    venv_dir: Optional[Path] = None,
    extra: Optional[Dict[str, str]] = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Build a deterministic, Jupyter-free environment preserving CUDA/NVIDIA & auth secrets."""
    src = dict(os.environ if base_env is None else base_env)
    cleaned: Dict[str, str] = {}
    for k, v in src.items():
        if k in PRESERVED_EXACT_VARS:
            cleaned[k] = v
            continue
        if k in TOXIC_EXACT_VARS or k == "VIRTUAL_ENV":
            continue
        if any(k.startswith(pref) for pref in TOXIC_PREFIXES):
            continue
        cleaned[k] = v

    forced = {
        "MPLBACKEND": "Agg",
        "QT_QPA_PLATFORM": "offscreen",
        "PYGAME_HIDE_SUPPORT_PROMPT": "1",
        "TOKENIZERS_PARALLELISM": "false",
        "PIP_NO_INPUT": "1",
        "PIP_NO_WARN_SCRIPT_LOCATION": "1",
        "UV_NO_PROGRESS": "1",
        "UV_LINK_MODE": "copy",
        "PYTHONWARNINGS": "ignore",
        "PYTHONUNBUFFERED": "1",
        "PYTHONNOUSERSITE": "1",
        "DEBIAN_FRONTEND": "noninteractive",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "HF_HUB_VERBOSITY": "error",
        "HF_HOME": str(HF_CACHE_DIR),
        "HF_HUB_CACHE": str(HF_CACHE_DIR / "hub"),
        "TRITON_CACHE_DIR": str(CACHE_DIR / "triton"),
        "TORCHINDUCTOR_CACHE_DIR": str(CACHE_DIR / "inductor"),
        "MPLCONFIGDIR": str(CACHE_DIR / "matplotlib"),
        "XDG_CACHE_HOME": str(CACHE_DIR / "xdg"),
        "WAN2GP_DISABLE_AUDIO": "1",
        "TF_CPP_MIN_LOG_LEVEL": "3",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    }
    cleaned.update(forced)

    if venv_dir is not None:
        vbin = str(Path(venv_dir) / "bin")
        cleaned["VIRTUAL_ENV"] = str(venv_dir)
        old_path = cleaned.get("PATH", "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")
        parts = [p for p in old_path.split(":") if p and p != vbin]
        cleaned["PATH"] = ":".join([vbin] + parts)

    if extra:
        for ek, ev in extra.items():
            if ev is not None:
                cleaned[ek] = str(ev)

    return cleaned


def _sanitize_self_env() -> None:
    """Neutralize inherited Colab/Jupyter variables in the server process without wiping auth."""
    for k in list(os.environ.keys()):
        if k in PRESERVED_EXACT_VARS:
            continue
        if k in TOXIC_EXACT_VARS or any(k.startswith(pref) for pref in TOXIC_PREFIXES):
            os.environ.pop(k, None)
    clean_defaults = build_clean_env(base_env=dict(os.environ))
    for k, v in clean_defaults.items():
        if k != "PATH":
            os.environ[k] = v


_sanitize_self_env()

import torch  # noqa: E402
from fastapi import FastAPI, Header, HTTPException  # noqa: E402
from pydantic import BaseModel  # noqa: E402
import uvicorn  # noqa: E402


SERVER_VERSION = "4.0.2-clean-l4"  # Must start with "4.0" (vps_novela_runner.py L2380)
REPOS_DIR = WORKSPACE_ROOT / "repos"
CONFIG_DIR = WORKSPACE_ROOT / "config"
OUTPUTS_DIR = WORKSPACE_ROOT / "outputs" / "wangp"

COLAB_AUTH_TOKEN = os.environ.get("COLAB_AUTH_TOKEN", "").strip()
DEFAULT_VIDEO_MODEL = os.environ.get("AI_STUDIO_VIDEO_MODEL", "ltx2_25_22B_distilled").strip()

SEED_VC_VENV = WORKSPACE_ROOT / "envs" / "seedvc"
SEED_VC_PYTHON = Path(os.environ.get("AI_STUDIO_SEEDVC_PY", str(SEED_VC_VENV / "bin" / "python")))
SEED_VC_DIR = Path(os.environ.get("AI_STUDIO_SEEDVC_DIR", str(REPOS_DIR / "seed-vc")))
WANGP_DIR = Path(os.environ.get("AI_STUDIO_WANGP_DIR", str(REPOS_DIR / "WanGP")))

# Verified against WanGP/shared/utils/generation_overrides.py OVERRIDE_DEFAULTS
WANGP_L4_OVERRIDES = (
    "attention_mode='sdpa', memory_profile=4, "
    "transformer_quantization='int8', text_encoder_quantization='int8'"
)

GPU_LOCK = threading.Lock()
JOBS_LOCK = threading.Lock()
JOBS: Dict[str, Dict[str, Any]] = {}
_WANGP_SESSION = None
_LAST_LOADED_MODEL: Optional[str] = None


BACKEND_MODEL_MAP: Dict[str, str] = {
    "auto": DEFAULT_VIDEO_MODEL,
    "wangp_wan22_s2v": DEFAULT_VIDEO_MODEL,
    "wan22_s2v_v4": DEFAULT_VIDEO_MODEL,
    "ltx25_v4": "ltx2_25_22B_distilled",
    "ltx25_distilled": "ltx2_25_22B_distilled",
    "ltx2_25_22b_distilled": "ltx2_25_22B_distilled",
    "longcat_avatar": "longcat_avatar_v1_6",
    "longcat_avatar_v1_6": "longcat_avatar_v1_6",
    "longcat_avatar_v1_5": "longcat_avatar_v1_5",
    "infinitetalk": "infinitetalk",
    "multitalk": "multitalk",
    "i2v_2_2": "i2v_2_2",
}


def _cleanup_vram() -> None:
    gc.collect()
    if torch.cuda.is_available():
        try:
            torch.cuda.synchronize()
        except Exception:
            pass
        try:
            torch.cuda.empty_cache()
            torch.cuda.ipc_collect()
        except Exception:
            pass


def _gpu_info() -> Dict[str, Any]:
    if not torch.cuda.is_available():
        return {"gpu": "CPU", "vram_gb": 0.0, "vram_free_gb": 0.0, "cuda_ok": False}
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
            env=build_clean_env(),
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
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        text=True,
        env=build_clean_env(),
        timeout=15,
    ).strip()
    return max(0.0, float(out))


def _validate_wav_quality(
    wav_path: Path,
    min_dur: float = 0.25,
    min_rms_dbfs: float = -55.0,
) -> Dict[str, float]:
    """Real Audio Quality Gate: rejects empty, truncated, or silent WAV outputs."""
    if not wav_path.is_file() or wav_path.stat().st_size < 1024:
        raise RuntimeError(f"Audio Quality Gate falhou: arquivo ausente ou vazio ({wav_path.name})")
    with wave.open(str(wav_path), "rb") as wf:
        n_frames = wf.getnframes()
        sr = wf.getframerate()
        sampwidth = wf.getsampwidth()
        if sr <= 0 or n_frames <= 0:
            raise RuntimeError("Audio Quality Gate falhou: WAV sem frames válidos.")
        dur = n_frames / float(sr)
        if dur < min_dur:
            raise RuntimeError(f"Audio Quality Gate falhou: duração {dur:.3f}s abaixo do mínimo ({min_dur}s).")
        if sampwidth != 2:
            raise RuntimeError(f"Audio Quality Gate falhou: esperado PCM 16-bit, recebido {sampwidth * 8}-bit.")
        raw = wf.readframes(n_frames)

    samples = array.array("h")
    samples.frombytes(raw)
    total = len(samples)
    if total == 0:
        raise RuntimeError("Audio Quality Gate falhou: 0 amostras PCM.")

    step = max(1, total // 120_000)
    sampled = samples[::step]
    peak = max(abs(x) for x in sampled)
    rms = math.sqrt(sum((x / 32768.0) ** 2 for x in sampled) / len(sampled))
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-12))
    peak_dbfs = 20.0 * math.log10(max(peak / 32768.0, 1e-12))

    if rms_dbfs < min_rms_dbfs or peak < 64:
        raise RuntimeError(
            f"Audio Quality Gate falhou (silêncio/sinal nulo): dur={dur:.2f}s "
            f"rms={rms_dbfs:.1f}dBFS peak={peak_dbfs:.1f}dBFS"
        )
    return {
        "duration": round(dur, 2),
        "rms_dbfs": round(rms_dbfs, 1),
        "peak_dbfs": round(peak_dbfs, 1),
    }


def _validate_video_output(mp4_path: Path, min_dur: float = 0.3, min_bytes: int = 10_000) -> Dict[str, Any]:
    """Real Video Quality Gate: guarantees valid playable H.264 stream with real duration."""
    if not mp4_path.is_file() or mp4_path.stat().st_size < min_bytes:
        sz = mp4_path.stat().st_size if mp4_path.is_file() else 0
        raise RuntimeError(f"Video Quality Gate falhou: MP4 ausente ou pequeno demais ({sz} bytes).")
    out = subprocess.check_output(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,codec_name:format=duration",
            "-of",
            "json",
            str(mp4_path),
        ],
        text=True,
        env=build_clean_env(),
        timeout=15,
    )
    meta = json.loads(out or "{}")
    streams = meta.get("streams") or []
    if not streams:
        raise RuntimeError("Video Quality Gate falhou: nenhum stream de vídeo válido no MP4.")
    dur = float((meta.get("format") or {}).get("duration") or 0.0)
    if dur < min_dur:
        raise RuntimeError(f"Video Quality Gate falhou: duração {dur:.3f}s abaixo do mínimo ({min_dur}s).")
    return {
        "duration": round(dur, 2),
        "width": int(streams[0].get("width") or 0),
        "height": int(streams[0].get("height") or 0),
        "bytes": mp4_path.stat().st_size,
    }


def _mux_no_freeze(
    raw_mp4: Path,
    wav_path: Optional[Path],
    out_mp4: Path,
    width: int,
    height: int,
    fps: int = 24,
) -> None:
    vf = (
        f"scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,"
        f"crop={width}:{height},setsar=1,fps={fps},format=yuv420p"
    )
    vid_dur = max(0.3, _probe_dur(raw_mp4))
    if wav_path and wav_path.is_file():
        aud_dur = max(0.3, _probe_dur(wav_path))
        target_dur = min(vid_dur, aud_dur)
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_mp4), "-i", str(wav_path),
            "-map", "0:v:0", "-map", "1:a:0",
            "-vf", vf, "-t", f"{target_dur:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(out_mp4),
        ]
    else:
        cmd = [
            "ffmpeg", "-y", "-i", str(raw_mp4),
            "-vf", vf,
            "-c:v", "libx264", "-preset", "fast", "-crf", "17",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(out_mp4),
        ]
    proc = subprocess.run(cmd, env=build_clean_env(), capture_output=True, text=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg mux falhou (rc={proc.returncode}): {(proc.stderr or proc.stdout)[-400:]}")


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

    init_kwargs: Dict[str, Any] = {
        "root": WANGP_DIR,
        "config_path": CONFIG_DIR / "wgp_config.json",
        "output_dir": OUTPUTS_DIR,
        "cli_args": ["--attention", "sdpa", "--profile", "4"],
        "console_output": False,
    }
    try:
        sig = inspect.signature(wangp_init)
        accepted = set(sig.parameters.keys())
        if not any(p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            init_kwargs = {k: v for k, v in init_kwargs.items() if k in accepted}
    except Exception:
        pass

    _WANGP_SESSION = wangp_init(**init_kwargs)
    return _WANGP_SESSION


def _resolve_model_type(requested_backend: str, has_audio: bool) -> str:
    key = (requested_backend or "auto").strip().lower()
    if key == "seed_vc_only":
        raise RuntimeError("Este worker foi iniciado em modo seed_vc_only (sem modelo de vídeo).")
    resolved = BACKEND_MODEL_MAP.get(key) or DEFAULT_VIDEO_MODEL
    if resolved == "seed_vc_only":
        raise RuntimeError("Worker configurado apenas para Seed-VC (VIDEO_MODEL=seed_vc_only).")
    if not has_audio and resolved in ("infinitetalk", "multitalk", "longcat_avatar_v1_5", "longcat_avatar_v1_6"):
        return "i2v_2_2"
    return resolved


def _resolve_model_resolution(model_type: str, width: int, height: int) -> str:
    portrait = height >= width
    if model_type in ("infinitetalk", "multitalk") or "longcat" in model_type:
        return "480x832" if portrait else "832x480"
    if "ltx2" in model_type:
        # Verified in models/ltx2/ltx2_handler.py: vae_block_size = 64
        w = max(256, min(704 if portrait else 1280, (int(width) // 64) * 64))
        h = max(256, min(1280 if portrait else 704, (int(height) // 64) * 64))
        return f"{w}x{h}"
    return "480x832" if portrait else "832x480"


def _build_wangp_task_settings(
    base_settings: Dict[str, Any],
    model_type: str,
    model_def: Dict[str, Any],
    schema_meta: Dict[str, Any],
    *,
    prompt: str,
    negative_prompt: str,
    img_path: Path,
    wav_path: Optional[Path],
    dur_target: float,
    width: int,
    height: int,
    fps: int,
    steps: int,
    guidance_scale: float,
    seed: int,
    resolution_override: Optional[str] = None,
    force_min_frames: bool = False,
) -> Dict[str, Any]:
    """Assemble validated per-family WanGP settings matching upstream handlers."""
    settings = dict(base_settings or {})
    native_fps = int(model_def.get("fps") or fps or 24)
    f_min = int(schema_meta.get("frames_minimum") or model_def.get("frames_minimum") or 17)
    f_step = int(schema_meta.get("frames_steps") or model_def.get("frames_steps") or 8)
    f_off = int(model_def.get("frames_offset") or 1)

    if force_min_frames:
        aligned_frames = f_min
    else:
        eff_fps = native_fps if ("longcat" in model_type or model_type in ("infinitetalk", "multitalk")) else fps
        raw_frames = max(f_min, int(math.ceil(dur_target * eff_fps)) + 4)
        steps_n = max(0, (raw_frames - f_off + f_step - 1) // f_step)
        aligned_frames = max(f_min, steps_n * f_step + f_off)

    settings["model_type"] = model_type
    settings["prompt"] = prompt
    settings["negative_prompt"] = negative_prompt
    settings["resolution"] = resolution_override or _resolve_model_resolution(model_type, width, height)
    settings["video_length"] = aligned_frames
    settings["seed"] = int(seed)

    # All 4 families (LTX-2.5, LongCat Avatar 1.5/1.6, InfiniteTalk, Wan2.2 I2V) accept "S" + image_start
    settings["image_start"] = str(img_path)
    settings["image_prompt_type"] = "S"

    if "longcat" in model_type:
        # LongCat Avatar supports anchor reference image (KI) preventing identity drift across windows
        settings["image_refs"] = [str(img_path)]
        settings["video_prompt_type"] = "KI"
        settings["force_fps"] = ""  # preserve native 25fps audio_stride=1; FFmpeg muxes cleanly to target fps
        settings["sample_solver"] = "distill"
        settings["num_inference_steps"] = 8
        settings["guidance_scale"] = 1.0
        settings["audio_guidance_scale"] = 1.0
        if wav_path is not None:
            settings["audio_guide"] = str(wav_path)
            settings["audio_prompt_type"] = "A"
        else:
            settings["audio_guide"] = None
            settings["audio_prompt_type"] = ""
    elif "ltx2" in model_type:
        settings["video_prompt_type"] = ""
        settings["image_refs"] = None
        settings["force_fps"] = str(int(fps or 24))
        if "distilled" in model_type:
            settings["num_inference_steps"] = 8
        else:
            settings["num_inference_steps"] = max(4, int(steps))
            settings["guidance_scale"] = float(guidance_scale)
        if wav_path is not None:
            settings["audio_guide"] = str(wav_path)
            settings["audio_prompt_type"] = "AS"
        else:
            settings["audio_guide"] = None
            settings["audio_prompt_type"] = ""
    elif model_type in ("infinitetalk", "multitalk"):
        settings["video_prompt_type"] = ""
        settings["image_refs"] = None
        settings["force_fps"] = ""
        settings["num_inference_steps"] = max(4, int(steps))
        settings["guidance_scale"] = float(guidance_scale)
        if wav_path is not None:
            settings["audio_guide"] = str(wav_path)
            settings["audio_prompt_type"] = "A"
        else:
            settings["audio_guide"] = None
            settings["audio_prompt_type"] = ""
    else:
        settings["video_prompt_type"] = ""
        settings["image_refs"] = None
        settings["force_fps"] = ""
        settings["audio_guide"] = None
        settings["audio_prompt_type"] = ""
        settings["num_inference_steps"] = max(4, int(steps))
        settings["guidance_scale"] = float(guidance_scale)

    settings["overrides"] = WANGP_L4_OVERRIDES
    return settings


def _evict_finished_jobs_locked(max_finished: int = 16) -> None:
    """Never evict queued or processing jobs; prune oldest completed/error jobs."""
    finished = [
        (j.get("created_at", 0.0), jid)
        for jid, j in JOBS.items()
        if j.get("status") in ("completed", "error")
    ]
    if len(finished) <= max_finished:
        return
    finished.sort(key=lambda item: item[0])
    for _, jid in finished[: len(finished) - max_finished]:
        JOBS.pop(jid, None)


def _read_smoke_status() -> bool:
    smoke_meta = CONFIG_DIR / "smoke_ok.json"
    if not smoke_meta.is_file():
        return False
    try:
        data = json.loads(smoke_meta.read_text(encoding="utf-8"))
        return bool(data.get("ok")) and data.get("video_model") == DEFAULT_VIDEO_MODEL
    except Exception:
        return False


app = FastAPI(title="AI Media Studio Colab Cinema Engine v4.0", version=SERVER_VERSION)


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
    seed_vc_ready = SEED_VC_PYTHON.is_file() and (SEED_VC_DIR / "inference.py").is_file()
    wangp_installed = (WANGP_DIR / "wgp.py").is_file() and (WANGP_DIR / "shared" / "api.py").is_file()
    wangp_ready = wangp_installed and DEFAULT_VIDEO_MODEL != "seed_vc_only"
    ltx2_ready = wangp_ready and "ltx2" in DEFAULT_VIDEO_MODEL
    longcat_ready = wangp_ready and "longcat" in DEFAULT_VIDEO_MODEL
    is_ready = bool(info["cuda_ok"] and seed_vc_ready and (wangp_ready or DEFAULT_VIDEO_MODEL == "seed_vc_only"))
    engines = [
        name
        for name, enabled in (
            ("wan22_s2v", wangp_ready),
            ("ltx25", ltx2_ready),
            ("longcat", longcat_ready),
            ("seed_vc", seed_vc_ready),
        )
        if enabled
    ]
    with JOBS_LOCK:
        active_jobs = sum(1 for j in JOBS.values() if j.get("status") in ("queued", "processing"))
    return {
        "ok": True,
        "ready": is_ready,
        "version": SERVER_VERSION,
        **info,
        "default_video_model": DEFAULT_VIDEO_MODEL,
        "loaded_video_model": _LAST_LOADED_MODEL,
        "workspace_root": str(WORKSPACE_ROOT),
        "cache_free_gb": disk_free_gb,
        "drive_mounted": False,
        "env_sanitized": (
            os.environ.get("MPLBACKEND") == "Agg"
            and "PYTHONPATH" not in os.environ
            and "JPY_PARENT_PID" not in os.environ
        ),
        "smoke_verified": _read_smoke_status(),
        "seed_vc_ready": seed_vc_ready,
        "wangp_ready": wangp_ready,
        "ltx2_ready": ltx2_ready,
        "longcat_ready": longcat_ready,
        "engines": engines,
        "wangp_session_warm": _WANGP_SESSION is not None,
        "active_jobs": active_jobs,
    }


@app.post("/admin/exec")
def admin_exec(req: ExecReq, x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=True)
    proc = subprocess.run(
        req.cmd,
        shell=True,
        env=build_clean_env(),
        capture_output=True,
        text=True,
        timeout=req.timeout,
    )
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

            # Verified in Plachtaa/seed-vc/inference.py L325-343: auto_f0_adjust applies inside if f0_condition
            auto_f0 = "True" if (req.f0_condition and int(req.semi_tone_shift) == 0) else "False"
            cmd = [
                str(SEED_VC_PYTHON),
                str(inf_py),
                "--source", str(g_wav),
                "--target", str(t_wav),
                "--output", str(out_dir),
                "--diffusion-steps", str(max(1, int(req.diffusion_steps))),
                "--semi-tone-shift", str(int(req.semi_tone_shift)),
                "--f0-condition", "True" if req.f0_condition else "False",
                "--auto-f0-adjust", auto_f0,
                "--fp16", "True",
            ]
            # Plachtaa/seed-vc (PyTorch 2.4 era) calls bare torch.load(); allow legacy checkpoints on PyTorch 2.6+/2.10
            svc_env = build_clean_env(
                venv_dir=SEED_VC_VENV,
                extra={
                    "HF_HOME": str(HF_CACHE_DIR),
                    "TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD": "1",
                },
            )
            proc = subprocess.run(
                cmd,
                cwd=str(SEED_VC_DIR),
                env=svc_env,
                capture_output=True,
                text=True,
                timeout=240,
            )
            gen = next(iter(sorted(out_dir.glob("*.wav"))), None)
            if proc.returncode != 0 or not gen or not gen.is_file():
                diag = (proc.stderr or proc.stdout or "").strip()[-600:]
                raise RuntimeError(f"Seed-VC falhou (rc={proc.returncode}): {diag}")

            ff = subprocess.run(
                ["ffmpeg", "-y", "-i", str(gen), "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(out_wav)],
                env=build_clean_env(),
                capture_output=True,
                text=True,
                timeout=60,
            )
            if ff.returncode != 0:
                raise RuntimeError(f"FFmpeg resample falhou: {(ff.stderr or ff.stdout)[-400:]}")

            # Real Audio Quality Gate (zero silent/corrupted fallback)
            aqg = _validate_wav_quality(out_wav, min_dur=0.25, min_rms_dbfs=-55.0)
            return {
                "ok": True,
                "engine": "seed-vc-v4-real",
                "elapsed_s": round(time.time() - t0, 2),
                "duration": aqg["duration"],
                "rms_dbfs": aqg["rms_dbfs"],
                "peak_dbfs": aqg["peak_dbfs"],
                "vram_used_gb": _smi_used_gb(),
                "character_id": req.character_id,
                "audio_b64": base64.b64encode(out_wav.read_bytes()).decode("ascii"),
            }
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)[:600])
        finally:
            shutil.rmtree(job_dir, ignore_errors=True)
            _cleanup_vram()


def _execute_scene_job(job_id: str, req: AnimateSceneReq) -> None:
    global _LAST_LOADED_MODEL
    job_dir = WORKSPACE_ROOT / "tmp" / f"scene_v4_{job_id}"
    job_dir.mkdir(parents=True, exist_ok=True)
    raw_cand: Optional[Path] = None
    t0 = time.time()
    try:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["status"] = "processing"
                JOBS[job_id]["started_at"] = t0

        img_p = job_dir / "keyframe.jpg"
        img_p.write_bytes(base64.b64decode(req.image_b64))
        wav_p: Optional[Path] = None
        dur_target = float(req.duration_sec or 3.2)
        if req.audio_b64:
            wav_p = job_dir / "dialogue.wav"
            wav_p.write_bytes(base64.b64decode(req.audio_b64))
            dur_target = max(1.0, min(12.0, _probe_dur(wav_p)))

        fps = int(req.fps or 24)
        final_mp4 = job_dir / "final_scene.mp4"
        resolved_model = _resolve_model_type(req.backend, has_audio=(wav_p is not None))

        with GPU_LOCK:
            session = _get_wangp_session()
            meta = session.get_model_metadata(resolved_model)
            if not meta:
                avail = [
                    (m.get("id") or m.get("model_type") or str(m))
                    for m in (session.list_model_metadata() or [])[:25]
                ]
                raise RuntimeError(
                    f"Modelo '{resolved_model}' não encontrado no WanGP instalado. Disponíveis (amostra): {avail}"
                )

            base_settings = session.get_default_settings(resolved_model)
            mdef = session.get_model_def(resolved_model) or {}
            schema = (session.get_model_schema(resolved_model) or {}).get("metadata", {})
            settings = _build_wangp_task_settings(
                base_settings,
                resolved_model,
                mdef,
                schema,
                prompt=req.prompt,
                negative_prompt=req.negative_prompt,
                img_path=img_p,
                wav_path=wav_p,
                dur_target=dur_target,
                width=int(req.width),
                height=int(req.height),
                fps=fps,
                steps=int(req.steps),
                guidance_scale=float(req.guidance_scale),
                seed=int(req.seed),
            )

            job = session.submit_task(settings)
            result = job.result()
            if not result.success or not result.generated_files:
                errs = "; ".join(str(e) for e in (result.errors or [])) or "WanGP retornou 0 arquivos."
                raise RuntimeError(errs[:600])

            _LAST_LOADED_MODEL = resolved_model
            raw_cand = Path(result.generated_files[0])
            _mux_no_freeze(raw_cand, wav_p, final_mp4, int(req.width), int(req.height), fps=fps)
            vqg = _validate_video_output(final_mp4, min_dur=0.3)
            peak_vram = _smi_used_gb()
            _cleanup_vram()

        vid_b64 = base64.b64encode(final_mp4.read_bytes()).decode("ascii")
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id].update({
                    "status": "completed",
                    "ok": True,
                    "backend": resolved_model,
                    "elapsed_s": round(time.time() - t0, 2),
                    "duration": vqg["duration"],
                    "vram_peak_gb": peak_vram,
                    "video_b64": vid_b64,
                })
    except Exception as exc:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id].update({
                    "status": "error",
                    "ok": False,
                    "elapsed_s": round(time.time() - t0, 2),
                    "error": str(exc)[:600],
                })
    finally:
        if raw_cand is not None:
            try:
                raw_cand.unlink(missing_ok=True)
            except Exception:
                pass
        shutil.rmtree(job_dir, ignore_errors=True)
        _cleanup_vram()


@app.post("/jobs/animate_scene")
def submit_scene_job(req: AnimateSceneReq, x_colab_token: str = Header(default="")):
    _require_token(x_colab_token, require_configured=False)
    job_id = uuid.uuid4().hex[:10]
    with JOBS_LOCK:
        _evict_finished_jobs_locked(max_finished=16)
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
        return dict(job)


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
