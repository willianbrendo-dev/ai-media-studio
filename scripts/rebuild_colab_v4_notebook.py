#!/usr/bin/env python3
"""Gera, sincroniza e valida os notebooks Colab Pro L4 Worker v4.0 (Clean-Slate · Zero Drive).

Fonte única de verdade:
  - Lê tools/colab-gpu-worker/colab_v4_cinema_server.py
  - Gera espelhos idênticos em:
      * tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb
      * notebooks/ai_studio_colab_novela_gpu.ipynb
  - Executa validação completa (AST, py_compile, JSON/nbformat, injeção de env tóxico,
    contrato de modelos WanGP, contrato VPS v4.0 e scripts/secret-scan.py).
"""

from __future__ import annotations

import ast
import json
import py_compile
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SERVER_PATH = REPO_ROOT / "tools" / "colab-gpu-worker" / "colab_v4_cinema_server.py"
NB_PRIMARY = REPO_ROOT / "tools" / "colab-gpu-worker" / "ai_studio_colab_novela_gpu.ipynb"
NB_MIRROR = REPO_ROOT / "notebooks" / "ai_studio_colab_novela_gpu.ipynb"


CLEAN_ENV_MODULE = r'''"""Deterministic environment sanitizer for Colab Pro L4 Worker v4.0."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

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

PRESERVED_EXACT_VARS = {
    "COLAB_AUTH_TOKEN",
}


def _augment_cuda_ld_library_path(existing: str) -> str:
    """Append existing host CUDA/NVIDIA library dirs without clobbering existing entries."""
    candidates: list[str] = []
    for base in ("/usr/lib64-nvidia", "/usr/local/cuda/lib64"):
        if Path(base).is_dir():
            candidates.append(base)
    usr_local = Path("/usr/local")
    if usr_local.is_dir():
        for p in sorted(usr_local.glob("cuda-12*/targets/x86_64-linux/lib")):
            if p.is_dir():
                candidates.append(str(p))
    parts = [x for x in (existing or "").split(":") if x]
    for c in candidates:
        if c not in parts:
            parts.append(c)
    return ":".join(parts)


def build_clean_env(
    ws: Path = Path("/content/ai_studio_v4"),
    venv_dir: Optional[Path] = None,
    extra: Optional[Dict[str, str]] = None,
    base_env: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Return a clean subprocess env preserving CUDA/NVIDIA libs & COLAB_AUTH_TOKEN."""
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

    ld_path = _augment_cuda_ld_library_path(cleaned.get("LD_LIBRARY_PATH", ""))
    if ld_path:
        cleaned["LD_LIBRARY_PATH"] = ld_path

    cache_dir = ws / "cache"
    hf_cache = ws / "hf_cache"
    cleaned.update({
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
        "HF_HOME": str(hf_cache),
        "HF_HUB_CACHE": str(hf_cache / "hub"),
        "TRITON_CACHE_DIR": str(cache_dir / "triton"),
        "TORCHINDUCTOR_CACHE_DIR": str(cache_dir / "inductor"),
        "MPLCONFIGDIR": str(cache_dir / "matplotlib"),
        "XDG_CACHE_HOME": str(cache_dir / "xdg"),
        "WAN2GP_DISABLE_AUDIO": "1",
        "TF_CPP_MIN_LOG_LEVEL": "3",
        "LC_ALL": "C.UTF-8",
        "LANG": "C.UTF-8",
    })

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
'''


def _lines(src: str) -> list[str]:
    return [line + "\n" for line in src.strip("\n").splitlines()]


def build_cells(server_code: str) -> list[dict]:
    md_cell = {
        "cell_type": "markdown",
        "metadata": {},
        "source": _lines(
            """
# AI Media Studio — Google Colab Pro L4 Worker v4.0 (Clean-Slate · Zero Drive)

Arquitetura à prova de falhas para **NVIDIA L4 (24 GB VRAM + RAM Alta)** em NVMe local (`/content/ai_studio_v4`), 100% sem Google Drive:
- **Isolamento hermético (`clean_env.py`):** neutraliza `MPLBACKEND=module://matplotlib_inline...`, `PYTHONPATH`, `PYTHONHOME`, `JPY_*`, `COLAB_*` (preservando `COLAB_AUTH_TOKEN`) e `LD_PRELOAD` em todas as células e no bootstrap do servidor, preservando e enriquecendo `LD_LIBRARY_PATH` (`/usr/lib64-nvidia`, `/usr/local/cuda/lib64`, `/usr/local/cuda-12*/targets/x86_64-linux/lib`), `CUDA_*` e `NVIDIA_*`.
- **Dual Venv Python 3.10 via `uv` imune a `libnppicc.so.12`:** `envs/seedvc` fixo em `torch==2.7.1+cu126` + `torchaudio==2.7.1+cu126` (nativo `soundfile`, zero `torchcodec`); `envs/wangp` em `torch==2.10.0+cu128` + `torchcodec==0.10.0` da wheel CPU (`--no-deps --index-url .../whl/cpu`) com fallback automático verificado para `cu126` (`2.7.1`) se necessário.
- **Configuração por família validada no upstream `WanGP`:** `vae_block_size=64` + `audio_prompt_type='AS'` para LTX-2.5, `image_start` (`S`) + `image_refs` (`KI`) + `distill` para LongCat Avatar 1.6/1.5, `image_start` + `audio_prompt_type='A'` para InfiniteTalk, com `INT8` (Transformer + Text Encoder) + `SDPA` + `Profile 4`.
- **Smoke test real + Quality Gates + Watchdog HTTP/2:** Célula 2 valida `torchaudio.save/load` + CUDA nos dois venvs; Célula 3 valida conversão real Seed-VC (Audio Quality Gate PCM) e micro-render real WanGP (Video Quality Gate `ffprobe` + liberação de VRAM); Célula 4 expõe `version='4.0.3-clean-l4'` (compatível com `vps_novela_runner.py`), valida `GET <tunnel>/health` externo e auto-recupera túnel/servidor sem caixas vermelhas.
"""
        ),
    }

    cell1 = f'''# @title 1. Limpeza Total, Sanitizador Hermético & Pré-Requisitos (Zero Google Drive)
REBUILD_FROM_SCRATCH = True  # @param {{type:'boolean'}}

import json, os, re, shutil, subprocess, sys, time, warnings
from pathlib import Path

warnings.filterwarnings("ignore")

# Bracket trick evita que pkill mate o próprio subshell bash -lc
subprocess.run(
    ["bash", "-lc", "pkill -f '[c]olab_v4_cinema_server' || true; pkill -f '[c]olab_gpu_server' || true; pkill -f '[c]loudflared' || true"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(1)
subprocess.run(
    ["bash", "-lc", "pkill -9 -f '[c]olab_v4_cinema_server' || true; pkill -9 -f '[c]loudflared' || true"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)

legacy_dirs = [
    "/content/ai_studio_cache", "/content/venv_cinema311", "/content/seed-vc",
    "/content/WanGP", "/content/LivePortrait", "/content/LatentSync", "/content/MuseTalk",
]
if REBUILD_FROM_SCRATCH:
    legacy_dirs.append("/content/ai_studio_v4")
for ld in legacy_dirs:
    shutil.rmtree(ld, ignore_errors=True)

WS = Path("/content/ai_studio_v4")
for sub in (
    "repos", "envs", "hf_cache/hub", "models/seedvc", "models/wangp",
    "config", "outputs/wangp", "tmp",
    "cache/triton", "cache/inductor", "cache/matplotlib", "cache/xdg",
):
    (WS / sub).mkdir(parents=True, exist_ok=True)

CLEAN_ENV_CODE = {CLEAN_ENV_MODULE!r}
(WS / "clean_env.py").write_text(CLEAN_ENV_CODE, encoding="utf-8")
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))
from clean_env import build_clean_env  # noqa: E402

SYS_ENV = build_clean_env(ws=WS)
assert SYS_ENV["MPLBACKEND"] == "Agg"
assert "PYTHONPATH" not in SYS_ENV and "JPY_PARENT_PID" not in SYS_ENV
print(f"[OK] Workspace limpo + sanitizador clean_env.py prontos em {{WS}}")

def run_clean(label, cmd, cwd=None, env=None):
    t0 = time.time()
    p = subprocess.run(
        cmd,
        cwd=cwd,
        env=env or SYS_ENV,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    dt = time.time() - t0
    if p.returncode != 0:
        tail = "\\n".join((p.stdout or "").splitlines()[-60:])
        raise RuntimeError(f"[ERRO] {{label}} falhou (rc={{p.returncode}}):\\n{{tail}}")
    print(f"[OK] {{label}} ({{dt:.1f}}s)")
    return (p.stdout or "").strip()

smi_gpu = run_clean("Detecção GPU NVIDIA", [
    "nvidia-smi", "--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"
])
if not smi_gpu or "NVIDIA" not in smi_gpu.upper():
    raise RuntimeError("GPU NVIDIA não detectada! Altere o Runtime do Colab para GPU L4.")

smi_full = subprocess.check_output(["nvidia-smi"], env=SYS_ENV, text=True)
m_cuda = re.search(r"CUDA Version:\\s*(\\d+)\\.(\\d+)", smi_full)
cuda_major, cuda_minor = (int(m_cuda.group(1)), int(m_cuda.group(2))) if m_cuda else (12, 6)

if (cuda_major, cuda_minor) >= (12, 8):
    cuda_cfg = {{"idx": "cu128", "torch": "2.10.0", "tv": "0.25.0", "ta": "2.10.0", "torchcodec": "0.10.0"}}
elif (cuda_major, cuda_minor) >= (12, 6):
    cuda_cfg = {{"idx": "cu126", "torch": "2.7.1", "tv": "0.22.1", "ta": "2.7.1", "torchcodec": ""}}
else:
    cuda_cfg = {{"idx": "cu124", "torch": "2.6.0", "tv": "0.21.0", "ta": "2.6.0", "torchcodec": ""}}
cuda_cfg["driver_cuda"] = f"{{cuda_major}}.{{cuda_minor}}"
(WS / "config/cuda_target.json").write_text(json.dumps(cuda_cfg, indent=2), encoding="utf-8")

disk_free_gb = round(shutil.disk_usage("/content").free / (1024 ** 3), 1)
print(
    f"[INFO] GPU: {{smi_gpu}} | Driver CUDA: {{cuda_major}}.{{cuda_minor}} | "
    f"PyTorch Alvo: {{cuda_cfg['torch']}}+{{cuda_cfg['idx']}} | Disco /content livre: {{disk_free_gb}} GB"
)

run_clean("apt-get dependências de mídia, OpenCV e build", [
    "bash", "-lc",
    "apt-get update -qq && apt-get install -y -qq "
    "ffmpeg libsndfile1 libportaudio2 libgl1 libglib2.0-0 libsm6 libxext6 libxrender1 "
    "build-essential git curl pkg-config"
])
run_clean("Instalação uv (gerenciador rápido em Rust)", [
    sys.executable, "-m", "pip", "install", "-q", "--no-input", "--upgrade", "uv"
])
run_clean("Download cloudflared CLI", [
    "bash", "-lc",
    "curl -fsSL -o /usr/local/bin/cloudflared "
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 "
    "&& chmod +x /usr/local/bin/cloudflared"
])
cf_ver = run_clean("Verificação cloudflared", ["cloudflared", "--version"])
ff_ver = run_clean("Verificação ffmpeg", ["bash", "-lc", "ffmpeg -version | head -n 1"])
uv_ver = run_clean("Verificação uv", ["uv", "--version"])
print(f"[OK] Sistema base pronto | {{ff_ver}} | {{uv_ver}} | {{cf_ver.splitlines()[0]}}")
'''

    cell2 = r'''# @title 2. Provisionamento Dual-Venv Python 3.10 (Seed-VC + WanGP · Isolamento Hermético)
VIDEO_MODEL = 'ltx2_25_22B_distilled'  # @param ['ltx2_25_22B_distilled', 'longcat_avatar_v1_6', 'longcat_avatar_v1_5', 'infinitetalk', 'i2v_2_2', 'seed_vc_only']
PRELOAD_WEIGHTS = True  # @param {type:'boolean'}
HF_TOKEN = ''  # @param {type:'string'}
COLAB_AUTH_TOKEN = ''  # @param {type:'string'}

import json, subprocess, sys, time, warnings
from pathlib import Path

warnings.filterwarnings("ignore")

WS = Path("/content/ai_studio_v4")
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))
from clean_env import build_clean_env  # noqa: E402

cuda_cfg = json.loads((WS / "config/cuda_target.json").read_text(encoding="utf-8"))

def _read_colab_secret(name: str) -> str:
    try:
        from google.colab import userdata
        return (userdata.get(name) or "").strip()
    except Exception:
        return ""

hf_tok = (HF_TOKEN or "").strip() or _read_colab_secret("HF_TOKEN")
auth_tok = (COLAB_AUTH_TOKEN or "").strip() or _read_colab_secret("COLAB_AUTH_TOKEN")

worker_env_cfg = {
    "VIDEO_MODEL": VIDEO_MODEL,
    "PRELOAD_WEIGHTS": bool(PRELOAD_WEIGHTS),
    "HF_TOKEN": hf_tok,
    "COLAB_AUTH_TOKEN": auth_tok,
}
(WS / "config/worker_env.json").write_text(json.dumps(worker_env_cfg, indent=2), encoding="utf-8")

extra_hf = {"HF_TOKEN": hf_tok} if hf_tok else None
BASE_ENV = build_clean_env(ws=WS, extra=extra_hf)

def run_clean(label, cmd, cwd=None, venv_dir=None, extra_env=None):
    t0 = time.time()
    merged_extra = dict(extra_hf or {})
    if extra_env:
        merged_extra.update(extra_env)
    env = build_clean_env(ws=WS, venv_dir=venv_dir, extra=merged_extra or None) if venv_dir else BASE_ENV
    p = subprocess.run(cmd, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    dt = time.time() - t0
    if p.returncode != 0:
        tail = "\n".join((p.stdout or "").splitlines()[-65:])
        raise RuntimeError(f"[ERRO] {label} falhou (rc={p.returncode}):\n{tail}")
    print(f"[OK] {label} ({dt:.1f}s)")
    return (p.stdout or "").strip()

run_clean("Provisionamento Python 3.10 (uv)", ["uv", "python", "install", "3.10"])

TORCH_VER = cuda_cfg["torch"]
TV_VER = cuda_cfg["tv"]
TA_VER = cuda_cfg["ta"]
TC_VER = cuda_cfg.get("torchcodec") or ""
TORCH_INDEX = f"https://download.pytorch.org/whl/{cuda_cfg['idx']}"

# --- 1. Ambiente Seed-VC (100% imune a torchcodec/libnppicc.so.12: PyTorch 2.7.1+cu126 + soundfile nativo) ---
SEEDVC_VENV = WS / "envs/seedvc"
SEEDVC_PY = str(SEEDVC_VENV / "bin/python")
SEEDVC_DIR = WS / "repos/seed-vc"

if not (SEEDVC_DIR / "inference.py").is_file():
    run_clean("Clone Plachtaa/seed-vc", [
        "git", "clone", "--depth", "1", "https://github.com/Plachtaa/seed-vc.git", str(SEEDVC_DIR)
    ])

ckpt_link = SEEDVC_DIR / "checkpoints"
if not ckpt_link.exists() and not ckpt_link.is_symlink():
    ckpt_link.symlink_to(WS / "models/seedvc")

run_clean("Criação venv Python 3.10 Seed-VC", ["uv", "venv", str(SEEDVC_VENV), "--python", "3.10", "--seed"])

# L4 (Ada Lovelace sm_89) com driver CUDA 12.8+/13.0 executa cu126 nativamente;
# torchaudio==2.7.1 usa soundfile direto sem tocar em libtorchcodec_core*.so nem libnppicc.so.12.
run_clean("Instalação PyTorch 2.7.1+cu126 + torchaudio 2.7.1 (Seed-VC · sem torchcodec)", [
    "uv", "pip", "install", "--python", SEEDVC_PY,
    "--index-url", "https://download.pytorch.org/whl/cu126",
    "torch==2.7.1", "torchaudio==2.7.1",
], venv_dir=SEEDVC_VENV)

svc_pkgs = [
    "numpy==1.26.4", "transformers==4.46.3", "accelerate",
    "huggingface-hub>=0.28.1", "safetensors", "tqdm",
    "scipy==1.13.1", "librosa==0.10.2", "munch==4.0.0", "einops==0.8.0",
    "descript-audio-codec==1.0.0", "soundfile==0.12.1", "pydub==0.25.1",
    "pyyaml", "resampy", "hydra-core==1.3.2", "python-dotenv", "jiwer==3.0.3",
]
run_clean("Instalação dependências limpas Seed-VC (soundfile nativo, zero GUI/pyaudio/torchcodec)", [
    "uv", "pip", "install", "--python", SEEDVC_PY, *svc_pkgs,
], venv_dir=SEEDVC_VENV)

# Valida imports + CUDA + torchaudio.save() + torchaudio.load() reais sem torchcodec
svc_check_code = (
    "import os, torch, torchaudio, transformers, librosa, munch, soundfile, dac\n"
    "from pathlib import Path\n"
    "assert os.environ.get('MPLBACKEND') == 'Agg'\n"
    "assert 'PYTHONPATH' not in os.environ\n"
    "assert torch.cuda.is_available()\n"
    "chk = Path('/content/ai_studio_v4/tmp/ta_save_check.wav')\n"
    "torchaudio.save(str(chk), torch.zeros(1, 1600), 16000)\n"
    "wav, sr = torchaudio.load(str(chk))\n"
    "assert chk.is_file() and chk.stat().st_size > 100 and sr == 16000 and tuple(wav.shape) == (1, 1600)\n"
    "chk.unlink(missing_ok=True)\n"
    "print('SEEDVC_ENV_OK', torch.__version__, torchaudio.__version__, torch.cuda.get_device_name(0))\n"
)
svc_check_py = WS / "tmp/seedvc_selfcheck.py"
svc_check_py.write_text(svc_check_code, encoding="utf-8")
svc_check = run_clean("Self-check Seed-VC (imports + CUDA + torchaudio.save/load)", [
    SEEDVC_PY, str(svc_check_py),
], venv_dir=SEEDVC_VENV)
print("   ->", svc_check.splitlines()[-1])

# --- 2. Ambiente Worker/WanGP (cu128 + torchcodec CPU --no-deps c/ fallback verificado p/ cu126) ---
WANGP_VENV = WS / "envs/wangp"
WANGP_PY = str(WANGP_VENV / "bin/python")
WANGP_DIR = WS / "repos/WanGP"

run_clean("Criação venv Python 3.10 Worker/WanGP", ["uv", "venv", str(WANGP_VENV), "--python", "3.10", "--seed"])

run_clean(f"Instalação PyTorch {TORCH_VER}+{cuda_cfg['idx']} (Worker/WanGP)", [
    "uv", "pip", "install", "--python", WANGP_PY,
    "--index-url", TORCH_INDEX,
    f"torch=={TORCH_VER}", f"torchvision=={TV_VER}", f"torchaudio=={TA_VER}",
], venv_dir=WANGP_VENV)

if cuda_cfg["idx"] == "cu128" and TC_VER:
    # Wheel CPU do torchcodec usa apenas ffmpeg do sistema e NÃO linka contra libnppicc.so.12
    run_clean(f"Instalação torchcodec=={TC_VER} (wheel CPU --no-deps, sem libnppicc.so.12)", [
        "uv", "pip", "install", "--python", WANGP_PY,
        "--no-deps", "--index-url", "https://download.pytorch.org/whl/cpu",
        f"torchcodec=={TC_VER}",
    ], venv_dir=WANGP_VENV)

if VIDEO_MODEL == "seed_vc_only":
    run_clean("Instalação FastAPI + Uvicorn (seed_vc_only)", [
        "uv", "pip", "install", "--python", WANGP_PY,
        "fastapi", "uvicorn[standard]", "pydantic>=2.9", "python-multipart",
    ], venv_dir=WANGP_VENV)
else:
    if not (WANGP_DIR / "wgp.py").is_file():
        run_clean("Clone deepbeepmeep/WanGP", [
            "git", "clone", "--depth", "1", "https://github.com/deepbeepmeep/WanGP.git", str(WANGP_DIR)
        ])
    ckpts_link = WANGP_DIR / "ckpts"
    if not ckpts_link.exists() and not ckpts_link.is_symlink():
        ckpts_link.symlink_to(WS / "models/wangp")

    kept_reqs = ["fastapi", "uvicorn[standard]", "python-multipart", "numpy==2.1.2", "cython", "setuptools", "wheel"]
    # Exclui torch/torchvision/torchaudio/torchcodec/numpy para não sobrescrever os wheels CUDA/CPU dedicados
    skip_prefixes = ("torch", "torchvision", "torchaudio", "torchcodec", "numpy")
    for raw in (WANGP_DIR / "requirements.txt").read_text(encoding="utf-8").splitlines():
        ln = raw.split("#", 1)[0].strip()
        if not ln:
            continue
        low = ln.lower()
        if low.startswith("--extra-index-url"):
            continue
        if "onnxruntime-gpu" in low and "3.11" in low:
            continue
        if low in skip_prefixes or any(
            low.startswith(p + "=") or low.startswith(p + ">") or low.startswith(p + "<") or low.startswith(p + " ")
            for p in skip_prefixes
        ):
            continue
        if low.startswith("insightface"):
            continue
        kept_reqs.append(ln)

    req_file = WS / "tmp/wangp_reqs_clean.txt"
    req_file.write_text("\n".join(kept_reqs) + "\n", encoding="utf-8")
    run_clean(f"Instalação dependências oficiais WanGP + FastAPI ({len(kept_reqs)} pacotes)", [
        "uv", "pip", "install", "--python", WANGP_PY, "-r", str(req_file),
    ], venv_dir=WANGP_VENV)
    run_clean("Compilação insightface==0.7.3 (Linux Python 3.10)", [
        "uv", "pip", "install", "--python", WANGP_PY, "--no-build-isolation", "insightface==0.7.3",
    ], venv_dir=WANGP_VENV)

    wgp_selfcheck = (
        "import os, sys, matplotlib, torch, torchaudio, fastapi, uvicorn\n"
        "from pathlib import Path\n"
        "assert os.environ.get('MPLBACKEND') == 'Agg'\n"
        "assert matplotlib.get_backend().lower() == 'agg'\n"
        "assert 'PYTHONPATH' not in os.environ\n"
        "assert torch.cuda.is_available()\n"
        "chk = Path('/content/ai_studio_v4/tmp/ta_wgp_check.wav')\n"
        "torchaudio.save(str(chk), torch.zeros(1, 1600), 16000)\n"
        "wav, sr = torchaudio.load(str(chk))\n"
        "assert chk.is_file() and chk.stat().st_size > 100 and sr == 16000 and tuple(wav.shape) == (1, 1600)\n"
        "chk.unlink(missing_ok=True)\n"
        "sys.path.insert(0, '/content/ai_studio_v4/repos/WanGP')\n"
        "from shared.utils import generation_overrides\n"
        "ov = generation_overrides.parse_overrides(\n"
        "    \"attention_mode='sdpa', memory_profile=4, transformer_quantization='int8', text_encoder_quantization='int8'\"\n"
        ")\n"
        "assert ov['memory_profile'] == 4 and ov['transformer_quantization'] == 'int8'\n"
        "from shared.api import init\n"
        "s = init(\n"
        "    root=Path('/content/ai_studio_v4/repos/WanGP'),\n"
        "    config_path=Path('/content/ai_studio_v4/config/wgp_config.json'),\n"
        "    output_dir=Path('/content/ai_studio_v4/outputs/wangp'),\n"
        "    cli_args=['--attention', 'sdpa', '--profile', '4'],\n"
        "    console_output=False,\n"
        ")\n"
        f"target = {VIDEO_MODEL!r}\n"
        "all_models = s.list_model_metadata() or []\n"
        "m = s.get_model_metadata(target)\n"
        "if not m:\n"
        "    ids = [x.get('id') or x.get('model_type') or str(x) for x in all_models]\n"
        "    raise RuntimeError(f'Modelo {target!r} ausente no WanGP clonado. IDs disponíveis: {ids[:40]}')\n"
        "print('WANGP_API_OK', torch.__version__, torchaudio.__version__, 'models=', len(all_models), 'target=', target)\n"
    )
    sc_path = WS / "tmp/wangp_selfcheck.py"
    sc_path.write_text(wgp_selfcheck, encoding="utf-8")
    try:
        wgp_check = run_clean("Self-check WanGP (torchaudio.save/load + shared.api.init + overrides + Agg + CUDA)", [
            WANGP_PY, str(sc_path),
        ], venv_dir=WANGP_VENV)
    except RuntimeError as exc:
        msg = str(exc)
        if cuda_cfg["idx"] == "cu128" and any(tok in msg for tok in ("libtorchcodec", "torchcodec", "undefined symbol", "torchaudio")):
            print("[WARN] torchcodec CPU apresentou incompatibilidade com torch 2.10+cu128 — aplicando fallback verificado para PyTorch 2.7.1+cu126 (sem torchcodec)...")
            run_clean("Remoção torchcodec (fallback cu126)", [
                "uv", "pip", "uninstall", "--python", WANGP_PY, "torchcodec",
            ], venv_dir=WANGP_VENV)
            run_clean("Instalação PyTorch 2.7.1+cu126 + torchvision 0.22.1 + torchaudio 2.7.1 (WanGP fallback)", [
                "uv", "pip", "install", "--python", WANGP_PY,
                "--index-url", "https://download.pytorch.org/whl/cu126",
                "torch==2.7.1", "torchvision==0.22.1", "torchaudio==2.7.1",
            ], venv_dir=WANGP_VENV)
            wgp_check = run_clean("Self-check WanGP (fallback cu126: torchaudio.save/load + shared.api.init + CUDA)", [
                WANGP_PY, str(sc_path),
            ], venv_dir=WANGP_VENV)
        else:
            raise
    print("   ->", wgp_check.splitlines()[-1])

print("Etapa 2 concluída: 0 conflitos, sanitização verificada e 100% dos imports OK na L4.")
'''

    cell3 = r'''# @title 3. Pré-Download de Pesos & Smoke Test Real End-to-End na L4 GPU
import array, json, math, shutil, struct, subprocess, sys, time, warnings, wave
from pathlib import Path

warnings.filterwarnings("ignore")

WS = Path("/content/ai_studio_v4")
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))
from clean_env import build_clean_env  # noqa: E402

env_cfg = json.loads((WS / "config/worker_env.json").read_text(encoding="utf-8"))
VIDEO_MODEL = env_cfg["VIDEO_MODEL"]
PRELOAD_WEIGHTS = env_cfg["PRELOAD_WEIGHTS"]
extra_hf = {"HF_TOKEN": env_cfg["HF_TOKEN"]} if env_cfg.get("HF_TOKEN") else None

SEEDVC_VENV = WS / "envs/seedvc"
SEEDVC_PY = str(SEEDVC_VENV / "bin/python")
WANGP_VENV = WS / "envs/wangp"
WANGP_PY = str(WANGP_VENV / "bin/python")

# TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD=1 apenas para Seed-VC (checkpoints legados PyTorch 2.4 em PyTorch 2.10)
svc_extra = dict(extra_hf or {})
svc_extra["TORCH_FORCE_NO_WEIGHTS_ONLY_LOAD"] = "1"
SVC_ENV = build_clean_env(ws=WS, venv_dir=SEEDVC_VENV, extra=svc_extra)
WGP_ENV = build_clean_env(ws=WS, venv_dir=WANGP_VENV, extra=extra_hf)

def write_test_wav(path: Path, freq: float = 220.0, dur: float = 1.4, sr: int = 16000) -> None:
    n = int(sr * dur)
    samples = bytearray()
    for i in range(n):
        t = i / sr
        env = math.sin(math.pi * min(1.0, i / 800.0))
        val = int(12000 * env * (0.65 * math.sin(2 * math.pi * freq * t) + 0.35 * math.sin(2 * math.pi * freq * 2.01 * t)))
        samples.extend(struct.pack("<h", val))
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(bytes(samples))

def verify_wav_not_silent(wav_path: Path) -> tuple[float, float]:
    norm = wav_path.with_suffix(".pcm16.wav")
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(wav_path), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(norm)],
        env=SVC_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True,
    )
    with wave.open(str(norm), "rb") as wf:
        dur = wf.getnframes() / float(wf.getframerate())
        raw = wf.readframes(wf.getnframes())
    arr = array.array("h")
    arr.frombytes(raw)
    peak = max((abs(x) for x in arr), default=0)
    rms = math.sqrt(sum((x / 32768.0) ** 2 for x in arr) / max(1, len(arr)))
    rms_dbfs = 20.0 * math.log10(max(rms, 1e-12))
    if dur < 0.3 or rms_dbfs < -55.0 or peak < 64:
        raise RuntimeError(f"Audio Quality Gate falhou no smoke test: dur={dur:.2f}s rms={rms_dbfs:.1f}dBFS peak={peak}")
    return round(dur, 2), round(rms_dbfs, 1)

def verify_mp4_valid(mp4_path: Path) -> dict:
    if not mp4_path.is_file() or mp4_path.stat().st_size < 10_000:
        raise RuntimeError(f"Video Quality Gate falhou: arquivo ausente ou muito pequeno ({mp4_path})")
    meta = json.loads(subprocess.check_output([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height,codec_name:format=duration",
        "-of", "json", str(mp4_path),
    ], env=WGP_ENV, text=True, timeout=15) or "{}")
    streams = meta.get("streams") or []
    dur = float((meta.get("format") or {}).get("duration") or 0.0)
    if not streams or dur < 0.3:
        raise RuntimeError(f"Video Quality Gate falhou: streams={len(streams)} dur={dur:.2f}s")
    return {
        "duration": round(dur, 2),
        "width": int(streams[0].get("width") or 0),
        "height": int(streams[0].get("height") or 0),
        "bytes": mp4_path.stat().st_size,
    }

t0 = time.time()
p_cuda = subprocess.run([
    WANGP_PY, "-c",
    "import torch; x = torch.randn((2048, 2048), device='cuda', dtype=torch.float16); "
    "y = x @ x; torch.cuda.synchronize(); del x, y; torch.cuda.empty_cache(); "
    "free_b, tot_b = torch.cuda.mem_get_info(0); "
    "print(f'CUDA_L4_OK {torch.cuda.get_device_name(0)} VRAM_livre={free_b/1024**3:.1f}/{tot_b/1024**3:.1f}GB')"
], env=WGP_ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
if p_cuda.returncode != 0:
    raise RuntimeError(p_cuda.stdout)
print(f"[OK] {p_cuda.stdout.strip()} ({time.time() - t0:.2f}s)")

smoke_record = WS / "config/smoke_ok.json"
smoke_record.unlink(missing_ok=True)

if not PRELOAD_WEIGHTS:
    print("[INFO] PRELOAD_WEIGHTS=False: pesos serão baixados sob demanda no primeiro job da VPS.")
else:
    smoke_dir = WS / "tmp/smoke_v4"
    shutil.rmtree(smoke_dir, ignore_errors=True)
    smoke_dir.mkdir(parents=True, exist_ok=True)
    try:
        src_wav = smoke_dir / "src.wav"
        tgt_wav = smoke_dir / "tgt.wav"
        svc_out = smoke_dir / "svc_out"
        svc_out.mkdir(exist_ok=True)
        write_test_wav(src_wav, freq=210.0, dur=1.4)
        write_test_wav(tgt_wav, freq=145.0, dur=1.8)

        t1 = time.time()
        print("Baixando pesos oficiais e validando Seed-VC (--f0-condition False) na L4 GPU...")
        p_svc = subprocess.run([
            SEEDVC_PY, str(WS / "repos/seed-vc/inference.py"),
            "--source", str(src_wav),
            "--target", str(tgt_wav),
            "--output", str(svc_out),
            "--diffusion-steps", "10",
            "--f0-condition", "False",
            "--auto-f0-adjust", "False",
            "--fp16", "True",
        ], cwd=str(WS / "repos/seed-vc"), env=SVC_ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=600)
        out_wavs = sorted(svc_out.glob("*.wav"))
        if p_svc.returncode != 0 or not out_wavs:
            raise RuntimeError(f"Seed-VC smoke test falhou (rc={p_svc.returncode}):\n{(p_svc.stdout or '')[-1800:]}")
        svc_dur, svc_rms = verify_wav_not_silent(out_wavs[0])
        print(f"[OK] Seed-VC validado ({time.time() - t1:.1f}s) -> {out_wavs[0].name} | dur={svc_dur:.2f}s | rms={svc_rms:.1f}dBFS")

        wgp_meta = {}
        if VIDEO_MODEL != "seed_vc_only":
            t2 = time.time()
            print(f"Baixando pesos INT8 e executando micro-render '{VIDEO_MODEL}' via WanGP na L4...")
            (smoke_dir / "wgp_out").mkdir(parents=True, exist_ok=True)
            smoke_img = smoke_dir / "frame.png"
            subprocess.run([
                "ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc2=size=480x832:duration=1:rate=1",
                "-frames:v", "1", str(smoke_img),
            ], env=WGP_ENV, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)

            smoke_script = (
                "import gc, json, sys, torch\n"
                "from pathlib import Path\n"
                "sys.path.insert(0, '/content/ai_studio_v4/repos/WanGP')\n"
                "from shared.api import init\n"
                "s = init(\n"
                "    root=Path('/content/ai_studio_v4/repos/WanGP'),\n"
                "    config_path=Path('/content/ai_studio_v4/config/wgp_config.json'),\n"
                "    output_dir=Path('/content/ai_studio_v4/tmp/smoke_v4/wgp_out'),\n"
                "    cli_args=['--attention', 'sdpa', '--profile', '4'],\n"
                "    console_output=False,\n"
                ")\n"
                f"model_id = {VIDEO_MODEL!r}\n"
                "cfg = s.get_default_settings(model_id)\n"
                "mdef = s.get_model_def(model_id) or {}\n"
                "schema = (s.get_model_schema(model_id) or {}).get('metadata', {})\n"
                "f_min = int(schema.get('frames_minimum') or mdef.get('frames_minimum') or 17)\n"
                "res = '384x640' if 'ltx2' in model_id else '480x832'\n"
                "cfg['model_type'] = model_id\n"
                "cfg['prompt'] = 'Cinematic still person standing in warm candlelit room, subtle natural breathing motion.'\n"
                "cfg['negative_prompt'] = 'watermark, overlaid text'\n"
                "cfg['resolution'] = res\n"
                "cfg['video_length'] = f_min\n"
                f"cfg['image_start'] = {str(smoke_img)!r}\n"
                "cfg['image_prompt_type'] = 'S'\n"
                "if 'longcat' in model_id:\n"
                f"    cfg['image_refs'] = [{str(smoke_img)!r}]\n"
                "    cfg['video_prompt_type'] = 'KI'\n"
                "    cfg['force_fps'] = ''\n"
                "    cfg['sample_solver'] = 'distill'\n"
                "    cfg['num_inference_steps'] = 8\n"
                "    cfg['guidance_scale'] = 1.0\n"
                "    cfg['audio_guidance_scale'] = 1.0\n"
                f"    cfg['audio_guide'] = {str(tgt_wav)!r}\n"
                "    cfg['audio_prompt_type'] = 'A'\n"
                "elif 'ltx2' in model_id:\n"
                "    cfg['video_prompt_type'] = ''\n"
                "    cfg['image_refs'] = None\n"
                "    cfg['force_fps'] = '24'\n"
                "    cfg['num_inference_steps'] = 8\n"
                f"    cfg['audio_guide'] = {str(tgt_wav)!r}\n"
                "    cfg['audio_prompt_type'] = 'AS'\n"
                "elif model_id in ('infinitetalk', 'multitalk'):\n"
                "    cfg['video_prompt_type'] = ''\n"
                "    cfg['image_refs'] = None\n"
                "    cfg['force_fps'] = ''\n"
                "    cfg['num_inference_steps'] = 6\n"
                f"    cfg['audio_guide'] = {str(tgt_wav)!r}\n"
                "    cfg['audio_prompt_type'] = 'A'\n"
                "else:\n"
                "    cfg['video_prompt_type'] = ''\n"
                "    cfg['image_refs'] = None\n"
                "    cfg['force_fps'] = ''\n"
                "    cfg['audio_prompt_type'] = ''\n"
                "    cfg['num_inference_steps'] = 6\n"
                "cfg['overrides'] = (\n"
                "    \"attention_mode='sdpa', memory_profile=4, \"\n"
                "    \"transformer_quantization='int8', text_encoder_quantization='int8'\"\n"
                ")\n"
                "job = s.submit_task(cfg)\n"
                "r = job.result()\n"
                "assert r.success and r.generated_files, 'WanGP falhou: ' + '; '.join(str(e) for e in (r.errors or []))\n"
                "p = Path(r.generated_files[0])\n"
                "assert p.is_file() and p.stat().st_size > 10000, f'Arquivo MP4 inválido: {p}'\n"
                "gc.collect()\n"
                "torch.cuda.synchronize()\n"
                "torch.cuda.empty_cache()\n"
                "free_b, tot_b = torch.cuda.mem_get_info(0)\n"
                "print('WANGP_SMOKE_JSON ' + json.dumps({'path': str(p), 'model': model_id, 'res': res, 'frames': f_min, 'vram_free_gb': round(free_b/1024**3, 1), 'vram_total_gb': round(tot_b/1024**3, 1)}))\n"
            )
            smoke_py = smoke_dir / "run_smoke_wgp.py"
            smoke_py.write_text(smoke_script, encoding="utf-8")
            p_wgp = subprocess.run([WANGP_PY, str(smoke_py)], env=WGP_ENV, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=1500)
            json_line = next((ln for ln in (p_wgp.stdout or "").splitlines() if ln.startswith("WANGP_SMOKE_JSON ")), "")
            if p_wgp.returncode != 0 or not json_line:
                tail = "\n".join((p_wgp.stdout or "").splitlines()[-65:])
                raise RuntimeError(f"WanGP smoke test '{VIDEO_MODEL}' falhou:\n{tail}")
            wgp_res = json.loads(json_line.split(" ", 1)[1])
            wgp_meta = verify_mp4_valid(Path(wgp_res["path"]))
            wgp_meta.update({"res": wgp_res["res"], "frames": wgp_res["frames"], "vram_free_gb": wgp_res["vram_free_gb"]})
            print(
                f"[OK] WanGP '{VIDEO_MODEL}' validado ({time.time() - t2:.1f}s) -> "
                f"{wgp_meta['width']}x{wgp_meta['height']} dur={wgp_meta['duration']}s "
                f"bytes={wgp_meta['bytes']} VRAM_livre={wgp_res['vram_free_gb']}/{wgp_res['vram_total_gb']}GB"
            )

        smoke_record.write_text(json.dumps({
            "ok": True,
            "video_model": VIDEO_MODEL,
            "seed_vc": {"duration": svc_dur, "rms_dbfs": svc_rms},
            "wangp": wgp_meta,
            "timestamp": int(time.time()),
        }, indent=2), encoding="utf-8")
    finally:
        shutil.rmtree(smoke_dir, ignore_errors=True)

    disk_after = round(shutil.disk_usage("/content").free / (1024 ** 3), 1)
    print(f"Pré-download + Smoke Test L4 concluídos com sucesso! Disco /content livre: {disk_after} GB")
'''

    cell4 = f'''# @title 4. Iniciar Servidor Worker Cinema Engine v4.0 + Túnel Cloudflare (Watchdog Auto-Recover)
import json, re, subprocess, sys, time, urllib.request, warnings
from pathlib import Path

warnings.filterwarnings("ignore")

WS = Path("/content/ai_studio_v4")
if str(WS) not in sys.path:
    sys.path.insert(0, str(WS))
from clean_env import build_clean_env  # noqa: E402

env_cfg = json.loads((WS / "config/worker_env.json").read_text(encoding="utf-8"))

SERVER_CODE = {server_code!r}

srv_file = WS / "colab_v4_cinema_server.py"
srv_file.write_text(SERVER_CODE.lstrip("\\n"), encoding="utf-8")

# Bracket trick evita self-kill do subshell bash -lc
subprocess.run(
    ["bash", "-lc", "pkill -f '[c]olab_v4_cinema_server' || true; pkill -f '[c]loudflared' || true"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
)
time.sleep(1)

WANGP_VENV = WS / "envs/wangp"
PY_BIN = str(WANGP_VENV / "bin/python")
AUTH_TOKEN = env_cfg.get("COLAB_AUTH_TOKEN") or ""
HDRS = {{"X-Colab-Token": AUTH_TOKEN}} if AUTH_TOKEN else {{}}

extra_srv = {{
    "PORT": "8787",
    "AI_STUDIO_ROOT": str(WS),
    "AI_STUDIO_VIDEO_MODEL": env_cfg["VIDEO_MODEL"],
    "COLAB_AUTH_TOKEN": AUTH_TOKEN,
}}
if env_cfg.get("HF_TOKEN"):
    extra_srv["HF_TOKEN"] = env_cfg["HF_TOKEN"]

SRV_ENV = build_clean_env(ws=WS, venv_dir=WANGP_VENV, extra=extra_srv)
CF_ENV = build_clean_env(ws=WS)

def probe_health(url: str, timeout: float = 3.0):
    req = urllib.request.Request(url, headers=HDRS)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))

def start_server():
    log_fp = open("/tmp/colab_v4_server.log", "a", encoding="utf-8")
    proc = subprocess.Popen(
        [PY_BIN, str(srv_file)],
        env=SRV_ENV,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    health_data = None
    for _ in range(50):
        if proc.poll() is not None:
            log_fp.flush()
            tail = Path("/tmp/colab_v4_server.log").read_text(encoding="utf-8", errors="ignore")[-2500:]
            raise RuntimeError(f"Servidor FastAPI encerrou prematuramente (rc={{proc.returncode}}):\\n{{tail}}")
        try:
            h = probe_health("http://127.0.0.1:8787/health", timeout=2.0)
            if h.get("ok"):
                health_data = h
                break
        except Exception:
            time.sleep(0.5)
    if not health_data:
        tail = Path("/tmp/colab_v4_server.log").read_text(encoding="utf-8", errors="ignore")[-1500:]
        raise RuntimeError(f"Timeout aguardando http://127.0.0.1:8787/health:\\n{{tail}}")
    if not str(health_data.get("version", "")).startswith("4.0"):
        raise RuntimeError(f"Versão incompatível com vps_novela_runner.py: {{health_data.get('version')}}")
    return proc, log_fp, health_data

def start_tunnel():
    cf_log_path = Path("/tmp/cloudflared_v4.log")
    cf_log_path.write_text("", encoding="utf-8")
    log_fp = open(cf_log_path, "a", encoding="utf-8")
    proc = subprocess.Popen(
        [
            "cloudflared", "tunnel",
            "--no-autoupdate",
            "--protocol", "http2",
            "--url", "http://127.0.0.1:8787",
            "--logfile", str(cf_log_path),
            "--loglevel", "info",
        ],
        env=CF_ENV,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    tunnel_url = ""
    for _ in range(60):
        if proc.poll() is not None:
            tail = cf_log_path.read_text(encoding="utf-8", errors="ignore")[-1200:]
            raise RuntimeError(f"cloudflared encerrou prematuramente (rc={{proc.returncode}}):\\n{{tail}}")
        txt = cf_log_path.read_text(encoding="utf-8", errors="ignore")
        m = re.search(r"https://[a-z0-9-]+\\.trycloudflare\\.com", txt)
        if m:
            tunnel_url = m.group(0)
            break
        time.sleep(0.5)
    if not tunnel_url:
        tail = cf_log_path.read_text(encoding="utf-8", errors="ignore")[-1200:]
        raise RuntimeError(f"Não foi possível obter URL trycloudflare.com em 30s:\\n{{tail}}")

    public_ok = False
    for _ in range(15):
        try:
            h = probe_health(f"{{tunnel_url}}/health", timeout=4.0)
            if h.get("ok"):
                public_ok = True
                break
        except Exception:
            time.sleep(1.0)
    return proc, log_fp, tunnel_url, public_ok

def print_banner(url: str, public_ok: bool, h: dict):
    status_txt = "ONLINE (probe externo OK)" if public_ok else "ATIVO (aguardando propagação DNS)"
    print("\\n====================================================================")
    print(f"PONTE L4 {{h.get('version')}} {{status_txt}}")
    print(f"URL: {{url}}")
    print(
        f"GPU={{h.get('gpu')}} | VRAM={{h.get('vram_free_gb')}}/{{h.get('vram_gb')}}GB | "
        f"Model={{h.get('default_video_model')}} | SmokeOK={{h.get('smoke_verified')}} | "
        f"EnvSanitized={{h.get('env_sanitized')}}"
    )
    print("====================================================================\\n", flush=True)

Path("/tmp/colab_v4_server.log").write_text("", encoding="utf-8")
srv_proc, srv_log, health_data = start_server()
print(
    f"[OK] Worker FastAPI {{health_data.get('version')}} ativo | GPU={{health_data.get('gpu')}} | "
    f"VRAM={{health_data.get('vram_free_gb')}}/{{health_data.get('vram_gb')}}GB | "
    f"model={{health_data.get('default_video_model')}} | ready={{health_data.get('ready')}}"
)

cf_proc, cf_log, tunnel_url, pub_ok = start_tunnel()
print_banner(tunnel_url, pub_ok, health_data)

srv_restarts = 0
last_hb = time.time()
try:
    while True:
        time.sleep(15)
        if srv_proc.poll() is not None:
            srv_restarts += 1
            tail = Path("/tmp/colab_v4_server.log").read_text(encoding="utf-8", errors="ignore")[-1500:]
            print(f"[WARN] Servidor encerrou (rc={{srv_proc.returncode}}, tentativa {{srv_restarts}}/3). Últimas linhas:\\n{{tail}}", flush=True)
            if srv_restarts > 3:
                raise RuntimeError("Servidor FastAPI falhou mais de 3 vezes consecutivas.")
            time.sleep(3)
            srv_proc, srv_log, health_data = start_server()
            print("[OK] Servidor FastAPI recuperado automaticamente.", flush=True)

        if cf_proc.poll() is not None:
            print(f"[WARN] Túnel cloudflared caiu (rc={{cf_proc.returncode}}) — reiniciando túnel...", flush=True)
            time.sleep(2)
            cf_proc, cf_log, tunnel_url, pub_ok = start_tunnel()
            print_banner(tunnel_url, pub_ok, health_data)

        if time.time() - last_hb >= 60:
            last_hb = time.time()
            try:
                h = probe_health("http://127.0.0.1:8787/health", timeout=3.0)
                print(
                    f"[HB {{time.strftime('%H:%M:%S')}}] VRAM Livre: {{h.get('vram_free_gb')}}/{{h.get('vram_gb')}}GB | "
                    f"Disco Livre: {{h.get('cache_free_gb')}}GB | Jobs: {{h.get('active_jobs')}} | URL: {{tunnel_url}}",
                    flush=True,
                )
            except Exception as exc:
                print(f"[WARN {{time.strftime('%H:%M:%S')}}] Falha no heartbeat local: {{exc}}", flush=True)
finally:
    for p in (cf_proc, srv_proc):
        try:
            p.terminate()
        except Exception:
            pass
    for fp in (cf_log, srv_log):
        try:
            fp.close()
        except Exception:
            pass
'''

    return [
        md_cell,
        {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _lines(cell1)},
        {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _lines(cell2)},
        {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _lines(cell3)},
        {"cell_type": "code", "execution_count": None, "metadata": {}, "outputs": [], "source": _lines(cell4)},
    ]


def verify_all(server_code: str) -> None:
    # 1. py_compile + ast.parse no servidor
    py_compile.compile(str(SERVER_PATH), doraise=True)
    ast.parse(server_code, filename=str(SERVER_PATH))
    assert 'SERVER_VERSION = "4.0.3-clean-l4"' in server_code, "SERVER_VERSION deve ser '4.0.3-clean-l4' (prefixo '4.0' para vps_novela_runner.py)!"
    print("[OK] colab_v4_cinema_server.py: py_compile + ast.parse + SERVER_VERSION='4.0.3-clean-l4'")

    # 2. JSON + ast.parse nas 4 células + espelho idêntico + zero URL Markdown corrompida
    raw_primary = NB_PRIMARY.read_text(encoding="utf-8")
    raw_mirror = NB_MIRROR.read_text(encoding="utf-8")
    assert raw_primary == raw_mirror, "Espelhos .ipynb divergiram!"
    parsed = json.loads(raw_primary)
    assert parsed.get("nbformat") == 4, "nbformat deve ser 4"
    code_cells = [c for c in parsed["cells"] if c["cell_type"] == "code"]
    assert len(code_cells) == 4, f"Esperadas 4 células de código, obtidas {len(code_cells)}"
    for idx, c in enumerate(code_cells, start=1):
        src = "".join(c["source"])
        ast.parse(src, filename=f"cell_{idx}.py")
        assert "](http" not in src, f"URL corrompida em formato Markdown na célula {idx}!"
        assert "pkill -f colab_" not in src and "pkill -f cloudflared" not in src, (
            f"pkill sem bracket trick encontrado na célula {idx}!"
        )
    cell2_src = "".join(code_cells[1]["source"])
    assert "https://download.pytorch.org/whl/cu126" in cell2_src and '"torch==2.7.1", "torchaudio==2.7.1"' in cell2_src
    assert '"--no-deps", "--index-url", "https://download.pytorch.org/whl/cpu"' in cell2_src
    assert "torchaudio.save(" in cell2_src and "torchaudio.load(" in cell2_src
    assert "fallback verificado para PyTorch 2.7.1+cu126" in cell2_src
    # Verifica que SERVER_CODE embutido na Célula 4 é exatamente igual ao arquivo standalone
    cell4_tree = ast.parse("".join(code_cells[3]["source"]), filename="cell_4.py")
    embedded_srv = None
    for node in cell4_tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "SERVER_CODE" and isinstance(node.value, ast.Constant):
                    embedded_srv = node.value.value
    assert embedded_srv == server_code, "SERVER_CODE na Célula 4 divergiu de colab_v4_cinema_server.py!"
    print("[OK] Ambos .ipynb idênticos, JSON válido, ast.parse OK nas 4 células, SERVER_CODE 100% sincronizado")

    try:
        import nbformat
        nbformat.validate(parsed)
        print("[OK] nbformat.validate passou 100%")
    except ImportError:
        print("[INFO] nbformat não instalado localmente (validação JSON v4.5 + AST já aprovada)")

    # 3. Simulação de injeção de variáveis tóxicas (clean_env.py)
    ns: dict = {}
    exec(CLEAN_ENV_MODULE, ns)
    toxic_input = {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": "/root",
        "LD_LIBRARY_PATH": "/usr/lib64-nvidia:/usr/local/cuda/lib64",
        "CUDA_VISIBLE_DEVICES": "0",
        "NVIDIA_VISIBLE_DEVICES": "all",
        "COLAB_AUTH_TOKEN": "tok_test_preserved_123",
        "MPLBACKEND": "module://matplotlib_inline.backend_inline",
        "JPY_PARENT_PID": "1234",
        "JPY_SESSION_NAME": "colab.ipynb",
        "PYTHONPATH": "/fake/colab:/env/python",
        "PYTHONHOME": "/fake/home",
        "COLAB_GPU": "L4",
        "COLAB_RELEASE_TAG": "release-colab-2026",
        "LD_PRELOAD": "/usr/lib/libtcmalloc.so",
        "DISPLAY": ":0",
    }
    clean = ns["build_clean_env"](venv_dir=Path("/content/ai_studio_v4/envs/wangp"), base_env=toxic_input)
    assert clean["MPLBACKEND"] == "Agg"
    assert "JPY_PARENT_PID" not in clean and "JPY_SESSION_NAME" not in clean
    assert "PYTHONPATH" not in clean and "PYTHONHOME" not in clean
    assert "COLAB_GPU" not in clean and "COLAB_RELEASE_TAG" not in clean
    assert "LD_PRELOAD" not in clean and "DISPLAY" not in clean
    assert "HF_HUB_ENABLE_HF_TRANSFER" not in clean, "Não forçar hf_transfer globalmente (WanGP usa hf_xet)!"
    assert clean["COLAB_AUTH_TOKEN"] == "tok_test_preserved_123", "COLAB_AUTH_TOKEN foi apagado indevidamente!"
    assert clean["LD_LIBRARY_PATH"] == "/usr/lib64-nvidia:/usr/local/cuda/lib64"
    assert clean["CUDA_VISIBLE_DEVICES"] == "0"
    assert clean["NVIDIA_VISIBLE_DEVICES"] == "all"
    assert clean["PATH"].startswith("/content/ai_studio_v4/envs/wangp/bin:")
    print("[OK] Simulação de ambiente tóxico neutralizada (COLAB_AUTH_TOKEN + CUDA/NVIDIA/LD_LIBRARY_PATH preservados)")

    # 4. Secret scan oficial do repositório
    secret_scan = REPO_ROOT / "scripts" / "secret-scan.py"
    if secret_scan.is_file():
        r = subprocess.run([sys.executable, str(secret_scan)], cwd=str(REPO_ROOT), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"secret-scan.py encontrou violações:\n{r.stdout}\n{r.stderr}")
        print(f"[OK] {r.stdout.strip()}")


def main() -> None:
    if not SERVER_PATH.is_file():
        raise FileNotFoundError(f"Servidor não encontrado em {SERVER_PATH}")
    server_code = SERVER_PATH.read_text(encoding="utf-8")
    nb = {
        "nbformat": 4,
        "nbformat_minor": 5,
        "metadata": {
            "kernelspec": {"display_name": "Python 3", "language": "python", "name": "python3"},
            "language_info": {"name": "python", "version": "3.10"},
            "accelerator": "GPU",
            "colab": {"gpuType": "L4", "provenance": []},
        },
        "cells": build_cells(server_code),
    }
    serialized = json.dumps(nb, indent=1, ensure_ascii=False) + "\n"
    NB_PRIMARY.parent.mkdir(parents=True, exist_ok=True)
    NB_MIRROR.parent.mkdir(parents=True, exist_ok=True)
    NB_PRIMARY.write_text(serialized, encoding="utf-8")
    NB_MIRROR.write_text(serialized, encoding="utf-8")
    verify_all(server_code)


if __name__ == "__main__":
    main()
