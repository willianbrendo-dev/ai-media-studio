#!/usr/bin/env python3
"""Introspect the live AI Media Studio codebase and generate the complete engineering dossier.

Outputs:
  - /home/wbrendo/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt
  - <repo_root>/docs/DOSSIE_TECNICO_AI_MEDIA_STUDIO.txt
Never prints raw API keys or credentials (records only key names and SET=True/False).
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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
                and dec.func.attr in ("get", "post", "put", "delete", "patch", "websocket")
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
    tabs = [i for i in ids if i.startswith(("tab-", "panel-", "view-"))]
    guided = [i for i in ids if i.startswith("gp-")]
    novela = [i for i in ids if i.startswith(("nov-", "qnv-", "btn-nov-", "btn-qnv-"))]
    hyper = [i for i in ids if i.startswith(("qhf-", "btn-qhf-"))]
    other_quick = [i for i in ids if i.startswith(("qvu-", "qvs-", "qsc-", "qmp-", "card-hard-"))]
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
    _, _, syms = _extract_python_symbols(mcp_py)
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


def _fetch_vps_env_status() -> Dict[str, Any]:
    """Query ONLY key presence (SET=True/False) and Docker/runtime summary on VPS without printing secret values."""
    script = """
import json
from pathlib import Path

vault = {}
vp = Path("/opt/ai-media-studio/media-control-panel/vault.json")
if vp.is_file():
    try:
        vault = json.loads(vp.read_text(encoding="utf-8"))
    except Exception:
        vault = {}

env_keys = {}
ep = Path("/opt/ai-media-studio/media-control-panel/.env")
if ep.is_file():
    for l in ep.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = l.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            env_keys[k.strip()] = bool(v.strip().strip("\\"'"))

all_keys = sorted(set(vault.keys()) | set(env_keys.keys()))
status = {k: bool(str(vault.get(k) or "").strip() or env_keys.get(k)) for k in all_keys}
voices = sorted(p.name for p in Path("/opt/ai-media-studio/voices/regional").iterdir()) if Path("/opt/ai-media-studio/voices/regional").is_dir() else []
print(json.dumps({"env_status": status, "voices_dir": voices}))
"""
    try:
        out = subprocess.check_output(
            ["ssh", "-o", "BatchMode=yes", VPS_HOST, "/opt/ai-media-studio/tools/video-use/.venv/bin/python", "-"],
            input=script,
            text=True,
            timeout=15,
        )
        return json.loads(out.strip().splitlines()[-1])
    except Exception as exc:
        return {"env_status": {"SSH_CHECK_NOTE": f"unavailable ({exc})"}, "voices_dir": []}


def _list_skills() -> Dict[str, List[str]]:
    res: Dict[str, List[str]] = {}
    roots = {
        "Global Antigravity (~/.gemini/config/skills)": Path("/home/wbrendo/.gemini/config/skills"),
        "Shared Agents (~/.agents/skills)": Path("/home/wbrendo/.agents/skills"),
        "OpenJarvis (~/.openjarvis/skills)": Path("/home/wbrendo/.openjarvis/skills"),
        "Dev Skills Vault (dev-skills-vault/skills)": REPO_ROOT / "dev-skills-vault/skills",
    }
    for label, p in roots.items():
        if p.is_dir():
            names = sorted(d.name for d in p.iterdir() if d.is_dir())
            res[label] = names
    return res


def build_dossier() -> str:
    now_str = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    vps_info = _fetch_vps_env_status()
    routes = _extract_fastapi_routes(REPO_ROOT / "media-control-panel/main.py")
    html_meta = _extract_index_html_summary(REPO_ROOT / "media-control-panel/static/index.html")
    mcp_tools = _extract_mcp_tools(REPO_ROOT / "openjarvis-client/studio_controller_mcp.py")
    skills_map = _list_skills()

    key_modules = [
        ("media-control-panel/main.py", "Servidor FastAPI central (porta 9000 VPS), Vault, orquestração Docker RAM-safe e rotas de estúdio"),
        ("media-control-panel/guided_pipelines.py", "Orquestrador assíncrono das 6 Trilhas Guiadas (RAM Relay, Checkpoints JSON, Safe-Zones, Roteirista IA)"),
        ("tools/hyperframes/vps_novela_runner.py", "Motor Clean Cinema Multi-Personagem, Dual-Style Live-Action/3D, 4 Cortes Virais, Flow Veo 3.1 Pack e Take Stitcher"),
        ("tools/hyperframes/regional_voice_engine.py", "Motor de Voz Nordestina: Gemini Multi-Modelo/Multi-Speaker (3.1/2.5/3.8), anti-429 backoff, cadência 1.16x SoXR 48kHz, F5-TTS & Seed-VC"),
        ("tools/hyperframes/novela_lipsync_animator.py", "Cascata Lip-Sync Real (Tier 1 Colab GPU LivePortrait/LatentSync/MuseTalk; Tier 2 Local CPU MediaPipe 478pts + Visemas 2.5D cv2.remap)"),
        ("tools/hyperframes/verify_lipsync_motion.py", "Auditor Computer Vision frame a frame (estabilização por âncora nasal, mouth ROI speech vs silence, piscar sem oclusão e Reaction Shot)"),
        ("tools/hyperframes/vps_ad_runner.py", "Motor HyperFrames POV Cinema Multi-Imagem Shopee 20:9 Anti-Crop & L'Oréal Gold Luxury"),
        ("tools/hyperframes/viral_reference_analyzer.py", "Extrator de DNA Viral (cut_cadence, WPM, energia de áudio e recomendações roteirizadas)"),
        ("tools/video-use/vps_zero_cost_runner.py", "Pipeline Zero-RAM Video Use Jump-Cut + faster-whisper INT8 + Correção de Cor FFmpeg + Legendas ASS"),
        ("openjarvis-client/studio_controller_mcp.py", "Servidor MCP stdio (OpenJarvis Operador Master) conectado às APIs do Painel na VPS e estação local"),
        ("openjarvis-client/clap_listener.py", "Daemon de voz local do OpenJarvis (detecção de palma, captura/transcrição e resposta falada via jarvis-falar)"),
        ("openjarvis-client/webcam_vision.py", "Módulo de visão local por webcam integrado ao OpenJarvis"),
    ]

    out: List[str] = []
    add = out.append

    add("=" * 96)
    add("AI MEDIA STUDIO — DOSSIÊ TÉCNICO E ARQUITETURAL COMPLETO")
    add(f"Gerado por introspecção em: {now_str}")
    add(f"Repositório Local: {REPO_ROOT}")
    add("VPS Produção: root@187.77.202.176:/opt/ai-media-studio/ (https://187.77.202.176.sslip.io)")
    add("Política de Segurança: Zero valores de segredos exibidos; apenas nomes de chaves e status SET=True/False.")
    add("=" * 96)
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 1: INVENTÁRIO COMPLETO DE ARQUIVOS, MÓDULOS E FUNÇÕES
    # ------------------------------------------------------------------
    add("-" * 96)
    add("SEÇÃO 1 · INVENTÁRIO COMPLETO DE ARQUIVOS, MÓDULOS, CLASSES E FUNÇÕES")
    add("-" * 96)
    add("")
    add("1.1 Stacks e Diretórios Principais na VPS (/opt/ai-media-studio/) e no Repositório:")
    stacks = [
        ("media-control-panel/", "Painel SaaS PWA + FastAPI (porta 9000 loopback behind Nginx TLS), sem Docker (sempre ativo)"),
        ("tools/hyperframes/", "Motores Zero-Idle-RAM de Motion Graphics, Novelinhas Multi-Personagem, Vozes Regionais e Lip-Sync CV"),
        ("tools/video-use/", "Utilitário adaptado browser-use/video-use (MIT) com .venv Python 3.12, faster-whisper INT8 e OpenCV/MediaPipe"),
        ("tools/colab-gpu-worker/", "Notebook 1-clique Google Colab T4 16GB (LivePortrait + LatentSync 1.5 + MuseTalk 1.5 + EchoMimicV3 + F5-TTS + Seed-VC)"),
        ("campaigns/", "Assets curados por campanha (ex: campaigns/elseve-kit2/ com 8 fotos curadas + viral_blueprint.json)"),
        ("voices/regional/", f"Banco de perfis vocais regionais na VPS: {', '.join(vps_info.get('voices_dir') or ['dona_socorro', 'seu_bastiao', 'enzo', 'narrador_sertao', 'profiles.json'])}"),
        ("supoclip/", "Stack Docker (PostgreSQL + Redis + Backend + Worker + Frontend portas 3107/8000) — desligado em repouso"),
        ("MoneyPrinterTurbo/", "Stack Docker Streamlit/FastAPI (portas 8501/8080) para vídeos faceless — desligado em repouso"),
        ("VoiceStudio/", "Stack Docker síntese & clonagem de voz — desligado em repouso"),
        ("openfilm/", "Stack Node/Electron web timeline editor (porta 3005) — sob demanda"),
        ("open-higgsfield/", "Stack generativa multimodal — sob demanda"),
        ("open-seo/", "Suíte SEO & automação web — sob demanda"),
        ("youtube-automation-agent/", "Agente de automação YouTube — sob demanda"),
        ("openjarvis-client/", "Cliente OpenJarvis (MCP studio_controller_mcp.py, clap_listener.py, webcam_vision.py, jarvis-falar)"),
    ]
    for sname, sdesc in stacks:
        add(f"  - {sname:<28} {sdesc}")
    add("")

    add("1.2 Detalhamento por Módulo Python (AST real com linhas aproximadas/exatas):")
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

    # Frontend index.html breakdown
    add("")
    add("### media-control-panel/static/index.html")
    add(f"    VPS Equiv.: /opt/ai-media-studio/media-control-panel/static/index.html | Tamanho: {_human_size(html_meta['bytes'])} ({html_meta['bytes']} bytes) | Linhas: {html_meta['lines']}")
    add(f"    Papel     : Interface PWA Dark-Glass B2B (Tailwind + Lucide Icons) com Modo Guiado (6 Trilhas) e Modo Hard")
    add(f"    Total de IDs de DOM mapeados: {html_meta['total_ids']}")
    add(f"    Abas/Navegação ({len(html_meta['tabs'])}): {', '.join(html_meta['tabs'][:30])}")
    add(f"    Modo Guiado Trilhas 1-6 (IDs gp-* — {len(html_meta['guided_ids'])}):")
    add(f"      {', '.join(html_meta['guided_ids'][:50])}")
    if len(html_meta["guided_ids"]) > 50:
        add(f"      ... +{len(html_meta['guided_ids']) - 50} IDs: {', '.join(html_meta['guided_ids'][50:95])}")
    add(f"    Novela Studio / Vozes / Stitcher (IDs nov-* / qnv-* — {len(html_meta['novela_ids'])}):")
    add(f"      {', '.join(html_meta['novela_ids'])}")
    add(f"    HyperFrames / Shopee POV (IDs qhf-* — {len(html_meta['hyper_ids'])}):")
    add(f"      {', '.join(html_meta['hyper_ids'])}")
    add(f"    Demais Cards Modo Hard (IDs card-hard-* / qvu-* / qvs-* — {len(html_meta['hard_cards_ids'])}):")
    add(f"      {', '.join(html_meta['hard_cards_ids'][:45])}")
    add(f"    Funções JavaScript inline ({len(html_meta['js_functions'])}):")
    for lno, fname in html_meta["js_functions"]:
        add(f"      * L{lno:<5} function {fname}()")

    # Colab notebook summary
    nb_path = REPO_ROOT / "tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb"
    if nb_path.is_file():
        nb_data = json.loads(nb_path.read_text(encoding="utf-8"))
        add("")
        add("### tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb")
        add(f"    VPS Equiv.: /opt/ai-media-studio/tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb | Tamanho: {_human_size(nb_path.stat().st_size)} | nbformat: {nb_data.get('nbformat')}")
        for c_idx, cell in enumerate(nb_data.get("cells") or []):
            first_line = "".join(cell.get("source") or [""]).splitlines()[0] if cell.get("source") else ""
            add(f"      * Célula {c_idx} [{cell.get('cell_type')}]: {first_line[:110]}")

    # OpenJarvis & Skills
    add("")
    add("### OpenJarvis CLI/MCP, jarvis-falar & Skills Instaladas")
    add(f"    Binário voz: /home/wbrendo/.local/bin/jarvis-falar (reprodução TTS em alto-falante via Piper/Edge-TTS)")
    add(f"    MCP Server : {REPO_ROOT / 'openjarvis-client/studio_controller_mcp.py'} ({len(mcp_tools)} @mcp.tool registradas):")
    for t in mcp_tools:
        add(f"      * L{t['line']}-L{t['end']:<4} {t['name']}({t['args']}) — {t['doc']}")
    add("    Catálogo de Skills por Diretório:")
    for sk_dir, sk_names in skills_map.items():
        add(f"      * {sk_dir} ({len(sk_names)} skills): {', '.join(sk_names)}")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 2: CATÁLOGO COMPLETO DE ROTAS FASTAPI
    # ------------------------------------------------------------------
    add("-" * 96)
    add(f"SEÇÃO 2 · CATÁLOGO COMPLETO DE ROTAS FASTAPI (main.py — {len(routes)} endpoints)")
    add("-" * 96)
    add("")
    for r in routes:
        eng_hint = "Painel/Infra"
        p = r["path"]
        if "/quick/novela" in p or "/colab" in p or "/voices/regional" in p:
            eng_hint = "Novela Studio v2.8 / Regional Voice / Lip-Sync / Colab"
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
        elif "/keys" in p or "/cascade" in p:
            eng_hint = "Vault Central & Cascata de IA"
        add(f"  [{r['method']:<6}] {r['path']:<48} -> {r['handler']} (L{r['line']}-L{r['end_line']})")
        add(f"           Motor   : {eng_hint}")
        add(f"           Params  : {r['params']}")
        if r["doc"]:
            add(f"           Resumo  : {r['doc']}")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 3: ARQUITETURA DOS MOTORES, CASCATAS DE IA & FFMPEG/CV2
    # ------------------------------------------------------------------
    add("-" * 96)
    add("SEÇÃO 3 · ARQUITETURA DOS MOTORES DE PRODUÇÃO, CASCATAS E SEGREDOS")
    add("-" * 96)
    add("")
    engines_arch = [
        (
            "1. Novelinhas Multi-Personagem (Live-Action 8K & 3D Pixar · Estrutura Viral 4 Cortes)",
            [
                "Fluxo: Roteiro JSON (generate_novela_script / build_fallback_novela_script) ->",
                "  Saneamento Cultural Nordestino (sanitize_northeast_slang bloqueando 'trem/uai/sô/mano/meo/daora/guri/bah/tchê') ->",
                "  Vozes 48kHz Estéreo SoXR (regional_voice_engine.py) ->",
                "  Retratos Keyframes 1080x1920 com boca FECHADA/neutra e mãos relaxadas (NVIDIA NIM flux.1-dev ou SVG fallback) ->",
                "  Animação Lip-Sync 30fps (Tier 1: Colab GPU LivePortrait+LatentSync 1.5/MuseTalk 1.5/EchoMimicV3; Tier 2: CPU MediaPipe FaceLandmarker 478pts + Visemas 2.5D cv2.remap) ->",
                "  Concatenação Clean Cinema 1080x1920 @ 30fps + 48kHz AAC loudnorm I=-16.",
                "Estrutura Dramática 4 Cortes ('viral_4_cuts'): Corte 1 Ataque -> Corte 2 Pausa 0.45s (respirar/piscar) + Invertida -> Corte 3 Choque ('O quê?!') -> Corte 4 Reaction Shot Mudo 1.8s-2.2s de vergonha.",
            ],
        ),
        (
            "2. POV Shopee Anti-Crop 20:9 & Clone Viral (vps_ad_runner.py + viral_reference_analyzer.py)",
            [
                "Safe-Zone Shopee 20:9 travada em X=96..884px (largura útil 788px, topo livre Y=0..235px, rodapé livre Y=1210..1920px e coluna direita livre X>=890px).",
                "Extração de DNA Viral (cut_cadence, WPM, energia de fala) sobre referências em campaigns/<id>/virals/ + curadoria estrita anti-confusão de kits.",
                "Render via HyperFrames Chromium headless (--low-memory-mode) + narração Edge-TTS/Gemini 48kHz SoXR.",
            ],
        ),
        (
            "3. Video Use Jump-Cut Zero-RAM (tools/video-use/vps_zero_cost_runner.py)",
            [
                "0 MB RAM ociosa (CLI efêmero por job). Transcrição via faster-whisper INT8 em CPU (2 vCPUs, sempre carregando float32 NumPy PCM para evitar erro PyAV metadata_errors),",
                "remoção de silêncios/hesitações, color grading LUT/eq FFmpeg, queima opcional de legendas ASS dentro da Safe-Zone e normalização loudnorm I=-16 48kHz SoXR.",
            ],
        ),
        (
            "4. Stacks Docker Sob Demanda (RAM Guard — Máximo 1 stack pesada nos 8 GB RAM da VPS)",
            [
                "SupoClip (Redis + Postgres + Backend + ARQ Worker + Frontend 3107/8000): cortes automáticos de vídeos longos.",
                "MoneyPrinterTurbo (8501/8080): geração de vídeos faceless com B-roll + legendas.",
                "VoiceStudio & OpenFilm (3005) & Open-Higgsfield: ligados via /api/projects/{id}/start com stop-all automático pelo GuidedPipelineOrchestrator (RAM Relay).",
            ],
        ),
    ]
    for title, bullets in engines_arch:
        add(f"  {title}")
        for b in bullets:
            add(f"    - {b}")
    add("")

    add("3.1 Cascatas de Fallback de IA:")
    add("  - LLM Roteiro/Texto (main.py -> call_ai_cascade): Groq -> OpenRouter -> Gemini -> OpenAI -> Anthropic -> Fallback Determinístico")
    add("  - Voz Nordestina (regional_voice_engine.py): Colab Seed-VC/F5-TTS (se selecionado+online) -> Gemini 3.1 Flash TTS Preview -> Gemini 2.5 Flash Preview TTS -> Gemini 3.8 Flash TTS (texto puro) -> Gemini 3.8 Flash Lite TTS -> Edge-TTS fonético (offline only)")
    add("  - Imagem/Ingrediente (vps_novela_runner.py): NVIDIA NIM black-forest-labs/flux.1-dev (768x1344 -> scale/crop Lanczos 1080x1920) -> SVG vetorial rsvg-convert Clean Cinema")
    add("  - Animação/Lip-Sync (novela_lipsync_animator.py): Tier 1 Colab GPU T4 (LivePortrait + LatentSync 1.5 / MuseTalk 1.5 / EchoMimicV3) -> Tier 2 Local CPU MediaPipe FaceLandmarker 478pts + Visemas 2.5D cv2.remap 30fps")
    add("")

    add("3.2 Variáveis de Ambiente e Chaves no Cofre da VPS (apenas nome e status SET=True/False):")
    for k, is_set in sorted((vps_info.get("env_status") or {}).items()):
        add(f"  - {k:<32} SET={is_set}")
    add("")

    # ------------------------------------------------------------------
    # SEÇÃO 4: SUÍTES DE TESTE E AUDITORIA
    # ------------------------------------------------------------------
    add("-" * 96)
    add("SEÇÃO 4 · SUÍTES DE TESTE AUTOMATIZADO (PLAYWRIGHT E2E, COMPUTER VISION & ÁUDIO)")
    add("-" * 96)
    add("")
    tests_dir = REPO_ROOT / "tests"
    if tests_dir.is_dir():
        for tf in sorted(tests_dir.iterdir()):
            if tf.is_file():
                first_lines = " | ".join(
                    l.strip() for l in tf.read_text(encoding="utf-8", errors="ignore").splitlines()[:4] if l.strip()
                )
                add(f"  - tests/{tf.name:<34} ({_human_size(tf.stat().st_size)}) :: {first_lines[:110]}")
    add("")
    add("=" * 96)
    add("FIM DO DOSSIÊ TÉCNICO AI MEDIA STUDIO")
    add("=" * 96)
    return "\n".join(out) + "\n"


def main() -> int:
    txt = build_dossier()
    HOME_OUT.write_text(txt, encoding="utf-8")
    DOCS_OUT.parent.mkdir(parents=True, exist_ok=True)
    DOCS_OUT.write_text(txt, encoding="utf-8")
    line_cnt = len(txt.splitlines())
    print(f"DOSSIE_GENERATED: {HOME_OUT} & {DOCS_OUT} ({line_cnt} lines, {len(txt)} chars)")
    assert line_cnt >= 300, f"Dossier too short: {line_cnt} lines"
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
