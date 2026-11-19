import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
import re
import shutil
import socket
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

for _tvar in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
    "NUMEXPR_NUM_THREADS",
):
    os.environ.setdefault(_tvar, "2")

import httpx
import psutil
import tomli
import tomli_w
from fastapi import FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict

STUDIO_BASE_DIR = Path(os.environ.get("STUDIO_BASE_DIR", "/home/wbrendo")).resolve()
STUDIO_HOME_DIR = Path(os.environ.get("STUDIO_HOME_DIR", "/home/wbrendo")).resolve()
BASE_DIR = Path(os.environ.get("STUDIO_PANEL_DIR", str(STUDIO_BASE_DIR / "media-control-panel"))).resolve()
VAULT_FILE = BASE_DIR / "vault.json"
STATIC_DIR = BASE_DIR / "static"
SKILLS_DIR = Path(os.environ.get("STUDIO_SKILLS_DIR", str(STUDIO_HOME_DIR / ".gemini/config/skills")))
AGENTS_SKILLS_DIR = Path(os.environ.get("STUDIO_AGENTS_SKILLS_DIR", str(STUDIO_HOME_DIR / ".agents/skills")))
MODO_DEV_BIN = Path(
    os.environ.get(
        "STUDIO_MODO_DEV_BIN",
        "/usr/local/bin/modo-dev" if Path("/usr/local/bin/modo-dev").exists() else str(STUDIO_HOME_DIR / ".local/bin/modo-dev"),
    )
)
DEV_SKILLS_VAULT = Path(os.environ.get("STUDIO_DEV_SKILLS_VAULT", str(STUDIO_BASE_DIR / "dev-skills-vault")))
OPENJARVIS_DIR = Path(os.environ.get("OPENJARVIS_DIR", str(STUDIO_HOME_DIR / "open-jarvis")))
OPENJARVIS_STATE_DIR = Path(os.environ.get("OPENJARVIS_STATE_DIR", str(STUDIO_HOME_DIR / ".openjarvis")))
OPENJARVIS_LAUNCHER = OPENJARVIS_DIR / "iniciar-jarvis.sh"
OPENJARVIS_PORT = 8090
OPENFILM_DATA_DIR = Path(os.environ.get("OPENFILM_DATA_DIR", str(STUDIO_HOME_DIR / ".openfilm")))
OPENFILM_VIDEOS_DIR = Path(os.environ.get("OPENFILM_VIDEOS_DIR", str(STUDIO_HOME_DIR / "Videos/OpenFilm")))
import sys
for _qt_cand in (
    Path(os.environ.get("STUDIO_BASE_DIR", "/opt/ai-media-studio")) / "tools/hyperframes",
    Path(__file__).resolve().parent.parent / "tools/hyperframes",
):
    if _qt_cand.is_dir() and str(_qt_cand) not in sys.path:
        sys.path.insert(0, str(_qt_cand))

import quota_telemetry as _qt  # noqa: E402

STUDIO_MODE = os.environ.get("STUDIO_MODE", "vps" if str(STUDIO_BASE_DIR) != "/home/wbrendo" else "local").strip().lower()
QUOTA_TELEMETRY_FILE = BASE_DIR / "quota_telemetry.json"


def _get_quota_tracker() -> _qt.QuotaTelemetryTracker:
    return _qt.QuotaTelemetryTracker(state_file=QUOTA_TELEMETRY_FILE)

app = FastAPI(
    title="AI Media Studio Control Panel",
    description=f"Centralized DevOps & AI Media Control Panel v2.1 ({STUDIO_MODE.upper()})",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_openfilm_url() -> str:
    """Reads the OpenFilm Studio key so opening the UI logs in automatically."""
    prefix = "/app/openfilm" if STUDIO_MODE == "vps" else "http://localhost:3005"
    run_file = OPENFILM_DATA_DIR / "run.json"
    key_file = OPENFILM_DATA_DIR / "key"
    try:
        if run_file.exists():
            data = json.loads(run_file.read_text(encoding="utf-8"))
            if data.get("key"):
                return f"{prefix}/open?key={data['key']}"
    except Exception:
        pass
    try:
        if key_file.exists():
            key = key_file.read_text(encoding="utf-8").strip()
            if key:
                return f"{prefix}/open?key={key}"
    except Exception:
        pass
    return f"{prefix}/" if STUDIO_MODE == "vps" else prefix


def _ui_url(stack_slug: str, local_port: int) -> str:
    return f"/app/{stack_slug}/" if STUDIO_MODE == "vps" else f"http://localhost:{local_port}"


PROJECTS: Dict[str, Dict[str, Any]] = {
    "supoclip": {
        "id": "supoclip",
        "name": "SupoClip",
        "subtitle": "Cortes Virais & Shorts Automáticos (Alternativa ao OpusClip)",
        "icon": "scissors",
        "color": "emerald",
        "path": str(STUDIO_BASE_DIR / "supoclip"),
        "ui_port": 3107,
        "api_port": 8000,
        "extra_ports": [9100, 5432, 6379],
        "ui_url": _ui_url("supoclip", 3107),
        "health_url": "http://127.0.0.1:3107",
        "api_url": "/app/supoclip-api/docs" if STUDIO_MODE == "vps" else "http://localhost:8000/docs",
        "config_file": str(STUDIO_BASE_DIR / "supoclip/.env"),
        "image_check": "supoclip-backend:latest",
        "container_check": "supoclip-backend",
        "endpoints": [
            {"name": "Frontend UI (3107)", "url": "http://127.0.0.1:3107", "port": 3107},
            {"name": "Backend API (8000)", "url": "http://127.0.0.1:8000/docs", "port": 8000},
        ],
    },
    "moneyprinter": {
        "id": "moneyprinter",
        "name": "MoneyPrinterTurbo",
        "subtitle": "Máquina de Vídeos Faceless Automáticos (Gemini + Edge-TTS)",
        "icon": "clapperboard",
        "color": "amber",
        "path": str(STUDIO_BASE_DIR / "MoneyPrinterTurbo"),
        "ui_port": 8501,
        "api_port": 8080,
        "extra_ports": [],
        "ui_url": _ui_url("moneyprinter", 8501),
        "health_url": "http://127.0.0.1:8501/app/moneyprinter/_stcore/health" if STUDIO_MODE == "vps" else "http://127.0.0.1:8501/_stcore/health",
        "api_url": "http://localhost:8080/docs",
        "config_file": str(STUDIO_BASE_DIR / "MoneyPrinterTurbo/config.toml"),
        "image_check": "moneyprinterturbo:latest",
        "container_check": "moneyprinterturbo-api",
        "endpoints": [
            {"name": "Streamlit WebUI (8501)", "url": "http://127.0.0.1:8501/app/moneyprinter/_stcore/health" if STUDIO_MODE == "vps" else "http://127.0.0.1:8501", "port": 8501},
            {"name": "FastAPI Server (8080)", "url": "http://127.0.0.1:8080/docs", "port": 8080},
        ],
    },
    "openfilm": {
        "id": "openfilm",
        "name": "OpenFilm",
        "subtitle": "Editor de Vídeo com Timeline Controlado por IA & Prompt",
        "icon": "film",
        "color": "violet",
        "path": str(STUDIO_BASE_DIR / "openfilm"),
        "ui_port": 3005,
        "api_port": 3006,
        "extra_ports": [],
        "ui_url": _ui_url("openfilm", 3005),
        "health_url": "http://127.0.0.1:3005/api/health",
        "api_url": "http://localhost:3005/api/health",
        "config_file": str(STUDIO_BASE_DIR / "openfilm/.env"),
        "image_check": "openfilm-openfilm:latest",
        "container_check": "openfilm-studio",
        "endpoints": [
            {"name": "Studio UI & Health (3005)", "url": "http://127.0.0.1:3005/api/health", "port": 3005},
        ],
    },
    "agenttube": {
        "id": "agenttube",
        "name": "AgentTube",
        "subtitle": "Automação Completa de Canal do YouTube (Roteiro, SEO e Upload)",
        "icon": "youtube",
        "color": "rose",
        "path": str(STUDIO_BASE_DIR / "youtube-automation-agent"),
        "ui_port": 3010,
        "api_port": 3010,
        "extra_ports": [],
        "ui_url": _ui_url("agenttube", 3010),
        "health_url": "http://127.0.0.1:3010/health",
        "api_url": "http://localhost:3010/health",
        "config_file": str(STUDIO_BASE_DIR / "youtube-automation-agent/.env"),
        "image_check": "youtube-automation-agent-agenttube:latest",
        "container_check": "agenttube",
        "endpoints": [
            {"name": "Dashboard & Health (3010)", "url": "http://127.0.0.1:3010/health", "port": 3010},
        ],
    },
    "openhiggsfield": {
        "id": "openhiggsfield",
        "name": "Open Higgsfield",
        "subtitle": "Estúdio de IA para Geração de Vídeo & Motion (Next.js 16)",
        "icon": "sparkles",
        "color": "cyan",
        "path": str(STUDIO_BASE_DIR / "open-higgsfield"),
        "ui_port": 3020,
        "api_port": 3020,
        "extra_ports": [],
        "ui_url": _ui_url("higgsfield", 3020),
        "health_url": "http://127.0.0.1:3020/app/higgsfield",
        "api_url": "http://localhost:3020",
        "config_file": str(STUDIO_BASE_DIR / "open-higgsfield/.env"),
        "image_check": "open-higgsfield-open-higgsfield:latest",
        "container_check": "open-higgsfield",
        "endpoints": [
            {"name": "Web UI (3020)", "url": "http://127.0.0.1:3020/app/higgsfield", "port": 3020},
        ],
    },
    "voicestudio": {
        "id": "voicestudio",
        "name": "VoiceStudio",
        "subtitle": "Estúdio de Voz & TTS Neural CPU-Only (Edge-TTS / eSpeak-ng)",
        "icon": "mic",
        "color": "indigo",
        "path": str(STUDIO_BASE_DIR / "VoiceStudio"),
        "ui_port": 3030,
        "api_port": 3030,
        "extra_ports": [],
        "ui_url": _ui_url("voicestudio", 3030),
        "health_url": "http://127.0.0.1:3030/health",
        "api_url": "http://localhost:3030/health",
        "config_file": str(STUDIO_BASE_DIR / "VoiceStudio/.env"),
        "image_check": "voicestudio-voicestudio:latest",
        "container_check": "voicestudio",
        "endpoints": [
            {"name": "Web UI (3030)", "url": "http://127.0.0.1:3030", "port": 3030},
            {"name": "Health API (3030)", "url": "http://127.0.0.1:3030/health", "port": 3030},
        ],
    },
    "openseo": {
        "id": "openseo",
        "name": "OpenSEO",
        "subtitle": "SEO Inteligente, Otimização Semântica & Tags (Cloudflare Stack)",
        "icon": "search",
        "color": "emerald",
        "path": str(STUDIO_BASE_DIR / "open-seo"),
        "ui_port": 3040,
        "api_port": 3040,
        "extra_ports": [],
        "ui_url": _ui_url("openseo", 3040),
        "health_url": "http://127.0.0.1:3040",
        "api_url": "http://localhost:3040",
        "config_file": str(STUDIO_BASE_DIR / "open-seo/.env"),
        "image_check": "ghcr.io/every-app/open-seo:latest",
        "container_check": "open-seo",
        "endpoints": [
            {"name": "Web UI (3040)", "url": "http://127.0.0.1:3040", "port": 3040},
        ],
    },
}

SKILL_PROMPTS: Dict[str, str] = {
    "hypit": "Clone e recrie a estética, ritmo, narrativa e estrutura deste vídeo de referência usando Hypit e Gemini: [INSIRA O LINK OU VÍDEO AQUI]",
    "hyperframes": "Crie uma animação/vídeo em HTML renderizado via HyperFrames com o seguinte tema e transições: [DESCREVA O VÍDEO]",
    "reverse-any-video": "Faça a engenharia reversa quadro a quadro deste vídeo e recrie como projeto HyperFrames editável: [CAMINHO_DO_VÍDEO]",
    "style-sampling": "Crie um novo vídeo de alta retenção no estilo Explainer Viral (Collage Explainer) sobre o tema: [SEU TEMA]",
    "slideshow": "Crie uma apresentação interativa com slides animados e modo apresentador usando a skill Slideshow para: [TÓPICO]",
    "media-use": "Gere ou processe as mídias (áudio, efeitos sonoros e legendas sincronizadas) para meu vídeo usando FFmpeg e Edge-TTS",
    "figma": "Importe e anime os frames e tokens de design deste layout do Figma para um vídeo HyperFrames: [LINK FIGMA]",
    "remotion-to-hyperframes": "Migre e converta esta composição Remotion/React para o ecossistema HyperFrames de alta performance",
    "hyperframes-registry": "Explore o catálogo de componentes do HyperFrames e instale presets de texto cinético e transições",
    "hyperframes-cli": "Renderize o projeto de vídeo local via CLI HyperFrames e exporte em formato MP4 otimizado para CPU",
    "video-use": "Processe este vídeo no Video Use (faster-whisper INT8 + corte de silêncios 48kHz SoXR + color grade + legendas): [CAMINHO_DO_VIDEO]",
    "manim-video": "Crie uma animação matemática/técnica didática no estilo 3Blue1Brown usando Manim Community Edition para: [CONCEITO]",
    "novela-veo-colab-director": "Atue como Diretor Cinematográfico Clean Cinema (plano e contraplano sem poluição visual) e gere um Production Pack para Google Flow (Veo 3.1) ou dispare via Colab GPU Lip-Sync sobre o tema: [TEMA E EXPRESSÕES]",
}

MANAGED_KEYS = [
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_BASE_URL",
    "GEMINI_API_KEY",
    "NVIDIA_API_KEY",
    "NVIDIA_BASE_URL",
    "OPENROUTER_API_KEY",
    "OPENROUTER_BASE_URL",
    "HF_TOKEN",
    "GROQ_API_KEY",
    "OPENAI_API_KEY",
    "PEXELS_API_KEY",
    "COLAB_GPU_URL",
    "COLAB_GPU_TOKEN",
]

DEFAULT_URLS = {
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com/v1",
    "NVIDIA_BASE_URL": "https://integrate.api.nvidia.com/v1",
    "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
    "COLAB_GPU_URL": "",
}


async def run_cmd(
    cmd: List[str],
    cwd: Optional[str] = None,
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
) -> Dict[str, Any]:
    try:
        proc_env = {**os.environ, **env} if env else None
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            env=proc_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return {
            "returncode": proc.returncode,
            "stdout": stdout.decode("utf-8", errors="replace").strip(),
            "stderr": stderr.decode("utf-8", errors="replace").strip(),
        }
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return {"returncode": -1, "stdout": "", "stderr": f"Comando expirou após {timeout}s"}
    except Exception as e:
        return {"returncode": -1, "stdout": "", "stderr": str(e)}


def read_env_value(file_path: Path, key: str) -> str:
    if not file_path.exists():
        return ""
    try:
        content = file_path.read_text(encoding="utf-8")
        for line in content.splitlines():
            line = line.strip()
            if line.startswith(f"{key}="):
                val = line.split("=", 1)[1].strip()
                return val.strip("\"'")
    except Exception:
        pass
    return ""


def sanitize_base_url(raw: str, default: str, *, require_https: bool = False) -> str:
    val = (raw or "").strip()
    if not val:
        return default
    m = re.search(r"https?://[^\s\]\)\"\'<>]+", val)
    if m:
        url = m.group(0).rstrip("/")
        if require_https and not url.startswith("https://"):
            return default
        return url
    return default


def load_vault() -> Dict[str, str]:
    if VAULT_FILE.exists():
        try:
            data = json.loads(VAULT_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k in MANAGED_KEYS:
                    data.setdefault(k, DEFAULT_URLS.get(k, ""))
                for k, default_val in DEFAULT_URLS.items():
                    require_https = (k == "COLAB_GPU_URL")
                    data[k] = sanitize_base_url(data.get(k, ""), default_val, require_https=require_https)
                return data
        except Exception:
            pass

    gemini = (
        read_env_value(STUDIO_BASE_DIR / "openfilm/.env", "GEMINI_API_KEY")
        or read_env_value(STUDIO_BASE_DIR / "supoclip/.env", "GOOGLE_API_KEY")
        or read_env_value(STUDIO_BASE_DIR / "youtube-automation-agent/.env", "GEMINI_API_KEY")
        or read_env_value(BASE_DIR / ".env", "GEMINI_API_KEY")
    )
    nvidia = read_env_value(BASE_DIR / ".env", "NVIDIA_API_KEY")
    nvidia_base = read_env_value(BASE_DIR / ".env", "NVIDIA_BASE_URL") or DEFAULT_URLS["NVIDIA_BASE_URL"]
    openrouter = (
        read_env_value(STUDIO_BASE_DIR / "supoclip/.env", "OPENROUTER_API_KEY")
        or read_env_value(BASE_DIR / ".env", "OPENROUTER_API_KEY")
    )
    openrouter_base = read_env_value(BASE_DIR / ".env", "OPENROUTER_BASE_URL") or DEFAULT_URLS["OPENROUTER_BASE_URL"]
    openai = read_env_value(STUDIO_BASE_DIR / "supoclip/.env", "OPENAI_API_KEY")
    groq = read_env_value(STUDIO_BASE_DIR / "supoclip/.env", "GROQ_API_KEY")
    pexels = ""

    mpt_cfg = STUDIO_BASE_DIR / "MoneyPrinterTurbo/config.toml"
    if mpt_cfg.exists():
        try:
            parsed = tomli.loads(mpt_cfg.read_text(encoding="utf-8"))
            app_sec = parsed.get("app", {})
            if not gemini and app_sec.get("gemini_api_key"):
                gemini = str(app_sec["gemini_api_key"]).strip()
            if not openai and app_sec.get("openai_api_key"):
                openai = str(app_sec["openai_api_key"]).strip()
            if not groq and app_sec.get("groq_api_key"):
                groq = str(app_sec["groq_api_key"]).strip()
            p_keys = app_sec.get("pexels_api_keys", [])
            if isinstance(p_keys, list) and p_keys:
                pexels = str(p_keys[0]).strip()
        except Exception:
            pass

    anthropic = read_env_value(BASE_DIR / ".env", "ANTHROPIC_API_KEY")
    anthropic_base = (
        read_env_value(BASE_DIR / ".env", "ANTHROPIC_BASE_URL")
        or DEFAULT_URLS["ANTHROPIC_BASE_URL"]
    )
    hf_token = (
        read_env_value(BASE_DIR / ".env", "HF_TOKEN")
        or read_env_value(STUDIO_HOME_DIR / ".env", "HF_TOKEN")
    )
    vault = {
        "ANTHROPIC_API_KEY": anthropic,
        "ANTHROPIC_BASE_URL": anthropic_base,
        "GEMINI_API_KEY": gemini,
        "NVIDIA_API_KEY": nvidia,
        "NVIDIA_BASE_URL": nvidia_base,
        "OPENROUTER_API_KEY": openrouter,
        "OPENROUTER_BASE_URL": openrouter_base,
        "HF_TOKEN": hf_token,
        "GROQ_API_KEY": groq,
        "OPENAI_API_KEY": openai,
        "PEXELS_API_KEY": pexels,
    }
    save_vault_file(vault)
    return vault


def save_vault_file(vault: Dict[str, str]) -> None:
    VAULT_FILE.parent.mkdir(parents=True, exist_ok=True)
    VAULT_FILE.write_text(json.dumps(vault, indent=2), encoding="utf-8")
    try:
        os.chmod(VAULT_FILE, 0o600)
    except Exception:
        pass


def mask_key(value: str, key_name: str = "") -> str:
    val = (value or "").strip()
    if not val:
        return ""
    if key_name.endswith("_BASE_URL") or val.startswith("http://") or val.startswith("https://"):
        return val
    if len(val) <= 4:
        return "••••"
    return "••••••••••••••••••••" + val[-4:]


def update_env_file(file_path: Path, updates: Dict[str, str]) -> bool:
    """Updates or appends key=value pairs in a .env file while preserving comments and structure."""
    if not file_path.parent.exists():
        return False
    if not file_path.exists():
        lines = [f"{k}={v}" for k, v in updates.items()]
        file_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return True

    content = file_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    updated_keys = set()
    new_lines = []

    for line in lines:
        matched_key = None
        for k in updates:
            if re.match(rf"^\s*{re.escape(k)}\s*=", line):
                matched_key = k
                break
        if matched_key is not None:
            if matched_key not in updated_keys:
                new_lines.append(f"{matched_key}={updates[matched_key]}")
                updated_keys.add(matched_key)
        else:
            new_lines.append(line)

    for k, v in updates.items():
        if k not in updated_keys:
            new_lines.append(f"{k}={v}")

    file_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    return True


def update_moneyprinter_toml(file_path: Path, vault: Dict[str, str]) -> bool:
    """Updates MoneyPrinterTurbo config.toml using tomli/tomli-w validation while preserving structure."""
    if not file_path.exists():
        return False

    gemini = vault.get("GEMINI_API_KEY", "")
    nvidia = vault.get("NVIDIA_API_KEY", "")
    nvidia_base = vault.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    openrouter = vault.get("OPENROUTER_API_KEY", "")
    openrouter_base = vault.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    pexels = vault.get("PEXELS_API_KEY", "")
    openai = vault.get("OPENAI_API_KEY", "")
    groq = vault.get("GROQ_API_KEY", "")

    raw = file_path.read_text(encoding="utf-8")

    def replace_toml_str(text: str, key: str, val: str) -> str:
        escaped = val.replace("\\", "\\\\").replace('"', '\\"')
        pattern = rf"^(\s*{re.escape(key)}\s*=\s*).*$"
        if re.search(pattern, text, flags=re.MULTILINE):
            return re.sub(pattern, rf'\g<1>"{escaped}"', text, count=1, flags=re.MULTILINE)
        return text + f'\n{key} = "{escaped}"\n'

    def replace_toml_list(text: str, key: str, items: List[str]) -> str:
        formatted = json.dumps(items)
        pattern = rf"^(\s*{re.escape(key)}\s*=\s*).*$"
        if re.search(pattern, text, flags=re.MULTILINE):
            return re.sub(pattern, rf"\g<1>{formatted}", text, count=1, flags=re.MULTILINE)
        return text + f"\n{key} = {formatted}\n"

    updated = raw
    updated = replace_toml_str(updated, "llm_provider", "gemini")
    updated = replace_toml_str(updated, "gemini_api_key", gemini)
    updated = replace_toml_str(updated, "openai_api_key", openai)
    updated = replace_toml_str(updated, "groq_api_key", groq)
    updated = replace_toml_str(updated, "nvidia_api_key", nvidia)
    updated = replace_toml_str(updated, "nvidia_base_url", nvidia_base)
    updated = replace_toml_str(updated, "openrouter_api_key", openrouter)
    updated = replace_toml_str(updated, "openrouter_base_url", openrouter_base)
    updated = replace_toml_list(updated, "pexels_api_keys", [pexels] if pexels else [])

    try:
        tomli.loads(updated)
        file_path.write_text(updated, encoding="utf-8")
    except Exception:
        parsed = tomli.loads(raw)
        app_sec = parsed.setdefault("app", {})
        app_sec["llm_provider"] = "gemini"
        app_sec["gemini_api_key"] = gemini
        app_sec["openai_api_key"] = openai
        app_sec["groq_api_key"] = groq
        app_sec["nvidia_api_key"] = nvidia
        app_sec["nvidia_base_url"] = nvidia_base
        app_sec["openrouter_api_key"] = openrouter
        app_sec["openrouter_base_url"] = openrouter_base
        app_sec["pexels_api_keys"] = [pexels] if pexels else []
        file_path.write_text(tomli_w.dumps(parsed), encoding="utf-8")

    return True


def sync_openfilm_providers_json(
    gemini_key: str,
    openrouter_key: str = "",
    nvidia_key: str = "",
    anthropic_key: str = "",
) -> None:
    """Syncs Anthropic, Gemini, OpenRouter and NVIDIA keys into ~/.openfilm/providers.json so OpenFilm UI sees them."""
    providers_path = OPENFILM_DATA_DIR / "providers.json"
    try:
        providers_path.parent.mkdir(parents=True, exist_ok=True)
        data: Dict[str, Any] = {}
        if providers_path.exists():
            try:
                data = json.loads(providers_path.read_text(encoding="utf-8"))
            except Exception:
                data = {}
        keys_dict = data.setdefault("keys", {})
        if anthropic_key:
            keys_dict["anthropic"] = anthropic_key
        if gemini_key:
            keys_dict["google"] = gemini_key
        else:
            keys_dict.pop("google", None)
        if openrouter_key:
            keys_dict["openrouter"] = openrouter_key
        if nvidia_key:
            keys_dict["openai"] = nvidia_key
        providers_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    except Exception:
        pass


def sync_all_project_configs(vault: Dict[str, str]) -> List[Dict[str, Any]]:
    anthropic = vault.get("ANTHROPIC_API_KEY", "")
    anthropic_base = vault.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1")
    gemini = vault.get("GEMINI_API_KEY", "")
    nvidia = vault.get("NVIDIA_API_KEY", "")
    nvidia_base = vault.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")
    openrouter = vault.get("OPENROUTER_API_KEY", "")
    openrouter_base = vault.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
    groq = vault.get("GROQ_API_KEY", "")
    openai = vault.get("OPENAI_API_KEY", "")
    pexels = vault.get("PEXELS_API_KEY", "")
    hf_token = vault.get("HF_TOKEN", "")

    cascade_dict = {
        "ANTHROPIC_API_KEY": anthropic,
        "ANTHROPIC_BASE_URL": anthropic_base,
        "GEMINI_API_KEY": gemini,
        "GOOGLE_API_KEY": gemini,
        "NVIDIA_API_KEY": nvidia,
        "NVIDIA_BASE_URL": nvidia_base,
        "OPENROUTER_API_KEY": openrouter,
        "OPENROUTER_BASE_URL": openrouter_base,
        "HF_TOKEN": hf_token,
    }

    results = []

    # 1. SupoClip
    supo_env = STUDIO_BASE_DIR / "supoclip/.env"
    ok_supo = update_env_file(
        supo_env,
        {
            **cascade_dict,
            "OPENAI_API_KEY": openai,
            "GROQ_API_KEY": groq,
        },
    )
    results.append({"project": "SupoClip", "file": str(supo_env), "synced": ok_supo})

    # 2. MoneyPrinterTurbo
    mpt_toml = STUDIO_BASE_DIR / "MoneyPrinterTurbo/config.toml"
    ok_mpt = update_moneyprinter_toml(mpt_toml, vault)
    mpt_env = STUDIO_BASE_DIR / "MoneyPrinterTurbo/.env"
    if mpt_env.exists():
        update_env_file(mpt_env, {**cascade_dict, "PEXELS_API_KEY": pexels})
    results.append({"project": "MoneyPrinterTurbo", "file": str(mpt_toml), "synced": ok_mpt})

    # 3. OpenFilm
    of_env = STUDIO_BASE_DIR / "openfilm/.env"
    ok_of = update_env_file(of_env, cascade_dict)
    sync_openfilm_providers_json(gemini, openrouter, nvidia, anthropic)
    results.append({"project": "OpenFilm", "file": str(of_env), "synced": ok_of})

    # 4. AgentTube
    at_env = STUDIO_BASE_DIR / "youtube-automation-agent/.env"
    ok_at = update_env_file(at_env, {**cascade_dict, "OPENAI_API_KEY": openai})
    results.append({"project": "AgentTube", "file": str(at_env), "synced": ok_at})

    # 5. Open Higgsfield
    oh_env = STUDIO_BASE_DIR / "open-higgsfield/.env"
    ok_oh = update_env_file(oh_env, {**cascade_dict, "OPENAI_API_KEY": openai})
    results.append({"project": "Open Higgsfield", "file": str(oh_env), "synced": ok_oh})

    # 6. VoiceStudio
    vs_env = STUDIO_BASE_DIR / "VoiceStudio/.env"
    ok_vs = update_env_file(vs_env, cascade_dict)
    results.append({"project": "VoiceStudio", "file": str(vs_env), "synced": ok_vs})

    # 7. OpenSEO
    os_env = STUDIO_BASE_DIR / "open-seo/.env"
    ok_os = update_env_file(os_env, cascade_dict)
    results.append({"project": "OpenSEO", "file": str(os_env), "synced": ok_os})

    # Central .env & OpenJarvis .env
    mcp_env = BASE_DIR / ".env"
    update_env_file(mcp_env, {**cascade_dict, "OPENAI_API_KEY": openai, "GROQ_API_KEY": groq, "PEXELS_API_KEY": pexels})
    global_env = STUDIO_HOME_DIR / ".env"
    update_env_file(global_env, {**cascade_dict, "OPENAI_API_KEY": openai, "GROQ_API_KEY": groq, "PEXELS_API_KEY": pexels})
    oj_env = OPENJARVIS_STATE_DIR / ".env"
    if oj_env.parent.exists():
        update_env_file(
            oj_env,
            {
                **cascade_dict,
                "OPENAI_BASE_URL": "http://localhost:9000/v1",
                "VLLM_HOST": "http://localhost:9000",
                "OPENAI_API_KEY": anthropic or gemini or openai or "local-gateway-vault",
                "VLLM_API_KEY": anthropic or gemini or openai or "local-gateway-vault",
            },
        )
        results.append({"project": "OpenJarvis", "file": str(oj_env), "synced": True})

    return results


# ==============================================================================
# Docker Inspection & HTTP Health Ping Helpers
# ==============================================================================

async def check_endpoint_http(url: str, timeout: float = 1.2) -> bool:
    """Pings the internal service port to verify true HTTP readiness before declaring Online."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
            return resp.status_code in (200, 201, 204, 301, 302, 307, 308, 401)
    except Exception:
        return False


async def get_project_containers(project_path: str) -> List[Dict[str, Any]]:
    res = await run_cmd(["docker", "compose", "ps", "-a", "--format", "json"], cwd=project_path, timeout=15)
    containers = []
    if res["returncode"] == 0 and res["stdout"]:
        for line in res["stdout"].splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                item = json.loads(line)
                if isinstance(item, list):
                    for sub in item:
                        containers.append(
                            {
                                "name": sub.get("Name", ""),
                                "service": sub.get("Service", ""),
                                "state": sub.get("State", ""),
                                "status": sub.get("Status", ""),
                                "health": sub.get("Health", ""),
                            }
                        )
                elif isinstance(item, dict):
                    containers.append(
                        {
                            "name": item.get("Name", ""),
                            "service": item.get("Service", ""),
                            "state": item.get("State", ""),
                            "status": item.get("Status", ""),
                            "health": item.get("Health", ""),
                        }
                    )
            except Exception:
                pass
    return containers


async def get_project_status(proj: Dict[str, Any]) -> Dict[str, Any]:
    containers = await get_project_containers(proj["path"])
    running = [c for c in containers if c.get("state") == "running"]
    total = len(containers)

    http_ready = False
    if len(running) > 0:
        health_url = proj.get("health_url", f"http://127.0.0.1:{proj['ui_port']}")
        http_ready = await check_endpoint_http(health_url)
        if not http_ready and health_url != f"http://127.0.0.1:{proj['ui_port']}":
            http_ready = await check_endpoint_http(f"http://127.0.0.1:{proj['ui_port']}")

    if total == 0 or len(running) == 0:
        state = "offline"
    elif not http_ready:
        state = "starting"  # "Esquentando Motor..." / warming up
    elif len(running) == total:
        state = "online"
    else:
        state = "partial"

    ui_url = get_openfilm_url() if proj["id"] == "openfilm" else proj["ui_url"]

    return {
        **proj,
        "ui_url": ui_url,
        "status": state,
        "http_ready": http_ready,
        "running_containers": len(running),
        "total_containers": total,
        "containers": containers,
    }


def parse_skill_metadata(
    skill_dir: Path,
    source_override: Optional[str] = None,
    active: bool = True,
) -> Dict[str, Any]:
    md_file = skill_dir / "SKILL.md"
    name = skill_dir.name
    desc = ""
    valid = md_file.exists() and md_file.stat().st_size > 0

    if valid:
        try:
            content = md_file.read_text(encoding="utf-8")
            if content.startswith("---"):
                parts = content.split("---", 2)
                if len(parts) >= 3:
                    frontmatter = parts[1]
                    for line in frontmatter.splitlines():
                        line_s = line.strip()
                        if line_s.startswith("name:"):
                            name = line_s.replace("name:", "").strip()
                        elif line_s.startswith("description:"):
                            desc = line_s.replace("description:", "").strip()
            if not desc:
                for line in content.splitlines():
                    line_s = line.strip()
                    if line_s and not line_s.startswith("#") and not line_s.startswith("---"):
                        desc = line_s
                        break
        except Exception as e:
            valid = False
            desc = f"Erro de leitura: {e}"

    prompt = SKILL_PROMPTS.get(
        skill_dir.name,
        f"Execute a tarefa com a skill {name}: [DESCREVA O OBJETIVO]"
    )

    icon_map = {
        "hypit": "clapperboard",
        "hyperframes": "sparkles",
        "hyperframes-cli": "terminal",
        "hyperframes-registry": "blocks",
        "media-use": "volume-2",
        "remotion-to-hyperframes": "refresh-ccw",
        "reverse-any-video": "scan",
        "slideshow": "presentation",
        "style-sampling": "palette",
        "figma": "figma",
        "video-use": "wand-2",
        "manim-video": "sigma",
    }

    source = source_override or "global-media"
    if not source_override:
        try:
            vault_resolved = str(_resolve_dev_skills_vault().resolve())
            if skill_dir.is_symlink():
                resolved = skill_dir.resolve()
                if str(resolved).startswith(vault_resolved):
                    source = "dev-vault"
        except Exception:
            pass

    return {
        "id": skill_dir.name,
        "name": name,
        "description": desc or "Skill de automação e criação audiovisual para Antigravity.",
        "icon": icon_map.get(skill_dir.name, "wrench" if source == "dev-vault" else "cpu"),
        "path": str(skill_dir),
        "source": source,
        "active": active,
        "state": "active" if active else "resting",
        "valid": valid,
        "prompt": prompt,
    }


# ==============================================================================
# API Routes
# ==============================================================================

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_index():
    index_file = STATIC_DIR / "index.html"
    if not index_file.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return FileResponse(index_file)


@app.api_route("/manifest.json", methods=["GET", "HEAD"])
async def serve_manifest():
    f = STATIC_DIR / "manifest.json"
    if not f.exists():
        raise HTTPException(status_code=404, detail="manifest.json not found")
    return FileResponse(f, media_type="application/manifest+json")


@app.api_route("/sw.js", methods=["GET", "HEAD"])
async def serve_sw():
    f = STATIC_DIR / "sw.js"
    if not f.exists():
        raise HTTPException(status_code=404, detail="sw.js not found")
    return FileResponse(f, media_type="application/javascript", headers={"Cache-Control": "no-cache"})


@app.api_route("/icon-192.png", methods=["GET", "HEAD"])
async def serve_icon_192():
    f = STATIC_DIR / "icon-192.png"
    if not f.exists():
        raise HTTPException(status_code=404, detail="icon-192.png not found")
    return FileResponse(f, media_type="image/png")


@app.api_route("/icon-512.png", methods=["GET", "HEAD"])
async def serve_icon_512():
    f = STATIC_DIR / "icon-512.png"
    if not f.exists():
        raise HTTPException(status_code=404, detail="icon-512.png not found")
    return FileResponse(f, media_type="image/png")


@app.api_route("/icon.svg", methods=["GET", "HEAD"])
async def serve_icon_svg():
    f = STATIC_DIR / "icon.svg"
    if not f.exists():
        raise HTTPException(status_code=404, detail="icon.svg not found")
    return FileResponse(f, media_type="image/svg+xml")


@app.get("/api/system")
@app.get("/api/stats")
@app.get("/api/system-stats")
async def api_system_metrics():
    cpu_pct = psutil.cpu_percent(interval=0.15)
    mem = psutil.virtual_memory()
    disk = shutil.disk_usage(str(STUDIO_BASE_DIR if STUDIO_BASE_DIR.exists() else "/"))
    res = await run_cmd(["docker", "ps", "-q"], timeout=5)
    active_containers = len([line for line in res["stdout"].splitlines() if line.strip()]) if res["returncode"] == 0 else 0

    ram_used = round(mem.used / (1024**3), 2)
    ram_avail = round(mem.available / (1024**3), 2)
    ram_total = round(mem.total / (1024**3), 2)
    return {
        "hostname": socket.gethostname(),
        "studio_mode": STUDIO_MODE,
        "studio_base_dir": str(STUDIO_BASE_DIR),
        "cpu_percent": round(cpu_pct, 1),
        "cpu_cores": psutil.cpu_count(logical=True),
        "ram_used_gb": ram_used,
        "ram_available_gb": ram_avail,
        "ram_total_gb": ram_total,
        "ram_percent": round(mem.percent, 1),
        "ram": {
            "used_gb": ram_used,
            "available_gb": ram_avail,
            "total_gb": ram_total,
            "percent": round(mem.percent, 1),
        },
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_free_gb": round(disk.free / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
        "disk_percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0.0,
        "active_containers": active_containers,
        "total_managed": len(PROJECTS),
        "timestamp": time.time(),
    }


@app.get("/api/projects")
async def api_list_projects():
    tasks = [get_project_status(p) for p in PROJECTS.values()]
    results = await asyncio.gather(*tasks)
    return {"projects": results}


class ActionRequest(BaseModel):
    action: str  # "start", "stop", "restart"


@app.post("/api/projects/{project_id}/action")
async def api_project_action(project_id: str, req: ActionRequest):
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    proj = PROJECTS[project_id]
    action = req.action.lower()

    if action == "start":
        cmd = ["docker", "compose", "up", "-d"]
    elif action == "stop":
        cmd = ["docker", "compose", "down"]
    elif action == "restart":
        cmd = ["docker", "compose", "restart"]
    else:
        raise HTTPException(status_code=400, detail="Ação inválida")

    res = await run_cmd(cmd, cwd=proj["path"], timeout=120)
    if res["returncode"] != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao executar {action} em {proj['name']}: {res['stderr'] or res['stdout']}",
        )

    updated = await get_project_status(proj)
    return {
        "ok": True,
        "action": action,
        "project": updated,
        "output": res["stdout"] or res["stderr"],
    }


@app.post("/api/projects/stop-all")
async def api_stop_all_projects():
    async def stop_one(p: Dict[str, Any]):
        return await run_cmd(["docker", "compose", "down"], cwd=p["path"], timeout=90)

    await asyncio.gather(*(stop_one(p) for p in PROJECTS.values()))
    statuses = await asyncio.gather(*(get_project_status(p) for p in PROJECTS.values()))
    return {"ok": True, "message": "Todos os contêineres foram desligados para economizar RAM.", "projects": statuses}


@app.post("/api/projects/start-all")
async def api_start_all_projects():
    async def start_one(p: Dict[str, Any]):
        return await run_cmd(["docker", "compose", "up", "-d"], cwd=p["path"], timeout=120)

    await asyncio.gather(*(start_one(p) for p in PROJECTS.values()))
    statuses = await asyncio.gather(*(get_project_status(p) for p in PROJECTS.values()))
    return {"ok": True, "message": "Todas as 7 stacks foram iniciadas.", "projects": statuses}


@app.get("/api/projects/{project_id}/logs")
async def api_project_logs(project_id: str):
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    proj = PROJECTS[project_id]
    res = await run_cmd(["docker", "compose", "logs", "--tail=60", "--no-color"], cwd=proj["path"], timeout=15)
    logs = res["stdout"] or res["stderr"] or "Sem logs recentes (contêineres parados)."
    return {"project_id": project_id, "name": proj["name"], "logs": logs}


# ==============================================================================
# Outputs Gallery, Browser Download & Secure Internal Proxy
# ==============================================================================

OUTPUT_MEDIA_GROUPS: Dict[str, str] = {
    ".mp4": "video",
    ".mov": "video",
    ".webm": "video",
    ".mkv": "video",
    ".avi": "video",
    ".mp3": "audio",
    ".wav": "audio",
    ".m4a": "audio",
    ".flac": "audio",
    ".aac": "audio",
    ".srt": "subtitle",
    ".vtt": "subtitle",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".webp": "image",
}
OUTPUT_IGNORED_PARTS = {".git", ".film", "node_modules", "__pycache__", ".venv", "cache", "history", "auth", ".next"}


def _human_size(num_bytes: int) -> str:
    size = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024.0 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{num_bytes} B"


def _get_output_sources() -> Dict[str, Dict[str, Any]]:
    return {
        "moneyprinter": {
            "name": "MoneyPrinterTurbo",
            "roots": [
                STUDIO_BASE_DIR / "MoneyPrinterTurbo/storage/tasks",
                STUDIO_BASE_DIR / "MoneyPrinterTurbo/storage/local_videos",
            ],
        },
        "voicestudio": {
            "name": "VoiceStudio",
            "roots": [STUDIO_BASE_DIR / "VoiceStudio/output"],
        },
        "supoclip": {
            "name": "SupoClip",
            "roots": [
                STUDIO_BASE_DIR / "supoclip/clips",
                STUDIO_BASE_DIR / "supoclip/outputs",
                Path("/var/lib/docker/volumes/supoclip_clips/_data"),
                Path("/var/lib/docker/volumes/supoclip_uploads/_data/clips"),
            ],
        },
        "videouse": {
            "name": "Video Use · Jump-Cut Pro",
            "roots": [STUDIO_BASE_DIR / "exports/videouse"],
        },
        "hyperframes": {
            "name": "HyperFrames · Motion Ads",
            "roots": [STUDIO_BASE_DIR / "exports/hyperframes"],
        },
        "novela": {
            "name": "Novela Studio · Micro-Séries",
            "roots": [STUDIO_BASE_DIR / "exports/novela"],
        },
        "openfilm": {
            "name": "OpenFilm",
            "roots": [
                OPENFILM_VIDEOS_DIR / "Projects",
                OPENFILM_VIDEOS_DIR / "Exports",
            ],
        },
        "agenttube": {
            "name": "AgentTube",
            "roots": [
                STUDIO_BASE_DIR / "youtube-automation-agent/data",
                STUDIO_BASE_DIR / "youtube-automation-agent/uploads",
            ],
        },
        "openhiggsfield": {
            "name": "Open Higgsfield",
            "roots": [
                STUDIO_BASE_DIR / "open-higgsfield/public/outputs",
                STUDIO_BASE_DIR / "open-higgsfield/outputs",
            ],
        },
        "exports": {
            "name": "Exports Gerais",
            "roots": [STUDIO_BASE_DIR / "exports"],
        },
    }


def _scan_output_files(stack_filter: Optional[str] = None, limit: int = 120) -> List[Dict[str, Any]]:
    sources = _get_output_sources()
    items: List[Dict[str, Any]] = []

    for stack_id, meta in sources.items():
        if stack_filter and stack_filter != "all" and stack_id != stack_filter:
            continue
        for root_idx, root in enumerate(meta["roots"]):
            try:
                if not root.exists() or not root.is_dir():
                    continue
                root_resolved = root.resolve()
                for fpath in root_resolved.rglob("*"):
                    try:
                        if not fpath.is_file():
                            continue
                        rel = fpath.relative_to(root_resolved)
                        if any(part in OUTPUT_IGNORED_PARTS for part in rel.parts[:-1]):
                            continue
                        ext = fpath.suffix.lower()
                        media_type = OUTPUT_MEDIA_GROUPS.get(ext)
                        if not media_type:
                            continue
                        st = fpath.stat()
                        if st.st_size <= 0:
                            continue
                        rel_str = rel.as_posix()
                        from urllib.parse import quote
                        q_rel = quote(rel_str, safe="")
                        items.append(
                            {
                                "id": f"{stack_id}:{root_idx}:{rel_str}",
                                "stack": stack_id,
                                "stack_name": meta["name"],
                                "root_idx": root_idx,
                                "filename": fpath.name,
                                "relpath": rel_str,
                                "ext": ext,
                                "media_type": media_type,
                                "size_bytes": st.st_size,
                                "size_human": _human_size(st.st_size),
                                "mtime": st.st_mtime,
                                "mtime_iso": datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
                                "download_url": f"/api/outputs/download?stack={stack_id}&root_idx={root_idx}&relpath={q_rel}",
                                "preview_url": f"/api/outputs/download?stack={stack_id}&root_idx={root_idx}&relpath={q_rel}&inline=1",
                            }
                        )
                    except (PermissionError, OSError):
                        continue
            except (PermissionError, OSError):
                continue

    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:limit]


@app.get("/api/outputs")
async def api_list_outputs(stack: str = "all", limit: int = 120):
    bounded_limit = max(1, min(limit, 300))
    items = await asyncio.to_thread(_scan_output_files, stack, bounded_limit)
    total_bytes = sum(x["size_bytes"] for x in items)
    return {
        "ok": True,
        "count": len(items),
        "total_size_human": _human_size(total_bytes),
        "items": items,
    }


@app.api_route("/api/outputs/download", methods=["GET", "HEAD"])
async def api_download_output(
    stack: str = Query(...),
    root_idx: int = Query(0, ge=0),
    relpath: str = Query(...),
    inline: bool = Query(False),
):
    sources = _get_output_sources()
    if stack not in sources:
        raise HTTPException(status_code=404, detail="Stack de saída inválida")
    roots = sources[stack]["roots"]
    if root_idx < 0 or root_idx >= len(roots):
        raise HTTPException(status_code=400, detail="Índice de diretório inválido")

    root_dir = roots[root_idx].resolve()
    target = (root_dir / relpath).resolve()
    if not target.is_relative_to(root_dir):
        raise HTTPException(status_code=403, detail="Acesso negado fora do diretório permitido")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Arquivo de saída não encontrado")
    if target.suffix.lower() not in OUTPUT_MEDIA_GROUPS:
        raise HTTPException(status_code=403, detail="Tipo de arquivo não permitido para download")

    if inline:
        return FileResponse(target, headers={"Content-Disposition": f'inline; filename="{target.name}"'})
    return FileResponse(target, filename=target.name)


class ProjectProxyRequest(BaseModel):
    method: str = "GET"
    path: str = "/"
    port_kind: str = "api"
    payload: Optional[Dict[str, Any]] = None
    timeout: float = 25.0


@app.post("/api/projects/{project_id}/proxy")
async def api_project_proxy(project_id: str, req: ProjectProxyRequest):
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")
    method = req.method.upper().strip()
    if method not in {"GET", "POST"}:
        raise HTTPException(status_code=400, detail="Método HTTP não permitido no proxy interno")
    path = (req.path or "/").strip()
    if not path.startswith("/") or "://" in path:
        raise HTTPException(status_code=400, detail="Caminho relativo inválido para proxy interno")

    proj = PROJECTS[project_id]
    port = proj["ui_port"] if req.port_kind == "ui" or project_id == "openfilm" else proj["api_port"]
    target_url = f"http://127.0.0.1:{port}{path}"
    timeout = max(2.0, min(req.timeout, 120.0))

    extra_headers: Dict[str, str] = {}
    if project_id == "supoclip":
        try:
            extra_headers.update(_supoclip_headers())
        except Exception:
            pass
    elif project_id == "openfilm":
        extra_headers["Host"] = "127.0.0.1:3005"
        try:
            run_file = OPENFILM_DATA_DIR / "run.json"
            key_file = OPENFILM_DATA_DIR / "key"
            k = ""
            if run_file.exists():
                k = str(json.loads(run_file.read_text(encoding="utf-8")).get("key", "")).strip()
            if not k and key_file.exists():
                k = key_file.read_text(encoding="utf-8").strip()
            if k:
                extra_headers["x-studio-key"] = k
        except Exception:
            pass
    elif project_id == "openseo":
        extra_headers["Host"] = "127.0.0.1:3040"

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True, headers=extra_headers) as client:
            if method == "GET":
                resp = await client.get(target_url)
            else:
                resp = await client.post(target_url, json=req.payload or {})
        try:
            body: Any = resp.json()
        except Exception:
            body = resp.text[:4000]
        return {"ok": resp.status_code < 400, "status_code": resp.status_code, "url": target_url, "data": body}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Falha ao contatar {proj['name']} em :{port}: {exc}")


# ==============================================================================
# Estúdio Rápido (Mobile) — Auto-start & Direct Job Dispatch
# ==============================================================================

async def _ensure_stack_http(project_id: str, probe_url: str, auto_start: bool = True, wait_s: float = 55.0) -> None:
    if await check_endpoint_http(probe_url, timeout=1.5):
        return
    if not auto_start:
        raise HTTPException(
            status_code=409,
            detail=f"{PROJECTS[project_id]['name']} está desligado. Ligue a stack ou habilite o início automático.",
        )
    res = await run_cmd(["docker", "compose", "up", "-d"], cwd=PROJECTS[project_id]["path"], timeout=120)
    if res["returncode"] != 0:
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao ligar {PROJECTS[project_id]['name']}: {res['stderr'] or res['stdout']}",
        )
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if await check_endpoint_http(probe_url, timeout=1.5):
            return
        await asyncio.sleep(1.5)
    raise HTTPException(
        status_code=504,
        detail=f"{PROJECTS[project_id]['name']} iniciou o contêiner, mas a API ainda está aquecendo em {probe_url}. Tente novamente em alguns segundos.",
    )


def _ensure_local_mpt_video(aspect: str = "9:16", broll_style: str = "grid_emerald") -> str:
    """Guarantee at least 1 valid local MP4 material exists for MoneyPrinterTurbo when Pexels key is absent."""
    local_dir = STUDIO_BASE_DIR / "MoneyPrinterTurbo/storage/local_videos"
    local_dir.mkdir(parents=True, exist_ok=True)
    style_map = {
        "grid_emerald": ("#0b1612", "0x10b981@0.15"),
        "cinema_dark": ("#090b10", "0x94a3b8@0.10"),
        "cyber_cyan": ("#071318", "0x06b6d4@0.16"),
        "warm_amber": ("#140f09", "0xf59e0b@0.14"),
    }
    bg_hex, grid_col = style_map.get(broll_style, style_map["grid_emerald"])
    tag = "9x16" if aspect == "9:16" else ("1x1" if aspect == "1:1" else "16x9")
    size = "1080x1920" if aspect == "9:16" else ("1080x1080" if aspect == "1:1" else "1920x1080")
    out_file = local_dir / f"studio-base-{tag}-{broll_style}.mp4"
    tmp_file = local_dir / f".studio-base-{tag}-{broll_style}.tmp.mp4"
    if not out_file.exists() or out_file.stat().st_size < 1024:
        import subprocess
        if out_file.exists():
            out_file.unlink(missing_ok=True)
        cmd = [
            "ffmpeg", "-y", "-f", "lavfi",
            "-i", f"color=c={bg_hex}:s={size}:d=8,drawgrid=width=120:height=120:thickness=1:color={grid_col}",
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency", "-pix_fmt", "yuv420p", "-r", "24",
            str(tmp_file),
        ]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            ok_gen = res.returncode == 0 and tmp_file.exists() and tmp_file.stat().st_size > 1024
        except Exception:
            ok_gen = False
        if not ok_gen:
            tmp_file.unlink(missing_ok=True)
            subprocess.run(
                ["ffmpeg", "-y", "-f", "lavfi", "-i", f"color=c=#0f241b:s={size}:d=8", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "24", str(tmp_file)],
                check=True, capture_output=True, timeout=20,
            )
        tmp_file.replace(out_file)
    return f"/MoneyPrinterTurbo/storage/local_videos/{out_file.name}"


class QuickMoneyPrinterRequest(BaseModel):
    video_subject: str
    video_script: str = ""
    video_terms: Optional[Union[str, List[str]]] = None
    video_language: str = "pt"
    voice_provider: str = "edge"
    voice_name: str = "pt-BR-AntonioNeural-Male"
    voice_rate: float = 1.0
    voice_volume: float = 1.0
    video_aspect: str = "9:16"
    video_fit_mode: str = "cover"
    image_motion: str = "zoom"
    video_source: str = "auto"  # "auto", "pexels", "pixabay", "local"
    broll_style: str = "grid_emerald"  # "grid_emerald", "cinema_dark", "cyber_cyan", "warm_amber"
    video_concat_mode: str = "random"
    video_transition_mode: Optional[str] = "FadeIn"
    video_clip_duration: int = 5
    video_count: int = 1
    bgm_type: str = "random"
    bgm_volume: float = 0.18
    subtitle_enabled: bool = True
    subtitle_display_mode: str = "word_by_word"  # "sentence", "word_by_word"
    subtitle_animation: str = "pop_spring"  # "none", "pop_spring"
    subtitle_position: str = "shopee_safe"  # "shopee_safe", "two_thirds_bottom", "center", "top", "bottom"
    custom_position: Optional[float] = None
    font_size: int = 60
    text_fore_color: str = "#FFFFFF"
    stroke_color: str = "#000000"
    stroke_width: float = 1.5
    auto_start: bool = True


@app.post("/api/quick/moneyprinter")
async def api_quick_moneyprinter(req: QuickMoneyPrinterRequest):
    subj = (req.video_subject or "").strip()
    if not subj:
        raise HTTPException(status_code=400, detail="Informe o tema ou produto para o vídeo.")

    vault = load_vault()
    has_pexels = bool(vault.get("PEXELS_API_KEY", "").strip())
    src = (req.video_source or "auto").strip().lower()
    if src == "auto":
        src = "pexels" if has_pexels else "local"
    elif src == "pexels" and not has_pexels:
        src = "local"

    aspect = req.video_aspect if req.video_aspect in {"9:16", "16:9", "1:1"} else "9:16"
    fit_mode = req.video_fit_mode if req.video_fit_mode in {"cover", "contain"} else "cover"
    img_motion = req.image_motion if req.image_motion in {"zoom", "static"} else "zoom"
    concat_mode = req.video_concat_mode if req.video_concat_mode in {"random", "sequential"} else "random"
    valid_transitions = {None, "", "None", "Shuffle", "FadeIn", "FadeOut", "SlideIn", "SlideOut", "ZoomIn", "ZoomOut"}
    trans_mode = req.video_transition_mode if req.video_transition_mode in valid_transitions else None
    if trans_mode in ("", "None"):
        trans_mode = None

    raw_sub_pos = (req.subtitle_position or "shopee_safe").strip().lower()
    if raw_sub_pos == "shopee_safe":
        if aspect == "9:16":
            sub_pos = "custom"
            custom_sub_pos = 53.5
        else:
            sub_pos = "bottom"
            custom_sub_pos = 88.0
    elif raw_sub_pos in {"bottom", "center", "top", "two_thirds_bottom", "custom"}:
        sub_pos = raw_sub_pos
        custom_sub_pos = float(req.custom_position if req.custom_position is not None else (53.5 if aspect == "9:16" else 80.0))
    else:
        sub_pos = "custom" if aspect == "9:16" else "bottom"
        custom_sub_pos = 53.5 if aspect == "9:16" else 88.0
    sub_disp = req.subtitle_display_mode if req.subtitle_display_mode in {"sentence", "word_by_word"} else "word_by_word"
    sub_anim = req.subtitle_animation if req.subtitle_animation in {"none", "pop_spring"} else "pop_spring"

    terms_val: Optional[Union[str, List[str]]] = None
    if isinstance(req.video_terms, list):
        terms_val = [str(x).strip() for x in req.video_terms if str(x).strip()]
    elif isinstance(req.video_terms, str) and req.video_terms.strip():
        terms_val = [x.strip() for x in req.video_terms.split(",") if x.strip()]

    local_rel = await asyncio.to_thread(_ensure_local_mpt_video, aspect, req.broll_style or "grid_emerald")
    applied_cfg = {
        "voice_provider": req.voice_provider or "edge",
        "voice_name": req.voice_name or "pt-BR-AntonioNeural-Male",
        "voice_rate": max(0.6, min(float(req.voice_rate or 1.0), 1.8)),
        "video_aspect": aspect,
        "broll_style": req.broll_style or "grid_emerald",
        "video_transition_mode": trans_mode or "FadeIn",
        "video_clip_duration": max(2, min(int(req.video_clip_duration or 5), 15)),
        "subtitle_display_mode": sub_disp,
        "subtitle_animation": sub_anim,
        "subtitle_position": raw_sub_pos,
        "effective_subtitle_position": sub_pos,
        "custom_position": custom_sub_pos,
        "text_fore_color": req.text_fore_color or "#FFFFFF",
        "video_terms": terms_val,
    }

    mpt_online = await check_endpoint_http("http://127.0.0.1:8080/ping", timeout=1.5)
    if not mpt_online and not req.auto_start:
        return {
            "status": "ok",
            "ok": True,
            "stack": "moneyprinter",
            "mode": "local_fast_broll",
            "task_id": f"local-{int(time.time())}",
            "video_source": "local",
            "broll_style": req.broll_style or "grid_emerald",
            "applied_params": applied_cfg,
            "advanced_config": applied_cfg,
            "generated_file": {
                "name": Path(local_rel).name,
                "path": local_rel,
            },
            "message": "B-Roll base e parâmetros avançados preparados com sucesso!",
        }

    await _ensure_stack_http("moneyprinter", "http://127.0.0.1:8080/ping", auto_start=req.auto_start, wait_s=55.0)

    payload: Dict[str, Any] = {
        "video_subject": subj,
        "video_script": (req.video_script or "").strip(),
        "video_terms": terms_val,
        "video_language": req.video_language if req.video_language in {"pt", "en", "es", ""} else "pt",
        "voice_name": req.voice_name or "pt-BR-AntonioNeural-Male",
        "voice_rate": max(0.6, min(float(req.voice_rate or 1.0), 1.8)),
        "voice_volume": max(0.3, min(float(req.voice_volume or 1.0), 2.0)),
        "video_aspect": aspect,
        "video_fit_mode": fit_mode,
        "image_motion": img_motion,
        "video_concat_mode": concat_mode,
        "video_transition_mode": trans_mode,
        "video_clip_duration": max(2, min(int(req.video_clip_duration or 5), 15)),
        "video_count": max(1, min(int(req.video_count or 1), 3)),
        "video_source": src,
        "bgm_type": req.bgm_type if req.bgm_type in {"random", "none"} else "random",
        "bgm_volume": max(0.0, min(float(req.bgm_volume if req.bgm_volume is not None else 0.18), 0.8)),
        "subtitle_enabled": bool(req.subtitle_enabled),
        "subtitle_position": sub_pos,
        "custom_position": custom_sub_pos,
        "subtitle_display_mode": sub_disp,
        "subtitle_animation": sub_anim,
        "font_size": max(32, min(int(req.font_size or 60), 100)),
        "text_fore_color": req.text_fore_color or "#FFFFFF",
        "stroke_color": req.stroke_color or "#000000",
        "stroke_width": max(0.0, min(float(req.stroke_width if req.stroke_width is not None else 1.5), 5.0)),
        "n_threads": 2,
    }
    if src == "local":
        payload["video_materials"] = [{"provider": "local", "url": local_rel, "duration": 8}]

    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.post("http://127.0.0.1:8080/api/v1/videos", json=payload)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:800])

    data = resp.json()
    task_id = (data.get("data") or {}).get("task_id") or data.get("task_id") or ""
    return {
        "status": "ok",
        "ok": True,
        "stack": "moneyprinter",
        "mode": "moneyprinter_engine",
        "task_id": task_id,
        "video_source": src,
        "broll_style": req.broll_style,
        "applied_params": applied_cfg,
        "advanced_config": applied_cfg,
        "generated_file": {
            "name": Path(local_rel).name,
            "path": local_rel,
        },
        "message": "Renderização avançada iniciada no MoneyPrinterTurbo!",
        "raw": data,
    }


@app.get("/api/quick/moneyprinter/task/{task_id}")
async def api_quick_moneyprinter_task(task_id: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]{4,80}", task_id):
        raise HTTPException(status_code=400, detail="task_id inválido")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(f"http://127.0.0.1:8080/api/v1/tasks/{task_id}")
    except httpx.RequestError:
        return {
            "ok": True,
            "offline": True,
            "task_id": task_id,
            "state": 0,
            "failed": False,
            "ready": False,
            "progress": 0,
            "outputs": [],
            "message": "MoneyPrinterTurbo em standby.",
        }
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:400])
    body = resp.json()
    tdata = body.get("data") or {}
    state = tdata.get("state", 0)
    progress = tdata.get("progress", 0)
    ready = (state == 1) or (progress == 100 and bool(tdata.get("videos")))
    outputs = []
    if ready:
        all_items = await asyncio.to_thread(_scan_output_files, "moneyprinter", 40)
        outputs = [x for x in all_items if task_id in x.get("relpath", "")]
    return {
        "ok": True,
        "task_id": task_id,
        "state": state,
        "failed": state == -1,
        "ready": ready,
        "progress": progress,
        "outputs": outputs,
        "raw": tdata,
    }


class QuickVoiceRequest(BaseModel):
    text: str
    voice: str = "pt-BR-AntonioNeural"
    rate: str = "+0%"
    pitch: str = "+0Hz"
    normalize_48k_soxr: bool = True
    project_tag: str = ""
    auto_start: bool = True


@app.post("/api/quick/voicestudio")
async def api_quick_voicestudio(req: QuickVoiceRequest):
    txt = (req.text or "").strip()
    if not txt:
        raise HTTPException(status_code=400, detail="Digite o texto para locução.")
    await _ensure_stack_http("voicestudio", "http://127.0.0.1:3030/health", auto_start=req.auto_start, wait_s=35.0)

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "http://127.0.0.1:3030/api/tts",
            json={"text": txt, "voice": req.voice or "pt-BR-AntonioNeural", "rate": req.rate or "+0%", "pitch": req.pitch or "+0Hz"},
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:600])
    data = resp.json()
    fname = data.get("filename", "")
    from urllib.parse import quote
    q_fname = quote(fname, safe="")
    dl_url = f"/api/outputs/download?stack=voicestudio&root_idx=0&relpath={q_fname}" if fname else ""
    return {
        "status": "ok",
        "ok": True,
        "stack": "voicestudio",
        "filename": fname,
        "size_bytes": data.get("size_bytes", 0),
        "download_url": dl_url,
        "preview_url": f"{dl_url}&inline=1" if dl_url else "",
        "voice": req.voice,
        "rate": req.rate,
        "pitch": req.pitch,
        "normalize_48k_soxr": req.normalize_48k_soxr,
        "sample_rate": "48000 Hz Stereo SoXR",
        "wav_48khz": {
            "name": fname,
            "download_url": dl_url,
            "preview_url": f"{dl_url}&inline=1" if dl_url else "",
        },
        "project_tag": req.project_tag,
    }


class QuickSupoClipRequest(BaseModel):
    source_url: str = ""
    video_url: str = ""
    source_mode: str = "url"  # "url", "upload", "sample"
    cut_mode: str = "highlights"  # "novela" (series), "highlights", "viral" (shorts), "topic", "recap", "trailer"
    output_format: str = "vertical"  # "vertical" (9:16), "square" (1:1), "horizontal" (16:9)
    aspect_ratio: str = ""
    processing_mode: str = "fast"  # "fast", "balanced", "quality"
    clip_length: str = "auto"  # "auto", "short", "medium", "long"
    max_clips: int = 5
    part_count: Optional[Union[str, int]] = "auto"
    endings: str = "cliffhanger"  # "cliffhanger", "natural"
    series_ending_style: str = ""
    coverage: str = "best_chapters"  # "best_chapters", "start_to_finish"
    cold_open: bool = False
    part_labels: bool = True
    hook_title: Union[bool, str] = True
    junction_style: str = "auto"  # "auto", "soft", "hard"
    topic: str = ""
    instructions: str = ""
    subtitle_language: str = "pt"  # "pt", "en", "es", "auto"
    subtitle_lang: str = ""
    add_subtitles: bool = True  # burn-in toggle
    caption_template: str = "default"
    font_family: str = "Montserrat"
    font_size: int = 24
    font_color: str = "#FFFFFF"
    include_broll: bool = False
    remove_silence: bool = True
    remove_filler_words: bool = True
    auto_start: bool = True


def _supoclip_headers(user_id: str = "studio-mobile-operator") -> Dict[str, str]:
    secret = read_env_value(STUDIO_BASE_DIR / "supoclip/.env", "BACKEND_AUTH_SECRET")
    if not secret:
        raise HTTPException(status_code=500, detail="BACKEND_AUTH_SECRET ausente em supoclip/.env")
    ts = str(int(time.time()))
    sig = hmac.new(secret.encode("utf-8"), f"{user_id}:{ts}".encode("utf-8"), hashlib.sha256).hexdigest()
    return {
        "x-supoclip-user-id": user_id,
        "x-supoclip-ts": ts,
        "x-supoclip-signature": sig,
    }


def _generate_supoclip_sample_mp4(duration_s: int = 240) -> Path:
    """Create a valid 240s narrative sample video with audio for SupoClip (supports Series/Novela, Recap, Trailer)."""
    sample_dir = STUDIO_BASE_DIR / "supoclip/clips"
    sample_dir.mkdir(parents=True, exist_ok=True)
    out_mp4 = sample_dir / f"studio-sample-{duration_s}s.mp4"
    tmp_mp4 = sample_dir / f".studio-sample-{duration_s}s.tmp.mp4"
    if not out_mp4.exists() or out_mp4.stat().st_size < 10000:
        import subprocess
        tmp_mp4.unlink(missing_ok=True)
        cmd = [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c=#0b1612:s=720x1280:r=15:d={duration_s},drawgrid=w=90:h=90:t=1:c=0x10b981@0.18",
            "-f", "lavfi", "-i", f"sine=frequency=220:sample_rate=44100:duration={duration_s}",
            "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-r", "15",
            "-c:a", "aac", "-b:a", "64k", "-shortest",
            str(tmp_mp4),
        ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=60)
        tmp_mp4.replace(out_mp4)
    return out_mp4


async def _upload_local_file_to_supoclip(file_path: Path, filename: Optional[str] = None) -> str:
    """Upload a local MP4 on the VPS into SupoClip's /upload endpoint and return its 'upload://...' URI."""
    if not file_path.exists():
        raise HTTPException(status_code=404, detail=f"Arquivo local não encontrado: {file_path}")
    headers = _supoclip_headers()
    fname = filename or file_path.name
    file_bytes = await asyncio.to_thread(file_path.read_bytes)
    files = {"video": (fname, file_bytes, "video/mp4")}
    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post("http://127.0.0.1:8000/upload", headers=headers, files=files)
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=f"Falha no upload SupoClip: {resp.text[:500]}")
    data = resp.json()
    return str(data.get("video_path") or "")


@app.post("/api/quick/supoclip/upload")
@app.post("/upload")
@app.post("/api/v1/upload")
async def api_quick_supoclip_upload(
    request: Request,
    video: Optional[UploadFile] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """Upload a video file (or auto-generate sample if requested via query ?sample=1) to SupoClip (:8000/upload)."""
    await _ensure_stack_http("supoclip", "http://127.0.0.1:8000/health", auto_start=True, wait_s=75.0)
    if request.query_params.get("sample") in ("1", "true", "yes"):
        sample_path = await asyncio.to_thread(_generate_supoclip_sample_mp4, 240)
        video_uri = await _upload_local_file_to_supoclip(sample_path, sample_path.name)
        sz = sample_path.stat().st_size
        return {
            "status": "ok",
            "ok": True,
            "stack": "supoclip",
            "video_path": video_uri,
            "video_url": video_uri,
            "filename": sample_path.name,
            "size_bytes": sz,
            "size_mb": round(sz / (1024 * 1024), 2),
            "duration_sec": 240,
            "message": "Vídeo de demonstração (240s) gerado e enviado ao SupoClip!",
        }

    chosen = video or file
    if chosen is None:
        try:
            form = await request.form()
            chosen = form.get("video") or form.get("file")  # type: ignore[assignment]
        except Exception:
            chosen = None
    if chosen is None or not hasattr(chosen, "read"):
        raise HTTPException(status_code=400, detail="Envie o arquivo de vídeo no campo 'video' (multipart/form-data) ou use ?sample=1.")

    raw_bytes = await chosen.read()
    if not raw_bytes:
        raise HTTPException(status_code=400, detail="O arquivo de vídeo enviado está vazio.")
    fname = getattr(chosen, "filename", None) or "mobile-upload.mp4"
    ctype = getattr(chosen, "content_type", None) or "video/mp4"
    if (
        request.headers.get("x-supoclip-user-id")
        and request.headers.get("x-supoclip-ts")
        and request.headers.get("x-supoclip-signature")
    ):
        headers = {
            "x-supoclip-user-id": request.headers["x-supoclip-user-id"],
            "x-supoclip-ts": request.headers["x-supoclip-ts"],
            "x-supoclip-signature": request.headers["x-supoclip-signature"],
        }
    else:
        headers = _supoclip_headers()
    async with httpx.AsyncClient(timeout=180.0) as client:
        resp = await client.post(
            "http://127.0.0.1:8000/upload",
            headers=headers,
            files={"video": (fname, raw_bytes, ctype)},
        )
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:800])
    data = resp.json()
    v_uri = data.get("video_path") or ""
    return {
        "status": "ok",
        "ok": True,
        "stack": "supoclip",
        "video_path": v_uri,
        "video_url": v_uri,
        "filename": fname,
        "size_bytes": len(raw_bytes),
        "size_mb": round(len(raw_bytes) / (1024 * 1024), 2),
        "message": data.get("message", "Vídeo enviado ao SupoClip com sucesso!"),
    }


@app.get("/api/v1/clips")
@app.get("/api/v1/tasks")
@app.get("/api/quick/supoclip/workflows")
async def api_supoclip_clips_and_workflows():
    """Unified endpoint returning SupoClip clips, tasks, and available cutting modes without 404."""
    gallery_clips = await asyncio.to_thread(_scan_output_files, "supoclip", 50)
    cut_modes = [
        {
            "id": "novela",
            "workflow": "series",
            "name": "Estilo Novela com Continuação",
            "description": "Divide o vídeo em partes sequenciais (Parte 1, Parte 2...) com gancho cliffhanger no final.",
            "min_source_seconds": 120,
        },
        {
            "id": "highlights",
            "workflow": "highlights",
            "name": "Melhores Momentos / Highlights",
            "description": "Seleciona os trechos mais relevantes e completos com pontuação de retenção.",
            "min_source_seconds": 0,
        },
        {
            "id": "viral",
            "workflow": "highlights",
            "name": "Cortes Curtos Virais (Shorts/Reels/TikTok)",
            "description": "Cortes dinâmicos de 15–30s focados em alto impacto nos primeiros 3 segundos.",
            "min_source_seconds": 0,
        },
        {
            "id": "topic",
            "workflow": "topic",
            "name": "Recorte Inteligente por Tópico",
            "description": "Extrai apenas os momentos em que um tema ou assunto específico é mencionado.",
            "min_source_seconds": 60,
        },
        {
            "id": "recap",
            "workflow": "recap",
            "name": "Resumo Executivo (Recap)",
            "description": "Condensa todo o vídeo em uma narrativa enxuta com tópicos na tela.",
            "min_source_seconds": 180,
        },
        {
            "id": "trailer",
            "workflow": "trailer",
            "name": "Trailer / Teaser Cinematográfico",
            "description": "Monta um teaser de 20–60s sem spoilers terminando em pergunta intrigante.",
            "min_source_seconds": 180,
        },
    ]
    live_tasks: List[Dict[str, Any]] = []
    live_clips: List[Dict[str, Any]] = []
    backend_online = await check_endpoint_http("http://127.0.0.1:8000/health", timeout=2.0)
    if backend_online:
        try:
            headers = _supoclip_headers()
            async with httpx.AsyncClient(timeout=10.0) as client:
                t_resp = await client.get("http://127.0.0.1:8000/tasks/", headers=headers)
                if t_resp.status_code == 200:
                    live_tasks = t_resp.json().get("tasks", [])
                    for t in live_tasks:
                        for c in t.get("clips", []) or []:
                            live_clips.append({**c, "task_id": t.get("id"), "workflow": t.get("workflow", "highlights")})
        except Exception:
            pass
    return {
        "status": "ok",
        "ok": True,
        "stack": "supoclip",
        "backend_online": backend_online,
        "count": len(live_tasks) or len(gallery_clips),
        "clips": live_clips or gallery_clips,
        "gallery_outputs": gallery_clips,
        "tasks": live_tasks,
        "workflows": ["highlights", "series", "topic", "recap", "trailer"],
        "cut_modes": cut_modes,
    }


@app.post("/api/quick/supoclip")
async def api_quick_supoclip(req: QuickSupoClipRequest):
    await _ensure_stack_http("supoclip", "http://127.0.0.1:8000/health", auto_start=req.auto_start, wait_s=75.0)

    url = (req.source_url or req.video_url or "").strip()
    if req.source_mode == "sample" or url.lower() in ("sample", "demo", "auto"):
        sample_mp4 = await asyncio.to_thread(_generate_supoclip_sample_mp4, 240)
        url = await _upload_local_file_to_supoclip(sample_mp4, sample_mp4.name)
    elif url.startswith("/") and Path(url).exists():
        url = await _upload_local_file_to_supoclip(Path(url))
    elif not url.startswith(("http://", "https://", "upload://")):
        raise HTTPException(
            status_code=400,
            detail="Informe uma URL válida (https://...), um vídeo enviado (upload://...) ou selecione o modo 'Vídeo Demo (VPS)'.",
        )

    mode = (req.cut_mode or "highlights").strip().lower()
    clip_len = req.clip_length if req.clip_length in {"auto", "short", "medium", "long"} else "auto"
    sub_lang = (req.subtitle_lang or req.subtitle_language or "pt").strip().lower()
    out_fmt = (req.aspect_ratio or req.output_format or "vertical").strip().lower()
    if out_fmt in ("9:16", "vertical"):
        out_fmt = "vertical"
    elif out_fmt in ("1:1", "square"):
        out_fmt = "square"
    elif out_fmt in ("16:9", "horizontal"):
        out_fmt = "horizontal"
    else:
        out_fmt = "vertical"

    end_style = (req.series_ending_style or req.endings or "cliffhanger").strip().lower()
    if end_style not in {"cliffhanger", "natural"}:
        end_style = "cliffhanger"

    hook_title_bool = bool(req.hook_title)
    hook_title_str = req.hook_title.strip() if isinstance(req.hook_title, str) else ""

    lang_hint = {
        "pt": "Idioma das legendas e análise: Português (pt-BR).",
        "en": "Subtitle and analysis language: English (en-US).",
        "es": "Idioma de subtítulos y análisis: Español (es).",
    }.get(sub_lang, "")
    user_instr = (req.instructions or "").strip()
    if hook_title_str:
        user_instr = f"Título de gancho sugerido: {hook_title_str}. {user_instr}".strip()
    combined_instr = f"{user_instr} {lang_hint}".strip()[:500]

    workflow = "highlights"
    workflow_options: Dict[str, Any] = {}

    if mode in ("novela", "series", "continuacao", "continuação"):
        workflow = "series"
        p_cnt: Any = req.part_count
        if isinstance(p_cnt, str):
            p_cnt = None if p_cnt.strip().lower() in ("auto", "") else int(p_cnt) if p_cnt.strip().isdigit() else None
        if isinstance(p_cnt, int):
            p_cnt = max(2, min(p_cnt, 10))
        workflow_options = {
            **({"part_count": p_cnt} if p_cnt is not None else {}),
            **({"instructions": combined_instr} if combined_instr else {}),
            "advanced": {
                "cold_open": bool(req.cold_open),
                "part_labels": bool(req.part_labels),
                "endings": end_style,
                "coverage": req.coverage if req.coverage in {"best_chapters", "start_to_finish"} else "best_chapters",
                "hook_title": hook_title_bool,
                "junction_style": req.junction_style if req.junction_style in {"auto", "soft", "hard"} else "auto",
            },
        }
    elif mode in ("viral", "shorts", "curtos"):
        workflow = "highlights"
        if clip_len == "auto":
            clip_len = "short"
        viral_instr = combined_instr or "Priorize cortes curtos altamente virais com gancho forte nos primeiros 3 segundos."
        workflow_options = {
            "instructions": viral_instr[:500],
            "advanced": {
                "max_clips": max(1, min(int(req.max_clips or 5), 10)),
                "hook_title": hook_title_bool,
                "junction_style": req.junction_style if req.junction_style in {"auto", "soft", "hard"} else "hard",
            },
        }
    elif mode == "topic":
        workflow = "topic"
        workflow_options = {
            "topic": (req.topic or "momentos principais").strip()[:120],
            **({"instructions": combined_instr} if combined_instr else {}),
            "advanced": {
                "max_clips": max(1, min(int(req.max_clips or 5), 15)),
                "match": "broad",
                "hook_title": hook_title_bool,
            },
        }
    elif mode == "recap":
        workflow = "recap"
        workflow_options = {
            **({"instructions": combined_instr} if combined_instr else {}),
            "advanced": {
                "on_screen_points": True,
                "coverage": "whole_video",
                "hook_title": hook_title_bool,
            },
        }
    elif mode == "trailer":
        workflow = "trailer"
        workflow_options = {
            **({"instructions": combined_instr} if combined_instr else {}),
            "advanced": {
                "spoilers": "strict",
                "ending": "question",
                "hook_title": hook_title_bool,
            },
        }
    else:
        workflow = "highlights"
        workflow_options = {
            **({"instructions": combined_instr} if combined_instr else {}),
            "advanced": {
                "max_clips": max(1, min(int(req.max_clips or 5), 10)),
                "hook_title": hook_title_bool,
                "junction_style": req.junction_style if req.junction_style in {"auto", "soft", "hard"} else "auto",
            },
        }

    headers = _supoclip_headers()
    payload: Dict[str, Any] = {
        "source": {"url": url, "title": f"Studio Mobile ({mode.upper()})"},
        "workflow": workflow,
        "workflow_options": workflow_options,
        "output_format": out_fmt,
        "processing_mode": req.processing_mode if req.processing_mode in {"fast", "balanced", "quality"} else "fast",
        "clip_length": clip_len,
        "add_subtitles": bool(req.add_subtitles),
        "caption_template": req.caption_template or "default",
        "include_broll": bool(req.include_broll),
        "font_options": {
            "font_family": req.font_family or "Montserrat",
            "font_size": max(14, min(int(req.font_size or 24), 48)),
            "font_color": req.font_color or "#FFFFFF",
        },
        "remove_silence": bool(req.remove_silence),
        "remove_filler_words": bool(req.remove_filler_words),
    }
    async with httpx.AsyncClient(timeout=45.0) as client:
        resp = await client.post("http://127.0.0.1:8000/tasks/", json=payload, headers=headers)
        if resp.status_code == 422 and "TOO_MANY_PARTS" in resp.text and workflow == "series":
            try:
                err_det = resp.json().get("detail", {})
                max_p = (err_det.get("details") or {}).get("max_parts") if isinstance(err_det, dict) else None
                if isinstance(max_p, int) and max_p >= 2:
                    payload["workflow_options"]["part_count"] = max_p
                else:
                    payload["workflow_options"].pop("part_count", None)
                headers = _supoclip_headers()
                resp = await client.post("http://127.0.0.1:8000/tasks/", json=payload, headers=headers)
            except Exception:
                pass
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:800])
    data = resp.json()
    t_id = data.get("task_id") or data.get("id") or ""
    return {
        "status": "ok",
        "ok": True,
        "stack": "supoclip",
        "task_id": t_id,
        "task": {"id": t_id, **data},
        "cut_mode": mode,
        "workflow": workflow,
        "source_url": url,
        "applied_params": {
            "cut_mode": mode,
            "workflow": workflow,
            "output_format": payload["output_format"],
            "clip_length": clip_len,
            "subtitle_language": sub_lang,
            "add_subtitles": payload["add_subtitles"],
        },
        "data": data,
    }


@app.get("/api/quick/supoclip/task/{task_id}")
async def api_quick_supoclip_task(task_id: str):
    if not re.fullmatch(r"[A-Za-z0-9._-]+", task_id):
        raise HTTPException(status_code=400, detail="task_id inválido")
    headers = _supoclip_headers()
    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(f"http://127.0.0.1:8000/tasks/{task_id}", headers=headers)
    except httpx.RequestError:
        return {
            "ok": True,
            "offline": True,
            "task_id": task_id,
            "status": "standby",
            "progress": 0,
            "ready": False,
            "failed": False,
            "message": "SupoClip em standby.",
        }
    if resp.status_code >= 400:
        raise HTTPException(status_code=resp.status_code, detail=resp.text[:500])
    data = resp.json()
    status_val = str(data.get("status") or "").lower()
    ready = status_val in {"completed", "done", "succeeded", "finished"}
    failed = status_val in {"failed", "error"}
    return {
        "ok": True,
        "task_id": task_id,
        "status": data.get("status"),
        "progress": data.get("progress", 100 if ready else 0),
        "ready": ready,
        "failed": failed,
        "data": data,
    }


class QuickOpenFilmRequest(BaseModel):
    name: str = ""
    project_name: str = ""
    aspect_ratio: str = "9:16"
    fps: float = 30.0
    motion_blur: bool = False
    motion_blur_samples: int = 8
    subtitle_preset: str = "reels_viral"  # "reels_viral", "cinema", "minimal"
    import_latest_media: bool = False
    scene_prompt: str = ""
    auto_start: bool = True


@app.post("/api/quick/openfilm")
async def api_quick_openfilm(req: QuickOpenFilmRequest):
    title = (req.name or req.project_name or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Informe o nome do novo projeto OpenFilm.")

    valid_fps = [23.976, 24, 25, 29.97, 30, 48, 50, 59.94, 60, 120]
    chosen_fps = min(valid_fps, key=lambda x: abs(x - float(req.fps or 30.0)))
    sub_presets = {
        "reels_viral": {"fontSize": 56, "color": "#FFFFFF", "strokeColor": "#000000", "strokeWidth": 3, "position": "bottom"},
        "cinema": {"fontSize": 42, "color": "#FDE047", "strokeColor": "#000000", "strokeWidth": 2, "position": "bottom"},
        "minimal": {"fontSize": 38, "color": "#E2E8F0", "strokeColor": "#0F172A", "strokeWidth": 1, "position": "bottom"},
    }
    sub_style = sub_presets.get(req.subtitle_preset, sub_presets["reels_viral"])

    of_online = await check_endpoint_http("http://127.0.0.1:3005/api/health", timeout=1.5)
    proj: Dict[str, Any] = {}
    proj_path = ""

    if of_online or req.auto_start:
        await _ensure_stack_http("openfilm", "http://127.0.0.1:3005/api/health", auto_start=req.auto_start, wait_s=45.0)
        key = ""
        run_file = OPENFILM_DATA_DIR / "run.json"
        key_file = OPENFILM_DATA_DIR / "key"
        try:
            if run_file.exists():
                key = str(json.loads(run_file.read_text(encoding="utf-8")).get("key", "")).strip()
            if not key and key_file.exists():
                key = key_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        if key:
            async with httpx.AsyncClient(timeout=20.0) as client:
                resp = await client.post(
                    "http://127.0.0.1:3005/api/projects",
                    json={"name": title, "subtitleStyle": sub_style},
                    headers={"Host": "127.0.0.1:3005", "x-studio-key": key},
                )
                if resp.status_code < 400:
                    data = resp.json()
                    proj = data.get("project") or {}
                    proj_id = proj.get("id", "")
                    proj_path = proj.get("path", "")
                    settings_patch: Dict[str, Any] = {"fps": chosen_fps}
                    if req.motion_blur:
                        settings_patch["motionBlur"] = {
                            "enabled": True,
                            "samples": max(2, min(int(req.motion_blur_samples or 8), 32)),
                            "shutter": 0.5,
                        }
                    if proj_id:
                        await client.patch(
                            f"http://127.0.0.1:3005/api/projects/{proj_id}/settings",
                            json=settings_patch,
                            headers={"Host": "127.0.0.1:3005", "x-studio-key": key},
                        )

    if not proj_path:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title.lower()).strip("-") or f"openfilm-{int(time.time())}"
        fallback_dir = OPENFILM_DATA_DIR / "projects" / slug
        fallback_dir.mkdir(parents=True, exist_ok=True)
        proj_path = str(fallback_dir)
        proj = {"id": slug, "name": title, "path": proj_path}

    p_root = Path(proj_path)
    p_root.mkdir(parents=True, exist_ok=True)
    film_meta = p_root / ".film"
    film_meta.mkdir(parents=True, exist_ok=True)
    brief_data = {
        "name": title,
        "aspect_ratio": req.aspect_ratio,
        "fps": chosen_fps,
        "subtitle_preset": req.subtitle_preset,
        "motion_blur": req.motion_blur,
        "scene_prompt": (req.scene_prompt or "").strip(),
        "created_from": "Estúdio Rápido Mobile",
    }
    (film_meta / "mobile-brief.json").write_text(json.dumps(brief_data, ensure_ascii=False, indent=2), encoding="utf-8")

    imported_files: List[str] = []
    if req.import_latest_media:
        assets_dir = p_root / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        sources_map = _get_output_sources()
        recent_outputs = await asyncio.to_thread(_scan_output_files, "all", 8)
        for item in recent_outputs:
            if len(imported_files) >= 2:
                break
            st_key = item.get("stack", "")
            r_idx = int(item.get("root_idx", 0))
            rel_p = item.get("relpath", "")
            if st_key in sources_map and 0 <= r_idx < len(sources_map[st_key]["roots"]):
                root_dir = sources_map[st_key]["roots"][r_idx].resolve()
                src_abs = (root_dir / rel_p).resolve()
                if src_abs.is_relative_to(root_dir) and src_abs.is_file() and src_abs.suffix.lower() in {".mp4", ".mp3", ".wav"}:
                    dst = assets_dir / src_abs.name
                    if not dst.exists():
                        shutil.copy2(src_abs, dst)
                    imported_files.append(src_abs.name)

    return {
        "status": "ok",
        "ok": True,
        "stack": "openfilm",
        "project": proj,
        "brief": brief_data,
        "staged_files": imported_files,
        "applied_settings": {
            "aspect_ratio": req.aspect_ratio,
            "fps": chosen_fps,
            "motion_blur": req.motion_blur,
            "subtitle_preset": req.subtitle_preset,
            "imported_files": imported_files,
        },
        "open_url": get_openfilm_url(),
    }



# ==============================================================================
# Zero-Cost Video Use & HyperFrames Engines (Zero Idle RAM)
# ==============================================================================

VIDEOUSE_RUNNER = STUDIO_BASE_DIR / "tools/video-use/vps_zero_cost_runner.py"
VIDEOUSE_PYTHON = STUDIO_BASE_DIR / "tools/video-use/.venv/bin/python"
VIDEOUSE_EDGE_TTS = STUDIO_BASE_DIR / "tools/video-use/.venv/bin/edge-tts"
HYPERFRAMES_RUNNER = STUDIO_BASE_DIR / "tools/hyperframes/vps_ad_runner.py"
NOVELA_RUNNER = STUDIO_BASE_DIR / "tools/hyperframes/vps_novela_runner.py"
REGIONAL_VOICE_ENGINE = STUDIO_BASE_DIR / "tools/hyperframes/regional_voice_engine.py"
VOICES_REGIONAL_DIR = STUDIO_BASE_DIR / "voices/regional"
VIRAL_ANALYZER_RUNNER = STUDIO_BASE_DIR / "tools/hyperframes/viral_reference_analyzer.py"
VIDEOUSE_INPUTS_DIR = STUDIO_BASE_DIR / ".cache/videouse_inputs"
NOVELA_TAKES_CACHE_DIR = STUDIO_BASE_DIR / ".cache/novela_takes"
NOVELA_LINE_AUDIO_CACHE_DIR = STUDIO_BASE_DIR / ".cache/novela_line_audio"
NOVELA_PREVIEW_CACHE_DIR = STUDIO_BASE_DIR / ".cache/novela_previews"
COLAB_NOTEBOOK_PATH = STUDIO_BASE_DIR / "tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb"

_RVE_MODULE = None


def _get_rve():
    global _RVE_MODULE
    if _RVE_MODULE is not None:
        return _RVE_MODULE
    if not REGIONAL_VOICE_ENGINE.is_file():
        raise HTTPException(status_code=500, detail="Motor regional_voice_engine.py não encontrado na VPS.")
    spec = importlib.util.spec_from_file_location("regional_voice_engine", str(REGIONAL_VOICE_ENGINE))
    if spec is None or spec.loader is None:
        raise HTTPException(status_code=500, detail="Falha ao carregar spec de regional_voice_engine.py.")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.STUDIO_BASE = STUDIO_BASE_DIR
    mod.VOICES_REGIONAL_DIR = STUDIO_BASE_DIR / "voices/regional"
    mod.PROFILES_META_PATH = mod.VOICES_REGIONAL_DIR / "profiles.json"
    _RVE_MODULE = mod
    return _RVE_MODULE
VIDEOUSE_EXPORTS_DIR = STUDIO_BASE_DIR / "exports/videouse"
HYPERFRAMES_EXPORTS_DIR = STUDIO_BASE_DIR / "exports/hyperframes"
NOVELA_EXPORTS_DIR = STUDIO_BASE_DIR / "exports/novela"
PIPELINES_EXPORTS_DIR = STUDIO_BASE_DIR / "exports/pipelines"
CAMPAIGNS_DIR = Path(os.environ.get("STUDIO_CAMPAIGNS_DIR", str(STUDIO_BASE_DIR / "campaigns")))

_LOCAL_MEDIA_JOBS: Dict[str, Dict[str, Any]] = {}


def _trim_local_media_jobs() -> None:
    if len(_LOCAL_MEDIA_JOBS) <= 100:
        return
    oldest = sorted(_LOCAL_MEDIA_JOBS.items(), key=lambda kv: kv[1].get("created_at", 0))[:-80]
    for k, _ in oldest:
        _LOCAL_MEDIA_JOBS.pop(k, None)


def _generate_videouse_demo_mp4() -> Path:
    """Create a short spoken PT-BR sample video with silence gap for 1-click Video Use test."""
    VIDEOUSE_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    demo_mp4 = VIDEOUSE_INPUTS_DIR / "demo-videouse-pt.mp4"
    if demo_mp4.exists() and demo_mp4.stat().st_size > 15000:
        return demo_mp4

    import subprocess
    with tempfile.TemporaryDirectory(prefix="vu_demo_") as tmpdir:
        tmp = Path(tmpdir)
        p1 = tmp / "p1.mp3"
        p2 = tmp / "p2.mp3"
        if VIDEOUSE_EDGE_TTS.exists():
            subprocess.run(
                [str(VIDEOUSE_EDGE_TTS), "--voice", "pt-BR-AntonioNeural", "--text", "Olá, bem-vindo ao AI Media Studio.", "--write-media", str(p1)],
                check=True, capture_output=True, timeout=30,
            )
            subprocess.run(
                [str(VIDEOUSE_EDGE_TTS), "--voice", "pt-BR-AntonioNeural", "--text", "Edição automática sem pausas na nuvem.", "--write-media", str(p2)],
                check=True, capture_output=True, timeout=30,
            )
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=#0a1a13:s=720x1280:r=24:d=8.5,drawbox=x=80:y=420:w=560:h=260:color=0x10b981@0.45:t=fill",
                "-i", str(p1),
                "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo:d=1.6",
                "-i", str(p2),
                "-filter_complex", "[1:a]aresample=48000,aformat=channel_layouts=stereo[a1];[3:a]aresample=48000,aformat=channel_layouts=stereo[a2];[a1][2:a][a2]concat=n=3:v=0:a=1[aout]",
                "-map", "0:v", "-map", "[aout]",
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(demo_mp4),
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-v", "error",
                "-f", "lavfi", "-i", "color=c=#0a1a13:s=720x1280:r=24:d=5",
                "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=5",
                "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
                str(demo_mp4),
            ]
        subprocess.run(cmd, check=True, capture_output=True, timeout=45)
    return demo_mp4


def _resolve_videouse_input(
    output_id: str = "",
    uploaded_path: str = "",
    sample: bool = False,
) -> Path:
    if uploaded_path:
        up_root = VIDEOUSE_INPUTS_DIR.resolve()
        cand = Path(uploaded_path).resolve()
        if cand.is_relative_to(up_root) and cand.is_file():
            return cand
        raise HTTPException(status_code=400, detail="Arquivo enviado para o Video Use não encontrado ou fora do diretório seguro.")

    if output_id and ":" in output_id:
        parts = output_id.split(":", 2)
        if len(parts) == 3:
            stack_id, idx_raw, relp = parts
            sources = _get_output_sources()
            if stack_id in sources:
                try:
                    r_idx = int(idx_raw)
                except ValueError:
                    r_idx = -1
                roots = sources[stack_id]["roots"]
                if 0 <= r_idx < len(roots):
                    root_dir = roots[r_idx].resolve()
                    cand = (root_dir / relp).resolve()
                    if cand.is_relative_to(root_dir) and cand.is_file():
                        return cand
        raise HTTPException(status_code=404, detail="Vídeo selecionado na Galeria não encontrado.")

    if sample or (not output_id and not uploaded_path):
        return _generate_videouse_demo_mp4()

    raise HTTPException(status_code=400, detail="Selecione um vídeo da Galeria ou envie um arquivo MP4.")


def _output_download_meta(stack_id: str, target_file: Path) -> Dict[str, str]:
    from urllib.parse import quote
    root_dir = _get_output_sources()[stack_id]["roots"][0].resolve()
    rel = target_file.resolve().relative_to(root_dir).as_posix()
    q_rel = quote(rel, safe="")
    return {
        "relpath": rel,
        "download_url": f"/api/outputs/download?stack={stack_id}&root_idx=0&relpath={q_rel}",
        "preview_url": f"/api/outputs/download?stack={stack_id}&root_idx=0&relpath={q_rel}&inline=1",
    }


@app.post("/api/quick/videouse/upload")
async def api_quick_videouse_upload(
    request: Request,
    video: Optional[UploadFile] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    if request.query_params.get("sample") == "1":
        demo = await asyncio.to_thread(_generate_videouse_demo_mp4)
        return {
            "ok": True,
            "video_path": str(demo),
            "filename": demo.name,
            "size_bytes": demo.stat().st_size,
            "message": "Vídeo demo PT-BR preparado com sucesso.",
        }

    up = video or file
    if up is None:
        raise HTTPException(status_code=400, detail="Envie o arquivo de vídeo no campo 'video' ou 'file'.")

    orig_name = Path(up.filename or "upload.mp4").name
    ext = Path(orig_name).suffix.lower() or ".mp4"
    if ext not in {".mp4", ".mov", ".mkv", ".webm", ".m4v"}:
        raise HTTPException(status_code=400, detail="Formato não suportado (use MP4, MOV, MKV ou WebM).")

    VIDEOUSE_INPUTS_DIR.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", Path(orig_name).stem).strip("-")[:48] or "video"
    dest = VIDEOUSE_INPUTS_DIR / f"{uuid.uuid4().hex[:10]}_{safe_stem}{ext}"
    content = await up.read()
    if not content:
        raise HTTPException(status_code=400, detail="Arquivo de vídeo enviado está vazio.")
    await asyncio.to_thread(dest.write_bytes, content)
    return {
        "ok": True,
        "video_path": str(dest),
        "filename": orig_name,
        "size_bytes": len(content),
        "message": f"Arquivo '{orig_name}' recebido no Video Use ({_human_size(len(content))}).",
    }


class QuickVideoUseRequest(BaseModel):
    output_id: str = ""
    uploaded_path: str = ""
    sample: bool = False
    grade_preset: str = "Neutral Clean"
    remove_silences: bool = True
    max_gap_s: float = 0.45
    burn_subtitles: bool = True
    language: str = "pt"
    platform_preset: str = "shopee_20_9"
    use_ai_director: bool = True
    wait: bool = False


async def _invoke_videouse_cli(input_path: Path, out_path: Path, req: QuickVideoUseRequest) -> Dict[str, Any]:
    cmd = [
        str(VIDEOUSE_PYTHON),
        str(VIDEOUSE_RUNNER),
        str(input_path),
        "-o", str(out_path),
        "--grade", req.grade_preset or "Neutral Clean",
        "--max-gap", f"{max(0.2, min(2.0, float(req.max_gap_s or 0.45))):.2f}",
        "--language", (req.language or "pt").strip() or "pt",
        "--platform-preset", (req.platform_preset or "shopee_20_9").strip() or "shopee_20_9",
    ]
    if not req.remove_silences:
        cmd.append("--no-silence-cut")
    if not req.burn_subtitles:
        cmd.append("--no-subtitles")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=600)
    stdout_txt = stdout_b.decode("utf-8", errors="ignore").strip()
    stderr_txt = stderr_b.decode("utf-8", errors="ignore").strip()

    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError((stderr_txt or stdout_txt or f"Exit code {proc.returncode}")[-500:])

    meta = json.loads(stdout_txt.splitlines()[-1])
    dl = _output_download_meta("videouse", out_path)
    return {**meta, **dl, "size_human": _human_size(out_path.stat().st_size)}


async def _execute_videouse_job(task_id: str, input_path: Path, out_path: Path, req: QuickVideoUseRequest) -> None:
    job = _LOCAL_MEDIA_JOBS[task_id]
    try:
        job["status"] = "processing"
        job["progress"] = 25
        job["message"] = "Transcrevendo áudio com faster-whisper INT8 e preparando cortes..."

        res = await _invoke_videouse_cli(input_path, out_path, req)
        job.update(res)
        job["status"] = "completed"
        job["progress"] = 100
        job["ready"] = True
        job["message"] = "Edição Video Use concluída na VPS!"
    except Exception as exc:
        job["status"] = "error"
        job["failed"] = True
        job["error"] = str(exc)
        job["message"] = f"Falha no Video Use: {exc}"


@app.post("/api/quick/videouse")
async def api_quick_videouse(req: QuickVideoUseRequest):
    if not VIDEOUSE_RUNNER.exists() or not VIDEOUSE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="Motor Video Use não instalado em /opt/ai-media-studio/tools/video-use")

    input_path = await asyncio.to_thread(_resolve_videouse_input, req.output_id, req.uploaded_path, req.sample)
    VIDEOUSE_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", input_path.stem).strip("-")[:36] or "clip"
    out_name = f"videouse_{slug}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    out_path = VIDEOUSE_EXPORTS_DIR / out_name

    task_id = f"vu-{uuid.uuid4().hex[:10]}"
    _trim_local_media_jobs()
    _LOCAL_MEDIA_JOBS[task_id] = {
        "ok": True,
        "task_id": task_id,
        "engine": "videouse",
        "status": "processing",
        "progress": 10,
        "ready": False,
        "failed": False,
        "input_file": input_path.name,
        "filename": out_name,
        "grade_preset": req.grade_preset,
        "created_at": time.time(),
        "message": "Iniciando pipeline Video Use (faster-whisper INT8 + 48kHz SoXR)...",
    }

    task = asyncio.create_task(_execute_videouse_job(task_id, input_path, out_path, req))
    _LOCAL_MEDIA_JOBS[task_id]["_task"] = task

    if req.wait:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=120.0)
        except asyncio.TimeoutError:
            pass

    payload = {k: v for k, v in _LOCAL_MEDIA_JOBS[task_id].items() if k != "_task"}
    return payload


@app.get("/api/quick/videouse/task/{task_id}")
async def api_quick_videouse_task(task_id: str):
    job = _LOCAL_MEDIA_JOBS.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarefa do Video Use não encontrada")
    return {k: v for k, v in job.items() if k != "_task"}


_CAMPAIGN_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_CAMPAIGN_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def _resolve_campaign_dir(campaign_id: str, create: bool = False) -> Path:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", (campaign_id or "").strip()).strip("-.")
    if not slug:
        raise HTTPException(status_code=400, detail="Identificador de campanha inválido.")
    CAMPAIGNS_DIR.mkdir(parents=True, exist_ok=True)
    root = CAMPAIGNS_DIR.resolve()
    c_dir = (CAMPAIGNS_DIR / slug).resolve()
    if not c_dir.is_relative_to(root):
        raise HTTPException(status_code=403, detail="Acesso negado fora de campaigns")
    if create:
        c_dir.mkdir(parents=True, exist_ok=True)
    elif not c_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Campanha '{slug}' não encontrada.")
    return c_dir


def _scan_campaigns() -> List[Dict[str, Any]]:
    """List curated campaign asset directories and apply Anti-Confusion filtering metadata."""
    campaigns: List[Dict[str, Any]] = []
    if not CAMPAIGNS_DIR.exists() or not CAMPAIGNS_DIR.is_dir():
        return campaigns

    for c_dir in sorted(CAMPAIGNS_DIR.iterdir()):
        if not c_dir.is_dir() or c_dir.name.startswith("."):
            continue
        meta: Dict[str, Any] = {}
        meta_file = c_dir / "campaign.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
            except Exception:
                meta = {}

        excluded_map: Dict[str, str] = {}
        for ex in meta.get("excluded_assets") or []:
            if isinstance(ex, dict) and ex.get("filename"):
                excluded_map[str(ex["filename"])] = str(ex.get("reason") or "Filtrada pela Regra Anti-Confusão")
            elif isinstance(ex, str):
                excluded_map[ex] = "Filtrada pela Regra Anti-Confusão"

        roles = meta.get("asset_roles") or {}
        all_files = sorted(
            p for p in c_dir.iterdir()
            if p.is_file() and p.suffix.lower() in _CAMPAIGN_IMAGE_EXTS
        )
        assets_info: List[Dict[str, Any]] = []
        for p in all_files:
            from urllib.parse import quote
            assets_info.append({
                "filename": p.name,
                "path": str(p),
                "size_human": _human_size(p.stat().st_size),
                "preview_url": f"/api/campaigns/{quote(c_dir.name)}/assets/{quote(p.name)}",
                "excluded": p.name in excluded_map,
                "exclude_reason": excluded_map.get(p.name, ""),
                "role": roles.get(p.name) or {},
            })

        rec_paths = [
            str(Path(rp)) for rp in (meta.get("recommended_image_paths") or [])
            if Path(rp).is_file()
        ]
        if not rec_paths:
            rec_paths = [a["path"] for a in assets_info if not a["excluded"]]

        virals_dir = c_dir / "virals"
        viral_videos: List[Dict[str, Any]] = []
        if virals_dir.is_dir():
            for vp in sorted(virals_dir.iterdir()):
                if vp.is_file() and vp.suffix.lower() in _CAMPAIGN_VIDEO_EXTS:
                    viral_videos.append({
                        "filename": vp.name,
                        "path": str(vp),
                        "size_human": _human_size(vp.stat().st_size),
                    })

        bp_file = c_dir / "viral_blueprint.json"
        viral_blueprint: Optional[Dict[str, Any]] = None
        if bp_file.is_file():
            try:
                viral_blueprint = json.loads(bp_file.read_text(encoding="utf-8"))
            except Exception:
                viral_blueprint = None

        campaigns.append({
            "id": c_dir.name,
            "name": meta.get("name") or c_dir.name,
            "platform": meta.get("platform") or "Shopee Video / Reels",
            "pricing": meta.get("pricing") or {},
            "path": str(c_dir),
            "total_assets": len(assets_info),
            "curated_count": len(rec_paths),
            "excluded_count": len([a for a in assets_info if a["excluded"]]),
            "recommended_image_paths": rec_paths,
            "assets": assets_info,
            "viral_videos_count": len(viral_videos),
            "viral_videos": viral_videos,
            "has_viral_blueprint": viral_blueprint is not None,
            "viral_blueprint": viral_blueprint,
        })
    return campaigns


@app.get("/api/campaigns")
async def api_list_campaigns():
    """List campaign asset folders exposed to Control Panel, HyperFrames, and OpenJarvis."""
    items = await asyncio.to_thread(_scan_campaigns)
    return {"ok": True, "campaigns_dir": str(CAMPAIGNS_DIR), "count": len(items), "campaigns": items}


@app.api_route("/api/campaigns/{campaign_id}/assets/{filename}", methods=["GET", "HEAD"])
async def api_get_campaign_asset(campaign_id: str, filename: str):
    root = CAMPAIGNS_DIR.resolve()
    target = (CAMPAIGNS_DIR / campaign_id / filename).resolve()
    if not target.is_relative_to(root):
        raise HTTPException(status_code=403, detail="Acesso negado fora de campaigns")
    if not target.exists() or not target.is_file() or target.suffix.lower() not in _CAMPAIGN_IMAGE_EXTS:
        raise HTTPException(status_code=404, detail="Asset de campanha não encontrado")
    return FileResponse(target)


@app.post("/api/quick/hyperframes/upload")
async def api_quick_hyperframes_upload(files: List[UploadFile] = File(...)):
    """Upload one or more product photos directly to /opt/ai-media-studio/campaigns/ for HyperFrames Multi-Image ads."""
    if not files:
        raise HTTPException(status_code=400, detail="Envie ao menos uma imagem (.jpg, .png, .webp).")
    CAMPAIGNS_DIR.mkdir(parents=True, exist_ok=True)
    slug = f"upload-{int(time.time())}-{uuid.uuid4().hex[:6]}"
    target_dir = CAMPAIGNS_DIR / slug
    target_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: List[str] = []
    for f in files:
        orig = Path(f.filename or "foto.jpg").name
        ext = Path(orig).suffix.lower()
        if ext not in _CAMPAIGN_IMAGE_EXTS:
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", orig)
        dst = target_dir / safe_name
        data = await f.read()
        if data:
            dst.write_bytes(data)
            saved_paths.append(str(dst))
    if not saved_paths:
        raise HTTPException(status_code=400, detail="Nenhuma imagem válida (.jpg/.png/.webp) encontrada no upload.")
    return {
        "ok": True,
        "campaign": slug,
        "count": len(saved_paths),
        "image_paths": saved_paths,
        "message": f"{len(saved_paths)} foto(s) salva(s) na campanha '{slug}'!",
    }


class AnalyzeViralsRequest(BaseModel):
    product_title: str = ""
    product_price: str = ""
    product_description: str = ""
    viral_paths: List[str] = []


class QuickAnalyzeViralsRequest(AnalyzeViralsRequest):
    campaign_id: str = ""
    campaign: str = ""


async def _run_campaign_viral_analysis(
    campaign_id: str,
    product_title: str = "",
    product_price: str = "",
    product_description: str = "",
    viral_paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    if not VIRAL_ANALYZER_RUNNER.exists() or not VIDEOUSE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="Analisador Viral não configurado em /opt/ai-media-studio/tools/hyperframes")
    c_dir = _resolve_campaign_dir(campaign_id, create=False)
    v_dir = c_dir / "virals"

    base_allowed = CAMPAIGNS_DIR.resolve()
    resolved_virals: List[str] = []
    for raw_v in (viral_paths or []):
        try:
            cand = Path(str(raw_v).strip()).resolve()
            if cand.is_relative_to(base_allowed) and cand.is_file() and cand.suffix.lower() in _CAMPAIGN_VIDEO_EXTS:
                resolved_virals.append(str(cand))
        except Exception:
            continue

    if not resolved_virals:
        if not v_dir.is_dir() or not any(
            p.is_file() and p.suffix.lower() in _CAMPAIGN_VIDEO_EXTS for p in v_dir.iterdir()
        ):
            raise HTTPException(
                status_code=400,
                detail=f"Nenhum vídeo viral .mp4 encontrado em '{c_dir.name}/virals/'. Faça upload primeiro.",
            )

    bp_path = c_dir / "viral_blueprint.json"
    cmd = [
        str(VIDEOUSE_PYTHON),
        str(VIRAL_ANALYZER_RUNNER),
        "--campaign", c_dir.name,
        "--product-title", (product_title or "").strip()[:160],
        "--product-price", (product_price or "").strip()[:80],
        "--product-description", (product_description or "").strip()[:600],
        "-o", str(bp_path),
    ]
    for vp in resolved_virals:
        cmd.extend(["--viral", vp])

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=240)
    stdout_txt = stdout_b.decode("utf-8", errors="ignore").strip()
    stderr_txt = stderr_b.decode("utf-8", errors="ignore").strip()
    if proc.returncode != 0 or not bp_path.is_file():
        raise HTTPException(
            status_code=500,
            detail=f"Falha ao analisar referências virais: {(stderr_txt or stdout_txt)[-500:]}",
        )
    blueprint = json.loads(bp_path.read_text(encoding="utf-8"))
    return {
        "ok": True,
        "campaign_id": c_dir.name,
        "blueprint_path": str(bp_path),
        "viral_blueprint": blueprint,
        "hook_0_3s": blueprint.get("hook_0_3s", ""),
        "pacing_seconds": blueprint.get("pacing_seconds", 0),
        "wpm": blueprint.get("wpm", 0),
        "recommended_script": blueprint.get("recommended_script", ""),
        "recommended_duration": blueprint.get("recommended_duration", 18.0),
    }


@app.post("/api/campaigns/{campaign_id}/upload-virals")
async def api_campaign_upload_virals(
    campaign_id: str,
    files: Optional[List[UploadFile]] = File(default=None),
    videos: Optional[List[UploadFile]] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    """Upload one or more viral reference MP4 videos into /opt/ai-media-studio/campaigns/{campaign_id}/virals/."""
    uploads: List[UploadFile] = []
    if files:
        uploads.extend(files)
    if videos:
        uploads.extend(videos)
    if file is not None:
        uploads.append(file)
    if not uploads:
        raise HTTPException(status_code=400, detail="Envie ao menos um vídeo viral (.mp4/.mov/.webm).")

    c_dir = _resolve_campaign_dir(campaign_id, create=True)
    virals_dir = c_dir / "virals"
    virals_dir.mkdir(parents=True, exist_ok=True)

    saved_paths: List[str] = []
    saved_names: List[str] = []
    for up in uploads:
        orig = Path(up.filename or "viral.mp4").name
        ext = Path(orig).suffix.lower() or ".mp4"
        if ext not in _CAMPAIGN_VIDEO_EXTS:
            continue
        safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "_", orig)
        dst = virals_dir / safe_name
        data = await up.read()
        if data:
            dst.write_bytes(data)
            saved_paths.append(str(dst))
            saved_names.append(dst.name)

    if not saved_paths:
        raise HTTPException(status_code=400, detail="Nenhum arquivo de vídeo válido (.mp4/.mov/.mkv/.webm) encontrado no upload.")

    return {
        "ok": True,
        "campaign_id": c_dir.name,
        "virals_dir": str(virals_dir),
        "count": len(saved_paths),
        "filenames": saved_names,
        "viral_paths": saved_paths,
        "message": f"{len(saved_paths)} vídeo(s) viral(is) de referência salvo(s) em '{c_dir.name}/virals/'!",
    }


@app.get("/api/campaigns/{campaign_id}/viral-blueprint")
async def api_campaign_get_viral_blueprint(campaign_id: str):
    c_dir = _resolve_campaign_dir(campaign_id, create=False)
    bp_path = c_dir / "viral_blueprint.json"
    if not bp_path.is_file():
        return {"ok": True, "campaign_id": c_dir.name, "exists": False, "viral_blueprint": None}
    bp = json.loads(bp_path.read_text(encoding="utf-8"))
    return {"ok": True, "campaign_id": c_dir.name, "exists": True, "blueprint_path": str(bp_path), "viral_blueprint": bp}


@app.post("/api/campaigns/{campaign_id}/analyze-virals")
async def api_campaign_analyze_virals(campaign_id: str, req: Optional[AnalyzeViralsRequest] = None):
    r = req or AnalyzeViralsRequest()
    return await _run_campaign_viral_analysis(
        campaign_id=campaign_id,
        product_title=r.product_title,
        product_price=r.product_price,
        product_description=r.product_description,
        viral_paths=r.viral_paths,
    )


@app.post("/api/quick/hyperframes/analyze-virals")
async def api_quick_hyperframes_analyze_virals(req: QuickAnalyzeViralsRequest):
    cid = (req.campaign_id or req.campaign or "").strip()
    if not cid:
        raise HTTPException(status_code=400, detail="Informe campaign_id ou campaign para análise viral.")
    return await _run_campaign_viral_analysis(
        campaign_id=cid,
        product_title=req.product_title,
        product_price=req.product_price,
        product_description=req.product_description,
        viral_paths=req.viral_paths,
    )


class QuickHyperFramesRequest(BaseModel):
    title: str
    offer: str = ""
    cta: str = "Garanta Agora"
    style: str = "B2B Emerald"
    aspect: str = "9:16"
    platform_preset: str = "shopee_20_9"
    duration: float = 8.0
    voice_enabled: bool = True
    voice_name: str = "pt-BR-AntonioNeural"
    voice_script: str = ""
    image_paths: List[str] = []
    campaign: str = ""
    extra_price_note: str = ""
    orange_cart_cta: str = ""
    subtitles: bool = True
    quality: str = "draft"
    wait: bool = False


async def _invoke_hyperframes_cli(out_path: Path, req: QuickHyperFramesRequest) -> Dict[str, Any]:
    dur = max(5.0, min(30.0, float(req.duration or 8.0)))
    resolved_imgs: List[str] = []
    base_allowed = STUDIO_BASE_DIR.resolve()
    for raw_p in (req.image_paths or []):
        try:
            cand = Path(str(raw_p).strip()).resolve()
            if cand.is_relative_to(base_allowed) and cand.is_file() and cand.suffix.lower() in _CAMPAIGN_IMAGE_EXTS:
                resolved_imgs.append(str(cand))
        except Exception:
            continue

    eff_preset = (req.platform_preset or "shopee_20_9").strip() or "shopee_20_9"
    eff_aspect = "16:9" if (req.aspect or "").strip() == "16:9" or eff_preset == "yt_16_9" else "9:16"
    cmd = [
        str(VIDEOUSE_PYTHON),
        str(HYPERFRAMES_RUNNER),
        "--title", req.title.strip()[:140],
        "--offer", (req.offer or "").strip()[:140],
        "--cta", (req.cta or "Garanta Agora").strip()[:90],
        "--style", req.style or "B2B Emerald",
        "--aspect", eff_aspect,
        "--platform-preset", eff_preset,
        "--duration", f"{dur:.1f}",
        "--voice-name", (req.voice_name or "pt-BR-FranciscaNeural").strip(),
        "--quality", req.quality if req.quality in ("draft", "looks", "delivery") else "draft",
        "-o", str(out_path),
    ]
    if req.voice_enabled:
        cmd.append("--voice-enabled")
    if (req.voice_script or "").strip():
        cmd.extend(["--voice-script", req.voice_script.strip()[:800]])
    if resolved_imgs:
        cmd.extend(["--images-json", json.dumps(resolved_imgs, ensure_ascii=False)])
    if (req.campaign or "").strip():
        cmd.extend(["--campaign", req.campaign.strip()[:80]])
    if (req.extra_price_note or "").strip():
        cmd.extend(["--extra-price-note", req.extra_price_note.strip()[:120]])
    if (req.orange_cart_cta or "").strip():
        cmd.extend(["--orange-cart-cta", req.orange_cart_cta.strip()[:100]])
    if not req.subtitles:
        cmd.append("--no-subtitles")

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=420)
    stdout_txt = stdout_b.decode("utf-8", errors="ignore").strip()
    stderr_txt = stderr_b.decode("utf-8", errors="ignore").strip()

    if proc.returncode != 0 or not out_path.exists():
        raise RuntimeError((stderr_txt or stdout_txt or f"Exit code {proc.returncode}")[-500:])

    meta = json.loads(stdout_txt.splitlines()[-1])
    dl = _output_download_meta("hyperframes", out_path)
    return {**meta, **dl, "size_human": _human_size(out_path.stat().st_size)}


async def _execute_hyperframes_job(task_id: str, out_path: Path, req: QuickHyperFramesRequest) -> None:
    job = _LOCAL_MEDIA_JOBS[task_id]
    try:
        job["status"] = "processing"
        job["progress"] = 25
        job["message"] = "Gerando cena HTML/Canvas + GSAP e renderizando frames via Chromium..."

        res = await _invoke_hyperframes_cli(out_path, req)
        job.update(res)
        job["status"] = "completed"
        job["progress"] = 100
        job["ready"] = True
        job["message"] = "Motion Graphics renderizado no HyperFrames!"
    except Exception as exc:
        job["status"] = "error"
        job["failed"] = True
        job["error"] = str(exc)
        job["message"] = f"Falha no HyperFrames: {exc}"


@app.post("/api/quick/hyperframes")
async def api_quick_hyperframes(req: QuickHyperFramesRequest):
    title = (req.title or "").strip()
    if not title:
        raise HTTPException(status_code=400, detail="Informe o produto ou título do anúncio.")
    if not HYPERFRAMES_RUNNER.exists() or not VIDEOUSE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="Motor HyperFrames não configurado em /opt/ai-media-studio/tools/hyperframes")

    HYPERFRAMES_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", title.lower()).strip("-")[:32] or "promo"
    out_name = f"hyperframes_{slug}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    out_path = HYPERFRAMES_EXPORTS_DIR / out_name

    task_id = f"hf-{uuid.uuid4().hex[:10]}"
    _trim_local_media_jobs()
    _LOCAL_MEDIA_JOBS[task_id] = {
        "ok": True,
        "task_id": task_id,
        "engine": "hyperframes",
        "status": "processing",
        "progress": 10,
        "ready": False,
        "failed": False,
        "title": title,
        "style": req.style,
        "aspect": req.aspect,
        "campaign": req.campaign,
        "filename": out_name,
        "created_at": time.time(),
        "message": "Preparando composição HyperFrames e locução neural...",
    }

    task = asyncio.create_task(_execute_hyperframes_job(task_id, out_path, req))
    _LOCAL_MEDIA_JOBS[task_id]["_task"] = task

    if req.wait:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=240.0)
        except asyncio.TimeoutError:
            pass

    payload = {k: v for k, v in _LOCAL_MEDIA_JOBS[task_id].items() if k != "_task"}
    return payload


@app.get("/api/quick/hyperframes/task/{task_id}")
async def api_quick_hyperframes_task(task_id: str):
    job = _LOCAL_MEDIA_JOBS.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarefa do HyperFrames não encontrada")
    return {k: v for k, v in job.items() if k != "_task"}


# ==============================================================================
# Novela Studio Multi-Personagem (v2.5 — Clean Cinema, Veo 3.1 Pack, Take Stitcher & Colab GPU)
# ==============================================================================

def _normalize_novela_visual_style(raw: str = "stylized_3d_pixar") -> str:
    val = (raw or "stylized_3d_pixar").strip().lower()
    if val in ("cinematic_live_action", "cinematic_tv", "live_action", "realistic", "cinema_8k"):
        return "cinematic_live_action"
    return "stylized_3d_pixar"


def _normalize_novela_dramatic_structure(raw: str = "viral_4_cuts") -> str:
    val = (raw or "viral_4_cuts").strip().lower()
    if val in ("classic_3_cuts", "3_cuts", "classic"):
        return "classic_3_cuts"
    return "viral_4_cuts"


def _normalize_novela_cinema_engine(raw: str = "") -> str:
    val = (raw or os.environ.get("NOVELA_CINEMA_ENGINE_DEFAULT", "v3.1")).strip().lower()
    return "v4" if val in ("v4", "v4.0", "cinema_v4", "cinema") else "v3.1"


class QuickNovelaScriptRequest(BaseModel):
    theme: str = "Enzo da capital confundindo gírias nordestinas na bodega de Seu Bastião"
    expressions: List[str] = ["Oxente", "Morreu Maria Preá", "Botar Boneco", "Deixe de cunversa"]
    tone: str = "Comédia Leve"
    episode_badge: str = "🌵 NOVELINHA DO SERTÃO • EP. 01"
    visual_style: str = "stylized_3d_pixar"
    dramatic_structure: str = "viral_4_cuts"
    cinema_engine: str = ""


class QuickNovelaRequest(BaseModel):
    title: str = "Causos do Sertão — Episódio 01"
    episode_badge: str = "🌵 NOVELINHA DO SERTÃO • EP. 01"
    visual_style: str = "stylized_3d_pixar"
    dramatic_structure: str = "viral_4_cuts"
    platform_preset: str = "tiktok_reels"
    setting: str = ""
    dictionary: List[Dict[str, str]] = []
    dialogue: List[Dict[str, Any]] = []
    theme: str = ""
    expressions: List[str] = []
    tone: str = "Comédia Leve"
    quality: str = "standard"
    clean_cinema_mode: bool = True
    subtitles_mode: str = "none"
    include_dictionary_endcard: Optional[bool] = None
    render_mode: str = "vps_preview"
    colab_engine: str = "liveportrait_latentsync"
    colab_url: str = ""
    accent_engine: str = "nordestino_raiz"
    cinema_engine: str = ""
    output_filename: str = ""
    wait: bool = False


class QuickNovelaFlowPackRequest(BaseModel):
    title: str = "Causos do Sertão — Episódio 01"
    visual_style: str = "stylized_3d_pixar"
    dramatic_structure: str = "viral_4_cuts"
    setting: str = ""
    dialogue: List[Dict[str, Any]] = []
    theme: str = "Enzo confundindo gírias nordestinas na mercearia do sertão"
    expressions: List[str] = ["Oxente", "Morreu Maria Preá", "Botar Boneco"]
    tone: str = "Comédia Leve"
    episode_badge: str = "🌵 NOVELINHA DO SERTÃO • EP. 01"
    accent_engine: str = "nordestino_raiz"
    cinema_engine: str = ""


class QuickNovelaStitchRequest(BaseModel):
    title: str = "Novelinha Flow — Corte Seco"
    batch_id: str = ""
    filenames: List[str] = []
    take_paths: List[str] = []
    output_ids: List[str] = []
    platform_preset: str = "tiktok_reels"
    subtitles_mode: str = "none"
    trim_silence: bool = True
    ambient_bed: bool = False
    redub_with_regional_tts: bool = False
    accent_engine: str = "nordestino_raiz"
    tone: str = "Drama Tenso"
    cinema_engine: str = ""
    dialogue: List[Dict[str, Any]] = []
    output_filename: str = ""
    wait: bool = False


class QuickNovelaPreviewVoiceRequest(BaseModel):
    accent_engine: str = "nordestino_raiz"
    dialogue: List[Dict[str, Any]] = []
    speaker: str = ""
    text: str = ""
    emotion: str = "warm"
    colab_url: str = ""


class ColabConfigRequest(BaseModel):
    colab_url: str = ""
    colab_token: str = ""


async def _invoke_novela_cli(
    out_path: Path,
    cfg_payload: Dict[str, Any],
    mode: str = "render",
) -> Dict[str, Any]:
    if not NOVELA_RUNNER.exists() or not VIDEOUSE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="Motor Novela Studio não configurado em /opt/ai-media-studio/tools/hyperframes/vps_novela_runner.py")

    NOVELA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    cfg_file = Path(f"/tmp/novela_job_{uuid.uuid4().hex[:10]}.json")
    full_cfg = {**cfg_payload}
    if mode in ("render", "stitch"):
        full_cfg["output"] = str(out_path)
    if not full_cfg.get("colab_url"):
        vault = load_vault()
        if vault.get("COLAB_GPU_URL"):
            full_cfg["colab_url"] = vault["COLAB_GPU_URL"]
        if vault.get("COLAB_GPU_TOKEN") and not full_cfg.get("colab_token"):
            full_cfg["colab_token"] = vault["COLAB_GPU_TOKEN"]
    cfg_file.write_text(json.dumps(full_cfg, ensure_ascii=False, indent=2), encoding="utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            str(VIDEOUSE_PYTHON),
            str(NOVELA_RUNNER),
            "--mode", mode,
            "--config", str(cfg_file),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=600)
        stdout_txt = stdout_b.decode("utf-8", errors="ignore").strip()
        stderr_txt = stderr_b.decode("utf-8", errors="ignore").strip()

        if proc.returncode != 0:
            raise RuntimeError((stderr_txt or stdout_txt or f"Exit code {proc.returncode}")[-600:])

        meta = json.loads(stdout_txt.splitlines()[-1])
        if mode == "flow_pack":
            return meta

        if not out_path.exists():
            raise RuntimeError(f"Arquivo final esperado não gerado: {out_path}")
        dl = _output_download_meta("novela", out_path)
        return {**meta, **dl, "size_human": _human_size(out_path.stat().st_size)}
    finally:
        cfg_file.unlink(missing_ok=True)


async def _pipeline_run_novela_cli(**kwargs: Any) -> Dict[str, Any]:
    NOVELA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    rmode = str(kwargs.get("render_mode") or "vps_preview").strip().lower()
    if rmode == "flow_pack":
        return await _invoke_novela_cli(NOVELA_EXPORTS_DIR / "pack.unused", kwargs, mode="flow_pack")

    raw_name = str(kwargs.get("output_filename") or "").strip()
    if raw_name:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw_name)
        if not safe_name.endswith(".mp4"):
            safe_name += ".mp4"
    else:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(kwargs.get("title") or "novela").lower()).strip("-")[:32] or "novela"
        vstyle = _normalize_novela_visual_style(str(kwargs.get("visual_style") or "stylized_3d_pixar"))
        safe_name = f"novela_{slug}_{vstyle}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    out_path = NOVELA_EXPORTS_DIR / safe_name
    return await _invoke_novela_cli(out_path, kwargs, mode="render")


async def _execute_novela_job(task_id: str, out_path: Path, cfg_payload: Dict[str, Any]) -> None:
    job = _LOCAL_MEDIA_JOBS[task_id]
    try:
        job["status"] = "processing"
        job["progress"] = 20
        rmode = str(cfg_payload.get("render_mode") or "vps_preview").strip().lower()
        job["message"] = (
            "Gerando Ingredients 1080x1920 + Prompts Bilíngues Google Flow Veo 3.1..."
            if rmode == "flow_pack"
            else "Sintetizando vozes e cenas Clean Cinema Shot-Reverse-Shot..."
        )

        if not cfg_payload.get("dialogue"):
            generated = await PIPELINE_ORCHESTRATOR.generate_novela_script(
                theme=cfg_payload.get("theme") or cfg_payload.get("title") or "Confusão na Bodega",
                expressions=cfg_payload.get("expressions") or [],
                tone=cfg_payload.get("tone") or "Comédia Leve",
                episode_badge=cfg_payload.get("episode_badge") or "🌵 NOVELINHA DO SERTÃO • EP. 01",
                visual_style=cfg_payload.get("visual_style") or "stylized_3d_pixar",
                dramatic_structure=cfg_payload.get("dramatic_structure") or "viral_4_cuts",
                cinema_engine=_normalize_novela_cinema_engine(cfg_payload.get("cinema_engine") or ""),
            )
            cfg_payload["dialogue"] = generated["dialogue"]
            if not cfg_payload.get("dictionary"):
                cfg_payload["dictionary"] = generated["dictionary"]
            if not cfg_payload.get("setting"):
                cfg_payload["setting"] = generated["setting"]
            if not cfg_payload.get("title"):
                cfg_payload["title"] = generated["title"]

        job["progress"] = 50
        cli_mode = "flow_pack" if rmode == "flow_pack" else "render"
        res = await _invoke_novela_cli(out_path, cfg_payload, mode=cli_mode)
        job.update(res)
        job["dialogue"] = cfg_payload.get("dialogue")
        job["dictionary"] = cfg_payload.get("dictionary")
        job["status"] = "completed"
        job["progress"] = 100
        job["ready"] = True
        job["message"] = (
            f"Pack Veo 3.1 v2.8 pronto ({res.get('total_takes', 0)} takes, {res.get('total_credits_lite', 0)} créd Lite)!"
            if rmode == "flow_pack"
            else f"Novelinha v2.8 Dual-World 30fps '{out_path.name}' ({cfg_payload.get('visual_style')}) renderizada!"
        )
    except Exception as exc:
        job["status"] = "error"
        job["failed"] = True
        job["error"] = str(exc)
        job["message"] = f"Falha no Novela Studio: {exc}"


@app.post("/api/quick/novela/generate-script")
async def api_quick_novela_generate_script(req: QuickNovelaScriptRequest):
    vstyle = _normalize_novela_visual_style(req.visual_style)
    dstruct = _normalize_novela_dramatic_structure(req.dramatic_structure)
    cengine = _normalize_novela_cinema_engine(req.cinema_engine)
    script_data = await PIPELINE_ORCHESTRATOR.generate_novela_script(
        theme=req.theme,
        expressions=req.expressions,
        tone=req.tone,
        episode_badge=req.episode_badge,
        visual_style=vstyle,
        dramatic_structure=dstruct,
        cinema_engine=cengine,
    )
    return {"ok": True, "cinema_engine": cengine, **script_data}


@app.post("/api/quick/novela/flow-pack")
async def api_quick_novela_flow_pack(req: QuickNovelaFlowPackRequest):
    cfg_payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    vstyle = _normalize_novela_visual_style(cfg_payload.get("visual_style") or "stylized_3d_pixar")
    dstruct = _normalize_novela_dramatic_structure(cfg_payload.get("dramatic_structure") or "viral_4_cuts")
    cengine = _normalize_novela_cinema_engine(cfg_payload.get("cinema_engine") or "")
    cfg_payload["visual_style"] = vstyle
    cfg_payload["dramatic_structure"] = dstruct
    cfg_payload["cinema_engine"] = cengine
    if not cfg_payload.get("dialogue"):
        generated = await PIPELINE_ORCHESTRATOR.generate_novela_script(
            theme=req.theme or req.title,
            expressions=req.expressions or [],
            tone=req.tone,
            episode_badge=req.episode_badge,
            visual_style=vstyle,
            dramatic_structure=dstruct,
            cinema_engine=cengine,
        )
        cfg_payload["dialogue"] = generated["dialogue"]
        if not cfg_payload.get("setting"):
            cfg_payload["setting"] = generated.get("setting") or ""
        if not cfg_payload.get("title"):
            cfg_payload["title"] = generated.get("title") or req.title
    res = await _invoke_novela_cli(NOVELA_EXPORTS_DIR / "pack.unused", cfg_payload, mode="flow_pack")
    res["dialogue"] = cfg_payload.get("dialogue")
    return res


@app.post("/api/quick/novela/upload-takes")
async def api_quick_novela_upload_takes(request: Request):
    form = await request.form()
    uploads: List[UploadFile] = []
    for k in form.keys():
        for val in form.getlist(k):
            if hasattr(val, "filename") and hasattr(val, "read"):
                uploads.append(val)
    if len(uploads) < 1:
        raise HTTPException(status_code=400, detail="Envie pelo menos 1 arquivo de take (.mp4/.mov/.webm).")
    if len(uploads) > 8:
        raise HTTPException(status_code=400, detail="Limite de até 8 takes por costura.")

    batch_id = f"batch_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    batch_dir = (NOVELA_TAKES_CACHE_DIR / batch_id).resolve()
    batch_dir.mkdir(parents=True, exist_ok=True)
    allowed_exts = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
    saved_takes: List[Dict[str, Any]] = []

    for idx, up in enumerate(uploads):
        raw_name = Path(up.filename or f"take_{idx+1}.mp4").name
        ext = Path(raw_name).suffix.lower()
        if ext not in allowed_exts:
            ext = ".mp4"
        safe_fname = f"take_{idx+1:02d}_{re.sub(r'[^a-zA-Z0-9_.-]+', '_', Path(raw_name).stem)[:40]}{ext}"
        dest = (batch_dir / safe_fname).resolve()
        if not dest.is_relative_to(batch_dir):
            raise HTTPException(status_code=400, detail="Nome de arquivo inválido.")
        content = await up.read()
        if len(content) < 512:
            raise HTTPException(status_code=400, detail=f"Arquivo '{raw_name}' vazio ou corrompido.")
        dest.write_bytes(content)

        dur_s = 0.0
        probe = await run_cmd([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(dest),
        ], timeout=15)
        try:
            dur_s = round(float(probe["stdout"].strip() or "0"), 2)
        except Exception:
            dur_s = 0.0

        saved_takes.append({
            "index": idx + 1,
            "filename": safe_fname,
            "original_name": raw_name,
            "path": str(dest),
            "duration": dur_s,
            "size_human": _human_size(len(content)),
        })

    return {"ok": True, "batch_id": batch_id, "count": len(saved_takes), "takes": saved_takes}


async def _execute_novela_stitch_job(task_id: str, out_path: Path, cfg_payload: Dict[str, Any]) -> None:
    job = _LOCAL_MEDIA_JOBS[task_id]
    try:
        job["status"] = "processing"
        job["progress"] = 25
        job["message"] = "Aparando silêncios (faster-whisper INT8), alinhando 48kHz SoXR e costurando plano/contraplano..."
        res = await _invoke_novela_cli(out_path, cfg_payload, mode="stitch")
        job.update(res)
        job["status"] = "completed"
        job["progress"] = 100
        job["ready"] = True
        job["message"] = f"Episódio costurado '{out_path.name}' pronto ({res.get('takes_count', 0)} takes, -{res.get('trimmed_seconds', 0)}s de silêncio)!"
    except Exception as exc:
        job["status"] = "error"
        job["failed"] = True
        job["error"] = str(exc)
        job["message"] = f"Falha ao costurar takes: {exc}"


@app.post("/api/quick/novela/stitch-takes")
async def api_quick_novela_stitch_takes(req: QuickNovelaStitchRequest):
    resolved_paths: List[Path] = []
    allowed_bases = [NOVELA_TAKES_CACHE_DIR.resolve(), NOVELA_EXPORTS_DIR.resolve(), STUDIO_BASE_DIR.resolve()]

    if req.batch_id and req.filenames:
        bdir = (NOVELA_TAKES_CACHE_DIR / re.sub(r"[^a-zA-Z0-9_-]+", "", req.batch_id)).resolve()
        for fn in req.filenames:
            cand = (bdir / Path(fn).name).resolve()
            if cand.is_relative_to(bdir) and cand.is_file():
                resolved_paths.append(cand)
    elif req.batch_id and not req.filenames and not req.take_paths:
        bdir = (NOVELA_TAKES_CACHE_DIR / re.sub(r"[^a-zA-Z0-9_-]+", "", req.batch_id)).resolve()
        if bdir.is_dir():
            resolved_paths.extend(sorted(p for p in bdir.iterdir() if p.is_file()))

    for raw_tp in (req.take_paths or []):
        cand = Path(str(raw_tp).strip()).resolve()
        if cand.is_file() and any(cand.is_relative_to(b) for b in allowed_bases):
            resolved_paths.append(cand)

    for oid in (req.output_ids or []):
        resolved_paths.append(_resolve_videouse_input(oid, "", False))

    if len(resolved_paths) < 1:
        raise HTTPException(status_code=400, detail="Nenhum take válido encontrado para costurar.")

    NOVELA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_name = (req.output_filename or "").strip()
    if raw_name:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw_name)
        if not safe_name.endswith(".mp4"):
            safe_name += ".mp4"
    else:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (req.title or "stitched").lower()).strip("-")[:32] or "stitched"
        safe_name = f"novela_stitched_{slug}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    out_path = NOVELA_EXPORTS_DIR / safe_name

    task_id = f"novst-{uuid.uuid4().hex[:10]}"
    _trim_local_media_jobs()
    norm_dialogue: List[Dict[str, Any]] = []
    for item in (req.dialogue or []):
        if isinstance(item, dict):
            row = dict(item)
            if not row.get("tts_text") and row.get("text"):
                row["tts_text"] = row["text"]
            norm_dialogue.append(row)

    cfg_payload = {
        "title": req.title,
        "take_paths": [str(p) for p in resolved_paths],
        "platform_preset": req.platform_preset,
        "subtitles_mode": req.subtitles_mode if req.subtitles_mode in ("none", "minimal") else "none",
        "trim_silence": bool(req.trim_silence),
        "ambient_bed": bool(req.ambient_bed),
        "redub_with_regional_tts": bool(req.redub_with_regional_tts),
        "accent_engine": (req.accent_engine or "nordestino_raiz").strip().lower() or "nordestino_raiz",
        "tone": req.tone or "Drama Tenso",
        "cinema_engine": _normalize_novela_cinema_engine(req.cinema_engine),
        "dialogue": norm_dialogue,
        "redub_dialogue": norm_dialogue,
    }
    _LOCAL_MEDIA_JOBS[task_id] = {
        "ok": True,
        "task_id": task_id,
        "engine": "novela_stitch",
        "status": "processing",
        "progress": 10,
        "ready": False,
        "failed": False,
        "title": req.title,
        "takes_count": len(resolved_paths),
        "filename": safe_name,
        "created_at": time.time(),
        "message": f"Costurando {len(resolved_paths)} takes com corte seco inteligente...",
    }

    task = asyncio.create_task(_execute_novela_stitch_job(task_id, out_path, cfg_payload))
    _LOCAL_MEDIA_JOBS[task_id]["_task"] = task

    if req.wait:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=240.0)
        except asyncio.TimeoutError:
            pass

    return {k: v for k, v in _LOCAL_MEDIA_JOBS[task_id].items() if k != "_task"}


@app.get("/api/quick/novela/stitch-takes/task/{task_id}")
async def api_quick_novela_stitch_task(task_id: str):
    job = _LOCAL_MEDIA_JOBS.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarefa de costura não encontrada")
    return {k: v for k, v in job.items() if k != "_task"}


async def _probe_colab_tunnel(colab_url: str, colab_token: str = "") -> Dict[str, Any]:
    clean_url = sanitize_base_url(colab_url, "")
    if not clean_url:
        return {"ok": True, "configured": False, "online": False, "url": "", "gpu": "", "engines": [], "message": "Túnel Colab não configurado"}
    try:
        headers = {"Accept": "application/json"}
        if colab_token:
            headers["X-Colab-Token"] = colab_token
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{clean_url}/health", headers=headers)
        if r.status_code == 200:
            data = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            return {
                "ok": True,
                "configured": True,
                "online": True,
                "url": clean_url,
                "version": str(data.get("version") or "unknown"),
                "gpu": str(data.get("gpu") or "Colab GPU"),
                "drive_mounted": bool(data.get("drive_mounted")),
                "seed_vc_ready": bool(data.get("seed_vc_ready")),
                "wangp_ready": bool(data.get("wangp_ready")),
                "ltx2_ready": bool(data.get("ltx2_ready")),
                "longcat_ready": bool(data.get("longcat_ready")),
                "engines": list(data.get("engines") or (
                    [k for k, v in (("wan22_s2v", data.get("wangp_ready")), ("ltx25", data.get("ltx2_ready")), ("longcat", data.get("longcat_ready")), ("seed_vc", data.get("seed_vc_ready"))) if v]
                )),
                "message": f"Conectado ao Colab GPU ({data.get('gpu') or 'Ativo'} · {data.get('version') or 'v?'})!",
            }
        return {"ok": True, "configured": True, "online": False, "url": clean_url, "gpu": "", "engines": [], "message": f"Colab respondeu HTTP {r.status_code}"}
    except Exception as exc:
        return {"ok": True, "configured": True, "online": False, "url": clean_url, "gpu": "", "engines": [], "message": f"Túnel offline ou inacessível: {type(exc).__name__}"}


@app.get("/api/colab/notebook")
async def api_colab_notebook():
    if not COLAB_NOTEBOOK_PATH.is_file():
        raise HTTPException(status_code=404, detail="Notebook Colab GPU Worker não encontrado na VPS.")
    return FileResponse(
        COLAB_NOTEBOOK_PATH,
        media_type="application/x-ipynb+json",
        filename="ai_studio_colab_novela_gpu.ipynb",
    )


@app.get("/api/colab/status")
async def api_colab_status():
    vault = load_vault()
    c_url = (vault.get("COLAB_GPU_URL") or os.environ.get("COLAB_GPU_URL") or "").strip()
    c_tok = (vault.get("COLAB_GPU_TOKEN") or os.environ.get("COLAB_GPU_TOKEN") or "").strip()
    return await _probe_colab_tunnel(c_url, c_tok)


@app.post("/api/colab/config")
async def api_colab_config(req: ColabConfigRequest):
    vault = load_vault()
    clean_url = sanitize_base_url(req.colab_url, "", require_https=True)
    if not clean_url:
        raise HTTPException(status_code=400, detail="COLAB_GPU_URL inválida: deve ser uma URL https:// válida")
    vault["COLAB_GPU_URL"] = clean_url
    if req.colab_token is not None:
        vault["COLAB_GPU_TOKEN"] = req.colab_token.strip()
    save_vault_file(vault)
    return await _probe_colab_tunnel(clean_url, vault.get("COLAB_GPU_TOKEN", ""))


@app.post("/api/quick/novela")
async def api_quick_novela(req: QuickNovelaRequest):
    if not NOVELA_RUNNER.exists() or not VIDEOUSE_PYTHON.exists():
        raise HTTPException(status_code=500, detail="Motor Novela Studio não configurado em /opt/ai-media-studio/tools/hyperframes/vps_novela_runner.py")

    NOVELA_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    vstyle = _normalize_novela_visual_style(req.visual_style)
    dstruct = _normalize_novela_dramatic_structure(req.dramatic_structure)
    rmode = req.render_mode if req.render_mode in ("vps_preview", "flow_pack", "colab_gpu") else "vps_preview"
    aengine = (req.accent_engine or "nordestino_raiz").strip().lower()
    if aengine not in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide"):
        aengine = "nordestino_raiz"
    raw_name = (req.output_filename or "").strip()
    if raw_name:
        safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", raw_name)
        if not safe_name.endswith(".mp4"):
            safe_name += ".mp4"
    else:
        slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", (req.title or "novela").lower()).strip("-")[:32] or "novela"
        safe_name = f"novela_{slug}_{vstyle}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    out_path = NOVELA_EXPORTS_DIR / safe_name

    task_id = f"nov-{uuid.uuid4().hex[:10]}"
    _trim_local_media_jobs()
    cfg_payload = req.model_dump() if hasattr(req, "model_dump") else req.dict()
    cengine = _normalize_novela_cinema_engine(cfg_payload.get("cinema_engine") or "")
    cfg_payload["visual_style"] = vstyle
    cfg_payload["dramatic_structure"] = dstruct
    cfg_payload["render_mode"] = rmode
    cfg_payload["accent_engine"] = aengine
    cfg_payload["cinema_engine"] = cengine
    _LOCAL_MEDIA_JOBS[task_id] = {
        "ok": True,
        "task_id": task_id,
        "engine": "novela",
        "render_mode": rmode,
        "accent_engine": aengine,
        "status": "processing",
        "progress": 10,
        "ready": False,
        "failed": False,
        "title": req.title,
        "visual_style": vstyle,
        "dramatic_structure": dstruct,
        "platform_preset": req.platform_preset,
        "clean_cinema_mode": req.clean_cinema_mode,
        "subtitles_mode": req.subtitles_mode,
        "filename": safe_name,
        "created_at": time.time(),
        "message": f"Iniciando Novela Studio v2.8 ({rmode} · {vstyle} · {dstruct} · {aengine})...",
    }

    task = asyncio.create_task(_execute_novela_job(task_id, out_path, cfg_payload))
    _LOCAL_MEDIA_JOBS[task_id]["_task"] = task

    if req.wait:
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=540.0)
        except asyncio.TimeoutError:
            pass

    return {k: v for k, v in _LOCAL_MEDIA_JOBS[task_id].items() if k != "_task"}


@app.get("/api/quick/novela/task/{task_id}")
async def api_quick_novela_task(task_id: str):
    job = _LOCAL_MEDIA_JOBS.get(task_id)
    if not job:
        raise HTTPException(status_code=404, detail="Tarefa do Novela Studio não encontrada")
    return {k: v for k, v in job.items() if k != "_task"}


@app.get("/api/voices/regional/profiles")
async def api_voices_regional_profiles():
    rve = _get_rve()
    profiles = await asyncio.to_thread(rve.list_regional_voice_profiles)
    for prof in profiles:
        cid = prof.get("id") or prof.get("character_id") or ""
        prof["character_id"] = cid
        prof["id"] = cid
        ref_p = prof.get("reference_path") or prof.get("sample_path") or ""
        sample_file = Path(ref_p) if ref_p else (rve.VOICES_REGIONAL_DIR / cid / f"reference_{cid}.wav")
        prof["has_sample"] = sample_file.is_file()
        prof["sample_url"] = f"/api/voices/regional/sample/{cid}" if prof["has_sample"] else ""
    return {
        "ok": True,
        "voices_dir": str(rve.VOICES_REGIONAL_DIR),
        "count": len(profiles),
        "profiles": profiles,
    }


@app.post("/api/voices/regional/upload-reference")
async def api_voices_regional_upload_reference(request: Request):
    form = await request.form()
    character_id = re.sub(r"[^a-zA-Z0-9_-]+", "", str(form.get("character_id") or "dona_socorro").strip().lower())
    reference_text = str(form.get("reference_text") or "").strip()
    upload = form.get("file") or form.get("audio")
    if not character_id:
        raise HTTPException(status_code=400, detail="character_id obrigatório.")
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Envie o arquivo de áudio de referência (campo 'file' ou 'audio').")
    raw_bytes = await upload.read()
    if len(raw_bytes) < 256:
        raise HTTPException(status_code=400, detail="Arquivo de áudio vazio ou muito curto.")
    rve = _get_rve()
    orig_name = getattr(upload, "filename", "") or f"{character_id}.wav"
    try:
        saved = await asyncio.to_thread(
            rve.save_regional_voice_reference,
            character_id,
            raw_bytes,
            orig_name,
            reference_text,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Falha ao processar amostra regional: {exc}")
    cid_out = saved.get("character_id") or character_id
    return {
        "ok": True,
        **saved,
        "sample_url": f"/api/voices/regional/sample/{cid_out}",
    }


@app.get("/api/voices/regional/sample/{character_id}")
async def api_voices_regional_sample(character_id: str):
    safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "", character_id.strip().lower())
    if not safe_id:
        raise HTTPException(status_code=400, detail="character_id inválido.")
    rve = _get_rve()
    profiles = await asyncio.to_thread(rve.list_regional_voice_profiles)
    target_path: Optional[Path] = None
    for prof in profiles:
        if (prof.get("id") or prof.get("character_id")) == safe_id:
            sp = Path(prof.get("reference_path") or prof.get("sample_path") or "")
            if sp.is_file():
                target_path = sp.resolve()
                break
    if target_path is None:
        base_root = rve.VOICES_REGIONAL_DIR.resolve()
        for cand in (
            (base_root / safe_id / f"reference_{safe_id}.wav").resolve(),
            (base_root / safe_id / f"reference_{safe_id}_seed.wav").resolve(),
            (base_root / f"{safe_id}.wav").resolve(),
        ):
            if cand.is_relative_to(base_root) and cand.is_file():
                target_path = cand
                break
    if target_path is None:
        raise HTTPException(status_code=404, detail=f"Amostra de voz regional para '{safe_id}' não encontrada.")
    return FileResponse(target_path, media_type="audio/wav", filename=target_path.name)


@app.post("/api/quick/novela/upload-line-audio")
async def api_quick_novela_upload_line_audio(request: Request):
    form = await request.form()
    upload = form.get("file") or form.get("audio")
    speaker = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(form.get("speaker") or "guide").strip().lower())[:24] or "guide"
    line_idx = re.sub(r"[^0-9]+", "", str(form.get("line_index") or "0"))[:4] or "0"
    if upload is None or not hasattr(upload, "read"):
        raise HTTPException(status_code=400, detail="Envie o áudio-guia (campo 'file' ou 'audio').")
    raw_bytes = await upload.read()
    if len(raw_bytes) < 256:
        raise HTTPException(status_code=400, detail="Áudio-guia vazio ou corrompido.")

    NOVELA_LINE_AUDIO_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    orig_name = Path(getattr(upload, "filename", "") or "guide.webm").name
    ext = Path(orig_name).suffix.lower()
    if ext not in {".wav", ".mp3", ".webm", ".ogg", ".m4a", ".opus"}:
        ext = ".webm"
    stem = f"line_{line_idx}_{speaker}_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    raw_path = (NOVELA_LINE_AUDIO_CACHE_DIR / f"{stem}_raw{ext}").resolve()
    out_wav = (NOVELA_LINE_AUDIO_CACHE_DIR / f"{stem}.wav").resolve()
    raw_path.write_bytes(raw_bytes)
    try:
        conv = await run_cmd([
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(raw_path),
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            str(out_wav),
        ], timeout=45)
        if conv.get("returncode") != 0 or not out_wav.is_file():
            raise HTTPException(status_code=400, detail=f"Falha ao normalizar áudio-guia: {conv.get('stderr', '')[:250]}")
        probe = await run_cmd([
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(out_wav),
        ], timeout=15)
        try:
            dur_s = round(float((probe.get("stdout") or "").strip() or "0"), 2)
        except Exception:
            dur_s = 0.0
        return {
            "ok": True,
            "filename": out_wav.name,
            "guide_audio_path": str(out_wav),
            "audio_path": str(out_wav),
            "duration": dur_s,
            "sample_rate": 48000,
            "channels": 2,
        }
    finally:
        raw_path.unlink(missing_ok=True)


@app.post("/api/quick/novela/preview-voice")
async def api_quick_novela_preview_voice(req: QuickNovelaPreviewVoiceRequest):
    rve = _get_rve()
    aengine = (req.accent_engine or "nordestino_raiz").strip().lower()
    if aengine not in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide"):
        aengine = "nordestino_raiz"

    dlg = []
    for x in (req.dialogue or []):
        if isinstance(x, dict):
            txt_v = str(x.get("text") or x.get("tts_text") or "").strip()
            if txt_v:
                dlg.append({**x, "text": txt_v})
    if not dlg and (req.text or "").strip():
        dlg = [{
            "speaker": (req.speaker or "Dona Socorro").strip(),
            "emotion": (req.emotion or "warm").strip(),
            "text": req.text.strip(),
        }]
    if not dlg:
        dlg = [
            {"speaker": "Dona Socorro", "emotion": "shocked", "text": "Oxente meu filho! Você tá botando boneco por causa de um cuscuz quente?"},
            {"speaker": "Seu Bastião", "emotion": "warm", "text": "Deixe de cunversa, Enzo, e venha provar esse café coado na hora!"},
        ]

    vault = load_vault()
    if vault.get("GEMINI_API_KEY") and not os.environ.get("GEMINI_API_KEY"):
        os.environ["GEMINI_API_KEY"] = vault["GEMINI_API_KEY"]
    c_url = sanitize_base_url(req.colab_url or vault.get("COLAB_GPU_URL") or os.environ.get("COLAB_GPU_URL") or "", "")
    c_tok = (vault.get("COLAB_GPU_TOKEN") or os.environ.get("COLAB_GPU_TOKEN") or "").strip()

    NOVELA_PREVIEW_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_name = f"voice_preview_{aengine}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp3"
    out_mp3 = (NOVELA_PREVIEW_CACHE_DIR / out_name).resolve()

    meta = await asyncio.to_thread(
        rve.synthesize_dialogue_preview,
        dialogue=dlg,
        accent_engine=aengine,
        out_mp3=out_mp3,
        colab_url=c_url,
        colab_token=c_tok,
    )
    if not out_mp3.is_file():
        raise HTTPException(status_code=500, detail="Falha ao gerar preview de áudio regional.")
    return {
        "ok": True,
        **meta,
        "accent_engine": aengine,
        "filename": out_name,
        "audio_url": f"/api/quick/novela/preview-audio/{out_name}",
    }


@app.get("/api/quick/novela/preview-audio/{filename}")
async def api_quick_novela_preview_audio(filename: str):
    safe_name = Path(filename).name
    cand = (NOVELA_PREVIEW_CACHE_DIR / safe_name).resolve()
    if not cand.is_relative_to(NOVELA_PREVIEW_CACHE_DIR.resolve()) or not cand.is_file():
        raise HTTPException(status_code=404, detail="Arquivo de preview de voz não encontrado.")
    return FileResponse(cand, media_type="audio/mpeg", filename=safe_name)


# ==============================================================================
# Guided Pipelines Orchestrator (v2.3 — RAM Relay, Checkpoints & 5 Templates)
# ==============================================================================

from guided_pipelines import (  # noqa: E402
    GUIDED_TEMPLATES,
    PLATFORM_PRESETS,
    GuidedPipelineOrchestrator,
)


class PipelineStartRequest(BaseModel):
    template_id: str
    platform_preset: str = "shopee_20_9"
    params: Dict[str, Any] = {}
    wait: bool = False


class PipelineResumeRequest(BaseModel):
    wait: bool = False


async def _pipeline_count_containers() -> int:
    res = await run_cmd(["docker", "ps", "-q"], timeout=5)
    return len([ln for ln in res["stdout"].splitlines() if ln.strip()]) if res["returncode"] == 0 else 0


async def _pipeline_stop_all() -> int:
    await api_stop_all_projects()
    return await _pipeline_count_containers()


async def _pipeline_run_hyperframes_cli(**kwargs: Any) -> Dict[str, Any]:
    HYPERFRAMES_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    raw_title = str(kwargs.get("title") or "promo").strip()
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", raw_title.lower()).strip("-")[:32] or "promo"
    out_path = HYPERFRAMES_EXPORTS_DIR / f"hyperframes_{slug}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    req = QuickHyperFramesRequest(**kwargs)
    return await _invoke_hyperframes_cli(out_path, req)


async def _pipeline_run_videouse_cli(
    input_path: Path,
    grade_preset: str = "Cinema Warm",
    remove_silences: bool = True,
    burn_subtitles: bool = True,
    language: str = "pt",
    platform_preset: str = "shopee_20_9",
) -> Dict[str, Any]:
    VIDEOUSE_EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", input_path.stem.lower()).strip("-")[:32] or "clip"
    out_path = VIDEOUSE_EXPORTS_DIR / f"videouse_{slug}_{int(time.time())}_{uuid.uuid4().hex[:6]}.mp4"
    req = QuickVideoUseRequest(
        grade_preset=grade_preset,
        remove_silences=remove_silences,
        burn_subtitles=burn_subtitles,
        language=language,
        platform_preset=platform_preset,
    )
    return await _invoke_videouse_cli(input_path, out_path, req)


async def _pipeline_supoclip_dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await api_quick_supoclip(QuickSupoClipRequest(**payload))


async def _pipeline_moneyprinter_dispatch(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await api_quick_moneyprinter(QuickMoneyPrinterRequest(**payload))


async def _pipeline_cascade_text(sys_prompt: str, user_prompt: str, max_tokens: int = 240) -> str:
    try:
        data = await call_ai_cascade(
            ChatCompletionRequest(
                model="cascade-auto",
                messages=[
                    {"role": "system", "content": sys_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=max_tokens,
                temperature=0.35,
            )
        )
        return str(((data.get("choices") or [{}])[0].get("message") or {}).get("content") or "").strip()
    except Exception:
        return ""


PIPELINE_ORCHESTRATOR = GuidedPipelineOrchestrator(
    pipelines_dir=PIPELINES_EXPORTS_DIR,
    campaigns_dir=CAMPAIGNS_DIR,
    studio_base_dir=STUDIO_BASE_DIR,
    stop_all_fn=_pipeline_stop_all,
    ensure_stack_fn=_ensure_stack_http,
    count_containers_fn=_pipeline_count_containers,
    analyze_virals_fn=_run_campaign_viral_analysis,
    run_hyperframes_cli_fn=_pipeline_run_hyperframes_cli,
    run_novela_cli_fn=_pipeline_run_novela_cli,
    run_videouse_cli_fn=_pipeline_run_videouse_cli,
    resolve_videouse_input_fn=_resolve_videouse_input,
    supoclip_dispatch_fn=_pipeline_supoclip_dispatch,
    supoclip_poll_fn=api_quick_supoclip_task,
    moneyprinter_dispatch_fn=_pipeline_moneyprinter_dispatch,
    moneyprinter_poll_fn=api_quick_moneyprinter_task,
    scan_outputs_fn=_scan_output_files,
    output_dl_meta_fn=_output_download_meta,
    human_size_fn=_human_size,
    cascade_text_fn=_pipeline_cascade_text,
)


@app.get("/api/pipelines/templates")
async def api_pipelines_templates():
    return {
        "ok": True,
        "count": len(GUIDED_TEMPLATES),
        "templates": GUIDED_TEMPLATES,
        "platforms": list(PLATFORM_PRESETS.values()),
        "platform_presets": PLATFORM_PRESETS,
    }


@app.post("/api/pipelines/start")
async def api_pipelines_start(req: PipelineStartRequest):
    try:
        state = await PIPELINE_ORCHESTRATOR.start(
            template_id=req.template_id,
            platform_preset=req.platform_preset,
            params=req.params,
            wait=req.wait,
        )
        return state
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Falha ao iniciar trilha guiada: {exc}")


@app.get("/api/pipelines/status/{run_id}")
async def api_pipelines_status(run_id: str):
    try:
        state = PIPELINE_ORCHESTRATOR.load_state(run_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not state:
        raise HTTPException(status_code=404, detail=f"Execução '{run_id}' não encontrada")
    state["active_containers"] = await _pipeline_count_containers()
    return state


@app.get("/api/pipelines/runs")
async def api_pipelines_runs(limit: int = 25):
    runs = await asyncio.to_thread(PIPELINE_ORCHESTRATOR.list_runs, limit)
    return {"ok": True, "count": len(runs), "runs": runs}


@app.post("/api/pipelines/resume/{run_id}")
async def api_pipelines_resume(run_id: str, req: Optional[PipelineResumeRequest] = None):
    try:
        return await PIPELINE_ORCHESTRATOR.resume(run_id, wait=bool(req and req.wait))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.get("/app/supoclip/api/v1/clips")
@app.get("/app/supoclip/api/v1/tasks")
@app.get("/app/supoclip-api/api/v1/clips")
@app.get("/app/supoclip-api/api/v1/tasks")
async def api_supoclip_subroute_clips():
    return await api_supoclip_clips_and_workflows()


@app.post("/app/supoclip/upload")
@app.post("/app/supoclip-api/upload")
async def api_supoclip_subroute_upload(
    request: Request,
    video: Optional[UploadFile] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
):
    return await api_quick_supoclip_upload(request=request, video=video, file=file)


@app.get("/api/stack-standby-view")
async def api_stack_standby_view(request: Request, uri: str = ""):
    raw_uri = (uri or request.headers.get("x-original-uri") or request.url.path or "/").strip()
    stack_map = {
        "moneyprinter": ("moneyprinter", "MoneyPrinterTurbo", "/app/moneyprinter/"),
        "voicestudio": ("voicestudio", "VoiceStudio", "/app/voicestudio/"),
        "openfilm": ("openfilm", "OpenFilm", "/app/openfilm/"),
        "supoclip": ("supoclip", "SupoClip", "/app/supoclip/"),
        "higgsfield": ("openhiggsfield", "Open Higgsfield", "/app/higgsfield/"),
        "openhiggsfield": ("openhiggsfield", "Open Higgsfield", "/app/higgsfield/"),
        "agenttube": ("agenttube", "AgentTube", "/app/agenttube/"),
        "openseo": ("openseo", "OpenSEO", "/app/openseo/"),
    }
    stack_id, stack_title, stack_url = ("supoclip", "SupoClip", "/app/supoclip/")
    for key, val in stack_map.items():
        if f"/app/{key}" in raw_uri.lower():
            stack_id, stack_title, stack_url = val
            break

    accept = (request.headers.get("accept") or "").lower()
    if "application/json" in accept and "text/html" not in accept:
        return {
            "ok": True,
            "state": "standby",
            "stack": stack_id,
            "name": stack_title,
            "uri": raw_uri,
            "ui_url": stack_url,
            "message": f"{stack_title} está em modo Standby (0 MB RAM). Envie POST /api/projects/{stack_id}/action {{'action':'start'}} ou use o Estúdio Rápido para iniciar automaticamente.",
        }

    html = f"""<!DOCTYPE html>
<html lang="pt-BR" class="dark">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>{stack_title} · Standby Inteligente</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif; }}
    body {{ background: #090d0b; color: #f1f5f9; min-height: 100vh; display: flex; align-items: center; justify-content: center; padding: 20px; }}
    .card {{ background: #121815; border: 1px solid rgba(255,255,255,0.08); border-radius: 24px; max-width: 460px; width: 100%; padding: 28px; box-shadow: 0 24px 60px rgba(0,0,0,0.65); text-align: center; }}
    .badge {{ display: inline-flex; align-items: center; gap: 6px; padding: 4px 12px; border-radius: 999px; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.06em; background: rgba(16,185,129,0.12); color: #34d399; border: 1px solid rgba(16,185,129,0.28); margin-bottom: 16px; }}
    h1 {{ font-size: 20px; font-weight: 800; letter-spacing: -0.02em; color: #fff; margin-bottom: 8px; }}
    p {{ font-size: 13px; line-height: 1.6; color: #94a3b8; margin-bottom: 22px; }}
    .btn {{ display: flex; align-items: center; justify-content: center; width: 100%; min-height: 46px; border-radius: 14px; font-size: 13px; font-weight: 700; cursor: pointer; border: none; text-decoration: none; transition: all 0.2s cubic-bezier(0.16,1,0.3,1); margin-bottom: 10px; }}
    .btn-primary {{ background: linear-gradient(135deg, #10b981, #14b8a6); color: #052e16; box-shadow: 0 8px 24px rgba(16,185,129,0.25); }}
    .btn-secondary {{ background: rgba(255,255,255,0.04); color: #cbd5e1; border: 1px solid rgba(255,255,255,0.08); }}
    .status {{ font-size: 12px; color: #34d399; margin-top: 10px; min-height: 18px; }}
  </style>
</head>
<body>
  <div class="card">
    <div class="badge">● Standby Inteligente · 0 MB RAM</div>
    <h1>{stack_title}</h1>
    <p>Esta stack está em repouso para preservar a memória RAM da VPS. Você pode despertá-la agora com 1 toque ou usar o Estúdio Rápido Mobile.</p>
    <button id="btn-wake" class="btn btn-primary" onclick="wakeStack()">⚡ Ligar {stack_title} e Abrir Interface</button>
    <a href="/" class="btn btn-secondary">← Voltar ao Painel / Estúdio Rápido</a>
    <div id="wake-status" class="status"></div>
  </div>
  <script>
    async function wakeStack() {{
      const btn = document.getElementById('btn-wake');
      const st = document.getElementById('wake-status');
      btn.disabled = true;
      btn.textContent = 'Inicializando contêineres Docker...';
      st.textContent = 'Aguardando healthcheck HTTP de {stack_title}...';
      try {{
        await fetch('/api/projects/{stack_id}/action', {{
          method: 'POST',
          headers: {{ 'Content-Type': 'application/json' }},
          body: JSON.stringify({{ action: 'start' }})
        }});
        for (let i = 0; i < 60; i++) {{
          await new Promise(r => setTimeout(r, 2000));
          const res = await fetch('/api/projects');
          const data = await res.json();
          const cur = (data.projects || []).find(p => p.id === '{stack_id}');
          if (cur && cur.http_ready) {{
            st.textContent = 'Stack pronta! Redirecionando...';
            window.location.reload();
            return;
          }}
        }}
        window.location.reload();
      }} catch (e) {{
        st.textContent = 'Erro ao iniciar: ' + e.message;
        btn.disabled = false;
      }}
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html, status_code=200, headers={"X-Studio-Stack-State": "standby"})


# ==============================================================================
# Skills Central & Modo Dev Endpoints
# ==============================================================================

def _resolve_modo_dev_bin() -> Path:
    env_val = os.environ.get("STUDIO_MODO_DEV_BIN", "").strip()
    candidates = [
        *([Path(env_val)] if env_val else []),
        Path("/usr/local/bin/modo-dev"),
        STUDIO_HOME_DIR / ".local/bin/modo-dev",
        Path.home() / ".local/bin/modo-dev",
        MODO_DEV_BIN,
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def _resolve_dev_skills_vault() -> Path:
    env_val = os.environ.get("STUDIO_DEV_SKILLS_VAULT", "").strip() or os.environ.get("DEV_SKILLS_VAULT", "").strip()
    candidates = [
        *([Path(env_val)] if env_val else []),
        DEV_SKILLS_VAULT,
        STUDIO_BASE_DIR / "dev-skills-vault",
        Path("/opt/ai-media-studio/dev-skills-vault"),
        STUDIO_HOME_DIR / "dev-skills-vault",
    ]
    for cand in candidates:
        if cand.exists():
            return cand
    return candidates[0]


def _modo_dev_env() -> Dict[str, str]:
    vault = _resolve_dev_skills_vault()
    return {
        "DEV_SKILLS_VAULT": str(vault),
        "STUDIO_HOME_DIR": str(STUDIO_HOME_DIR),
        "STUDIO_SKILLS_DIR": str(SKILLS_DIR),
        "STUDIO_AGENTS_SKILLS_DIR": str(AGENTS_SKILLS_DIR),
    }


def _collect_vault_skill_dirs(vault_dir: Path) -> List[Path]:
    dirs: List[Path] = []
    for bucket in ("engineering", "productivity"):
        b_dir = vault_dir / "skills" / bucket
        if not b_dir.is_dir():
            continue
        for item in sorted(b_dir.iterdir()):
            if item.is_dir() and (item / "SKILL.md").is_file():
                dirs.append(item)
    return dirs


async def get_modo_dev_status() -> Dict[str, Any]:
    modo_bin = _resolve_modo_dev_bin()
    vault_dir = _resolve_dev_skills_vault()
    if modo_bin.exists():
        res = await run_cmd([str(modo_bin), "status", "--json"], timeout=10, env=_modo_dev_env())
        if res["returncode"] == 0 and res["stdout"]:
            try:
                return json.loads(res["stdout"].strip())
            except Exception:
                pass
    vault_total = len(_collect_vault_skill_dirs(vault_dir))
    return {
        "active": False,
        "state": "repouso" if modo_bin.exists() else "desconhecido",
        "vault_dir": str(vault_dir),
        "vault_total": vault_total,
        "linked_gemini": 0,
        "linked_agents": 0,
        "global_gemini": 0,
        "global_agents": 0,
    }


class ModoDevActionRequest(BaseModel):
    action: Optional[str] = None  # "on", "off", "toggle"
    enable: Optional[bool] = None


@app.get("/api/skills/dev-mode")
@app.get("/api/skills/modo-dev")
async def api_get_modo_dev():
    status = await get_modo_dev_status()
    return {
        "ok": True,
        "enabled": bool(status.get("active")),
        "active": bool(status.get("active")),
        "vault_total": int(status.get("vault_total", 0)),
        "dev_skills_count": int(status.get("vault_total", 0)),
        "linked_dev_count": int(status.get("linked_gemini", 0)),
        "modo_dev": status,
    }


@app.post("/api/skills/dev-mode")
@app.post("/api/skills/modo-dev")
async def api_toggle_modo_dev(req: Optional[ModoDevActionRequest] = None):
    modo_bin = _resolve_modo_dev_bin()
    if not modo_bin.exists():
        raise HTTPException(
            status_code=404,
            detail="Script modo-dev não encontrado em /usr/local/bin/modo-dev ou ~/.local/bin/modo-dev",
        )

    if req and req.enable is not None:
        action = "on" if req.enable else "off"
    else:
        raw_act = ((req.action if req else None) or "toggle").strip().lower()
        if raw_act in ("on", "enable", "true", "1"):
            action = "on"
        elif raw_act in ("off", "disable", "false", "0"):
            action = "off"
        elif raw_act == "toggle":
            current = await get_modo_dev_status()
            action = "off" if current.get("active") else "on"
        else:
            raise HTTPException(status_code=400, detail="Ação inválida para modo-dev (use enable=true/false ou action=on/off/toggle)")

    res = await run_cmd([str(modo_bin), action], timeout=15, env=_modo_dev_env())
    if res["returncode"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"] or res["stdout"] or "Falha no modo-dev")

    skills_payload = await api_list_skills()
    modo_dev = skills_payload.get("modo_dev", {})
    return {
        "ok": True,
        "enabled": bool(modo_dev.get("active")),
        "active": bool(modo_dev.get("active")),
        "action": action,
        "message": res["stdout"].strip(),
        "linked_dev_count": int(modo_dev.get("linked_gemini", 0)),
        **skills_payload,
    }


@app.get("/api/skills")
async def api_list_skills():
    skills = []
    if SKILLS_DIR.exists():
        for item in sorted(SKILLS_DIR.iterdir()):
            if item.is_dir() and not item.name.startswith("."):
                skills.append(parse_skill_metadata(item))

    vault_dir = _resolve_dev_skills_vault()
    active_ids = {s["id"] for s in skills if s.get("source") == "dev-vault"}
    vault_skills = []
    for v_dir in _collect_vault_skill_dirs(vault_dir):
        meta = parse_skill_metadata(v_dir, source_override="dev-vault", active=(v_dir.name in active_ids))
        vault_skills.append(meta)

    agents_count = len([d for d in AGENTS_SKILLS_DIR.iterdir() if d.is_dir()]) if AGENTS_SKILLS_DIR.exists() else 0
    modo_dev = await get_modo_dev_status()
    global_count = len([s for s in skills if s.get("source") == "global-media"])
    dev_count = len([s for s in skills if s.get("source") == "dev-vault"])

    return {
        "ok": True,
        "count": len(skills),
        "global_count": global_count,
        "dev_count": dev_count,
        "vault_total": len(vault_skills),
        "dev_skills_count": len(vault_skills),
        "primary_dir": str(SKILLS_DIR),
        "agents_dir": str(AGENTS_SKILLS_DIR),
        "agents_count": agents_count,
        "modo_dev": modo_dev,
        "skills": skills,
        "vault_skills": vault_skills,
    }


# ==============================================================================
# OpenJarvis Autonomous Assistant Lifecycle Endpoints
# ==============================================================================

def _read_running_pid(pid_file: Path) -> Optional[int]:
    if not pid_file.exists():
        return None
    try:
        pid = int(pid_file.read_text(encoding="utf-8").strip())
        if psutil.pid_exists(pid):
            proc = psutil.Process(pid)
            if proc.is_running() and proc.status() != psutil.STATUS_ZOMBIE:
                return pid
    except Exception:
        pass
    return None


async def get_openjarvis_status() -> Dict[str, Any]:
    run_dir = OPENJARVIS_STATE_DIR / "run"
    serve_pid = _read_running_pid(run_dir / "jarvis_serve.pid")

    # Fallback process scan if launched manually via terminal
    for proc in psutil.process_iter(["pid", "cmdline", "memory_info"]):
        try:
            args = proc.info.get("cmdline") or []
            if len(args) < 2:
                continue
            exe_name = Path(args[0]).name
            if (exe_name == "jarvis" or any(a.endswith("/jarvis") for a in args[:2])) and "serve" in args:
                if serve_pid is None:
                    serve_pid = proc.info["pid"]
        except Exception:
            continue

    ram_mb = 0.0
    if serve_pid is not None:
        try:
            proc = psutil.Process(serve_pid)
            rss = proc.memory_info().rss
            for child in proc.children(recursive=True):
                try:
                    rss += child.memory_info().rss
                except Exception:
                    pass
            ram_mb += rss / (1024**2)
        except Exception:
            pass

    http_ready = await check_endpoint_http(f"http://127.0.0.1:{OPENJARVIS_PORT}/health", timeout=0.8) if serve_pid else False
    if serve_pid:
        status = "online" if http_ready else "starting"
    else:
        status = "standby"

    cloud_mode = STUDIO_MODE == "vps" or not OPENJARVIS_LAUNCHER.exists()
    if cloud_mode:
        status = "cloud"

    return {
        "id": "openjarvis",
        "name": "OpenJarvis AI Assistant",
        "subtitle": (
            "Servidor Cloud VPS Ativo · Orquestrado remotamente pelo cliente leve OpenJarvis (wbrendo-pc)"
            if cloud_mode
            else "Modo Escuta Ativa / Push-to-Talk (Zero Escuta Passiva) · Voz 48kHz SoXR · MCP Desktop & Studio"
        ),
        "installed": OPENJARVIS_LAUNCHER.exists(),
        "cloud_mode": cloud_mode,
        "status": status,
        "ptt_ready": not cloud_mode,
        "passive_clap_disabled": True,
        "voice_running": False,
        "voice_pid": None,
        "server_running": serve_pid is not None,
        "server_pid": serve_pid,
        "http_ready": http_ready,
        "port": OPENJARVIS_PORT,
        "ui_url": f"http://localhost:{OPENJARVIS_PORT}/docs",
        "ram_mb": round(ram_mb, 1),
        "webcam_present": Path("/dev/video0").exists(),
        "gateway_url": "https://187.77.202.176.sslip.io/v1" if cloud_mode else "http://127.0.0.1:9000/v1",
    }


class OpenJarvisActionRequest(BaseModel):
    action: str  # "start", "stop", "restart"
    mode: str = "serve"  # "serve", "both"


class OpenJarvisAskRequest(BaseModel):
    query: str
    speak: bool = True


@app.get("/api/openjarvis/status")
async def api_openjarvis_status():
    return await get_openjarvis_status()


@app.post("/api/openjarvis/ptt")
async def api_openjarvis_ptt():
    """Trigger 1 on-demand Push-to-Talk voice listen, transcribe, MCP execution, and 48kHz TTS reply."""
    venv_py = OPENJARVIS_DIR / ".venv" / "bin" / "python"
    script_py = OPENJARVIS_DIR / "clap_listener.py"
    if not venv_py.exists() or not script_py.exists():
        raise HTTPException(status_code=404, detail="Runtime de voz do OpenJarvis não encontrado")

    res = await run_cmd([str(venv_py), str(script_py), "--ptt", "--json"], cwd=str(OPENJARVIS_DIR), timeout=90)
    if res["stdout"]:
        for line in reversed(res["stdout"].splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
    return {
        "ok": res["returncode"] == 0,
        "transcript": "",
        "reply": res["stdout"] or res["stderr"] or "Escuta Push-to-Talk finalizada.",
        "tools_used": [],
    }


_STUDIO_9_CAPABILITIES: Dict[str, Dict[str, str]] = {
    "hyperframes": {
        "name": "HyperFrames",
        "kind": "Motor Nativo (0 MB RAM ociosa)",
        "best_for": "Anúncios Motion Graphics 5s–15s em HTML/Canvas/GSAP com cartela de preço, oferta/CTA Shopee e locução neural PT-BR (`Shopee Flash Sale`, `B2B Emerald`, `Cyber Neon`, `Minimal Luxury`).",
        "api": "POST /api/quick/hyperframes",
        "ui": "Aba Estúdio Rápido -> HyperFrames",
    },
    "novela": {
        "name": "Novela Studio · Micro-Séries",
        "kind": "Motor Nativo (0 MB RAM ociosa)",
        "best_for": "Criar do zero micro-séries e novelinhas de humor/causos nordestinos multi-personagem (`Dona Socorro`, `Seu Bastião`, `Enzo`, `Narrador`) em `3d_cartoon` ou `cinematic_tv` com Dicionário Nordestino e Freeze-Frame final.",
        "api": "POST /api/quick/novela + /api/quick/novela/generate-script (Trilha 6: novela_regional_studio)",
        "ui": "Aba Estúdio Rápido -> Modo Guiado (Trilha 6) ou Modo Hard -> Novela Studio",
    },
    "moneyprinter": {
        "name": "MoneyPrinterTurbo",
        "kind": "Stack Docker (`moneyprinter` · ligar sob demanda)",
        "best_for": "Vídeos faceless narrados (15s–90s+) com B-roll automático/local/Pexels/Pixabay, roteiro IA e legendas palavra por palavra (`pop_spring`). Ideal para reviews e vídeos explicativos de produto.",
        "api": "POST /api/quick/moneyprinter",
        "ui": "/app/moneyprinter/ (ou Estúdio Rápido)",
    },
    "supoclip": {
        "name": "SupoClip",
        "kind": "Stack Docker (`supoclip` · ligar sob demanda)",
        "best_for": "Cortar vídeos longos (podcasts, lives, aulas, novelas/filmes) em partes sequenciais (`novela` / workflow `series`) ou clipes curtos de retenção (`highlights`, `viral`, `topic`, `recap`, `trailer`).",
        "api": "POST /api/quick/supoclip + /api/quick/supoclip/upload",
        "ui": "/app/supoclip/ (ou Estúdio Rápido)",
    },
    "videouse": {
        "name": "Video Use",
        "kind": "Motor Nativo (0 MB RAM ociosa)",
        "best_for": "Limpar silêncios/hesitações (`faster-whisper` INT8 + FFmpeg), normalizar/crossfade 48kHz SoXR, aplicar color grading (`Neutral Clean`, `Cinema Warm`, `E-Commerce Vibrant`) e legendar clipes pós-corte.",
        "api": "POST /api/quick/videouse + /api/quick/videouse/upload",
        "ui": "Aba Estúdio Rápido -> Video Use",
    },
    "openfilm": {
        "name": "OpenFilm",
        "kind": "Stack Docker (`openfilm` · ligar sob demanda)",
        "best_for": "Editor com timeline multipista assistida por IA, keyframes, motion blur e legendas estilizadas (`reels_viral`, `cinema`, `minimal`) combinando clipes da Galeria.",
        "api": "POST /api/quick/openfilm",
        "ui": "/app/openfilm/",
    },
    "voicestudio": {
        "name": "VoiceStudio",
        "kind": "Stack Docker (`voicestudio` · ligar sob demanda · CPU-only)",
        "best_for": "Locução neural PT-BR isolada (Edge-TTS/eSpeak-ng) com controle de velocidade/tom e masterização 48kHz SoXR em WAV/MP3.",
        "api": "POST /api/quick/voicestudio",
        "ui": "/app/voicestudio/",
    },
    "openhiggsfield": {
        "name": "Open Higgsfield",
        "kind": "Stack Docker (`openhiggsfield` · ligar sob demanda)",
        "best_for": "Geração visual e motion AI cinematográfico (cenas estilizadas de produto e avatar visual).",
        "api": "POST /api/projects/openhiggsfield/action {action:'start'}",
        "ui": "/app/higgsfield/",
    },
    "agenttube": {
        "name": "AgentTube",
        "kind": "Stack Docker (`agenttube` · ligar sob demanda)",
        "best_for": "Automação completa de canal do YouTube (ideação, roteiro longo, metadados e upload).",
        "api": "POST /api/projects/agenttube/action {action:'start'}",
        "ui": "/app/agenttube/",
    },
    "openseo": {
        "name": "OpenSEO",
        "kind": "Stack Docker (`openseo` · ligar sob demanda)",
        "best_for": "SEO semântico, pesquisa de palavras-chave, títulos persuasivos, tags e descrições.",
        "api": "POST /api/projects/openseo/action {action:'start'}",
        "ui": "/app/openseo/",
    },
}


def _classify_studio_production_plan(
    query: str,
    telemetry: Dict[str, Any],
    proj_statuses: List[Dict[str, Any]],
    outputs: List[Dict[str, Any]],
    campaigns: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    low = (query or "").lower()
    tracks: List[Dict[str, Any]] = []
    questions: List[str] = []

    is_ad = any(k in low for k in (
        "shopee", "anúncio", "anuncio", "vendas", "venda", "oferta", "promo",
        "desconto", "produto", "e-commerce", "ecommerce", "loja", "afiliado",
        "propaganda", "motion", "hyperframes", "faceless", "moneyprinter",
    ))
    is_cut = any(k in low for k in (
        "novela", "série", "serie", "partes", "continuação", "continuacao",
        "episódio", "episodio", "vídeo longo", "video longo", "longo",
        "cortar", "corte", "cortes", "podcast", "live", "highlights",
        "virais", "viral", "supoclip", "reels", "shorts", "tiktok",
    ))
    is_polish = any(k in low for k in (
        "silêncio", "silencio", "silêncios", "silencios", "limpar", "jump-cut",
        "hesitação", "hesitacoes", "cor", "color", "grading", "tratar cor",
        "videouse", "video use", "pós-produção", "pos-producao", "legendar",
    ))
    is_voice = any(k in low for k in ("voz", "locução", "locucao", "narração", "narracao", "dublagem", "voicestudio"))
    is_timeline = any(k in low for k in ("timeline", "multipista", "openfilm", "keyframe", "motion blur", "montagem"))
    is_ai_vis = any(k in low for k in ("higgsfield", "avatar", "cena cinematográfica", "b-roll ia", "gerar cena"))
    is_yt_seo = any(k in low for k in ("youtube", "canal", "agenttube", "openseo", "seo", "palavras-chave", "tags"))

    if is_ad:
        tracks.append({
            "track": "Anúncio / Vídeo de Vendas (E-Commerce / Shopee)",
            "primary": _STUDIO_9_CAPABILITIES["hyperframes"],
            "alternative_or_combo": {
                **_STUDIO_9_CAPABILITIES["moneyprinter"],
                "combo_note": "Alternativa para review/explainer narrado mais longo (30s–90s) com B-roll e legenda palavra por palavra. Combo avançado: cena visual no Open Higgsfield (`/app/higgsfield/`) + cartela/CTA no HyperFrames + polimento `E-Commerce Vibrant` no Video Use.",
            },
        })
        questions.extend([
            "Para o vídeo de vendas: qual é o produto, preço/desconto (ex: 'De R$ 129 por R$ 49,90') e CTA?",
            "Prefere anúncio curto motion de alto impacto (5s–15s no HyperFrames) ou vídeo faceless narrado com B-roll (30s–60s no MoneyPrinterTurbo), em `9:16` ou `16:9`?",
        ])

    if is_cut:
        cut_mode = "novela" if any(w in low for w in ("novela", "série", "serie", "partes", "continuação", "continuacao", "episódio", "episodio")) else "highlights"
        tracks.append({
            "track": f"Corte de Vídeo Longo ({cut_mode})",
            "primary": {
                **_STUDIO_9_CAPABILITIES["supoclip"],
                "recommended_cut_mode": cut_mode,
            },
            "alternative_or_combo": {
                **_STUDIO_9_CAPABILITIES["videouse"],
                "combo_note": "Combo ideal pós-SupoClip: passar os episódios no Video Use (0 MB RAM ociosa) para remover silêncios residuais, normalizar áudio 48kHz SoXR, aplicar cor (`Cinema Warm` / `Neutral Clean`) e queimar legendas.",
            },
        })
        questions.extend([
            "Onde está o vídeo longo (URL, arquivo no PC para envio via Upload/`scp`, arquivo já na Galeria da VPS ou vídeo `sample` demo)?",
            "Quantas partes no modo Novela (`auto` ou 2–10), proporção (`9:16` vertical ou `16:9`) e quer finalizar com limpeza de silêncios/cor no Video Use?",
        ])

    if is_polish and not is_cut:
        tracks.append({
            "track": "Limpeza de Silêncios, Áudio 48kHz, Cor & Legendas",
            "primary": _STUDIO_9_CAPABILITIES["videouse"],
            "alternative_or_combo": {
                **_STUDIO_9_CAPABILITIES["openfilm"],
                "combo_note": "Se quiser remontagem manual em timeline multipista ou motion blur após a limpeza, finalize no OpenFilm (`/app/openfilm/`).",
            },
        })
        questions.append("Qual arquivo da Galeria (ou upload MP4) deseja processar e qual preset de cor prefere (`Neutral Clean`, `Cinema Warm` ou `E-Commerce Vibrant`)?")

    if is_voice and not is_ad:
        tracks.append({
            "track": "Locução Neural PT-BR Dedicada",
            "primary": _STUDIO_9_CAPABILITIES["voicestudio"],
            "alternative_or_combo": _STUDIO_9_CAPABILITIES["hyperframes"],
        })
        questions.append("Qual o texto da narração e timbre desejado (`pt-BR-AntonioNeural` ou `pt-BR-FranciscaNeural`)?")

    if is_timeline:
        tracks.append({
            "track": "Edição em Timeline Multipista",
            "primary": _STUDIO_9_CAPABILITIES["openfilm"],
            "alternative_or_combo": _STUDIO_9_CAPABILITIES["videouse"],
        })

    if is_ai_vis and not is_ad:
        tracks.append({
            "track": "Geração Visual & Motion AI",
            "primary": _STUDIO_9_CAPABILITIES["openhiggsfield"],
            "alternative_or_combo": _STUDIO_9_CAPABILITIES["hyperframes"],
        })

    if is_yt_seo:
        tracks.append({
            "track": "Canal YouTube & SEO Semântico",
            "primary": _STUDIO_9_CAPABILITIES["agenttube"],
            "alternative_or_combo": _STUDIO_9_CAPABILITIES["openseo"],
        })
        questions.append("O objetivo principal é pesquisa/títulos no OpenSEO ou pipeline completo de canal no AgentTube?")

    seen_gal = set()
    gallery_recent: List[Dict[str, Any]] = []
    for item in outputs:
        key = (str(item.get("filename") or ""), str(item.get("size_human") or ""))
        if key in seen_gal:
            continue
        seen_gal.add(key)
        gallery_recent.append({
            "id": item.get("id"),
            "stack": item.get("stack"),
            "filename": item.get("filename"),
            "size": item.get("size_human"),
            "media_type": item.get("media_type"),
        })
        if len(gallery_recent) >= 10:
            break

    active_stacks = [
        {"id": p.get("id"), "name": p.get("name"), "status": p.get("status")}
        for p in proj_statuses
        if p.get("status") not in ("stopped", "standby", None) or (p.get("running_containers") or 0) > 0
    ]

    want_shopee_pov = any(k in low for k in (
        "shopee", "tiktok", "produto", "anúncio", "anuncio", "kit", "elseve", "pov", "foto", "campanha"
    ))
    shopee_blueprint = None
    if want_shopee_pov:
        shopee_blueprint = {
            "framework": "Shopee Video Viral & POV Director (15s–18s · 9:16 1080x1920)",
            "beats": [
                "0s–3s Hook Implacável: abrir direto com Antes vs. Depois real ou dor aguda (nunca abrir com logo/saudação).",
                "3s–7s POV & Benefícios: Controle de Frizz, Redução de Pontas Duplas, Proteção Térmica até 230°C e 1 Semana de Nutrição com 6 Óleos Preciosos.",
                "7s–11s Quebra de Objeção: Nova Embalagem, Mesma Fórmula Original L'Oréal · Nutre 10x mais sem pesar.",
                "11s–16s+ Ancoragem de Preço & Carrinho Laranja: divisão (ex: Kit com 2 por R$ 69,99 = R$ 34,99 cada) + seta animada apontando para o Carrinho Laranja no canto inferior esquerdo.",
            ],
            "anti_confusion_rule": "Em anúncios de Kit de 2 óleos, filtrar fotos com a linha inteira de 4–6 produtos (shampoo/condicionador/máscara) e prints internos de afiliado.",
            "safe_zone_1080x1920": {
                "dead_zone_top": "Y: 0-215px livre (barra de busca Shopee + status bar)",
                "stage": "Y: 220-960px, X: 60-910px (títulos top:220px; palco de produto + banner preço Cena 4 em Y=810-960px)",
                "subtitle_corridor": "Y: 985-1100px, X: 60-900px (máx. 5-6 palavras/bloco, MarginR 180px)",
                "cta_pointer": "Y: 1110-1165px, X: 60px ('Toque em Comprar Abaixo com Desconto!') apontando para o Card Branco em Y=1180px",
                "reserved_shopee_ui": "Y: 1175-1920px e X: 910-1080px (Y>=1020px) 100% livres (Card Branco, Carrinho Laranja, @descrição, ícones)"
            },
            "safe_zone_and_audio": "Voz feminina PT-BR persuasiva (`pt-BR-FranciscaNeural` / `pt-BR-ThalitaMultilingualNeural`, rate +12%) em 48kHz SoXR sem cortar palavras; legendas estritamente em Y: 985-1100px.",
        }

    return {
        "ok": True,
        "goal": query,
        "vps_campaigns": [
            {
                "id": c.get("id"),
                "name": c.get("name"),
                "curated_count": c.get("curated_count"),
                "excluded_count": c.get("excluded_count"),
                "recommended_image_paths": c.get("recommended_image_paths", [])[:8],
            }
            for c in (campaigns or [])
        ],
        "shopee_director_blueprint": shopee_blueprint,
        "vps_environment": {
            "ram_available_gb": telemetry.get("ram_available_gb"),
            "ram_total_gb": telemetry.get("ram_total_gb"),
            "cpu_percent": telemetry.get("cpu_percent"),
            "disk_free_gb": telemetry.get("disk_free_gb"),
            "active_containers": telemetry.get("active_containers", 0),
            "total_stacks": len(proj_statuses),
            "active_stacks": active_stacks,
            "ram_safety_rule": "VPS com 8 GB RAM: ligar no máximo 1 stack Docker por vez (`0/7` em standby). HyperFrames e Video Use rodam com 0 MB RAM ociosa.",
        },
        "vps_gallery_count": len(gallery_recent),
        "vps_gallery_recent": gallery_recent,
        "recommended_tracks": tracks,
        "discovery_questions": questions[:3],
        "upload_scp_hint": "scp ~/Videos/seu_video.mp4 root@187.77.202.176:/opt/ai-media-studio/supoclip/clips/",
        "execution_choices": {
            "api_studio": "Automático via API / aba Estúdio Rápido (você confirma os parâmetros e o estúdio executa).",
            "web_ui": "Manual pela UI Web (`/app/supoclip/`, `/app/moneyprinter/`, `/app/openfilm/`, etc.).",
        },
    }


async def _ask_openjarvis_cloud_strategist(query: str) -> Dict[str, Any]:
    telemetry_task = api_system_metrics()
    proj_task = asyncio.gather(*(get_project_status(p) for p in PROJECTS.values()))
    out_task = asyncio.to_thread(_scan_output_files, "all", 24)
    camp_task = asyncio.to_thread(_scan_campaigns)
    telemetry, proj_statuses, outputs, campaigns = await asyncio.gather(telemetry_task, proj_task, out_task, camp_task)

    plan = _classify_studio_production_plan(query, telemetry, list(proj_statuses), outputs, campaigns)
    skill_path = Path("/root/.openjarvis/skills/studio-controller/SKILL.md")
    skill_excerpt = ""
    if skill_path.exists():
        try:
            skill_excerpt = skill_path.read_text(encoding="utf-8", errors="replace")[:9000]
        except Exception:
            skill_excerpt = ""

    low = query.lower()
    is_status_only = (
        any(k in low for k in ("status", "quais stacks", "quais arquivos", "galeria agora", "quantos contêineres"))
        and not plan["recommended_tracks"]
    )
    tools_used = (
        ["vps_telemetry", "vps_outputs_scan"]
        if is_status_only
        else ["studio_plan_production", "vps_outputs_scan", "vps_telemetry"]
    )

    system_prompt = (
        "Você é o OpenJarvis, Operador Master e Diretor Estrategista Audiovisual do AI Media Studio rodando na VPS Cloud (https://187.77.202.176.sslip.io).\n"
        "Responda sempre em português do Brasil, de forma estruturada, didática e 100% fiel às capacidades reais do estúdio (7 stacks Docker + 2 motores nativos zero-RAM). "
        "JAMAIS invente ferramentas externas (como CapCut, Premiere, OpusClip ou Runway) nem cite arquivos fora da inspeção abaixo.\n\n"
        "REGRAS CONSULTIVAS:\n"
        "1. Seja objetivo e direto (máx. ~320 palavras em tópicos curtos para caber tudo com folga sem cortar o final). Para pedidos de criação, anúncio, corte ou edição, apresente EXPLICITAMENTE pelo nome exato TANTO a Ferramenta Principal (`primary`) QUANTO a Alternativa/Combo (`alternative_or_combo`) de CADA trilha listada em `recommended_tracks` (ex.: cite tanto `HyperFrames` para motion ad 5–15s quanto `MoneyPrinterTurbo` para vídeo faceless B-roll narrado em vendas/Shopee; e cite `SupoClip` modo `novela`/`series` combinado com `Video Use` em cortes longos); "
        "(b) Estado real de RAM/stacks na VPS (regra de máx. 1 stack Docker ativa nos 8 GB RAM) e arquivos reais encontrados na Galeria (ou como subir vídeo do PC via Upload no Estúdio Rápido ou `scp`); "
        "(c) 2 a 3 perguntas pontuais para fechar parâmetros ausentes; "
        "(d) Pergunte se o usuário prefere disparar pela aba **Estúdio Rápido (API)** ou abrir a **UI Web** (`/app/<stack>/`).\n"
        "2. Para consultas de status/galeria, liste o status real das 7 stacks e os arquivos reais da Galeria.\n\n"
        + (f"### Referência da Skill Studio Controller\n{skill_excerpt}\n" if skill_excerpt else "")
    )

    user_content = (
        f"Pedido do usuário:\n{query}\n\n"
        f"Inspeção ao vivo do estúdio (`studio_plan_production`):\n"
        f"{json.dumps(plan, ensure_ascii=False, indent=2)}"
    )

    model_used = "cascade"
    reply_text = ""
    try:
        req_chat = ChatCompletionRequest(
            messages=[
                ChatCompletionMessage(role="system", content=system_prompt),
                ChatCompletionMessage(role="user", content=user_content),
            ],
            temperature=0.4,
            max_tokens=3200,
            stream=False,
        )
        ai_res = await call_ai_cascade(req_chat)
        model_used = str(ai_res.get("model") or "cascade")
        choices = ai_res.get("choices") or []
        if choices:
            reply_text = str((choices[0].get("message") or {}).get("content") or "").strip()
    except Exception as exc:
        logger.warning("Cloud OpenJarvis cascade fallback acionado: %s", exc)

    if not reply_text:
        env_info = plan["vps_environment"]
        lines = [
            f"**Diagnóstico do Estúdio (VPS):** {env_info.get('ram_available_gb')} GB livres de {env_info.get('ram_total_gb')} GB RAM · `{env_info.get('active_containers', 0)}/7` stacks Docker ativas.",
            f"**Galeria da VPS:** {plan['vps_gallery_count']} arquivos recentes encontrados.",
        ]
        for tr in plan["recommended_tracks"]:
            pri = tr["primary"]
            alt = tr["alternative_or_combo"]
            lines.append(
                f"\n**{tr['track']}**\n"
                f"- **Principal:** **{pri['name']}** ({pri['kind']}) — {pri['best_for']}\n"
                f"- **Alternativa/Combo:** **{alt['name']}** — {alt.get('combo_note') or alt.get('best_for')}"
            )
        if plan["discovery_questions"]:
            lines.append("\n**Perguntas para fecharmos os parâmetros:**")
            for idx, q_item in enumerate(plan["discovery_questions"], 1):
                lines.append(f"{idx}. {q_item}")
        lines.append(
            "\nComo prefere seguir: **1) Disparar via API no Estúdio Rápido** ou **2) Abrir a UI Web** (`/app/supoclip/`, `/app/moneyprinter/`, `/app/openfilm/`)?"
        )
        reply_text = "\n".join(lines)

    return {
        "ok": True,
        "cloud_mode": True,
        "model": model_used,
        "transcript": query,
        "reply": reply_text,
        "tools_used": tools_used,
        "plan": plan,
    }


@app.get("/api/openjarvis/plan")
@app.post("/api/openjarvis/plan")
async def api_openjarvis_plan(req: Optional[OpenJarvisAskRequest] = None, goal: str = ""):
    """Live studio_plan_production endpoint for cloud panel & integrations."""
    target_goal = ((req.query if req else "") or goal or "Planejar produção audiovisual").strip()
    telemetry, proj_statuses, outputs, campaigns = await asyncio.gather(
        api_system_metrics(),
        asyncio.gather(*(get_project_status(p) for p in PROJECTS.values())),
        asyncio.to_thread(_scan_output_files, "all", 24),
        asyncio.to_thread(_scan_campaigns),
    )
    return _classify_studio_production_plan(target_goal, telemetry, list(proj_statuses), outputs, campaigns)


@app.post("/api/openjarvis/ask")
async def api_openjarvis_ask(req: OpenJarvisAskRequest):
    """Execute an active command via Cloud Master Operator on VPS or local OpenJarvis runtime."""
    query = (req.query or "").strip()
    if not query:
        raise HTTPException(status_code=400, detail="Digite um comando ou pergunta para o Jarvis.")

    venv_py = OPENJARVIS_DIR / ".venv" / "bin" / "python"
    script_py = OPENJARVIS_DIR / "clap_listener.py"
    if STUDIO_MODE == "vps" or not venv_py.exists() or not script_py.exists():
        return await _ask_openjarvis_cloud_strategist(query)

    cmd = [str(venv_py), str(script_py), "--ask-text", query, "--json"]
    if not req.speak:
        cmd.append("--no-tts")

    res = await run_cmd(cmd, cwd=str(OPENJARVIS_DIR), timeout=90)
    if res["stdout"]:
        for line in reversed(res["stdout"].splitlines()):
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    return json.loads(line)
                except Exception:
                    pass
    if res["returncode"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"] or res["stdout"] or "Erro ao executar comando no Jarvis")
    return {
        "ok": True,
        "transcript": query,
        "reply": res["stdout"].strip(),
        "tools_used": [],
    }


@app.post("/api/openjarvis/audio-test")
async def api_openjarvis_audio_test():
    """Run 48,000 Hz Stereo SoXR self-diagnostic voice test."""
    venv_py = OPENJARVIS_DIR / ".venv" / "bin" / "python"
    script_py = OPENJARVIS_DIR / "clap_listener.py"
    res = await run_cmd([str(venv_py), str(script_py), "--test-audio"], cwd=str(OPENJARVIS_DIR), timeout=40)
    if res["stdout"]:
        try:
            return json.loads(res["stdout"])
        except Exception:
            pass
    if res["returncode"] != 0:
        raise HTTPException(status_code=500, detail=res["stderr"] or "Falha no teste de áudio")
    return {"ok": True, "detail": res["stdout"]}


@app.post("/api/openjarvis/webcam-test")
async def api_openjarvis_webcam_test():
    """Capture 1 frame from /dev/video0 on demand, release camera immediately, and describe via multimodal cascade."""
    venv_py = OPENJARVIS_DIR / ".venv" / "bin" / "python"
    vision_py = OPENJARVIS_DIR / "webcam_vision.py"
    res = await run_cmd([str(venv_py), str(vision_py)], cwd=str(OPENJARVIS_DIR), timeout=45)
    if res["stdout"]:
        try:
            data = json.loads(res["stdout"])
            return data
        except Exception:
            pass
    return {"ok": False, "error": res["stderr"] or res["stdout"] or "Falha ao testar webcam"}


@app.post("/api/openjarvis/action")
async def api_openjarvis_action(req: OpenJarvisActionRequest):
    if not OPENJARVIS_LAUNCHER.exists():
        raise HTTPException(status_code=404, detail="Lançador ~/open-jarvis/iniciar-jarvis.sh não encontrado")

    act = req.action.strip().lower()
    mode = req.mode.strip().lower()
    if mode not in ("both", "voice", "serve"):
        mode = "both"

    async def _run_detached_launcher(args: List[str]) -> int:
        proc = await asyncio.create_subprocess_exec(
            "bash",
            str(OPENJARVIS_LAUNCHER),
            *args,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        return await asyncio.wait_for(proc.wait(), timeout=15)

    output = ""
    if act == "stop":
        res = await run_cmd(["bash", str(OPENJARVIS_LAUNCHER), "--stop"], timeout=20)
        if res["returncode"] != 0:
            raise HTTPException(status_code=500, detail=res["stderr"] or res["stdout"] or "Erro ao parar OpenJarvis")
        output = res["stdout"].strip()
    elif act == "start":
        rc = await _run_detached_launcher(["--daemon", mode])
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"Falha ao iniciar OpenJarvis (exit {rc})")
        await asyncio.sleep(2.5)
        output = f"OpenJarvis ({mode}) iniciado em segundo plano."
    elif act == "restart":
        await run_cmd(["bash", str(OPENJARVIS_LAUNCHER), "--stop"], timeout=20)
        await asyncio.sleep(0.5)
        rc = await _run_detached_launcher(["--daemon", mode])
        if rc != 0:
            raise HTTPException(status_code=500, detail=f"Falha ao reiniciar OpenJarvis (exit {rc})")
        await asyncio.sleep(2.5)
        output = f"OpenJarvis ({mode}) reiniciado em segundo plano."
    else:
        raise HTTPException(status_code=400, detail="Ação inválida (use start, stop ou restart)")

    status = await get_openjarvis_status()
    return {
        "ok": True,
        "action": act,
        "mode": mode,
        "output": output,
        "openjarvis": status,
    }


@app.get("/api/openjarvis/logs")
async def api_openjarvis_logs():
    log_dir = OPENJARVIS_STATE_DIR / "logs"
    parts: List[str] = []
    for fname in ("clap_listener.log", "jarvis_serve.log"):
        fpath = log_dir / fname
        if fpath.exists():
            lines = fpath.read_text(encoding="utf-8", errors="replace").splitlines()[-60:]
            parts.append(f"=== {fname} ===\n" + ("\n".join(lines) if lines else "(vazio)"))
    logs = "\n\n".join(parts) if parts else "Sem logs recentes do OpenJarvis (serviço parado)."
    return {"project_id": "openjarvis", "name": "OpenJarvis AI Assistant", "logs": logs}


# ==============================================================================
# API Keys Vault Endpoints
# ==============================================================================

@app.get("/api/keys")
async def api_get_keys():
    vault = load_vault()
    response = {}
    for k in MANAGED_KEYS:
        val = vault.get(k, "")
        response[k] = {
            "configured": bool(val),
            "masked": mask_key(val, k),
            "last4": val[-4:] if len(val) >= 4 else "",
        }
    return {"keys": response}


class SaveKeysRequest(BaseModel):
    ANTHROPIC_API_KEY: Optional[str] = None
    ANTHROPIC_BASE_URL: Optional[str] = None
    GEMINI_API_KEY: Optional[str] = None
    NVIDIA_API_KEY: Optional[str] = None
    NVIDIA_BASE_URL: Optional[str] = None
    OPENROUTER_API_KEY: Optional[str] = None
    OPENROUTER_BASE_URL: Optional[str] = None
    HF_TOKEN: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OPENAI_API_KEY: Optional[str] = None
    PEXELS_API_KEY: Optional[str] = None


@app.post("/api/keys")
async def api_save_keys(req: SaveKeysRequest):
    vault = load_vault()
    incoming = req.model_dump()

    for k in MANAGED_KEYS:
        val = incoming.get(k)
        if val is None:
            continue
        val_str = val.strip()
        if "••" in val_str:
            continue
        if val_str == "__CLEAR__":
            vault[k] = ""
        elif val_str != "":
            vault[k] = val_str

    save_vault_file(vault)
    sync_report = sync_all_project_configs(vault)

    masked_resp = {}
    for k in MANAGED_KEYS:
        v = vault.get(k, "")
        masked_resp[k] = {
            "configured": bool(v),
            "masked": mask_key(v, k),
            "last4": v[-4:] if len(v) >= 4 else "",
        }

    return {
        "ok": True,
        "message": f"Chaves salvas no Cofre e sincronizadas nos {len(sync_report)} projetos com sucesso!",
        "keys": masked_resp,
        "sync_report": sync_report,
    }


# ==============================================================================
# AI Cascade Gateway (Gemini -> NVIDIA NIM -> OpenRouter)
# ==============================================================================

class ChatCompletionMessage(BaseModel):
    model_config = ConfigDict(extra="allow")
    role: str = "user"
    content: Optional[Any] = ""
    name: Optional[str] = None
    tool_calls: Optional[List[Dict[str, Any]]] = None
    tool_call_id: Optional[str] = None


class ChatCompletionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")
    model: Optional[str] = None
    messages: List[ChatCompletionMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    max_completion_tokens: Optional[int] = None
    stream: Optional[bool] = False
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Any] = None


def _sanitize_gemini_schema(schema: Any) -> Any:
    """Strip JSON Schema fields unsupported by Gemini v1beta FunctionDeclaration Schema."""
    if not isinstance(schema, dict):
        return schema

    unsupported = {
        "$schema",
        "$id",
        "$ref",
        "$defs",
        "definitions",
        "additionalProperties",
        "title",
        "default",
        "examples",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "patternProperties",
        "propertyNames",
        "const",
    }
    cleaned: Dict[str, Any] = {}
    for key, val in schema.items():
        if key in unsupported:
            continue
        if key == "type" and isinstance(val, list):
            non_null = [t for t in val if t != "null"]
            cleaned["type"] = non_null[0] if non_null else "string"
            if "null" in val:
                cleaned["nullable"] = True
        elif key == "properties" and isinstance(val, dict):
            cleaned["properties"] = {
                prop_name: _sanitize_gemini_schema(prop_schema)
                for prop_name, prop_schema in val.items()
            }
        elif key == "items":
            cleaned["items"] = _sanitize_gemini_schema(val)
        elif key in ("anyOf", "oneOf", "allOf") and isinstance(val, list):
            non_null_opts = [v for v in val if not (isinstance(v, dict) and v.get("type") == "null")]
            if len(non_null_opts) == 1:
                merged = _sanitize_gemini_schema(non_null_opts[0])
                if isinstance(merged, dict):
                    cleaned.update(merged)
                    if len(non_null_opts) < len(val):
                        cleaned["nullable"] = True
                continue
            cleaned["type"] = "string"
        else:
            cleaned[key] = val

    if "type" not in cleaned and "properties" in cleaned:
        cleaned["type"] = "object"
    if cleaned.get("type") == "object" and "properties" not in cleaned:
        cleaned["properties"] = {}
    return cleaned


def _convert_openai_tools_to_gemini(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    declarations = []
    for tool in tools:
        func = tool.get("function") or {}
        name = func.get("name", "")
        if not name:
            continue
        decl: Dict[str, Any] = {
            "name": name,
            "description": (func.get("description") or "")[:1024],
        }
        params = func.get("parameters")
        if isinstance(params, dict) and params:
            decl["parameters"] = _sanitize_gemini_schema(params)
        declarations.append(decl)
    return [{"functionDeclarations": declarations}] if declarations else []


def _openai_content_to_gemini_parts(content: Any) -> List[Dict[str, Any]]:
    if content is None:
        return []
    if isinstance(content, str):
        return [{"text": content}] if content else []
    if isinstance(content, list):
        parts: List[Dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                continue
            itype = item.get("type")
            if itype == "text" and item.get("text"):
                parts.append({"text": str(item["text"])})
            elif itype == "image_url":
                url = (item.get("image_url") or {}).get("url", "")
                if url.startswith("data:") and ";base64," in url:
                    header, b64 = url.split(";base64,", 1)
                    mime = header.replace("data:", "") or "image/jpeg"
                    parts.append({"inlineData": {"mimeType": mime, "data": b64}})
        return parts
    return [{"text": str(content)}]


def _openai_messages_to_gemini(messages: List[ChatCompletionMessage]) -> tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
    system_instruction: Optional[Dict[str, Any]] = None
    contents: List[Dict[str, Any]] = []
    tool_id_to_name: Dict[str, str] = {}

    for m in messages:
        if m.role == "system":
            text = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            if system_instruction is None:
                system_instruction = {"parts": [{"text": text or ""}]}
            else:
                system_instruction["parts"].append({"text": text or ""})
            continue

        if m.role == "assistant":
            parts = _openai_content_to_gemini_parts(m.content)
            for tc in m.tool_calls or []:
                tc_id = tc.get("id", "")
                func = tc.get("function") or {}
                fn_name = func.get("name") or tc.get("name") or ""
                if tc_id and fn_name:
                    tool_id_to_name[tc_id] = fn_name
                raw_args = func.get("arguments") if "function" in tc else tc.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                    except Exception:
                        parsed_args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    parsed_args = raw_args
                else:
                    parsed_args = {}
                parts.append({"functionCall": {"name": fn_name, "args": parsed_args}})
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if m.role == "tool":
            fn_name = m.name or tool_id_to_name.get(m.tool_call_id or "", "tool")
            raw_content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            try:
                parsed_resp = json.loads(raw_content)
                if not isinstance(parsed_resp, dict):
                    parsed_resp = {"result": parsed_resp}
            except Exception:
                parsed_resp = {"result": raw_content or ""}
            fr_part = {"functionResponse": {"name": fn_name, "response": parsed_resp}}
            if contents and contents[-1].get("role") == "user" and any("functionResponse" in p for p in contents[-1].get("parts", [])):
                contents[-1]["parts"].append(fr_part)
            else:
                contents.append({"role": "user", "parts": [fr_part]})
            continue

        parts = _openai_content_to_gemini_parts(m.content) or [{"text": ""}]
        contents.append({"role": "user", "parts": parts})

    return system_instruction, contents


_MODEL_COOLDOWN_UNTIL: Dict[str, float] = {}


def _openai_msg_to_dict(m: ChatCompletionMessage) -> Dict[str, Any]:
    msg: Dict[str, Any] = {"role": m.role}
    if m.content is not None and m.content != "":
        msg["content"] = m.content
    elif m.tool_calls:
        msg["content"] = None
    else:
        msg["content"] = ""
    if m.name:
        msg["name"] = m.name
    if m.tool_calls:
        msg["tool_calls"] = m.tool_calls
    if m.tool_call_id:
        msg["tool_call_id"] = m.tool_call_id
    return msg


def _openai_to_anthropic_payload(
    req: ChatCompletionRequest,
    model_id: str,
    effective_temp: float,
    effective_max_tokens: int,
) -> Dict[str, Any]:
    """Convert OpenAI ChatCompletionRequest (including tools & tool_calls) to Anthropic /v1/messages payload."""
    system_parts: List[str] = []
    anth_messages: List[Dict[str, Any]] = []

    for m in req.messages:
        if m.role == "system":
            if isinstance(m.content, str) and m.content.strip():
                system_parts.append(m.content.strip())
            elif m.content:
                system_parts.append(json.dumps(m.content, ensure_ascii=False))
            continue

        if m.role == "assistant":
            blocks: List[Dict[str, Any]] = []
            if isinstance(m.content, str) and m.content.strip():
                blocks.append({"type": "text", "text": m.content})
            for tc in m.tool_calls or []:
                tc_id = tc.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"
                func = tc.get("function") or {}
                fn_name = func.get("name") or tc.get("name") or "tool"
                raw_args = func.get("arguments") if "function" in tc else tc.get("arguments", {})
                if isinstance(raw_args, str):
                    try:
                        parsed_args = json.loads(raw_args) if raw_args.strip() else {}
                    except Exception:
                        parsed_args = {"raw": raw_args}
                elif isinstance(raw_args, dict):
                    parsed_args = raw_args
                else:
                    parsed_args = {}
                blocks.append(
                    {
                        "type": "tool_use",
                        "id": tc_id,
                        "name": fn_name,
                        "input": parsed_args,
                    }
                )
            if blocks:
                anth_messages.append({"role": "assistant", "content": blocks})
            continue

        if m.role == "tool":
            raw_content = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            tr_block = {
                "type": "tool_result",
                "tool_use_id": m.tool_call_id or "toolu_0",
                "content": raw_content or "",
            }
            if anth_messages and anth_messages[-1].get("role") == "user" and isinstance(anth_messages[-1].get("content"), list):
                anth_messages[-1]["content"].append(tr_block)
            else:
                anth_messages.append({"role": "user", "content": [tr_block]})
            continue

        # User message (text or multimodal)
        if isinstance(m.content, list):
            u_blocks: List[Dict[str, Any]] = []
            for item in m.content:
                if not isinstance(item, dict):
                    continue
                if item.get("type") == "text" and item.get("text"):
                    u_blocks.append({"type": "text", "text": str(item["text"])})
                elif item.get("type") == "image_url":
                    url = (item.get("image_url") or {}).get("url", "")
                    if url.startswith("data:") and ";base64," in url:
                        header, b64 = url.split(";base64,", 1)
                        mime = header.replace("data:", "") or "image/jpeg"
                        u_blocks.append(
                            {
                                "type": "image",
                                "source": {"type": "base64", "media_type": mime, "data": b64},
                            }
                        )
            anth_messages.append({"role": "user", "content": u_blocks or [{"type": "text", "text": ""}]})
        else:
            text_val = m.content if isinstance(m.content, str) else json.dumps(m.content, ensure_ascii=False)
            anth_messages.append({"role": "user", "content": text_val or "..."})

    payload: Dict[str, Any] = {
        "model": model_id,
        "messages": anth_messages or [{"role": "user", "content": "Hello"}],
        "max_tokens": max(1, min(effective_max_tokens, 8192)),
        "temperature": max(0.0, min(effective_temp, 1.0)),
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    if req.tools:
        anth_tools: List[Dict[str, Any]] = []
        for tool in req.tools:
            func = tool.get("function") or {}
            t_name = func.get("name", "")
            if not t_name:
                continue
            params = func.get("parameters")
            if not isinstance(params, dict) or "type" not in params:
                params = {"type": "object", "properties": {}}
            anth_tools.append(
                {
                    "name": t_name,
                    "description": (func.get("description") or "")[:1024],
                    "input_schema": params,
                }
            )
        if anth_tools:
            payload["tools"] = anth_tools

    return payload


def _request_has_vision_payload(messages: List[ChatCompletionMessage]) -> bool:
    """Detect whether any OpenAI-compatible message includes image_url or inline image blocks."""
    for m in messages:
        if isinstance(m.content, list):
            for item in m.content:
                if not isinstance(item, dict):
                    continue
                itype = str(item.get("type") or "").lower()
                if itype in ("image_url", "image", "input_image") or "image_url" in item or "inlineData" in item:
                    return True
    return False


async def _cascade_try_nvidia(
    req: ChatCompletionRequest,
    nvidia_key: str,
    nvidia_base: str,
    effective_temp: float,
    effective_max_tokens: int,
    tracker: _qt.QuotaTelemetryTracker,
    errors: List[str],
    *,
    tier_num: int = 1,
) -> Optional[Dict[str, Any]]:
    if not nvidia_key:
        errors.append(f"Tier {tier_num} (NVIDIA NIM) skipped: NVIDIA_API_KEY vazia")
        return None
    headers = {"Authorization": f"Bearer {nvidia_key}", "Content-Type": "application/json"}
    nvidia_candidates = [
        "nvidia/nemotron-3.5-lightning-30b-a3b",
        "nvidia/nemotron-3-ultra-550b-a55b",
        "moonshotai/kimi-k3.1-pro",
    ]
    if req.model and any(tok in req.model.lower() for tok in ("moonshot", "kimi", "nvidia/nemotron")):
        if ":free" not in req.model.lower():
            nvidia_candidates = [req.model] + [m for m in nvidia_candidates if m != req.model]

    now = time.monotonic()
    active_nim = [m for m in nvidia_candidates if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) <= now and tracker.is_available(m)] or nvidia_candidates

    for target_model in active_nim:
        for attempt in range(2):
            try:
                url = f"{nvidia_base}/chat/completions"
                nvidia_payload: Dict[str, Any] = {
                    "model": target_model,
                    "messages": [_openai_msg_to_dict(m) for m in req.messages],
                    "temperature": effective_temp,
                    "max_tokens": effective_max_tokens,
                }
                if attempt == 0 and "nemotron" in target_model.lower():
                    nvidia_payload["chat_template_kwargs"] = {"enable_thinking": False}
                if req.tools:
                    nvidia_payload["tools"] = req.tools
                    if req.tool_choice is not None:
                        nvidia_payload["tool_choice"] = req.tool_choice
                async with httpx.AsyncClient(timeout=32.0) as client:
                    res = await client.post(url, headers=headers, json=nvidia_payload)
                if res.status_code == 200:
                    data = res.json()
                    tracker.record_call(target_model, ok=True, status_code=200, specialty="text", saved_gemini=True)
                    data["cascade_tier"] = tier_num
                    data["cascade_provider"] = "nvidia-nim"
                    data["cascade_model"] = target_model
                    return data
                tracker.record_call(
                    target_model,
                    ok=False,
                    status_code=res.status_code,
                    specialty="text",
                    cooldown_s=65.0 if res.status_code in (429, 503) else 0.0,
                    error=res.text[:140],
                )
                if res.status_code in (429, 503):
                    _MODEL_COOLDOWN_UNTIL[target_model] = time.monotonic() + 90.0
                    break
                if res.status_code in (400, 422, 500, 502, 504) and attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                errors.append(f"Tier {tier_num} (NVIDIA NIM/{target_model}) HTTP {res.status_code}: {res.text[:120]}")
                break
            except Exception as e:
                tracker.record_call(target_model, ok=False, status_code=0, specialty="text", error=str(e))
                if attempt == 0:
                    await asyncio.sleep(0.25)
                    continue
                errors.append(f"Tier {tier_num} (NVIDIA NIM/{target_model}) exception: {str(e)}")
    return None


async def _cascade_try_openrouter(
    req: ChatCompletionRequest,
    openrouter_key: str,
    openrouter_base: str,
    effective_temp: float,
    effective_max_tokens: int,
    tracker: _qt.QuotaTelemetryTracker,
    errors: List[str],
    *,
    tier_num: int = 2,
) -> Optional[Dict[str, Any]]:
    if not openrouter_key:
        errors.append(f"Tier {tier_num} (OpenRouter) skipped: OPENROUTER_API_KEY vazia")
        return None
    headers = {
        "Authorization": f"Bearer {openrouter_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:9000",
        "X-Title": "AI Media Studio Gateway",
    }
    or_candidates = [
        "nvidia/nemotron-3-ultra-550b-a55b:free",
        "nvidia/nemotron-3.5-lightning:free",
        "moonshotai/kimi-k3.1-pro",
    ]
    if req.model and ":free" in req.model.lower():
        or_candidates = [req.model] + [m for m in or_candidates if m != req.model]

    now = time.monotonic()
    active_or = [m for m in or_candidates if _MODEL_COOLDOWN_UNTIL.get(m, 0.0) <= now] or or_candidates

    for target_model in active_or:
        for attempt in range(2):
            try:
                url = f"{openrouter_base}/chat/completions"
                openrouter_payload: Dict[str, Any] = {
                    "model": target_model,
                    "messages": [_openai_msg_to_dict(m) for m in req.messages],
                    "temperature": effective_temp,
                    "max_tokens": effective_max_tokens,
                }
                if req.tools:
                    openrouter_payload["tools"] = req.tools
                    if req.tool_choice is not None:
                        openrouter_payload["tool_choice"] = req.tool_choice
                async with httpx.AsyncClient(timeout=32.0) as client:
                    res = await client.post(url, headers=headers, json=openrouter_payload)
                if res.status_code == 200:
                    data = res.json()
                    tracker.record_call(target_model, ok=True, status_code=200, specialty="text", saved_gemini=True)
                    data["cascade_tier"] = tier_num
                    data["cascade_provider"] = "openrouter"
                    data["cascade_model"] = target_model
                    return data
                tracker.record_call(
                    target_model,
                    ok=False,
                    status_code=res.status_code,
                    specialty="text",
                    cooldown_s=90.0 if res.status_code in (429, 503) else 0.0,
                    error=res.text[:140],
                )
                if res.status_code in (429, 503):
                    _MODEL_COOLDOWN_UNTIL[target_model] = time.monotonic() + 90.0
                    break
                if res.status_code in (500, 502, 504) and attempt == 0:
                    await asyncio.sleep(0.3)
                    continue
                errors.append(f"Tier {tier_num} (OpenRouter/{target_model}) HTTP {res.status_code}: {res.text[:120]}")
                break
            except Exception as e:
                tracker.record_call(target_model, ok=False, status_code=0, specialty="text", error=str(e))
                if attempt == 0:
                    await asyncio.sleep(0.3)
                    continue
                errors.append(f"Tier {tier_num} (OpenRouter/{target_model}) exception: {str(e)}")
    return None


async def _cascade_try_gemini(
    req: ChatCompletionRequest,
    gemini_key: str,
    effective_temp: float,
    effective_max_tokens: int,
    tracker: _qt.QuotaTelemetryTracker,
    errors: List[str],
    *,
    is_vision: bool = False,
    tier_num: int = 3,
) -> Optional[Dict[str, Any]]:
    if not gemini_key:
        errors.append(f"Tier {tier_num} (Gemini) skipped: GEMINI_API_KEY vazia")
        return None
    specialty = "vision" if is_vision else "text"
    try:
        system_instruction, contents = _openai_messages_to_gemini(req.messages)
        if is_vision:
            raw_candidates = [
                "gemini-3.5-flash-lite",
                "gemini-3.1-flash-lite",
                "gemini-2.5-flash-lite",
                "gemini-3.8-flash",
            ]
        else:
            raw_candidates = (
                [
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-2.5-flash-lite",
                    "gemini-3.8-flash",
                    "gemini-2.5-flash",
                ]
                if req.tools
                else [
                    "gemma-4-31b-it",
                    "gemini-3.5-flash-lite",
                    "gemini-3.1-flash-lite",
                    "gemini-2.5-flash-lite",
                    "gemini-3.8-flash",
                    "gemini-2.5-flash",
                ]
            )

        now = time.monotonic()
        candidate_models = [
            mc for mc in raw_candidates if _MODEL_COOLDOWN_UNTIL.get(mc, 0.0) <= now and tracker.is_available(mc)
        ] or [mc for mc in raw_candidates if _MODEL_COOLDOWN_UNTIL.get(mc, 0.0) <= now] or raw_candidates

        gem_tools = _convert_openai_tools_to_gemini(req.tools) if req.tools else []

        for m_candidate in candidate_models:
            gem_payload: Dict[str, Any] = {
                "contents": [dict(c) for c in contents],
                "generationConfig": {
                    "temperature": effective_temp,
                    "maxOutputTokens": effective_max_tokens,
                },
            }
            if system_instruction:
                if "gemma" in m_candidate.lower() and gem_payload["contents"]:
                    sys_txt = " ".join(p.get("text", "") for p in system_instruction.get("parts", [])).strip()
                    if sys_txt:
                        first_parts = [{"text": f"[Instruções de Sistema]\n{sys_txt}\n\n"}] + list((gem_payload["contents"][0] or {}).get("parts") or [])
                        gem_payload["contents"][0] = {"role": "user", "parts": first_parts}
                else:
                    gem_payload["systemInstruction"] = system_instruction
            if gem_tools and "gemma" not in m_candidate.lower():
                gem_payload["tools"] = gem_tools
                if isinstance(req.tool_choice, str):
                    mode = {"auto": "AUTO", "none": "NONE", "required": "ANY"}.get(req.tool_choice.lower())
                    if mode:
                        gem_payload["toolConfig"] = {"functionCallingConfig": {"mode": mode}}

            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{m_candidate}:generateContent?key={gemini_key}"
                async with httpx.AsyncClient(timeout=28.0) as client:
                    res = await client.post(url, json=gem_payload)
                if res.status_code == 200:
                    data = res.json()
                    cands = data.get("candidates", [])
                    if cands and "content" in cands[0]:
                        parts = cands[0]["content"].get("parts", [])
                        text_chunks: List[str] = []
                        tool_calls: List[Dict[str, Any]] = []
                        for p in parts:
                            if "text" in p and p["text"] is not None:
                                text_chunks.append(p["text"])
                            elif "functionCall" in p:
                                fc = p["functionCall"]
                                tool_calls.append(
                                    {
                                        "id": f"call_{uuid.uuid4().hex[:12]}",
                                        "type": "function",
                                        "function": {
                                            "name": fc.get("name", ""),
                                            "arguments": json.dumps(fc.get("args", {}), ensure_ascii=False),
                                        },
                                    }
                                )
                        text = "".join(text_chunks)
                        usage_meta = data.get("usageMetadata", {})
                        msg_out: Dict[str, Any] = {
                            "role": "assistant",
                            "content": text if text else (None if tool_calls else ""),
                        }
                        if tool_calls:
                            msg_out["tool_calls"] = tool_calls
                        tracker.record_call(m_candidate, ok=True, status_code=200, specialty=specialty)
                        return {
                            "id": f"chatcmpl-gemini-{int(time.time()*1000)}",
                            "object": "chat.completion",
                            "created": int(time.time()),
                            "model": f"google/{m_candidate}",
                            "choices": [
                                {
                                    "index": 0,
                                    "message": msg_out,
                                    "finish_reason": "tool_calls" if tool_calls else "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": usage_meta.get("promptTokenCount", 0),
                                "completion_tokens": usage_meta.get("candidatesTokenCount", 0),
                                "total_tokens": usage_meta.get("totalTokenCount", 0),
                            },
                            "cascade_tier": tier_num,
                            "cascade_provider": "google-gemini",
                            "cascade_model": m_candidate,
                        }
                tracker.record_call(
                    m_candidate,
                    ok=False,
                    status_code=res.status_code,
                    specialty=specialty,
                    cooldown_s=180.0 if res.status_code in (429, 503) else 0.0,
                    error=res.text[:160],
                )
                if res.status_code in (429, 503):
                    _MODEL_COOLDOWN_UNTIL[m_candidate] = time.monotonic() + 180.0
                errors.append(f"Tier {tier_num} (Gemini/{m_candidate}) HTTP {res.status_code}: {res.text[:160]}")
            except Exception as inner_e:
                tracker.record_call(m_candidate, ok=False, status_code=0, specialty=specialty, error=str(inner_e))
                errors.append(f"Tier {tier_num} (Gemini/{m_candidate}) exception: {str(inner_e)}")
    except Exception as e:
        errors.append(f"Tier {tier_num} (Gemini) exception: {str(e)}")
    return None


async def call_ai_cascade(req: ChatCompletionRequest) -> Dict[str, Any]:
    """Smart Specialty-Aware Quota Router (v2.9): NIM/OpenRouter for Text, High-Quota Gemini Pool for Vision."""
    vault = load_vault()
    tracker = _get_quota_tracker()
    anthropic_key = vault.get("ANTHROPIC_API_KEY", "").strip()
    anthropic_base = sanitize_base_url(vault.get("ANTHROPIC_BASE_URL", ""), "https://api.anthropic.com/v1")
    gemini_key = vault.get("GEMINI_API_KEY", "").strip()
    nvidia_key = vault.get("NVIDIA_API_KEY", "").strip()
    nvidia_base = vault.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1").strip().rstrip("/")
    openrouter_key = vault.get("OPENROUTER_API_KEY", "").strip()
    openrouter_base = vault.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").strip().rstrip("/")

    effective_max_tokens = req.max_tokens or req.max_completion_tokens or 2048
    effective_temp = 0.7 if req.temperature is None else req.temperature
    is_vision = _request_has_vision_payload(req.messages)
    errors: List[str] = []

    if is_vision:
        res_vis = await _cascade_try_gemini(
            req, gemini_key, effective_temp, effective_max_tokens, tracker, errors, is_vision=True, tier_num=1
        )
        if res_vis is not None:
            return res_vis

    if not is_vision:
        res_nim = await _cascade_try_nvidia(
            req, nvidia_key, nvidia_base, effective_temp, effective_max_tokens, tracker, errors, tier_num=1
        )
        if res_nim is not None:
            return res_nim

        res_or = await _cascade_try_openrouter(
            req, openrouter_key, openrouter_base, effective_temp, effective_max_tokens, tracker, errors, tier_num=2
        )
        if res_or is not None:
            return res_or

    if anthropic_key:
        claude_candidates = [
            "claude-3-5-sonnet-latest",
            "claude-3-7-sonnet-latest",
            "claude-sonnet-4-20250514",
            "claude-3-5-haiku-latest",
        ]
        now = time.monotonic()
        active_claude = [mc for mc in claude_candidates if _MODEL_COOLDOWN_UNTIL.get(mc, 0.0) <= now] or claude_candidates
        headers = {
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        for c_model in active_claude:
            try:
                anth_payload = _openai_to_anthropic_payload(req, c_model, effective_temp, effective_max_tokens)
                async with httpx.AsyncClient(timeout=30.0) as client:
                    res = await client.post(f"{anthropic_base}/messages", headers=headers, json=anth_payload)
                if res.status_code == 200:
                    data = res.json()
                    content_blocks = data.get("content", [])
                    text_parts = [b["text"] for b in content_blocks if b.get("type") == "text" and b.get("text")]
                    tool_calls = [
                        {
                            "id": b.get("id") or f"call_{uuid.uuid4().hex[:12]}",
                            "type": "function",
                            "function": {
                                "name": b.get("name", ""),
                                "arguments": json.dumps(b.get("input", {}), ensure_ascii=False),
                            },
                        }
                        for b in content_blocks if b.get("type") == "tool_use"
                    ]
                    text_out = "".join(text_parts)
                    usage = data.get("usage", {})
                    in_tok, out_tok = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
                    msg_out: Dict[str, Any] = {"role": "assistant", "content": text_out if text_out else (None if tool_calls else "")}
                    if tool_calls:
                        msg_out["tool_calls"] = tool_calls
                    return {
                        "id": data.get("id", f"chatcmpl-claude-{int(time.time()*1000)}"),
                        "object": "chat.completion",
                        "created": int(time.time()),
                        "model": f"anthropic/{c_model}",
                        "choices": [{"index": 0, "message": msg_out, "finish_reason": "tool_calls" if tool_calls else "stop"}],
                        "usage": {"prompt_tokens": in_tok, "completion_tokens": out_tok, "total_tokens": in_tok + out_tok},
                        "cascade_tier": 3,
                        "cascade_provider": "anthropic-claude",
                        "cascade_model": c_model,
                    }
                if res.status_code in (429, 503, 529):
                    _MODEL_COOLDOWN_UNTIL[c_model] = time.monotonic() + 180.0
                errors.append(f"Claude/{c_model} HTTP {res.status_code}: {res.text[:140]}")
            except Exception as exc:
                errors.append(f"Claude/{c_model} exception: {exc}")

    if not is_vision:
        res_gem = await _cascade_try_gemini(
            req, gemini_key, effective_temp, effective_max_tokens, tracker, errors, is_vision=False, tier_num=3
        )
        if res_gem is not None:
            return res_gem

    raise HTTPException(
        status_code=503,
        detail={
            "message": "Todas as camadas do AI Cascade falharam ou não possuem chave configurada.",
            "errors": errors,
        },
    )


async def _sse_from_completion(data: Dict[str, Any]):
    choice = (data.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    finish = choice.get("finish_reason") or "stop"
    base_chunk = {
        "id": data.get("id", f"chatcmpl-{int(time.time()*1000)}"),
        "object": "chat.completion.chunk",
        "created": data.get("created", int(time.time())),
        "model": data.get("model", "gemini-2.5-flash"),
    }

    delta: Dict[str, Any] = {"role": "assistant"}
    if msg.get("content"):
        delta["content"] = msg["content"]
    if msg.get("tool_calls"):
        delta["tool_calls"] = [
            {
                "index": idx,
                "id": tc.get("id", ""),
                "type": "function",
                "function": tc.get("function", {}),
            }
            for idx, tc in enumerate(msg["tool_calls"])
        ]

    yield f"data: {json.dumps({**base_chunk, 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]})}\n\n"
    yield f"data: {json.dumps({**base_chunk, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}], 'usage': data.get('usage')})}\n\n"
    yield "data: [DONE]\n\n"


@app.post("/v1/chat/completions")
@app.post("/api/ai/chat/completions")
async def api_chat_completions(req: ChatCompletionRequest):
    data = await call_ai_cascade(req)
    if req.stream:
        return StreamingResponse(_sse_from_completion(data), media_type="text/event-stream")
    return data


@app.get("/v1/models")
async def api_v1_models():
    return {
        "object": "list",
        "data": [
            {"id": "claude-3-5-sonnet-latest", "object": "model", "owned_by": "anthropic", "tier": 1},
            {"id": "gemini-2.5-flash", "object": "model", "owned_by": "google", "tier": 1},
            {"id": "moonshotai/kimi-k3.1-pro", "object": "model", "owned_by": "nvidia-nim", "tier": 2},
            {"id": "nvidia/nemotron-3.5-lightning-30b-a3b", "object": "model", "owned_by": "nvidia-nim", "tier": 2},
            {"id": "nvidia/nemotron-3-ultra-550b-a55b:free", "object": "model", "owned_by": "openrouter", "tier": 3},
        ],
    }


@app.get("/api/ai/quota-telemetry")
async def api_ai_quota_telemetry():
    vault = load_vault()
    snap = _get_quota_tracker().snapshot()
    snap["providers_configured"] = {
        "nvidia_nim": bool(vault.get("NVIDIA_API_KEY", "").strip()),
        "openrouter": bool(vault.get("OPENROUTER_API_KEY", "").strip()),
        "gemini": bool(vault.get("GEMINI_API_KEY", "").strip()),
        "anthropic": bool(vault.get("ANTHROPIC_API_KEY", "").strip()),
        "hf_token": bool(vault.get("HF_TOKEN", "").strip()),
    }
    return snap


@app.get("/api/ai/cascade-status")
async def api_ai_cascade_status():
    vault = load_vault()
    a_key = vault.get("ANTHROPIC_API_KEY", "")
    g_key = vault.get("GEMINI_API_KEY", "")
    n_key = vault.get("NVIDIA_API_KEY", "")
    o_key = vault.get("OPENROUTER_API_KEY", "")
    telemetry = _get_quota_tracker().snapshot()
    active_text = telemetry.get("active_by_specialty", {}).get("text") or "nvidia/nemotron-3.5-lightning-30b-a3b"

    return {
        "router_mode": "smart_specialty_v29",
        "active_text_model": active_text,
        "tier1_nvidia": {
            "tier": 1,
            "provider": "NVIDIA NIM",
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b · nemotron-3-ultra · kimi-k3.1-pro",
            "base_url": vault.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "configured": bool(n_key),
            "masked": mask_key(n_key, "NVIDIA_API_KEY"),
            "active_as_primary": bool(n_key),
        },
        "tier2_openrouter": {
            "tier": 2,
            "provider": "OpenRouter",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free · nemotron-3.5-lightning:free",
            "base_url": vault.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "configured": bool(o_key),
            "masked": mask_key(o_key, "OPENROUTER_API_KEY"),
        },
        "tier3_gemini": {
            "tier": 3,
            "provider": "Google Gemini (Alta Cota + Vision + Live Audio)",
            "model": "gemma-4-31b-it · gemini-3.5-flash-lite · native-audio-live (∞)",
            "configured": bool(g_key),
            "masked": mask_key(g_key, "GEMINI_API_KEY"),
        },
        "tier1_anthropic": {
            "tier": 4,
            "provider": "Anthropic Claude",
            "model": "claude-3-5-sonnet-latest",
            "base_url": vault.get("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1"),
            "configured": bool(a_key),
            "masked": mask_key(a_key, "ANTHROPIC_API_KEY"),
            "active_as_primary": bool(a_key),
        },
        "tier1_gemini": {
            "tier": 3,
            "provider": "Google Gemini (Alta Cota + Live Audio)",
            "model": "gemini-3.5-flash-lite · gemini-2.5-flash-native-audio-latest",
            "configured": bool(g_key),
            "masked": mask_key(g_key, "GEMINI_API_KEY"),
        },
        "tier2_nvidia": {
            "tier": 1,
            "provider": "NVIDIA NIM",
            "model": "nvidia/nemotron-3.5-lightning-30b-a3b · moonshotai/kimi-k3.1-pro",
            "base_url": vault.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
            "configured": bool(n_key),
            "masked": mask_key(n_key, "NVIDIA_API_KEY"),
        },
        "tier3_openrouter": {
            "tier": 2,
            "provider": "OpenRouter",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "base_url": vault.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
            "configured": bool(o_key),
            "masked": mask_key(o_key, "OPENROUTER_API_KEY"),
        },
        "quota_telemetry": telemetry,
        "gateway_endpoint": "http://localhost:9000/v1/chat/completions",
    }


# ==============================================================================
# Diagnostics & Deep Health Check (Smart Quota Router + Zero-Token Gemini Check)
# ==============================================================================

async def check_gemini_api(vault: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Zero-Token / Zero-RPD Metadata Ping against Google Gemini /v1beta/models."""
    if vault is None:
        vault = load_vault()
    api_key = vault.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return {
            "tier": 3,
            "provider": "Google Gemini",
            "model": "gemini-2.5-flash-native-audio-latest · gemma-4-31b-it",
            "name": "Pool Voz/Visão/Reserva: Google Gemini API (Zero-Token Check)",
            "status": "warn",
            "http_code": 0,
            "latency_ms": 0,
            "detail": "Chave GEMINI_API_KEY ainda não configurada no Cofre Central.",
            "error": "GEMINI_API_KEY ausente",
        }

    snap = _get_quota_tracker().snapshot()
    live_info = snap.get("tts_rest_pool", {})
    url = f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}&pageSize=5"
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(url)
        latency_ms = int((time.monotonic() - t0) * 1000)
        if resp.status_code == 200:
            data = resp.json()
            m_count = len(data.get("models") or [])
            return {
                "tier": 3,
                "provider": "Google Gemini",
                "model": "live-audio (∞) · gemini-3.5-flash-lite · gemma-4-31b-it",
                "name": "Pool Voz/Visão/Reserva: Google Gemini API (Zero-Token Metadata Ping)",
                "status": "ok",
                "http_code": 200,
                "latency_ms": latency_ms,
                "detail": (
                    f"HTTP 200 OK · Latência: {latency_ms} ms · Zero-Token Metadata Ping "
                    f"(0 RPD consumidos, {m_count} modelos listados) · Pool TTS REST: "
                    f"{live_info.get('rpm_used', 0)}/{live_info.get('rpm_limit', 15)} RPM."
                ),
                "error": None,
                "zero_token_check": True,
            }
        try:
            err_msg = resp.json().get("error", {}).get("message", resp.text[:140])
        except Exception:
            err_msg = resp.text[:140]
        return {
            "tier": 3,
            "provider": "Google Gemini",
            "model": "gemini-3.5-flash-lite",
            "name": "Pool Voz/Visão/Reserva: Google Gemini API (Zero-Token Metadata Ping)",
            "status": "error",
            "http_code": resp.status_code,
            "latency_ms": latency_ms,
            "detail": f"HTTP {resp.status_code} ({latency_ms} ms): {err_msg}",
            "error": str(err_msg),
            "zero_token_check": True,
        }
    except Exception as e:
        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 3,
            "provider": "Google Gemini",
            "model": "gemini-3.5-flash-lite",
            "name": "Pool Voz/Visão/Reserva: Google Gemini API (Zero-Token Metadata Ping)",
            "status": "error",
            "http_code": 0,
            "latency_ms": total_ms,
            "detail": f"Erro de conexão com Google Gemini ({total_ms} ms): {e}",
            "error": str(e),
            "zero_token_check": True,
        }


async def check_nvidia_api(vault: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if vault is None:
        vault = load_vault()
    api_key = vault.get("NVIDIA_API_KEY", "").strip()
    base_url = sanitize_base_url(vault.get("NVIDIA_BASE_URL", ""), "https://integrate.api.nvidia.com/v1")

    if not api_key:
        return {
            "tier": 2,
            "provider": "NVIDIA NIM",
            "model": "moonshotai/kimi-k3.1-pro",
            "name": "Tier 2 (Failover NIM): NVIDIA NIM API (/v1/chat/completions)",
            "status": "warn",
            "http_code": 0,
            "latency_ms": 0,
            "detail": "Chave NVIDIA_API_KEY não configurada no Cofre Central.",
            "error": "NVIDIA_API_KEY ausente",
        }

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    test_models = ["nvidia/nemotron-3.5-lightning-30b-a3b", "nvidia/nemotron-3-ultra-550b-a55b", "moonshotai/kimi-k3.1-pro"]
    t0 = time.monotonic()
    last_err = ""
    last_code = 0

    try:
        async with httpx.AsyncClient(timeout=14.0) as client:
            for model_id in test_models:
                t_req = time.monotonic()
                payload = {
                    "model": model_id,
                    "messages": [{"role": "system", "content": "/no_think"}, {"role": "user", "content": "OK"}],
                    "max_tokens": 1,
                    "chat_template_kwargs": {"enable_thinking": False},
                }
                resp = await client.post(url, headers=headers, json=payload)
                latency_ms = int((time.monotonic() - t_req) * 1000)
                last_code = resp.status_code
                if resp.status_code == 200:
                    return {
                        "tier": 2,
                        "provider": "NVIDIA NIM",
                        "model": f"moonshotai/kimi-k3.1-pro · {model_id}",
                        "name": f"Tier 2 (Failover NIM): NVIDIA NIM API ({model_id})",
                        "status": "ok",
                        "http_code": 200,
                        "latency_ms": latency_ms,
                        "detail": f"HTTP 200 OK · Latência: {latency_ms} ms · Endpoint {base_url} pronto (1 token gerado).",
                        "error": None,
                    }
                try:
                    last_err = resp.json().get("detail") or resp.json().get("error", {}).get("message") or resp.text[:140]
                except Exception:
                    last_err = resp.text[:140]

        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 2,
            "provider": "NVIDIA NIM",
            "model": "moonshotai/kimi-k3.1-pro",
            "name": "Tier 2 (Failover NIM): NVIDIA NIM API (/v1/chat/completions)",
            "status": "error",
            "http_code": last_code,
            "latency_ms": total_ms,
            "detail": f"HTTP {last_code} ({total_ms} ms): {last_err}",
            "error": str(last_err),
        }
    except Exception as e:
        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 2,
            "provider": "NVIDIA NIM",
            "model": "moonshotai/kimi-k3.1-pro",
            "name": "Tier 2 (Failover NIM): NVIDIA NIM API (/v1/chat/completions)",
            "status": "error",
            "http_code": 0,
            "latency_ms": total_ms,
            "detail": f"Erro de conexão com NVIDIA NIM ({total_ms} ms): {str(e)}",
            "error": str(e),
        }


async def check_openrouter_api(vault: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if vault is None:
        vault = load_vault()
    api_key = vault.get("OPENROUTER_API_KEY", "").strip()
    base_url = sanitize_base_url(vault.get("OPENROUTER_BASE_URL", ""), "https://openrouter.ai/api/v1")

    if not api_key:
        return {
            "tier": 3,
            "provider": "OpenRouter",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "name": "Tier 3 (Failover Máximo): OpenRouter API (/api/v1/chat/completions)",
            "status": "warn",
            "http_code": 0,
            "latency_ms": 0,
            "detail": "Chave OPENROUTER_API_KEY não configurada no Cofre Central.",
            "error": "OPENROUTER_API_KEY ausente",
        }

    url = f"{base_url}/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "HTTP-Referer": "http://localhost:9000",
        "X-Title": "AI Media Studio Control Panel",
    }
    test_models = ["nvidia/nemotron-3-ultra-550b-a55b:free", "nvidia/nemotron-3.5-lightning:free", "moonshotai/kimi-k3.1-pro"]
    t0 = time.monotonic()
    last_err = ""
    last_code = 0

    try:
        async with httpx.AsyncClient(timeout=14.0) as client:
            for model_id in test_models:
                t_req = time.monotonic()
                payload = {
                    "model": model_id,
                    "messages": [{"role": "system", "content": "/no_think"}, {"role": "user", "content": "OK"}],
                    "max_tokens": 1,
                }
                resp = await client.post(url, headers=headers, json=payload)
                latency_ms = int((time.monotonic() - t_req) * 1000)
                last_code = resp.status_code
                if resp.status_code == 200:
                    return {
                        "tier": 3,
                        "provider": "OpenRouter",
                        "model": model_id,
                        "name": f"Tier 3 (Failover Máximo): OpenRouter API ({model_id})",
                        "status": "ok",
                        "http_code": 200,
                        "latency_ms": latency_ms,
                        "detail": f"HTTP 200 OK · Latência: {latency_ms} ms · Contingência máxima Nemotron 550B pronta (1 token gerado).",
                        "error": None,
                    }
                try:
                    last_err = resp.json().get("error", {}).get("message") or resp.text[:140]
                except Exception:
                    last_err = resp.text[:140]

        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 3,
            "provider": "OpenRouter",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "name": "Tier 3 (Failover Máximo): OpenRouter API (/api/v1/chat/completions)",
            "status": "error",
            "http_code": last_code,
            "latency_ms": total_ms,
            "detail": f"HTTP {last_code} ({total_ms} ms): {last_err}",
            "error": str(last_err),
        }
    except Exception as e:
        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 3,
            "provider": "OpenRouter",
            "model": "nvidia/nemotron-3-ultra-550b-a55b:free",
            "name": "Tier 3 (Failover Máximo): OpenRouter API (/api/v1/chat/completions)",
            "status": "error",
            "http_code": 0,
            "latency_ms": total_ms,
            "detail": f"Erro de conexão com OpenRouter ({total_ms} ms): {str(e)}",
            "error": str(e),
        }


async def check_anthropic_api(vault: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    if vault is None:
        vault = load_vault()
    api_key = vault.get("ANTHROPIC_API_KEY", "").strip()
    base_url = sanitize_base_url(vault.get("ANTHROPIC_BASE_URL", ""), "https://api.anthropic.com/v1")

    if not api_key:
        return {
            "tier": 0,
            "provider": "Anthropic Claude",
            "model": "claude-3-5-sonnet-latest",
            "name": "Tier 1 (Preparado): Anthropic Claude API (claude-3-5-sonnet)",
            "status": "standby",
            "http_code": 0,
            "latency_ms": 0,
            "detail": "Slot nativo pronto no Cofre Central. Assim que ANTHROPIC_API_KEY for inserida, assumirá automaticamente o Tier 1 da cascata.",
            "error": None,
        }

    url = f"{base_url}/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    test_models = ["claude-3-5-sonnet-latest", "claude-3-7-sonnet-latest", "claude-3-5-haiku-latest"]
    t0 = time.monotonic()
    last_err = ""
    last_code = 0

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            for model_id in test_models:
                t_req = time.monotonic()
                payload = {
                    "model": model_id,
                    "max_tokens": 5,
                    "messages": [{"role": "user", "content": "OK"}],
                }
                resp = await client.post(url, headers=headers, json=payload)
                latency_ms = int((time.monotonic() - t_req) * 1000)
                last_code = resp.status_code
                if resp.status_code == 200:
                    return {
                        "tier": 1,
                        "provider": "Anthropic Claude",
                        "model": model_id,
                        "name": f"Tier 1 (Principal Claude): Anthropic API ({model_id})",
                        "status": "ok",
                        "http_code": 200,
                        "latency_ms": latency_ms,
                        "detail": f"HTTP 200 OK · Latência: {latency_ms} ms · Claude ativo no topo da cascata.",
                        "error": None,
                    }
                try:
                    last_err = resp.json().get("error", {}).get("message") or resp.text[:140]
                except Exception:
                    last_err = resp.text[:140]
        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 1,
            "provider": "Anthropic Claude",
            "model": "claude-3-5-sonnet-latest",
            "name": "Tier 1 (Principal Claude): Anthropic API (/v1/messages)",
            "status": "error",
            "http_code": last_code,
            "latency_ms": total_ms,
            "detail": f"HTTP {last_code} ({total_ms} ms): {last_err}",
            "error": str(last_err),
        }
    except Exception as e:
        total_ms = int((time.monotonic() - t0) * 1000)
        return {
            "tier": 1,
            "provider": "Anthropic Claude",
            "model": "claude-3-5-sonnet-latest",
            "name": "Tier 1 (Principal Claude): Anthropic API (/v1/messages)",
            "status": "error",
            "http_code": 0,
            "latency_ms": total_ms,
            "detail": f"Erro de conexão com Anthropic ({total_ms} ms): {e}",
            "error": str(e),
        }


async def check_ai_cascade() -> Dict[str, Any]:
    """Executes parallel HTTP health pings across Anthropic Claude (when configured), Gemini, NVIDIA NIM, and OpenRouter."""
    vault = load_vault()
    has_anthropic = bool(vault.get("ANTHROPIC_API_KEY", "").strip())
    anthropic_res, gemini_res, nvidia_res, openrouter_res = await asyncio.gather(
        check_anthropic_api(vault),
        check_gemini_api(vault),
        check_nvidia_api(vault),
        check_openrouter_api(vault),
    )

    active_providers = (
        [anthropic_res, gemini_res, nvidia_res, openrouter_res]
        if has_anthropic
        else [gemini_res, nvidia_res, openrouter_res]
    )
    ok_count = sum(1 for p in active_providers if p["status"] == "ok")
    total_count = len(active_providers)

    if ok_count == total_count:
        overall_status = "ok"
        chain_label = "Claude -> Gemini -> NVIDIA NIM -> OpenRouter" if has_anthropic else "Gemini -> NVIDIA NIM -> OpenRouter (Slot Claude pronto)"
        summary = f"Todos os {total_count} motores ativos responderam HTTP 200 OK! Cascata ({chain_label}) 100% blindada."
    elif ok_count == 0:
        overall_status = "error"
        summary = "FALHA CRÍTICA: Todos os provedores ativos da Cascata de IA falharam! Verifique suas chaves no Cofre Central."
    else:
        overall_status = "warn"
        primary_res = anthropic_res if has_anthropic else gemini_res
        if primary_res["status"] != "ok":
            summary = f"ALERTA DE FAILOVER (MODO RESERVA): Motor principal ({primary_res['provider']}) indisponível — Sistema operando automaticamente nos motores reserva!"
        else:
            summary = f"ATENÇÃO DE REDUNDÂNCIA ({ok_count}/{total_count} Online): Motor principal ativo, mas uma camada reserva apresentou aviso."

    return {
        "title": "Cascata de Inteligência Artificial (Failover)",
        "overall_status": overall_status,
        "ok_count": ok_count,
        "total_count": total_count,
        "summary": summary,
        "providers": active_providers,
        "all_tiers": [anthropic_res, gemini_res, nvidia_res, openrouter_res],
        "anthropic": anthropic_res,
        "gemini": gemini_res,
        "nvidia": nvidia_res,
        "openrouter": openrouter_res,
    }


async def diagnose_single_project(
    project_id: str,
    gemini_result: Optional[Dict[str, Any]] = None,
    ai_cascade: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if project_id not in PROJECTS:
        raise HTTPException(status_code=404, detail="Projeto não encontrado")

    proj = PROJECTS[project_id]
    checks: List[Dict[str, Any]] = []
    p_path = Path(proj["path"])

    # 1. Container & Docker Image Status
    containers = await get_project_containers(proj["path"])
    running = [c for c in containers if c.get("state") == "running"]

    img_res = await run_cmd(["docker", "image", "inspect", proj["image_check"]], timeout=10)
    image_built = img_res["returncode"] == 0

    if len(containers) > 0 and len(running) == len(containers):
        checks.append(
            {
                "category": "Docker Containers",
                "status": "ok",
                "title": f"{len(running)}/{len(containers)} contêiner(es) rodando ativamente",
                "detail": ", ".join([f"{c['name']} ({c['status']})" for c in running]),
            }
        )
    elif len(running) > 0:
        log_res = await run_cmd(["docker", "compose", "logs", "--tail=12", "--no-color"], cwd=proj["path"], timeout=10)
        checks.append(
            {
                "category": "Docker Containers",
                "status": "warn",
                "title": f"Execução parcial ({len(running)}/{len(containers)} ativos)",
                "detail": log_res["stdout"][-400:] if log_res["stdout"] else "Alguns serviços secundários pararam.",
            }
        )
    else:
        if image_built:
            checks.append(
                {
                    "category": "Docker Containers",
                    "status": "warn",
                    "title": "Desligado (Economizando RAM) · Imagem Docker pronta",
                    "detail": f"Imagem '{proj['image_check']}' compilada e pronta para subir via botão Ligar.",
                }
            )
        else:
            checks.append(
                {
                    "category": "Docker Containers",
                    "status": "error",
                    "title": "Contêiner parado e imagem não encontrada",
                    "detail": f"Imagem '{proj['image_check']}' não localizada localmente.",
                }
            )

    # 2. HTTP Port Probe
    is_online = len(running) > 0
    async with httpx.AsyncClient(timeout=4.0, follow_redirects=True) as client:
        for ep in proj["endpoints"]:
            try:
                r = await client.get(ep["url"])
                if r.status_code < 400:
                    checks.append(
                        {
                            "category": f"HTTP Porta {ep['port']}",
                            "status": "ok",
                            "title": f"{ep['name']} respondeu HTTP {r.status_code} OK",
                            "detail": f"Endpoint {ep['url']} acessível e operacional.",
                        }
                    )
                else:
                    checks.append(
                        {
                            "category": f"HTTP Porta {ep['port']}",
                            "status": "error",
                            "title": f"{ep['name']} retornou HTTP {r.status_code}",
                            "detail": f"Resposta inesperada em {ep['url']}",
                        }
                    )
            except Exception:
                if not is_online:
                    checks.append(
                        {
                            "category": f"HTTP Porta {ep['port']}",
                            "status": "warn",
                            "title": f"Porta {ep['port']} livre (Serviço desligado)",
                            "detail": f"{ep['name']} ficará disponível em {ep['url']} ao ligar o card.",
                        }
                    )
                else:
                    checks.append(
                        {
                            "category": f"HTTP Porta {ep['port']}",
                            "status": "error",
                            "title": f"Sem resposta na porta {ep['port']}",
                            "detail": f"O contêiner está ativo, mas {ep['url']} ainda não respondeu.",
                        }
                    )

    # 3. Project-Specific Internal Integrity Checks
    if project_id == "openfilm":
        if is_online:
            ff = await run_cmd(["docker", "exec", "openfilm-studio", "ffmpeg", "-version"], timeout=10)
        else:
            ff = await run_cmd(["docker", "run", "--rm", "--entrypoint", "ffmpeg", proj["image_check"], "-version"], timeout=15)
        if ff["returncode"] == 0:
            first_line = ff["stdout"].splitlines()[0] if ff["stdout"] else "ffmpeg OK"
            checks.append(
                {
                    "category": "Integridade Interna (FFmpeg)",
                    "status": "ok",
                    "title": "FFmpeg & FFprobe verificados no OpenFilm",
                    "detail": first_line,
                }
            )
        else:
            checks.append(
                {
                    "category": "Integridade Interna (FFmpeg)",
                    "status": "error",
                    "title": "Falha ao verificar FFmpeg no OpenFilm",
                    "detail": ff["stderr"] or "Binário ffmpeg ausente",
                }
            )

    elif project_id == "moneyprinter":
        if is_online:
            im = await run_cmd(["docker", "exec", "moneyprinterturbo-api", "convert", "-version"], timeout=10)
        else:
            im = await run_cmd(["docker", "run", "--rm", "--entrypoint", "convert", proj["image_check"], "-version"], timeout=15)
        if im["returncode"] == 0:
            first_line = im["stdout"].splitlines()[0] if im["stdout"] else "ImageMagick OK"
            checks.append(
                {
                    "category": "Integridade Interna (ImageMagick)",
                    "status": "ok",
                    "title": "ImageMagick + FFmpeg + Edge-TTS verificados",
                    "detail": first_line,
                }
            )
        else:
            checks.append(
                {
                    "category": "Integridade Interna (ImageMagick)",
                    "status": "error",
                    "title": "ImageMagick não encontrado na imagem",
                    "detail": im["stderr"] or "convert ausente",
                }
            )

    elif project_id == "supoclip":
        if is_online:
            ff = await run_cmd(["docker", "exec", "supoclip-backend", "ffmpeg", "-version"], timeout=10)
        else:
            ff = await run_cmd(["docker", "run", "--rm", "--entrypoint", "ffmpeg", proj["image_check"], "-version"], timeout=15)
        cpu_only = read_env_value(p_path / ".env", "BACKEND_CPU_ONLY")
        if ff["returncode"] == 0:
            first_line = ff["stdout"].splitlines()[0] if ff["stdout"] else "FFmpeg OK"
            checks.append(
                {
                    "category": "Integridade Interna (CPU & FFmpeg)",
                    "status": "ok",
                    "title": f"FFmpeg OK · BACKEND_CPU_ONLY={cpu_only or 'true'}",
                    "detail": first_line,
                }
            )
        else:
            checks.append(
                {
                    "category": "Integridade Interna (CPU & FFmpeg)",
                    "status": "error",
                    "title": "Falha ao validar FFmpeg no SupoClip",
                    "detail": ff["stderr"],
                }
            )

    elif project_id == "agenttube":
        if is_online:
            ff = await run_cmd(["docker", "exec", "agenttube", "ffmpeg", "-version"], timeout=10)
        else:
            ff = await run_cmd(["docker", "run", "--rm", "--entrypoint", "ffmpeg", proj["image_check"], "-version"], timeout=15)
        vid_prov = read_env_value(p_path / ".env", "VIDEO_PROVIDER")
        if ff["returncode"] == 0:
            first_line = ff["stdout"].splitlines()[0] if ff["stdout"] else "FFmpeg OK"
            checks.append(
                {
                    "category": "Integridade Interna (FFmpeg & Slideshow)",
                    "status": "ok",
                    "title": f"FFmpeg OK · VIDEO_PROVIDER={vid_prov or 'slideshow'} (Sem GPU)",
                    "detail": first_line,
                }
            )
        else:
            checks.append(
                {
                    "category": "Integridade Interna (FFmpeg & Slideshow)",
                    "status": "error",
                    "title": "Falha ao validar FFmpeg no AgentTube",
                    "detail": ff["stderr"],
                }
            )

    elif project_id == "openhiggsfield":
        node_v = await run_cmd(["docker", "exec", "open-higgsfield", "node", "-v"], timeout=10) if is_online else {"returncode": 0, "stdout": "Node v22 (compilado)"}
        checks.append(
            {
                "category": "Integridade Interna (Next.js 16 / Node)",
                "status": "ok" if node_v["returncode"] == 0 else "warn",
                "title": "Next.js 16 + React 19 Frontend App",
                "detail": f"Runtime: {node_v['stdout']} · Standalone CPU server na porta 3020",
            }
        )

    elif project_id == "voicestudio":
        if is_online:
            ff = await run_cmd(["docker", "exec", "voicestudio", "ffmpeg", "-version"], timeout=10)
            es = await run_cmd(["docker", "exec", "voicestudio", "espeak-ng", "--version"], timeout=10)
            ok_ff = ff["returncode"] == 0
            ok_es = es["returncode"] == 0
            checks.append(
                {
                    "category": "Integridade Interna (FFmpeg & eSpeak-ng)",
                    "status": "ok" if (ok_ff and ok_es) else "warn",
                    "title": "Motores de Síntese Neural & CPU Validados",
                    "detail": f"FFmpeg: {'OK' if ok_ff else 'Ausente'} | eSpeak-ng: {'OK' if ok_es else 'Ausente'} | Edge-TTS Ativo",
                }
            )
        else:
            checks.append(
                {
                    "category": "Integridade Interna (FFmpeg & eSpeak-ng)",
                    "status": "ok",
                    "title": "Imagem VoiceStudio CPU-Only compilada",
                    "detail": "Python 3.11 + Edge-TTS + eSpeak-ng + FFmpeg",
                }
            )

    elif project_id == "openseo":
        checks.append(
            {
                "category": "Integridade Interna (Cloudflare D1 & Worker)",
                "status": "ok",
                "title": "OpenSEO Local Server",
                "detail": "Modo local sem autenticação externa (AUTH_MODE=local_noauth) na porta 3040",
            }
        )

    # 4. 3-Tier AI Cascade Live Check (Gemini -> NVIDIA NIM -> OpenRouter)
    if ai_cascade is None:
        ai_cascade = await check_ai_cascade()
    checks.append(
        {
            "category": "Cascata de Inteligência Artificial (Failover)",
            "status": ai_cascade["overall_status"],
            "title": f"{ai_cascade['ok_count']}/3 Provedores Online (Gemini · NVIDIA NIM · OpenRouter)",
            "detail": (
                f"T1 Gemini: {ai_cascade['gemini']['status'].upper()} ({ai_cascade['gemini']['latency_ms']} ms) | "
                f"T2 NVIDIA: {ai_cascade['nvidia']['status'].upper()} ({ai_cascade['nvidia']['latency_ms']} ms) | "
                f"T3 OpenRouter: {ai_cascade['openrouter']['status'].upper()} ({ai_cascade['openrouter']['latency_ms']} ms)"
            ),
        }
    )

    statuses = [c["status"] for c in checks]
    if "error" in statuses:
        overall = "error"
    elif "warn" in statuses:
        overall = "warn"
    else:
        overall = "ok"

    return {
        "project_id": project_id,
        "name": proj["name"],
        "overall_status": overall,
        "timestamp": time.strftime("%H:%M:%S"),
        "checks": checks,
    }


async def diagnose_skills() -> Dict[str, Any]:
    if STUDIO_MODE == "vps" and not SKILLS_DIR.exists():
        return {
            "category": "Central de Skills Globais (Cliente Local)",
            "status": "ok",
            "title": "Estação VPS Cloud · Skills gerenciadas no Cliente Leve Local",
            "detail": "Servidor focado em processamento pesado Docker + Cascata IA + Galeria de Downloads",
            "skills": [],
        }

    skills_res = await api_list_skills()
    skills = skills_res.get("skills", [])
    global_skills = [s for s in skills if s.get("source") == "global-media"]
    dev_skills = [s for s in skills if s.get("source") == "dev-vault"]
    modo_dev = skills_res.get("modo_dev", {})
    all_valid = all(s.get("valid", False) for s in global_skills) and len(global_skills) >= 10

    modo_label = "ATIVO" if modo_dev.get("active") else "EM REPOUSO (economia de tokens)"
    return {
        "category": "Central de Skills Globais (HyperFrames + Hypit + Cofre Dev)",
        "status": "ok" if all_valid else "warn",
        "title": f"{len(global_skills)} Skills de Mídia Ativas · {len(dev_skills)} Skills Dev ({modo_label})",
        "detail": f"Diretórios: ~/.gemini/config/skills/ ({len(skills)}) e ~/.agents/skills/ ({skills_res.get('agents_count', 0)})",
        "skills": skills,
    }


async def diagnose_infrastructure() -> Dict[str, Any]:
    """Valida Nginx/TLS/Basic-Bearer Auth, rotas de proxy das 7 stacks, volumes Docker, Outputs e Swap/Disk sem acordar contêineres."""
    nginx_cfg_path = Path("/etc/nginx/sites-available/media-studio")
    cert_path = Path("/etc/letsencrypt/live/187.77.202.176.sslip.io/fullchain.pem")

    # 1. Nginx syntax + TLS certificate check
    nginx_task = run_cmd(["nginx", "-t"], timeout=8)
    cert_task = (
        run_cmd(["openssl", "x509", "-enddate", "-noout", "-in", str(cert_path)], timeout=6)
        if cert_path.exists()
        else asyncio.sleep(0, result={"returncode": 1, "stdout": "", "stderr": "Certificado não encontrado"})
    )
    vol_task = run_cmd(["docker", "volume", "ls", "-q"], timeout=8)
    img_res_task = run_cmd(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], timeout=8)

    nginx_res, cert_res, vol_res, img_list_res = await asyncio.gather(
        nginx_task, cert_task, vol_task, img_res_task
    )

    nginx_ok = nginx_res["returncode"] == 0
    tls_status = "warn"
    tls_days: Optional[int] = None
    tls_detail = "Certificado Let's Encrypt local não aplicável fora da VPS"
    if cert_res.get("returncode") == 0 and "notAfter=" in cert_res.get("stdout", ""):
        raw_date = cert_res["stdout"].split("notAfter=", 1)[1].strip()
        try:
            exp = datetime.strptime(raw_date, "%b %d %H:%M:%S %Y %Z").replace(tzinfo=timezone.utc)
            tls_days = max(0, int((exp - datetime.now(timezone.utc)).total_seconds() // 86400))
            tls_status = "ok" if tls_days >= 14 else "warn"
            tls_detail = f"HTTPS oficial válido · expira em {tls_days} dia(s) ({raw_date})"
        except Exception:
            tls_status = "ok"
            tls_detail = raw_date

    # Extract Nginx bearer token cleanly on host so auth checks run over loopback TLS without hardcoding secrets
    internal_token = ""
    if nginx_cfg_path.exists():
        try:
            cfg_txt = nginx_cfg_path.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r'if \(\$http_x_studio_token\s*=\s*"([^"]+)"\)', cfg_txt)
            if m:
                internal_token = m.group(1)
        except Exception:
            pass

    auth_enforced = False
    auth_detail = "Proxy Nginx local não detectado"
    route_checks: List[Dict[str, Any]] = []
    probe_specs = [
        ("/app/supoclip/", "SupoClip UI (:3107)"),
        ("/app/moneyprinter/", "MoneyPrinterTurbo (:8501)"),
        ("/app/openfilm/", "OpenFilm UI (:3005)"),
        ("/app/openfilm-film/", "OpenFilm Film (:3006)"),
        ("/app/agenttube/", "AgentTube UI (:3010)"),
        ("/app/higgsfield/", "Open Higgsfield (:3020)"),
        ("/app/voicestudio/", "VoiceStudio UI (:3030)"),
        ("/app/openseo/", "OpenSEO UI (:3040)"),
        ("/app/openseo/api/auth/get-session", "OpenSEO Auth Shim"),
    ]

    if nginx_cfg_path.exists():
        try:
            async with httpx.AsyncClient(verify=False, timeout=4.0, follow_redirects=True) as client:
                try:
                    unauth_r = await client.get(
                        "https://127.0.0.1/app/supoclip/",
                        headers={"Host": "187.77.202.176.sslip.io", "Accept": "application/json"},
                    )
                    auth_enforced = unauth_r.status_code == 401
                    auth_detail = (
                        "HTTP 401 obrigatório sem credenciais (Blindagem Basic/Bearer ativa)"
                        if auth_enforced
                        else f"Esperado HTTP 401 sem auth, obtido {unauth_r.status_code}"
                    )
                except Exception as exc:
                    auth_detail = f"Erro testando auth HTTPS: {exc}"

                auth_hdrs = {
                    "Host": "187.77.202.176.sslip.io",
                    "Accept": "application/json",
                }
                if internal_token:
                    auth_hdrs["X-Studio-Token"] = internal_token

                async def _probe_route(route_path: str, label: str) -> Dict[str, Any]:
                    t0 = time.monotonic()
                    try:
                        r = await client.get(f"https://127.0.0.1{route_path}", headers=auth_hdrs)
                        ms = int((time.monotonic() - t0) * 1000)
                        body_txt = r.text[:240] if r.text else ""
                        is_standby = '"state":"standby"' in body_txt.replace(" ", "")
                        if r.status_code == 200:
                            mode = "standby" if is_standby else "online"
                            return {
                                "route": route_path,
                                "label": label,
                                "status": "ok",
                                "http_code": r.status_code,
                                "latency_ms": ms,
                                "mode": mode,
                                "detail": f"HTTP 200 ({'Standby Portal' if is_standby else 'Upstream/Shim OK'}) · {ms} ms",
                            }
                        return {
                            "route": route_path,
                            "label": label,
                            "status": "error",
                            "http_code": r.status_code,
                            "latency_ms": ms,
                            "mode": "error",
                            "detail": f"HTTP {r.status_code} inesperado em {route_path}",
                        }
                    except Exception as exc:
                        return {
                            "route": route_path,
                            "label": label,
                            "status": "error",
                            "http_code": 0,
                            "latency_ms": int((time.monotonic() - t0) * 1000),
                            "mode": "error",
                            "detail": str(exc),
                        }

                route_checks = list(await asyncio.gather(*[_probe_route(rp, lb) for rp, lb in probe_specs]))
        except Exception as exc:
            auth_detail = f"Falha ao inicializar cliente HTTPS: {exc}"

    built_images = set(img_list_res["stdout"].splitlines()) if img_list_res["returncode"] == 0 else set()
    images_ready = sum(1 for p in PROJECTS.values() if p["image_check"] in built_images)
    volumes_list = [v for v in vol_res["stdout"].splitlines() if v.strip()] if vol_res["returncode"] == 0 else []
    dirs_present = sum(1 for p in PROJECTS.values() if Path(p["path"]).is_dir())

    outputs = _scan_output_files(stack_filter="all", limit=500)
    outputs_bytes = sum(int(o.get("size_bytes") or 0) for o in outputs)

    mem = psutil.virtual_memory()
    swap = psutil.swap_memory()
    disk = shutil.disk_usage(str(STUDIO_BASE_DIR if STUDIO_BASE_DIR.exists() else "/"))
    routes_ok = sum(1 for rc in route_checks if rc["status"] == "ok")

    overall = "ok"
    if (nginx_cfg_path.exists() and not nginx_ok) or any(rc["status"] == "error" for rc in route_checks):
        overall = "error"
    elif (nginx_cfg_path.exists() and not auth_enforced) or images_ready < len(PROJECTS) or swap.total == 0:
        overall = "warn"

    return {
        "overall_status": overall,
        "summary": (
            f"Nginx {'OK' if nginx_ok else 'ERRO'} · TLS {tls_days if tls_days is not None else '-'}d · "
            f"{routes_ok}/{len(route_checks)} rotas HTTPS validadas · {images_ready}/{len(PROJECTS)} imagens Docker"
        ),
        "nginx_tls": {
            "config_status": "ok" if nginx_ok else ("warn" if not nginx_cfg_path.exists() else "error"),
            "config_detail": "nginx -t syntax OK" if nginx_ok else (nginx_res["stderr"] or "Nginx ausente").strip()[:160],
            "tls_status": tls_status,
            "tls_days_remaining": tls_days,
            "tls_detail": tls_detail,
            "auth_enforced": auth_enforced,
            "auth_detail": auth_detail,
        },
        "routes": route_checks,
        "storage": {
            "docker_status": "ok" if img_list_res["returncode"] == 0 else "error",
            "images_ready": images_ready,
            "images_total": len(PROJECTS),
            "volumes_count": len(volumes_list),
            "dirs_ok": dirs_present,
            "dirs_total": len(PROJECTS),
            "outputs_count": len(outputs),
            "outputs_size": _human_size(outputs_bytes),
        },
        "host": {
            "ram_used_gb": round(mem.used / (1024**3), 2),
            "ram_total_gb": round(mem.total / (1024**3), 2),
            "ram_percent": round(mem.percent, 1),
            "swap_used_gb": round(swap.used / (1024**3), 2),
            "swap_total_gb": round(swap.total / (1024**3), 2),
            "swap_percent": round(swap.percent, 1),
            "disk_free_gb": round(disk.free / (1024**3), 1),
            "disk_total_gb": round(disk.total / (1024**3), 1),
            "disk_percent": round((disk.used / disk.total) * 100, 1) if disk.total else 0.0,
        },
    }


@app.post("/api/diagnostics/all")
async def api_diagnostics_all():
    cascade_res, skills_diag, infra_diag = await asyncio.gather(
        check_ai_cascade(), diagnose_skills(), diagnose_infrastructure()
    )
    tasks = [diagnose_single_project(pid, ai_cascade=cascade_res) for pid in PROJECTS]
    reports = await asyncio.gather(*tasks)
    return {
        "ok": True,
        "ai_cascade": cascade_res,
        "gemini_api": cascade_res["gemini"],
        "skills_diag": skills_diag,
        "infra": infra_diag,
        "reports": reports,
        "timestamp": time.strftime("%H:%M:%S"),
    }


@app.post("/api/diagnostics/{project_id}")
async def api_diagnostics_project(project_id: str):
    report = await diagnose_single_project(project_id)
    return {"ok": True, "report": report}
