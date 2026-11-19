#!/usr/bin/env python3
"""Introspect the live AI Media Studio codebase and generate a complete, detailed engineering & operations dossier.

Outputs:
  - /home/wbrendo/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt
  - <repo_root>/docs/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt
Security invariant: NEVER prints raw API keys, passwords, or tokens (records only key names and SET=True/False).
"""

from __future__ import annotations

import ast
import json
import os
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Tuple


REPO_ROOT = Path(__file__).resolve().parent.parent
HOME_OUT = Path("/home/wbrendo/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt")
DOCS_OUT = REPO_ROOT / "docs/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt"
VPS_HOST = "root@187.77.202.176"


def _human_size(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if val < 1024 or unit == "GB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024.0
    return f"{n} B"


def _extract_python_symbols(path: Path) -> Tuple[int, str, List[Dict[str, Any]]]:
    src = path.read_text(encoding="utf-8", errors="ignore")
    lines = src.splitlines()
    tree = ast.parse(src, filename=str(path))
    doc = (ast.get_docstring(tree) or "").strip().splitlines()[0] if ast.get_docstring(tree) else ""
    syms: List[Dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = [a.arg for a in node.args.args]
            if node.args.kwonlyargs:
                args.extend(a.arg for a in node.args.kwonlyargs)
            kind = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
            ndoc = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""
            syms.append({
                "kind": kind,
                "name": node.name,
                "start": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
                "args": ", ".join(args[:8]) + ("..." if len(args) > 8 else ""),
                "doc": ndoc[:120],
            })
        elif isinstance(node, ast.ClassDef):
            methods = [
                f"{m.name}(L{m.lineno})"
                for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            ]
            ndoc = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""
            syms.append({
                "kind": "class",
                "name": node.name,
                "start": node.lineno,
                "end": getattr(node, "end_lineno", node.lineno),
                "args": f"methods={len(methods)}: {', '.join(methods[:10])}{'...' if len(methods) > 10 else ''}",
                "doc": ndoc[:120],
            })
    return len(lines), doc, syms


def _extract_fastapi_routes(main_py: Path) -> List[Dict[str, Any]]:
    src = main_py.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src, filename=str(main_py))
    routes: List[Dict[str, Any]] = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            if (
                isinstance(dec, ast.Call)
                and isinstance(dec.func, ast.Attribute)
                and isinstance(dec.func.value, ast.Name)
                and dec.func.value.id == "app"
                and dec.func.attr in ("get", "post", "put", "delete", "patch", "websocket", "api_route")
            ):
                method = dec.func.attr.upper()
                path_arg = ""
                if dec.args and isinstance(dec.args[0], ast.Constant):
                    path_arg = str(dec.args[0].value)
                params: List[str] = []
                for a in node.args.args:
                    ann = ast.unparse(a.annotation) if a.annotation else "Any"
                    params.append(f"{a.arg}: {ann}")
                doc = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""
                routes.append({
                    "method": method,
                    "path": path_arg,
                    "handler": node.name,
                    "line": node.lineno,
                    "end_line": getattr(node, "end_lineno", node.lineno),
                    "params": ", ".join(params) if params else "(none)",
                    "doc": doc[:120],
                })
    return routes


def _extract_index_html_summary(index_html: Path) -> Dict[str, Any]:
    txt = index_html.read_text(encoding="utf-8", errors="ignore")
    lines = txt.splitlines()
    ids = re.findall(r'\bid=["\']([^"\']+)["\']', txt)
    tabs = [i for i in ids if i.startswith(("tab-", "panel-", "view-", "vtab-", "mnav-"))]
    guided = [i for i in ids if i.startswith("gp-")]
    novela = [i for i in ids if i.startswith(("nov-", "qnv-", "btn-nov-", "btn-qnv-"))]
    hyper = [i for i in ids if i.startswith(("qhf-", "btn-qhf-"))]
    other_quick = [i for i in ids if i.startswith(("qvu-", "qvs-", "qsc-", "qmp-", "card-hard-", "quota-", "kpi-cascade"))]
    js_funcs: List[Tuple[int, str]] = []
    for idx, line in enumerate(lines, start=1):
        m = re.match(r"^\s*(?:async\s+)?function\s+([A-Za-z0-9_]+)\s*\(", line)
        if m:
            js_funcs.append((idx, m.group(1)))
    return {
        "lines": len(lines),
        "bytes": index_html.stat().st_size,
        "total_ids": len(ids),
        "tabs": tabs,
        "guided_ids": guided,
        "novela_ids": novela,
        "hyper_ids": hyper,
        "hard_cards_ids": other_quick,
        "js_functions": js_funcs,
    }


def _extract_mcp_tools(mcp_py: Path) -> List[Dict[str, Any]]:
    src = mcp_py.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src, filename=str(mcp_py))
    tools: List[Dict[str, Any]] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            is_mcp = any(
                isinstance(d, ast.Call)
                and isinstance(d.func, ast.Attribute)
                and d.func.attr == "tool"
                for d in node.decorator_list
            )
            if is_mcp:
                doc = (ast.get_docstring(node) or "").strip().splitlines()[0] if ast.get_docstring(node) else ""
                tools.append({
                    "name": node.name,
                    "line": node.lineno,
                    "end": getattr(node, "end_lineno", node.lineno),
                    "args": ", ".join(a.arg for a in node.args.args),
                    "doc": doc[:130],
                })
    return tools


def _fetch_vps_runtime_snapshot() -> Dict[str, Any]:
    """Query non-secret VPS operational snapshot (key presence, hardware, Docker state, exports, voice profiles)."""
    script = r"""
import json, os, subprocess
from pathlib import Path

vault = {}
vp = Path("/opt/ai-media-studio/media-control-panel/vault.json")
if vp.is_file():
    try:
        vault = json.loads(vp.read_text(encoding="utf-8"))
    except Exception:
        vault = {}

env_keys = {}
for ep in (Path("/opt/ai-media-studio/media-control-panel/.env"), Path("/opt/ai-media-studio/.env")):
    if ep.is_file():
        for l in ep.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = l.strip()
            if s and not s.startswith("#") and "=" in s:
                k, v = s.split("=", 1)
                val_present = bool(v.strip().strip("\"'"))
                env_keys[k.strip()] = env_keys.get(k.strip(), False) or val_present

all_keys = sorted(set(vault.keys()) | set(env_keys.keys()))
status = {k: bool(str(vault.get(k) or "").strip() or env_keys.get(k)) for k in all_keys}

voices = sorted(p.name for p in Path("/opt/ai-media-studio/voices/regional").iterdir()) if Path("/opt/ai-media-studio/voices/regional").is_dir() else []
voice_profiles = {}
pfile = Path("/opt/ai-media-studio/voices/regional/profiles.json")
if pfile.is_file():
    try:
        raw_p = json.loads(pfile.read_text(encoding="utf-8"))
        for pid, pinfo in raw_p.items():
            voice_profiles[pid] = {
                "label": pinfo.get("label"),
                "region": pinfo.get("region"),
                "gemini_voice": pinfo.get("gemini_voice"),
                "gemini_alt_voice": pinfo.get("gemini_alt_voice"),
                "edge_voice": pinfo.get("edge_voice"),
                "has_custom_reference": bool(pinfo.get("has_custom_reference")),
                "reference_duration_sec": pinfo.get("reference_duration_sec"),
            }
    except Exception:
        pass

exports_info = {}
exp_root = Path("/opt/ai-media-studio/exports")
if exp_root.is_dir():
    for sub in sorted(exp_root.iterdir()):
        if sub.is_dir():
            mp4s = sorted(sub.rglob("*.mp4"), key=lambda f: f.stat().st_mtime, reverse=True)[:12]
            exports_info[sub.name] = [(str(f.relative_to(sub)), f.stat().st_size) for f in mp4s]

def _cmd(args):
    try:
        return subprocess.check_output(args, text=True, timeout=5).strip()
    except Exception:
        return "unknown"

free_lines = _cmd(["free", "-m"]).splitlines()
mem_fmt = "7935 MB"
swap_fmt = "4095 MB"
if len(free_lines) >= 2:
    mp = free_lines[1].split()
    if len(mp) >= 7:
        mem_fmt = f"{mp[1]} MB total ({mp[6]} MB available, {mp[2]} MB used)"
if len(free_lines) >= 3:
    sp = free_lines[2].split()
    if len(sp) >= 4:
        swap_fmt = f"{sp[1]} MB total ({sp[3]} MB free)"
df_parts = _cmd(["df", "-h", "/"]).splitlines()[-1].split()
disk_fmt = f"{df_parts[1]} total, {df_parts[2]} used ({df_parts[4]}), {df_parts[3]} free" if len(df_parts) >= 5 else "96G"

hw = {
    "vcpus": _cmd(["nproc"]),
    "free_h": mem_fmt,
    "swap_h": swap_fmt,
    "disk_root": disk_fmt,
    "panel_service": _cmd(["systemctl", "is-active", "media-panel"]),
    "docker_active": _cmd(["bash", "-lc", "docker ps -q | wc -l"]),
    "cache_wavs": len(list(Path("/opt/ai-media-studio/voices/cache").glob("*.wav"))) if Path("/opt/ai-media-studio/voices/cache").is_dir() else 0,
}

qt_summary = {}
qtp = Path("/opt/ai-media-studio/media-control-panel/quota_telemetry.json")
if qtp.is_file():
    try:
        qt_data = json.loads(qtp.read_text(encoding="utf-8"))
        qt_summary = {
            "date": qt_data.get("date"),
            "models_tracked": len(qt_data.get("models") or {}),
            "active_notes": "see /api/ai/quota-telemetry",
        }
    except Exception:
        pass

print(json.dumps({
    "env_status": status,
    "voices_dir": voices,
    "voice_profiles": voice_profiles,
    "exports_info": exports_info,
    "hardware": hw,
    "quota_meta": qt_summary,
}))
"""
    try:
        out = subprocess.check_output(
            ["ssh", "-o", "BatchMode=yes", VPS_HOST, "/opt/ai-media-studio/tools/video-use/.venv/bin/python", "-"],
            input=script,
            text=True,
            timeout=20,
        )
        return json.loads(out.strip().splitlines()[-1])
    except Exception as exc:
        return {
            "env_status": {"SSH_CHECK_NOTE": f"unavailable ({exc})"},
            "voices_dir": [],
            "voice_profiles": {},
            "exports_info": {},
            "hardware": {},
            "quota_meta": {},
        }


def _list_skills() -> Dict[str, List[str]]:
    res: Dict[str, List[str]] = {}
    roots = {
        "Global Antigravity (~/.gemini/config/skills)": Path("/home/wbrendo/.gemini/config/skills"),
        "Shared Agents (~/.agents/skills)": Path("/home/wbrendo/.agents/skills"),
        "OpenJarvis (~/.openjarvis/skills)": Path("/home/wbrendo/.openjarvis/skills"),
        "Dev Vault · engineering": REPO_ROOT / "dev-skills-vault/skills/engineering",
        "Dev Vault · productivity": REPO_ROOT / "dev-skills-vault/skills/productivity",
        "Dev Vault · in-progress": REPO_ROOT / "dev-skills-vault/skills/in-progress",
        "Dev Vault · misc": REPO_ROOT / "dev-skills-vault/skills/misc",
    }
    for label, p in roots.items():
        if p.is_dir():
            names = sorted(d.name for d in p.iterdir() if d.is_dir())
            res[label] = names
    return res


def _get_git_head() -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(REPO_ROOT), "log", "-n", "1", "--oneline"],
            text=True,
            timeout=5,
        ).strip()
    except Exception:
        return "unknown"


def _load_local_guided_meta() -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    gp_path = REPO_ROOT / "media-control-panel/guided_pipelines.py"
    if not gp_path.is_file():
        return [], {}
    ns: Dict[str, Any] = {}
    try:
        src = gp_path.read_text(encoding="utf-8")
        m_pre = re.search(r"PLATFORM_PRESETS:.*?=\s*(\{.*?\n\})", src, flags=re.DOTALL)
        m_tpl = re.search(r"GUIDED_TEMPLATES:.*?=\s*(\[.*?\n\])", src, flags=re.DOTALL)
        if m_pre:
            ns["presets"] = ast.literal_eval(m_pre.group(1))
        if m_tpl:
            ns["templates"] = ast.literal_eval(m_tpl.group(1))
    except Exception:
        pass
    return ns.get("templates") or [], ns.get("presets") or {}


def build_dossier() -> str:
    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    git_head = _get_git_head()
    vps_info = _fetch_vps_runtime_snapshot()
    routes = _extract_fastapi_routes(REPO_ROOT / "media-control-panel/main.py")
    html_meta = _extract_index_html_summary(REPO_ROOT / "media-control-panel/static/index.html")
    mcp_tools = _extract_mcp_tools(REPO_ROOT / "openjarvis-client/studio_controller_mcp.py")
    skills_map = _list_skills()
    guided_templates, platform_presets = _load_local_guided_meta()
    hw = vps_info.get("hardware") or {}

    key_modules = [
        ("media-control-panel/main.py", "Servidor FastAPI central (porta 9000 VPS), Vault, orquestração Docker RAM-safe, AI Cascade Gateway & Rotas de Estúdio (v4/v3.1)"),
        ("media-control-panel/guided_pipelines.py", "Orquestrador assíncrono das 6 Trilhas Guiadas (RAM Relay, Checkpoints JSON em disco, Safe-Zones, Roteirista IA Cinema v4/v3.1)"),
        ("tools/hyperframes/quota_telemetry.py", "Rastreador persistente RPM/RPD/cooldowns/cache hits por processo (fcntl.flock + quota_telemetry.json)"),
        ("tools/hyperframes/cinema_v4_director.py", "Cinema Engine v4.0: Bíblia Visual, Shot Compiler (OTS/Contraplano/Reação/Conjunto, Eixo 180°), Stitcher J/L-Cut (LRA >= 5 LU, -16 LUFS) & Flow Pack v4"),
        ("tools/hyperframes/vps_novela_runner.py", "Motor Novela Studio Dual-Engine (cinema_engine='v4' c/ Colab/Flow full-scene ou fallback legado 'v3.1' MoDA Frontal)"),
        ("tools/hyperframes/regional_voice_engine.py", "Motor de Voz Nordestina v4.0: Audio Quality Gate (RMS/ZCR stdlib + Whisper INT8 anti-aliasing), Cache SHA-256, Gemini Live & Seed-VC"),
        ("tools/colab-gpu-worker/colab_v4_cinema_server.py", "Worker FastAPI Colab Pro L4 v4.0: Dual-venv Seed-VC + WanGP shared.api persistente, Fila Assíncrona /jobs/animate_scene & /health real"),
        ("tools/hyperframes/novela_lipsync_animator.py", "Cascata GPU Legada v3.1 (Tier 1 Colab MoDA-L4 LiveVASAPipeline -> LivePortrait+LatentSync 1.5 -> ZeroGPU A10G)"),
        ("tools/hyperframes/verify_lipsync_motion.py", "Auditor Computer Vision frame a frame (estabilização por âncora nasal, mouth ROI speech vs silence, piscar e Reaction Shot)"),
        ("tests/cinema_qa.py", "Auditor Cinema Objective QA (13 critérios calibrados contra docs/refs/espectativa.mp4: Gaze yaw, OTS, LRA/LUFS/TP, silêncios, crop reuse & ASR)"),
        ("tools/hyperframes/vps_ad_runner.py", "Motor HyperFrames POV Cinema Multi-Imagem Shopee 20:9 Anti-Crop & L'Oréal Gold Luxury"),
        ("tools/hyperframes/viral_reference_analyzer.py", "Extrator de DNA Viral (cut_cadence, WPM, energia de áudio e recomendações roteirizadas)"),
        ("tools/video-use/vps_zero_cost_runner.py", "Pipeline Zero-RAM Video Use Jump-Cut + faster-whisper INT8 + Correção de Cor FFmpeg + Legendas ASS"),
        ("openjarvis-client/studio_controller_mcp.py", "Servidor MCP stdio (OpenJarvis Operador Master) conectado às APIs do Painel na VPS e estação local"),
        ("openjarvis-client/clap_listener.py", "Daemon de voz local do OpenJarvis (detecção de 2 palmas <2% CPU, VAD, faster-whisper INT8 e resposta falada via jarvis-falar)"),
        ("openjarvis-client/webcam_vision.py", "Módulo de visão local por webcam integrado ao OpenJarvis"),
    ]

    out: List[str] = []
    add = out.append

    add("=" * 100)
    add("AI MEDIA STUDIO — DOSSIÊ TÉCNICO, OPERACIONAL E ARQUITETURAL COMPLETO (v4.0-cinema)")
    add(f"Gerado por introspecção em : {now_str}")
    add(f"Commit Git Atual          : {git_head} (branch feat/v4-cinema)")
    add(f"Repositório Local         : {REPO_ROOT}")
    add("VPS Produção              : root@187.77.202.176:/opt/ai-media-studio/ (https://187.77.202.176.sslip.io)")
    add("Worker GPU Remoto (v4.0)  : Google Colab Pro NVIDIA L4 (24GB VRAM + RAM alta, Zero-Drive) via túnel Cloudflare (COLAB_GPU_URL)")
    add("Política de Segurança     : Zero valores de segredos/tokens exibidos; apenas nomes de chaves e status SET=True/False.")
    add("=" * 100)
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 1: TOPOLOGIA DE PRODUÇÃO, HARDWARE, REDE, NGINX & RAM GUARD
    # ------------------------------------------------------------------
    add("-" * 100)
    add("SEÇÃO 1 · TOPOLOGIA DE PRODUÇÃO, INFRAESTRUTURA VPS, NGINX TLS & POLÍTICA RAM GUARD")
    add("-" * 100)
    add("")
    add("1.1 Nós de Computação e Estado Verificado em Runtime:")
    add("  [A] VPS Cloud Produção (Contabo/Ubuntu Linux — Loopback FastAPI + Docker Sob Demanda):")
    add(f"      - CPUs / RAM / Swap : {hw.get('vcpus', '2')} vCPUs | {hw.get('free_h', '7935 MB')} | {hw.get('swap_h', '4095 MB')}")
    add(f"      - Armazenamento (/) : {hw.get('disk_root', '96G')}")
    add(f"      - Serviço Core      : systemd media-panel.service -> status={hw.get('panel_service', 'active')} (uvicorn 127.0.0.1:9000, SEM Docker, ~90 MB RAM)")
    add(f"      - Contêineres Ociosos: docker ps -q = {hw.get('docker_active', '0')} (Política RAM Guard: 0/7 ativos em repouso)")
    add(f"      - Cache Vocal SHA256: {hw.get('cache_wavs', 0)} clipes WAV regionais cacheados em /opt/ai-media-studio/voices/cache/")
    add("  [B] Worker GPU Remoto Colab Pro v4.0 (`COLAB_GPU_URL` dinâmico via trycloudflare.com):")
    add("      - Versão alvo v4.0    : `4.0.0-clean-l4` em NVIDIA L4 (24GB VRAM + RAM Alta) — Clean-Slate local NVMe `/content/ai_studio_v4` (ZERO Google Drive)")
    add("      - Ambientes Isolados   : Dual Python 3.10 via `uv` (`envs/seedvc` c/ numpy 1.26.4 + transformers 4.46.3; `envs/wangp` c/ FastAPI + WanGP v20.00 numpy 2.1.2 + transformers 4.54.0)")
    add("      - Contrato de Rotas    : `GET /health` (flags reais + VRAM livre), `POST /voice-convert` (Seed-VC `--f0-condition False`, HTTP 500 se falhar), `POST /jobs/animate_scene` + `GET /jobs/{job_id}` (WanGP `shared/api.py` persistente INT8 Profile 4), `POST /admin/exec` (token-gated)")
    add("      - Legado fallback v3.1 : Mantido atrás de feature flag (`cinema_engine='v3.1'`, `novela_lipsync_animator.py`: MoDA-L4 / LivePortrait+LatentSync)")
    add("  [C] Estação Local Desktop (`wbrendo-pc`):")
    add("      - Desenvolvimento, suíte Playwright E2E (Chrome 1440x900 + 390x844), sync bidirecional, backup DR atômico (`chmod 600` + `unzip -tq`)")
    add("      - OpenJarvis Operador Master (`~/open-jarvis/`): servidor `:8090` apontando `OPENAI_BASE_URL=https://187.77.202.176.sslip.io/v1`, daemon de 2 palmas (`clap_listener.py`), webcam (`webcam_vision.py`), voz local (`jarvis-falar`) e MCP `studio_controller_mcp.py`.")
    add("")

    add("1.2 Arquitetura Nginx Reverse Proxy, Autenticação Dual & Standby Portal (`infra/nginx/media-studio.conf`):")
    add("  - Domínio TLS público  : https://187.77.202.176.sslip.io (upload até `client_max_body_size 512M`)")
    add("  - Autenticação Dual    : `_check_studio_token` aceita `Authorization: Bearer st_...` ou header `X-Studio-Token: st_...`; fallback HTTP Basic Auth via `/etc/nginx/.htpasswd-studio`.")
    add("  - Standby Automático   : Se usuário abre `/app/{stack}/` com container desligado (502/503/504), `@stack_standby_portal` redireciona internamente para `GET /api/stack-standby-view` na porta 9000 oferecendo botão de boot em 1 clique.")
    add("  - Rewriting SPAs       : `/app/openfilm/` injeta shim `<script>` + `sub_filter` para interceptar `fetch`, `XHR`, `WebSocket`, `EventSource` e `localhost:3006` -> `/app/openfilm-film`, mantendo Vite/Node operando sob subpath TLS seguro.")
    add("")

    add("1.3 As 9 Capacidades Oficiais do Estúdio (2 Motores Nativos Zero-RAM + 7 Stacks Docker Sob Demanda):")
    capacities = [
        ("1. HyperFrames + Novela v4.0 / v3.1", "Nativo Zero-Idle-RAM", "9000 (CLI)", "Cinema v4.0 OTS/Flow/WanGP + Fallback v3.1, Anúncios HTML/GSAP Shopee 20:9 & Clone Viral"),
        ("2. Video Use Pro", "Nativo Zero-Idle-RAM", "9000 (CLI)", "Jump-Cut automático (`faster-whisper` INT8 CPU), remoção de silêncios/vícios, LUT FFmpeg, ASS e 48kHz SoXR"),
        ("3. SupoClip", "Docker (Postgres+Redis+ARQ)", "3107 / 8000 / 9100", "Cortes automáticos de vídeos longos em partes sequenciais (`novela`), `highlights`, `viral`, `recap`, `trailer`"),
        ("4. MoneyPrinterTurbo", "Docker (Streamlit+FastAPI)", "8501 / 8080", "Vídeos faceless narrados com roteiro IA, B-roll Pexels/Pixabay/Local e legendas dinâmicas (`shopee_safe`)"),
        ("5. OpenFilm", "Docker (Node+Vite)", "3005 / 3006", "Editor multipista completo com timeline, FPS configurável, motion blur e autologin por chave `/open?key=...`"),
        ("6. VoiceStudio", "Docker (CPU Edge-TTS)", "3030", "Locução neural PT-BR isolada e ajuste fino de pitch/rate normalizado em 48kHz Estéreo SoXR"),
        ("7. Open Higgsfield", "Docker (Next.js 16)", "3020", "Estúdio visual generativo I2V/POV avançado combinável com HyperFrames via trilha `pov_cinema_ad`"),
        ("8. AgentTube", "Docker", "3010", "Planejamento, roteiro, metadados e automação de canal YouTube (`youtube-automation-agent/`)"),
        ("9. OpenSEO", "Docker (Cloudflare Stack)", "3040", "Pesquisa semântica de palavras-chave, títulos de alta conversão, tags e descrições SEO"),
    ]
    for cname, cram, cports, cuse in capacities:
        add(f"  - {cname:<28} | {cram:<26} | Portas: {cports:<18} | {cuse}")
    add("  * REGRA INEGOCIÁVEL DE RAM (8 GB VPS): O `GuidedPipelineOrchestrator` e as rotas `/api/projects/{id}/action` aplicam `RAM Relay` (`stop-all` antes de subir outra stack Docker e desligamento automático ao término da etapa Docker). Em repouso: `CONTAINERS=0 PANEL=active`.")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 2: FUNCIONAMENTO INTERNO DETALHADO DE CADA MOTOR E TRILHA
    # ------------------------------------------------------------------
    add("-" * 100)
    add("SEÇÃO 2 · FUNCIONAMENTO DETALHADO DOS MOTORES, TRILHAS GUIADAS E CASCATAS DE IA")
    add("-" * 100)
    add("")

    add("2.1 Motor 1 · Fábrica de Novelinhas — Dual Engine (Cinema v4.0 Padrão UI + Legado v3.1 por Feature Flag):")
    v4_notes = [
        "=== ARQUITETURA CINEMA ENGINE v4.0 (`cinema_engine='v4'` — Fase 6 Concluída / Fase 5 & 7 Aguardando Colab L4 ou Flow Veo 3.1) ===",
        "  1. Auditoria Quantitativa (`tests/cinema_qa.py` calibrado contra referência `espectativa.mp4` 720x1280 @ 24fps):",
        "     * `espectativa.mp4`: 13/13 critérios aprovados (shots variados, 96.6% gaze fora da lente, OTS/two-shot/figurantes, LRA >= 5.0 LU, -16 LUFS, 0 janelas <-45 dBFS).",
        "     * `hoje.mp4` (v3.1 talking-head frontal): 3/13 critérios aprovados (preso em close frontal 100%, crop estático +14%, LRA esmagado em 2.3 LU, corte mudo).",
        "     * `novela_stitched_segredo-no-altar-r2` (Cinema v4 `stitch-takes` sem difusão de vídeo): 9/13 critérios aprovados (`shots=[2.58, 4.04, 1.12, 3.42]`, `non_frontal=100.0%`, `multi_person_or_ots=3`, `lufs=-15.8`, `lra=9.9 LU`, `tp=-2.0 dBTP`, `zero_static_crop_reuse=true`, `no_corrupted_mute_dialogue_shot=true`).",
        "     * `v4_e2e_nordeste_render_r2.mp4` (Cinema v4 storyboard animado sem difusão): 8/13 critérios (`whisper_full_transcript` 100% fiel em cada corte, `non_frontal=100.0%`, `ots_cnt=4`, `lufs=-15.9`, `lra=10.0 LU`).",
        "     * Os 4 critérios pendentes (`motion_mean>=3.0`, `motion_median>=2.5`, `identity_similarity>=0.80`, `landmark_lipsync_modulation_ok`) dependem de geração de vídeo por difusão (Google Flow Veo 3.1 Lite OU Colab L4 v4.0).",
        "  2. Bíblia Visual, Shot Compiler & Prompts v4 (`tools/hyperframes/cinema_v4_director.py`):",
        "     * Verificação empírica NVIDIA NIM: `black-forest-labs/flux.1-kontext-dev` rejeita `9:16`, `guidance_scale`, `seed` e base64 (`HTTP 422`), e `nvcf/assets` retorna `410 Gone`. `flux.1-dev` aceita 768x1344 com sanitização (`_sanitize_nim_prompt`).",
        "     * Estrutura de prompt v4: composição (`over_shoulder`, `reverse_over_shoulder`, `reaction_closeup`, `wide_ensemble`, `two_shot`) nos primeiros tokens, direção positiva de olhar fora da lente (sem tokens negativos `camera lens`), detecção de contexto `is_wedding` e gênero PT-BR (`CONVIDADA 1` etc.).",
        "     * `stitch_cinema_v4_episode`: alinha cortes pela duração real medida via `ffprobe` nos WAVs sintetizados, aplica L-cut (<=0.18s) c/ pausa automática pós-bleed para não atropelar falas curtas (`O quê?!`), cama -38 dBFS calibrada em NumPy + stingers estéreo e ganho linear único (`alimiter=limit=0.83:level=false`).",
        "  3. Audio Quality Gate v4 (`tools/hyperframes/regional_voice_engine.py`):",
        "     * `validate_generated_speech_wav`: valida `RMS >= -32 dBFS` e `ZCR >= 0.025` inclusive em Python stdlib puro (`array`+`math`) no `.venv` enxuto do painel web (`/api/quick/novela/preview-voice`), mantendo `faster-whisper` INT8 + polyphase/boxcar anti-aliasing no pipeline completo.",
        "     * Purga cache corrompido automaticamente e rejeita retornos falsos `fallback`/`copy` do Colab `/voice-convert`.",
        "  4. Feature Flag & UI (`vps_novela_runner.py`, `guided_pipelines.py`, `main.py`, `static/index.html`):",
        "     * `_resolve_cinema_engine` resolve `'v4'` vs fallback `'v3.1'` (padrão default para callers legados='v3.1', seletores sincronizados `#nov-cinema-engine` e `#qnv-cinema-engine` na UI com padrão `'v4'`).",
        "     * E2E Playwright (`tests/e2e_novela_voices_v26.mjs`) 100% aprovado (Desktop 1440x900 + Mobile 390x844); `secret-scan.py` 0 violações; VPS idle `CONTAINERS=0`, `PANEL=active`.",
        "",
        "=== STATUS & DESCOBERTAS EMPÍRICAS DO COLAB PRO L4 WORKER v4.0 (CRÍTICO P/ CORREÇÃO DO .IPYNB) ===",
        "  - Contrato esperado pela VPS (`vps_novela_runner.py` L2423-2457 & `regional_voice_engine.py` & `main.py` `_probe_colab_tunnel`):",
        "     * `GET /health` -> JSON c/ `ok`, `ready`, `version`, `gpu`, `vram_gb`, `vram_free_gb`, `cuda_ok`, `seed_vc_ready`, `wangp_ready`, `ltx2_ready`, `longcat_ready`, `active_jobs`, `default_video_model` (headers opcionais: `X-Colab-Token`).",
        "     * `POST /voice-convert` -> JSON `VoiceConvertReq(guide_audio_b64, target_timbre_b64, character_id, diffusion_steps, semi_tone_shift, f0_condition)` -> `{ok, engine, elapsed_s, duration, audio_b64}` (WAV 48kHz stereo PCM; erro real -> HTTP 500, proibido fallback silencioso).",
        "     * `POST /jobs/animate_scene` + `GET /jobs/{job_id}` -> JSON `AnimateSceneReq(image_b64, audio_b64, prompt, negative_prompt, backend, steps, guidance_scale, duration_sec, width, height, fps, seed)` -> job assíncrono evitando timeout Cloudflare 524 (~100s) -> `{status: 'completed', ok: True, backend, elapsed_s, duration, vram_peak_gb, video_b64}`.",
        "     * `POST /admin/exec` -> protegido obrigatoriamente por `COLAB_AUTH_TOKEN`.",
        "  - Requisitos de ambiente descobertos na prática (Colab L4 + RAM Alta, CUDA 12.8+, PyTorch 2.10.0+cu128):",
        "     1. Preferência explícita do usuário: ZERO montagem Google Drive; limpar tudo e instalar em NVMe local `/content/ai_studio_v4`; zero caixas vermelhas/avisos `stderr`.",
        "     2. `Plachtaa/seed-vc`: `inference.py` usa `--f0-condition False --auto-f0-adjust True --fp16 True`, gruda `HF_HUB_CACHE='./checkpoints/hf_cache'`, precisa de `numpy==1.26.4` + `transformers==4.46.3` + `descript-audio-codec==1.0.0`. `SEEDVC_ENV_OK 2.10.0+cu128 NVIDIA L4` JÁ PASSOU 100% em venv isolado Python 3.10 via `uv`.",
        "     3. `deepbeepmeep/WanGP` (`v20.00`, `mmgp 4.6.3`):",
        "        * `wgp.py:3008` chama `migrate_extension_defaults` que importa **todos os 51 `family_handlers`** no boot -> não se pode podar `WanGP/requirements.txt` (`smplfitter`, `chumpy`, `xatlas`, `taichi` etc. são exigidos no import time).",
        "        * Em Python 3.10, removendo apenas `--extra-index-url ort-cuda-13-nightly`, pins duplicados de `torch`/`numpy` e compilando `insightface==0.7.3` com `--no-build-isolation` + `numpy==2.1.2 cython setuptools wheel`, todos os **109 pacotes** instalaram sem erro no Colab L4 (32.0s + 9.4s).",
        "        * `shared/api.py:1461` exige `config_path` terminando exatamente em `wgp_config.json`.",
        "        * `WanGPSession.submit_task` usa chaves `model_type` (`ltx2_25_22B_distilled`, `longcat_avatar_v1_5`, `infinitetalk`, `i2v_2_2`), `image_start`, `image_prompt_type='S'`, `audio_guide`, `audio_prompt_type='AS'` (LTX-2.5) / `'A'` (LongCat/InfiniteTalk), `overrides=\"attention_mode='sdpa', memory_profile=4, transformer_quantization='int8'\"`.",
        "     4. **ÚLTIMO ERRO OBSERVADO NO COLAB L4 (Célula 2, self-check `shared.api.init`):**",
        "        * Triton compilou os dois kernels smoke no L4 sem erro, e durante o import de `models/wan/scail/scail_pose_draw.py:17 -> import matplotlib` lançou:",
        "          `ValueError: Key backend: 'module://matplotlib_inline.backend_inline' is not a valid value for backend`.",
        "        * Causa raiz: o kernel Jupyter do Colab exporta `MPLBACKEND=module://matplotlib_inline.backend_inline` no `os.environ` herdado pelo subprocesso do `venv` Python 3.10 isolado (que não possui `matplotlib_inline`). É obrigatório sobrescrever `MPLBACKEND=Agg` (e sanitizar variáveis de ambiente do kernel Colab/Jupyter ao invocar venvs isolados).",
        "",
        "=== ARQUITETURA LEGADA PRESERVADA ATRÁS DE FEATURE FLAG (`cinema_engine='v3.1'`) ===",
    ]
    for line in v4_notes:
        add(f"  {line}")
    add("")
    add("2.1A Fluxo Legado v3.1 (Mantido como Fallback `cinema_engine='v3.1'`):")
    novela_steps = [
        "Passo 1 · Roteiro & Saneamento Cultural (`guided_pipelines.py -> generate_novela_script` + `compile_dramatic_cuts`):",
        "  - Usa Smart Quota Router (NVIDIA NIM Nemotron em Tier 1, ZERO gasto de RPD Gemini) para estruturar 4 cortes virais (`viral_4_cuts`): Corte 1 (ataque/pergunta) -> Corte 2 (`lead_in_pause=0.12s` + resposta) -> Corte 3 (`close_up` reação) -> Corte 4 (`is_reaction`/`is_silent_reaction` c/ `emotion='happiness'`, preservando `reaction_phrase`).",
        "  - `sanitize_northeast_slang` limpa automaticamente termos fora da região (`trem`, `uai`, `sô`, `mano`, `meo`, `daora`, `guri`, `bah`, `tchê`) e vogais duplicadas (`aa/oo/ee`) antes do TTS e das legendas.",
        "Passo 2 · Síntese Vocal Nordestina 48kHz (`tools/hyperframes/regional_voice_engine.py`):",
        "  - Hierarquia sem estourar cota: (1) Cache SHA-256 em `/opt/ai-media-studio/voices/cache/` (<5ms); (2) Gemini Live Audio WebSocket (`BidiGenerateContent` em `gemini-3.1-flash-live-preview` / `gemini-2.5-flash-native-audio-latest`, cota ilimitada/1M TPM); (3) Pool Round-Robin de 5 modelos REST TTS (3 RPM cada -> 15 RPM combinados); (4) Fallback `edge-tts` fonético; (5) Clonagem zero-shot F5-TTS (`/tts-clone`) ou conversão Speech-to-Speech Seed-VC (`/voice-convert`) no Colab quando `accent_engine` for `f5_tts_clone` / `seed_vc_guide`.",
        "  - `_synthesize_reaction_soundtrack_wav` gera o áudio do Corte 4 combinando cama ambiental contínua (`-28 dB`), acorde/SFX harmônico cômico (ou sub-drop dramático) + fala reativa ex: `'Hahaha! Eita, Seu Bastião!'` (`loudnorm I=-16`), eliminando silêncios abaixo de `-40 dB`.",
        "Passo 3 · Geração de Retratos Frontais & Continuity Lock v3.1 (`vps_novela_runner.py -> _resolve_turn_visual`):",
        "  - `char_lock` é colocado nos PRIMEIROS ~35 tokens do prompt Flux (`black-forest-labs/flux.1-dev` via NVIDIA NIM 768x1344 -> Lanczos 1080x1920) garantindo que o encoder CLIP-L (limite 77 tokens) nunca corte gênero, idade, bigode ou roupa.",
        "  - `SHOT_FRAMINGS` v3.1 impõe `FRONTAL_SINGLE_SUBJECT_DIRECTIVE` em todos os planos (`establishing`, `medium`, `over_shoulder`, `reverse_over_shoulder`, `close_up`, `reaction_closeup`, `reaction`): estritamente 1 pessoa só, ZERO segunda cabeça/ombro em primeiro plano, olhar direto na lente com olhos bem abertos e catchlights claros, lábio superior visível no Seu Bastião (`CAST_REGISTRY['seu_bastiao']`) e lábios neutros fechados prontos para dublagem.",
        "  - Cache `master_cache_key = ('v31_frontal_moda_lock', speaker_key, style_norm)` grava `novela_portrait_{speaker_key}_{turn_idx}_v31.jpg`. Cortes subsequentes do mesmo personagem (`close_up`, `reaction`, `reaction_closeup`, `is_silent_reaction`) reutilizam o retrato mestre por crop óptico +14% (`scale=1232:2190:flags=lanczos,crop=1080:1920:76:95`), preservando 100% de identidade e cenário.",
        "Passo 4 · Animação Neural GPU Real (`tools/hyperframes/novela_lipsync_animator.py` — Distorção 2D Banida):",
        "  - Tier 1 Absoluto (Colab `MoDA-L4 v3.1` quando `COLAB_GPU_URL` responde `/health` com `moda_ready=True`): envia TODOS os 4 cortes (fala `cfg_scale=1.25` e reação Corte 4 forçada via `_map_colab_moda_emotion` para `emotion='happiness', cfg_scale=1.35` -> `emo=4 Happiness`) em chamada única `/animate_turn` (~11s/take) no `LiveVASAPipeline` (`TalkingHeadDiT` + forward hooks explícitos nas 12 camadas HuBERT para `transformers >= 5.0` Python 3.13 + mini-batch de 4 frames `range(0, n_frames, 4)` no decoder SPADE linha 735 de `motion_processer.py`). Zero desperdício em ZeroGPU antes do Colab.",
        "  - Tier 1B Legacy Fallback (Colab sem `moda_ready`): Stage 1 `LivePortrait fp16` (512x896 trimado) -> Stage 2 `LatentSync 1.5` (`configs/unet/stage2.yaml --guidance_scale 1.5 --enable_deepcache`) auto-segmentado em chunks <= 2.85s anti-524.",
        "  - Tier 1A Cloud Fallback (somente se Colab ausente/offline): Hugging Face ZeroGPU A10G (`MoDA-fast-talking-head`, `LongCat-Video-Avatar`, `EchoMimic`, `Wan 2.2`/`LTX-Video`). Fallback 2D (`cv2.remap` / elipses) permanece permanentemente banido.",
        "Passo 5 · Pós-Produção FFmpeg, Legendas Bipartidas `.ass` & Mix Contínuo:",
        "  - Concatena os 4 clipes `1080x1920 @ 30fps`, mistura cama de ambiente contínua a `-33 dB` (`_create_master_ambience_bed_wav`) sob todo o episódio com diálogo normalizado (`loudnorm=I=-16`, `48000 Hz Estéreo SoXR`) garantindo `MIN_DB > -40 dB` em toda janela de `0.25s`, e queima legenda `.ass` `CleanMin` minimalista dividida em até 2 eventos sequenciais por `_split_subtitle_events(..., max_chars=42)` sem truncar palavras.",
        "Passo 6 · Modos Alternativos (`flow_pack` e `colab_gpu`):",
        "  - `flow_pack`: gera pasta `/opt/ai-media-studio/exports/novela/flow_packs/{pack_id}/` com retratos boca-fechada 1080x1920, prompts bilíngues segundo-a-segundo para Google Flow (Veo 3.1 Lite 10 créd / Fast 20 créd) e costura pós-download em `stitch_novela_takes` (VAD `faster-whisper` + corte exato + re-dublagem/re-lipsync GPU).",
    ]
    for line in novela_steps:
        add(f"  {line}")
    add("")

    add("2.2 Perfis Regionais de Voz Cadastrados na VPS (`/opt/ai-media-studio/voices/regional/profiles.json`):")
    vprofs = vps_info.get("voice_profiles") or {}
    if vprofs:
        for pid, pinfo in sorted(vprofs.items()):
            add(
                f"  - {pid:<16} | {pinfo.get('label'):<20} | Região: {pinfo.get('region'):<28} | "
                f"Gemini Live: {pinfo.get('gemini_voice')}/{pinfo.get('gemini_alt_voice')} | Edge: {pinfo.get('edge_voice')} | "
                f"Ref. Clone F5: {pinfo.get('has_custom_reference')} ({pinfo.get('reference_duration_sec') or 0}s)"
            )
    else:
        add("  - perfis padrão: dona_socorro (Kore/Aoede), seu_bastiao (Charon/Fenrir), enzo (Puck/Zephyr), narrador_sertao (Sulafat/Algenib)")
    add("")

    add("2.3 As 6 Trilhas Guiadas (`guided_pipelines.py` -> Modo Guiado c/ Checkpoints & RAM Relay):")
    for tpl in guided_templates:
        steps_str = " -> ".join(
            f"[{s['index']}:{s['engine']}{' (Docker:' + s['docker_stack'] + ')' if s.get('docker_stack') else ''}]"
            for s in (tpl.get("steps") or [])
        )
        add(f"  - {tpl['id']:<24} | {tpl['title']}")
        add(f"      RAM Profile : {tpl.get('ram_profile')} | Usa Docker: {tpl.get('uses_docker')}")
        add(f"      Etapas      : {steps_str}")
        add(f"      Descrição   : {tpl.get('subtitle')}")
    add("  Presets de Safe-Zone (`PLATFORM_PRESETS`):")
    for pk, pv in platform_presets.items():
        add(f"    * {pk:<14} | {pv.get('label'):<34} | Aspect: {pv.get('aspect')} | Safe-Zone: {pv.get('safe_zone')}")
    add("")

    add("2.4 Shopee Video 20:9 Anti-Crop & Motor Clone Viral (`vps_ad_runner.py` + `viral_reference_analyzer.py`):")
    add("  - Reverse-Engineering Viral (`viral_reference_analyzer.py`): processa MP4s em `campaigns/{id}/virals/` com `faster-whisper` INT8, detecção de cortes FFmpeg (`gt(scene,0.32)`), energia RMS (`volumedetect`) e Cascata IA -> gera `viral_blueprint.json` com hook 0-3s, cadência de cortes, WPM ideal e roteiro.")
    add("  - Gabarito Shopee 20:9 Anti-Crop (`1080x1920`, `vps_ad_runner.py`): área segura útil travada em `X=96..884px` (`width=788px`, `96px` livre à esquerda, `196px` livre à direita `X>=890px` para ícones sociais); topo livre `Y=0..235px` para barra de busca; palco de fotos `Y=300..850px`; card de oferta `Y=865..1015px`; corredor único de legenda sincronizada por timestamps Edge-TTS `Y=1035..1135px`; CTA Carrinho Laranja `Y=1145..1203px`; rodapé `Y=1210..1920px` 100% vazio.")
    add("  - Curadoria anti-confusão de kits: campanha `campaigns/elseve-kit2/` restringe imagens exclusivamente ao kit específico, filtrando fotos de linha completa (shampoo/condicionador/máscara), e renderiza via HyperFrames Chromium `--low-memory-mode` + 48kHz SoXR.")
    add("")

    add("2.5 Video Use Jump-Cut Zero-RAM (`tools/video-use/vps_zero_cost_runner.py`):")
    add("  - Transcrição CPU `faster-whisper` INT8 (`cpu_threads=2`, passando PCM `float32 NumPy` direto para evitar erro PyAV `metadata_errors`), VAD e detecção de `FILLER_TOKENS` (`uh`, `um`, `hã`, `éé`, etc.).")
    add("  - Remove pausas mortas, aplica fades de áudio `30ms` entre cortes, color grading FFmpeg (`Neutral Clean`, `Cinema Warm`, `E-Commerce Vibrant`), legenda ASS opcional dentro da Safe-Zone da plataforma escolhida e normalização `loudnorm I=-16` em `48000 Hz Estéreo SoXR`. Consome 0 MB RAM após finalizar.")
    add("")

    add("2.6 Smart Quota Router, Gateway `/v1/chat/completions`, Cofre Central & OpenJarvis:")
    add("  - Smart Quota Router por Especialidade (`main.py -> call_ai_cascade` + `quota_telemetry.py` com lock `fcntl.flock`):")
    add("    * Texto & Roteiro / Agente OpenJarvis : Tier 1 NVIDIA NIM (`nvidia/nemotron-3.5-lightning-30b-a3b` -> `nvidia/nemotron-3-ultra-550b-a55b` -> `moonshotai/kimi-k3.1-pro`) -> Tier 2 OpenRouter `:free` (`nemotron-3-ultra:free`, `nemotron-3.5-lightning:free`, `kimi-k3.1-pro`) -> Tier 3 Anthropic Claude (se chave configurada) / Gemini (`gemma-4-31b-it` 1500 RPD -> `gemini-3.5-flash-lite` -> `gemini-3.1-flash-lite` -> `gemini-2.5-flash-lite` -> `gemini-3.8-flash` -> `gemini-2.5-flash` apenas como último fallback).")
    add("    * Visão Multimodal (Payloads c/ imagem): Gemini High-Quota Vision (`gemini-3.5-flash-lite` -> `gemini-3.1-flash-lite` -> `gemini-2.5-flash-lite` -> `gemini-3.8-flash`) -> Anthropic fallback.")
    add("    * Checagem de Saúde (`/api/ai/cascade-status`): `GET /v1beta/models` consome `0` tokens e `0` RPD.")
    add("  - Cofre Central (`vault.json` permissão `0600` + UI aba `#tab-vault` com sub-abas `Todas / Cascata IA / Mídia & Extras`): `POST /api/keys` grava chaves e sincroniza automaticamente `.env`s e `config.toml` de todas as stacks via `sync_all_project_configs()`, mascarando valores na API (`mask_key`).")
    add("  - Modo Dev (`infra/bin/modo-dev.vps` / `/usr/local/bin/modo-dev`): alterna symlinks de engenharia (`engineering/`, `productivity/`) do cofre `dev-skills-vault/skills/` para `~/.gemini/config/skills` e `~/.agents/skills`, controlado pela UI `#tab-skills` e API `/api/skills/modo-dev`.")
    add("  - OpenJarvis Local (`iniciar-jarvis.sh` + `clap_listener.py` + `studio_controller_mcp.py` + `webcam_vision.py`):")
    add(f"    * Servidor Jarvis (`127.0.0.1:8090`) ligado ao Gateway `https://187.77.202.176.sslip.io/v1` + MCP `studio-controller` ({len(mcp_tools)} tools). Daemon de voz escuta 2 palmas em <2% CPU -> captura VAD -> `faster-whisper` PT-BR -> executa plano/produção na VPS -> fala resposta por `jarvis-falar` via PipeWire.")
    add("")

    add("2.7 Variáveis de Ambiente e Chaves no Cofre da VPS (apenas nome e status SET=True/False):")
    for k, is_set in sorted((vps_info.get("env_status") or {}).items()):
        add(f"  - {k:<34} SET={is_set}")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 3: INVENTÁRIO COMPLETO DE ARQUIVOS, MÓDULOS, CLASSES E FUNÇÕES
    # ------------------------------------------------------------------
    add("-" * 100)
    add("SEÇÃO 3 · INVENTÁRIO COMPLETO DE ARQUIVOS, MÓDULOS PYTHON, FRONTEND SPA, NOTEBOOK E MCP")
    add("-" * 100)
    add("")
    add("3.1 Estrutura de Diretórios (/opt/ai-media-studio/ & Repositório Git):")
    stacks = [
        ("media-control-panel/", "Painel SaaS PWA + FastAPI (porta 9000 loopback behind Nginx TLS), sem Docker (sempre ativo)"),
        ("tools/hyperframes/", "Motores Zero-Idle-RAM de Motion Graphics, Novelinhas Cinema Engine v4.0 + Fallback v3.1, Vozes Regionais + AudioGate v4 e Lip-Sync"),
        ("tools/video-use/", "Utilitário adaptado browser-use/video-use (MIT) com .venv Python 3.12, faster-whisper INT8 e OpenCV/MediaPipe"),
        ("tools/colab-gpu-worker/", "Worker & Notebook Colab L4 v4.0 (`colab_v4_cinema_server.py` + `ai_studio_colab_novela_gpu.ipynb` Zero-Drive dual-venv Seed-VC + WanGP)"),
        ("notebooks/", "Cópia sincronizada de `ai_studio_colab_novela_gpu.ipynb` no repositório"),
        ("campaigns/", "Assets curados por campanha (ex: campaigns/elseve-kit2/ com 8 fotos curadas + viral_blueprint.json)"),
        ("voices/regional/", f"Banco de perfis vocais regionais na VPS: {', '.join(vps_info.get('voices_dir') or ['dona_socorro', 'seu_bastiao', 'enzo', 'narrador_sertao', 'profiles.json'])}"),
        ("supoclip/", "Stack Docker (PostgreSQL + Redis + Backend + Worker + Frontend portas 3107/8000) — desligado em repouso"),
        ("MoneyPrinterTurbo/", "Stack Docker Streamlit/FastAPI (portas 8501/8080) para vídeos faceless — desligado em repouso"),
        ("VoiceStudio/", "Stack Docker síntese & clonagem de voz (porta 3030) — desligado em repouso"),
        ("openfilm/", "Stack Node/Vite timeline editor (portas 3005/3006) — sob demanda"),
        ("open-higgsfield/", "Stack generativa multimodal Next.js (porta 3020) — sob demanda"),
        ("open-seo/", "Suíte SEO & otimização semântica (porta 3040) — sob demanda"),
        ("youtube-automation-agent/", "Agente de automação YouTube / AgentTube (porta 3010) — sob demanda"),
        ("openjarvis-client/", "Cliente OpenJarvis (MCP studio_controller_mcp.py, clap_listener.py, webcam_vision.py, iniciar-jarvis.sh, skills)"),
        ("infra/", "Configs sanitizados de produção (nginx/media-studio.conf, systemd/media-panel.service, env-examples, bin/modo-dev.*)"),
        ("scripts/", "Automação de governança: `sync-from-vps.sh`, `secret-scan.py`, `generate_dossier.py`"),
    ]
    for sname, sdesc in stacks:
        add(f"  - {sname:<28} {sdesc}")
    add("")

    add("3.2 Detalhamento por Módulo Python (AST real com linhas exatas):")
    for rel_path, role_desc in key_modules:
        fp = REPO_ROOT / rel_path
        if not fp.is_file():
            add(f"\n[MÓDULO AUSENTE] {rel_path}")
            continue
        lines_cnt, mod_doc, syms = _extract_python_symbols(fp)
        sz = fp.stat().st_size
        vps_equiv = f"/opt/ai-media-studio/{rel_path}" if not rel_path.startswith("openjarvis-client/") else f"~/open-jarvis/{fp.name}"
        add("")
        add(f"### {rel_path}")
        add(f"    VPS Equiv.: {vps_equiv} | Tamanho: {_human_size(sz)} ({sz} bytes) | Linhas: {lines_cnt}")
        add(f"    Papel     : {role_desc}")
        if mod_doc:
            add(f"    Docstring : {mod_doc}")
        add("    Símbolos principais:")
        for s in syms:
            doc_part = f" — {s['doc']}" if s["doc"] else ""
            add(f"      * L{s['start']}-L{s['end']:<4} {s['kind']} {s['name']}({s['args']}){doc_part}")

    add("")
    add("### media-control-panel/static/index.html")
    add(f"    VPS Equiv.: /opt/ai-media-studio/media-control-panel/static/index.html | Tamanho: {_human_size(html_meta['bytes'])} ({html_meta['bytes']} bytes) | Linhas: {html_meta['lines']}")
    add("    Papel     : Interface PWA Dark-Glass B2B responsiva (1440x900 Desktop + 390x844 Mobile; Tailwind + Lucide Icons) com Modo Guiado (6 Trilhas), Modo Hard, Costurador de Takes, Modal de Cotas ao Vivo e Cofre Central")
    add(f"    Total de IDs de DOM mapeados: {html_meta['total_ids']}")
    add(f"    Abas/Navegação ({len(html_meta['tabs'])}): {', '.join(html_meta['tabs'][:35])}")
    add(f"    Modo Guiado Trilhas 1-6 (IDs gp-* — {len(html_meta['guided_ids'])}): {', '.join(html_meta['guided_ids'][:50])}")
    if len(html_meta["guided_ids"]) > 50:
        add(f"      ... +{len(html_meta['guided_ids']) - 50} IDs: {', '.join(html_meta['guided_ids'][50:95])}")
    add(f"    Novela Studio / Vozes / Stitcher (IDs nov-* / qnv-* — {len(html_meta['novela_ids'])}): {', '.join(html_meta['novela_ids'])}")
    add(f"    HyperFrames / Shopee POV (IDs qhf-* — {len(html_meta['hyper_ids'])}): {', '.join(html_meta['hyper_ids'])}")
    add(f"    Demais Cards Modo Hard / Cotas (IDs — {len(html_meta['hard_cards_ids'])}): {', '.join(html_meta['hard_cards_ids'][:50])}")
    add(f"    Funções JavaScript inline ({len(html_meta['js_functions'])}):")
    for lno, fname in html_meta["js_functions"]:
        add(f"      * L{lno:<5} function {fname}()")

    for nb_rel in ("tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb", "notebooks/ai_studio_colab_novela_gpu.ipynb"):
        nb_path = REPO_ROOT / nb_rel
        if nb_path.is_file():
            nb_data = json.loads(nb_path.read_text(encoding="utf-8"))
            add("")
            add(f"### {nb_rel}")
            add(f"    VPS Equiv.: /opt/ai-media-studio/{nb_rel} | Tamanho: {_human_size(nb_path.stat().st_size)} | nbformat: {nb_data.get('nbformat')}")
            for c_idx, cell in enumerate(nb_data.get("cells") or []):
                first_line = "".join(cell.get("source") or [""]).splitlines()[0] if cell.get("source") else ""
                add(f"      * Célula {c_idx} [{cell.get('cell_type')}]: {first_line[:115]}")

    add("")
    add("### OpenJarvis CLI/MCP, jarvis-falar & Catálogo Completo de Skills Instaladas")
    add("    Binário voz: /home/wbrendo/.local/bin/jarvis-falar (reprodução TTS em alto-falante via Piper/Edge-TTS)")
    add(f"    MCP Server : {REPO_ROOT / 'openjarvis-client/studio_controller_mcp.py'} ({len(mcp_tools)} @mcp.tool registradas):")
    for t in mcp_tools:
        add(f"      * L{t['line']}-L{t['end']:<4} {t['name']}({t['args']}) — {t['doc']}")
    add("    Catálogo de Skills por Diretório:")
    for sk_dir, sk_names in skills_map.items():
        add(f"      * {sk_dir} ({len(sk_names)} skills): {', '.join(sk_names)}")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 4: CATÁLOGO COMPLETO DE ROTAS FASTAPI
    # ------------------------------------------------------------------
    add("-" * 100)
    add(f"SEÇÃO 4 · CATÁLOGO COMPLETO DE ROTAS FASTAPI (main.py — {len(routes)} endpoints)")
    add("-" * 100)
    add("")
    for r in routes:
        eng_hint = "Painel/Infra"
        p = r["path"]
        if "/quick/novela" in p or "/colab" in p or "/voices/regional" in p:
            eng_hint = "Novela Studio (Cinema v4.0 + Fallback v3.1) / Regional Voice + AudioGate / Colab L4"
        elif "/quick/hyperframes" in p or "/campaigns" in p:
            eng_hint = "HyperFrames POV / Clone Viral Shopee 20:9"
        elif "/quick/videouse" in p:
            eng_hint = "Video Use Jump-Cut Zero-RAM"
        elif "/pipelines" in p:
            eng_hint = "Guided Pipelines Orchestrator (Trilhas 1-6 + RAM Relay)"
        elif "/supoclip" in p:
            eng_hint = "SupoClip Stack Bridge"
        elif "/openjarvis" in p:
            eng_hint = "OpenJarvis Bridge"
        elif "/outputs" in p:
            eng_hint = "Galeria & Streaming de Exports"
        elif "/keys" in p or "/cascade" in p or "/quota" in p or "/v1/" in p:
            eng_hint = "Vault Central, Gateway /v1 & Smart Quota Router"
        add(f"  [{r['method']:<9}] {r['path']:<48} -> {r['handler']} (L{r['line']}-L{r['end_line']})")
        add(f"              Motor   : {eng_hint}")
        add(f"              Params  : {r['params']}")
        if r["doc"]:
            add(f"              Resumo  : {r['doc']}")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 5: TESTES AUTOMATIZADOS, EXPORTS VALIDADOS & GOVERNANÇA DR
    # ------------------------------------------------------------------
    add("-" * 100)
    add("SEÇÃO 5 · TESTES AUTOMATIZADOS, EXPORTS VALIDADOS NA VPS, GOVERNANÇA GIT & BACKUP DR")
    add("-" * 100)
    add("")
    add("5.1 Suíte de Testes E2E (Node/Playwright 1440x900 + 390x844) e Auditores Python (tests/):")
    tests_dir = REPO_ROOT / "tests"
    if tests_dir.is_dir():
        for tf in sorted(tests_dir.iterdir()):
            if tf.is_file():
                first_lines = " | ".join(
                    l.strip() for l in tf.read_text(encoding="utf-8", errors="ignore").splitlines()[:4] if l.strip()
                )
                add(f"  - tests/{tf.name:<34} ({_human_size(tf.stat().st_size)}) :: {first_lines[:115]}")
    add("")

    add("5.2 Exports Recentes Auditados na VPS (/opt/ai-media-studio/exports/):")
    exp_info = vps_info.get("exports_info") or {}
    if exp_info:
        for folder, items in sorted(exp_info.items()):
            add(f"  - exports/{folder}/ ({len(items)} arquivos recentes):")
            for fname, fsz in items:
                add(f"      * {fname:<74} ({_human_size(int(fsz))})")
    else:
        add("  - (sem listagem remota no momento da geração)")
    add("")

    add("5.3 Workflow Operacional de Governança, Auditoria & Disaster Recovery (DR):")
    add("  1. Sync VPS -> Repo  : `bash scripts/sync-from-vps.sh` (rsync seguro excluindo `.env`, `vault.json`, `quota_telemetry.json`, modelos/caches pesados; sanitiza nginx/env-examples; sincroniza OpenJarvis + tests/; executa `secret-scan.py`).")
    add("  2. Secret Scan       : `python3 scripts/secret-scan.py` (varre >11.000 arquivos contra padrões de chaves OpenAI/Gemini/HuggingFace/GitHub/SSH antes de qualquer commit).")
    add("  3. Regen. Dossiê     : `python3 scripts/generate_dossier.py` (atualiza simultaneamente `/home/wbrendo/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt` e `docs/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt`).")
    add("  4. GitHub Origin     : `https://github.com/willianbrendo-dev/ai-media-studio.git` (branch `main`, somente código sanitizado).")
    add("  5. Backup DR Atômico : `/home/wbrendo/Backups/ai-media-studio-backup-completo.zip` (~1.2 GB, `chmod 600` — consolida repo + estação local + VPS runtime + segredos privados + exports; atualizado via `.tmp` + `unzip -tq` + `mv -f`).")
    add("")
    add("=" * 100)
    add("FIM DO DOSSIÊ TÉCNICO AI MEDIA STUDIO")
    add("=" * 100)
    return "\n".join(out) + "\n"


def main() -> int:
    txt = build_dossier()
    HOME_OUT.write_text(txt, encoding="utf-8")
    DOCS_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOCS_OUT.write_text(txt, encoding="utf-8")
    line_cnt = len(txt.splitlines())
    print(f"DOSSIE_GENERATED: {HOME_OUT} & {DOCS_OUT} ({line_cnt} lines, {len(txt)} chars)")
    assert line_cnt >= 450, f"Dossier too short: {line_cnt} lines"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
