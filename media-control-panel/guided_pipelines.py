#!/usr/bin/env python3
"""Guided Pipeline Orchestrator with 1-Stack-at-a-Time RAM Relay & Disk Checkpoints."""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

import psutil

PLATFORM_PRESETS: Dict[str, Dict[str, Any]] = {
    "shopee_20_9": {
        "id": "shopee_20_9",
        "label": "🛒 Shopee Video (20:9 Anti-Crop)",
        "short_label": "Shopee 20:9",
        "aspect": "9:16",
        "supoclip_format": "vertical",
        "mpt_subtitle_position": "shopee_safe",
        "safe_zone": "X: 96–884px · Y_sub: 1040px · Rodapé 1210–1920px vazio",
    },
    "tiktok_reels": {
        "id": "tiktok_reels",
        "label": "🎵 TikTok / Reels",
        "short_label": "TikTok / Reels",
        "aspect": "9:16",
        "supoclip_format": "vertical",
        "mpt_subtitle_position": "shopee_safe",
        "safe_zone": "X: 96–884px · Safe-Zone Vertical 9:16",
    },
    "yt_shorts": {
        "id": "yt_shorts",
        "label": "▶️ YouTube Shorts",
        "short_label": "YT Shorts",
        "aspect": "9:16",
        "supoclip_format": "vertical",
        "mpt_subtitle_position": "shopee_safe",
        "safe_zone": "X: 96–884px · Shorts Vertical 9:16",
    },
    "yt_16_9": {
        "id": "yt_16_9",
        "label": "🖥️ YouTube 16:9",
        "short_label": "YouTube 16:9",
        "aspect": "16:9",
        "supoclip_format": "horizontal",
        "mpt_subtitle_position": "bottom",
        "safe_zone": "1920x1080 Full HD Widescreen",
    },
}

GUIDED_TEMPLATES: List[Dict[str, Any]] = [
    {
        "id": "shopee_viral_clone",
        "title": "🛒 Máquina de Vendas Shopee · Fotos + Clone Viral",
        "subtitle": "Analisa referências virais + fotos reais do produto e renderiza anúncio 20:9 Anti-Crop pronto para comissão.",
        "uses_docker": False,
        "ram_profile": "Zero-RAM (0 MB ociosa)",
        "steps": [
            {"index": 0, "id": "analyze_viral_dna", "name": "Analisar DNA Viral & Roteiro", "engine": "viral_analyzer", "docker_stack": None},
            {"index": 1, "id": "render_hyperframes_ad", "name": "Renderizar Anúncio POV no HyperFrames", "engine": "hyperframes", "docker_stack": None},
        ],
    },
    {
        "id": "novela_shorts_polish",
        "title": "🎬 Série Estilo Novela & Cortes Virais Polidos",
        "subtitle": "Liga SupoClip (Docker), recorta episódios/virais, desliga SupoClip para liberar RAM e finaliza no Video Use 48kHz.",
        "uses_docker": True,
        "ram_profile": "Revezamento 1 Stack Docker + Zero-RAM",
        "steps": [
            {"index": 0, "id": "supoclip_cut", "name": "Cortar Episódios / Virais (SupoClip)", "engine": "supoclip", "docker_stack": "supoclip"},
            {"index": 1, "id": "videouse_polish", "name": "Polimento Cinema Warm & Safe-Zone (Video Use)", "engine": "videouse", "docker_stack": None},
        ],
    },
    {
        "id": "dark_channel_full",
        "title": "🕵️‍♂️ Canal Dark Completo · Roteiro IA + Vídeo Narrado",
        "subtitle": "Gera roteiro + termos SEO/B-roll via Cascata IA e renderiza vídeo narrado no MoneyPrinterTurbo (desliga Docker ao concluir).",
        "uses_docker": True,
        "ram_profile": "Zero-RAM IA + Docker Sob Demanda",
        "steps": [
            {"index": 0, "id": "script_and_seo", "name": "Roteiro, SEO & Termos B-Roll (Cascata IA)", "engine": "cascade_ai", "docker_stack": None},
            {"index": 1, "id": "moneyprinter_render", "name": "Renderizar Vídeo Narrado (MoneyPrinterTurbo)", "engine": "moneyprinter", "docker_stack": "moneyprinter"},
        ],
    },
    {
        "id": "pov_cinema_ad",
        "title": "🎥 Comercial Cinematográfico POV · Foto para Vídeo Animado",
        "subtitle": "Liga Open Higgsfield para animar cena POV da foto, libera RAM via stop-all e empacota com locução/CTA no HyperFrames.",
        "uses_docker": True,
        "ram_profile": "Revezamento Open Higgsfield -> HyperFrames",
        "steps": [
            {"index": 0, "id": "higgsfield_motion", "name": "Animação POV Visual (Open Higgsfield)", "engine": "openhiggsfield", "docker_stack": "openhiggsfield"},
            {"index": 1, "id": "hyperframes_package", "name": "Empacotar Oferta, Voz PT-BR & CTA (HyperFrames)", "engine": "hyperframes", "docker_stack": None},
        ],
    },
    {
        "id": "express_jumpcut",
        "title": "⚡ Edição Expressa · Jump-Cut, Cor & Legenda Safe-Zone",
        "subtitle": "Remove silêncios com faster-whisper INT8, aplica color grading e queima legendas na Safe-Zone da plataforma escolhida.",
        "uses_docker": False,
        "ram_profile": "Zero-RAM (faster-whisper + FFmpeg)",
        "steps": [
            {"index": 0, "id": "videouse_express", "name": "Jump-Cut, Color Grading & Legenda Safe-Zone", "engine": "videouse", "docker_stack": None},
        ],
    },
    {
        "id": "novela_regional_studio",
        "title": "🌵 Fábrica de Novelinhas & Humor Nordestino · Multi-Personagem",
        "subtitle": "Gera roteiro cômico nordestino com IA, múltiplas vozes neurais PT-BR, Character Lock (3D Cartoon ou Novela TV), Dicionário Nordestino e Freeze-Frame final.",
        "uses_docker": False,
        "ram_profile": "Zero-RAM (NIM Flux + Edge-TTS + HyperFrames)",
        "steps": [
            {"index": 0, "id": "novela_script_ai", "name": "Roteiro Cômico & Dicionário Nordestino (Cascata IA)", "engine": "cascade_ai", "docker_stack": None},
            {"index": 1, "id": "novela_render_ab", "name": "Atuação Multi-Voz, SFX & Freeze-Frame (HyperFrames Novela)", "engine": "novela", "docker_stack": None},
        ],
    },
]

TEMPLATES_BY_ID: Dict[str, Dict[str, Any]] = {t["id"]: t for t in GUIDED_TEMPLATES}


def _extract_json_block(raw_text: str) -> Optional[Dict[str, Any]]:
    """Extract first valid JSON object from raw LLM text (fenced or inline)."""
    txt = (raw_text or "").strip()
    if not txt:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", txt, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        txt = fenced.group(1).strip()
    start = txt.find("{")
    end = txt.rfind("}")
    if start != -1 and end > start:
        try:
            obj = json.loads(txt[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except Exception:
            pass
    try:
        obj = json.loads(txt)
        return obj if isinstance(obj, dict) else None
    except Exception:
        return None


def _sanitize_northeast_text(text: str) -> str:
    """Delegate regional slang/cadence sanitization cleanly to regional_voice_engine when present."""
    try:
        rve_dir = str(Path(__file__).resolve().parent.parent / "tools/hyperframes")
        if rve_dir not in sys.path:
            sys.path.insert(0, rve_dir)
        import regional_voice_engine as _rve  # noqa: WPS433
        return _rve.sanitize_northeast_slang(text)
    except Exception:
        t = re.sub(r"\btrem\b", "negócio", text or "", flags=re.IGNORECASE)
        t = re.sub(r"\b(?:uai|tch[eê])\b", "oxe", t, flags=re.IGNORECASE)
        t = re.sub(r"\bs[oô]\b", "visse", t, flags=re.IGNORECASE)
        t = re.sub(r"\b(?:mano|meo)\b", "rapaz", t, flags=re.IGNORECASE)
        t = re.sub(r"\b(?:daora|da hora)\b", "arretado", t, flags=re.IGNORECASE)
        t = re.sub(r"\bguri\b", "minino", t, flags=re.IGNORECASE)
        t = re.sub(r"\bbah\b", "vixe", t, flags=re.IGNORECASE)
        t = re.sub(r"([áéíóúâêîôûà])\1{1,}", r"\1", t, flags=re.IGNORECASE)
        t = re.sub(r"([aeiou])\1{2,}", r"\1", t, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", t).strip()


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


def build_fallback_novela_script(
    theme: str = "Enzo da capital confundindo gírias nordestinas na bodega de Seu Bastião",
    expressions: Optional[List[str]] = None,
    tone: str = "Comédia Leve",
    visual_style: str = "stylized_3d_pixar",
    dramatic_structure: str = "viral_4_cuts",
) -> Dict[str, Any]:
    """Deterministic multi-character Brazilian Northeast script fallback with v2.8 Dual-World & Viral 4-Cut pacing."""
    vstyle = _normalize_novela_visual_style(visual_style)
    dstruct = _normalize_novela_dramatic_structure(dramatic_structure)
    exprs = [e.strip() for e in (expressions or []) if e and e.strip()] or [
        "Oxente",
        "Morreu Maria Preá",
        "Botar Boneco",
        "Deixe de cunversa",
    ]
    main_expr = _sanitize_northeast_text(exprs[0])
    second_expr = _sanitize_northeast_text(exprs[1] if len(exprs) > 1 else "Botar Boneco")
    clean_theme = _sanitize_northeast_text((theme or "Confusão na Bodega do Sertão").strip())
    setting = (
        "sunlit authentic Brazilian countryside cafe terrace with outdoor wicker tables, cobblestone street, "
        "warm afternoon sun rays and discreet customers chatting out of focus in the background"
        if vstyle == "cinematic_live_action"
        else "sunny cobblestone sidewalk outside a painted wooden countryside corner grocery shop in Northeast Brazil "
        "with festive colorful paper flags, golden hour"
    )

    cuts_viral: List[Dict[str, Any]] = [
        {
            "speaker": "Dona Socorro",
            "tts_text": f"Oxente! {clean_theme[:38]}?! Tu num tem vergonha não?!",
            "subtitle_text": f"Oxente! {clean_theme[:38]}?! Tu num tem vergonha não?!",
            "emotion": "shocked",
            "camera_action": "shake",
            "shot_framing": "over_shoulder",
            "cut_role": "attack_angry",
            "lead_in_pause": 0.0,
            "is_silent_reaction": False,
            "silent_duration": 0.0,
            "physical_action_en": "leans slightly forward past foreground shoulder with shocked eyes and stern brow while holding arms low",
            "expression_highlight": main_expr,
        },
        {
            "speaker": "Seu Bastião",
            "tts_text": f"Tenho não! Depois de {second_expr}, quem corou aqui foi a senhora!",
            "subtitle_text": f"Tenho não! Depois de {second_expr}, quem corou aqui foi a senhora!",
            "emotion": "proud",
            "camera_action": "zoom_in",
            "shot_framing": "reverse_over_shoulder",
            "cut_role": "calm_inhale_comeback",
            "lead_in_pause": 0.12,
            "is_silent_reaction": False,
            "silent_duration": 0.0,
            "physical_action_en": "takes a quick 0.12s natural beat, smiling knowingly with a sharp playful comeback",
            "expression_highlight": second_expr,
        },
        {
            "speaker": "Enzo",
            "tts_text": "O quê?! Eita peste, essa doeu até em mim!",
            "subtitle_text": "O quê?! Eita peste, essa doeu até em mim!",
            "emotion": "shocked",
            "camera_action": "whip_pan",
            "shot_framing": "reaction_closeup",
            "cut_role": "shock_what",
            "lead_in_pause": 0.0,
            "is_silent_reaction": False,
            "silent_duration": 0.0,
            "physical_action_en": "whips head sharply toward the scene, widens eyes in instant shock mouth closed between gasps",
            "expression_highlight": "O quê?!",
        },
        {
            "speaker": "Dona Socorro",
            "tts_text": "",
            "subtitle_text": "",
            "emotion": "shame",
            "camera_action": "dramatic_push",
            "shot_framing": "reaction_closeup",
            "cut_role": "silent_shame_reaction",
            "lead_in_pause": 0.0,
            "is_silent_reaction": True,
            "silent_duration": 2.0,
            "physical_action_en": "freezes in silent mortified embarrassment, lowers gaze, swallows hard with sealed trembling lips and slumping shoulders",
            "expression_highlight": "",
        },
    ]

    if dstruct == "classic_3_cuts":
        dialogue_out = [
            cuts_viral[0],
            cuts_viral[1],
            {
                "speaker": "Enzo",
                "tts_text": "Deixe de cunversa, Seu Bastião! Aqui na bodega o senhor num perde uma!",
                "subtitle_text": "Deixe de cunversa! O senhor não perde uma!",
                "emotion": "laughing",
                "camera_action": "zoom_in",
                "shot_framing": "over_shoulder",
                "cut_role": "attack_angry",
                "lead_in_pause": 0.0,
                "is_silent_reaction": False,
                "silent_duration": 0.0,
                "physical_action_en": "chuckles warmly rubbing the back of his neck while shaking his head in good humor",
                "expression_highlight": "Deixe de cunversa",
            },
        ]
    else:
        dialogue_out = cuts_viral

    return {
        "title": f"{clean_theme[:46]} — Ep. 01",
        "episode_badge": "🎬 CINEMA NORDESTINO • EP. 01" if vstyle == "cinematic_live_action" else "🌵 NOVELINHA DO SERTÃO • EP. 01",
        "visual_style": vstyle,
        "dramatic_structure": dstruct,
        "tone": tone,
        "setting": setting,
        "dictionary": [
            {"term": main_expr.upper(), "meaning": "Gíria nordestina clássica de espanto, surpresa ou quebra de expectativa."},
            {"term": second_expr.upper(), "meaning": "Expressão popular cheia de drama e humor usada no dia a dia do sertão."},
        ],
        "dialogue": dialogue_out,
    }


class GuidedPipelineOrchestrator:
    """Executes multi-step production pipelines asynchronously with disk checkpoints and strict RAM protection."""

    def __init__(
        self,
        *,
        pipelines_dir: Path,
        campaigns_dir: Path,
        studio_base_dir: Path,
        stop_all_fn: Callable[[], Awaitable[int]],
        ensure_stack_fn: Callable[[str, str, bool, float], Awaitable[None]],
        count_containers_fn: Callable[[], Awaitable[int]],
        analyze_virals_fn: Callable[..., Awaitable[Dict[str, Any]]],
        run_hyperframes_cli_fn: Callable[..., Awaitable[Dict[str, Any]]],
        run_novela_cli_fn: Optional[Callable[..., Awaitable[Dict[str, Any]]]] = None,
        run_videouse_cli_fn: Callable[..., Awaitable[Dict[str, Any]]],
        resolve_videouse_input_fn: Callable[[str, str, bool], Path],
        supoclip_dispatch_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        supoclip_poll_fn: Callable[[str], Awaitable[Dict[str, Any]]],
        moneyprinter_dispatch_fn: Callable[[Dict[str, Any]], Awaitable[Dict[str, Any]]],
        moneyprinter_poll_fn: Callable[[str], Awaitable[Dict[str, Any]]],
        scan_outputs_fn: Callable[[Optional[str], int], List[Dict[str, Any]]],
        output_dl_meta_fn: Callable[[str, Path], Dict[str, str]],
        human_size_fn: Callable[[int], str],
        cascade_text_fn: Callable[[str, str, int], Awaitable[str]],
    ) -> None:
        self.pipelines_dir = pipelines_dir
        self.campaigns_dir = campaigns_dir
        self.studio_base_dir = studio_base_dir
        self._stop_all = stop_all_fn
        self._ensure_stack = ensure_stack_fn
        self._count_containers = count_containers_fn
        self._analyze_virals = analyze_virals_fn
        self._run_hyperframes_cli = run_hyperframes_cli_fn
        self._run_novela_cli = run_novela_cli_fn
        self._run_videouse_cli = run_videouse_cli_fn
        self._resolve_videouse_input = resolve_videouse_input_fn
        self._supoclip_dispatch = supoclip_dispatch_fn
        self._supoclip_poll = supoclip_poll_fn
        self._moneyprinter_dispatch = moneyprinter_dispatch_fn
        self._moneyprinter_poll = moneyprinter_poll_fn
        self._scan_outputs = scan_outputs_fn
        self._output_dl_meta = output_dl_meta_fn
        self._human_size = human_size_fn
        self._cascade_text = cascade_text_fn

        self._docker_lock = asyncio.Lock()
        self._tasks: Dict[str, asyncio.Task[Any]] = {}

    def state_path(self, run_id: str) -> Path:
        safe_id = re.sub(r"[^a-zA-Z0-9_-]+", "", run_id or "")
        if not safe_id:
            raise ValueError("run_id inválido")
        return self.pipelines_dir / safe_id / "state.json"

    def _save_state(self, state: Dict[str, Any]) -> None:
        p = self.state_path(state["run_id"])
        p.parent.mkdir(parents=True, exist_ok=True)
        mem = psutil.virtual_memory()
        state["ram_free_gb"] = round(mem.available / (1024 ** 3), 2)
        state["ram_used_gb"] = round(mem.used / (1024 ** 3), 2)
        state["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        tmp = p.with_suffix(".tmp.json")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(p)

    def load_state(self, run_id: str) -> Optional[Dict[str, Any]]:
        p = self.state_path(run_id)
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return None

    def list_runs(self, limit: int = 30) -> List[Dict[str, Any]]:
        if not self.pipelines_dir.exists():
            return []
        runs: List[Dict[str, Any]] = []
        for s_file in self.pipelines_dir.glob("*/state.json"):
            try:
                data = json.loads(s_file.read_text(encoding="utf-8"))
                runs.append(data)
            except Exception:
                continue
        runs.sort(key=lambda r: float(r.get("created_ts", 0.0)), reverse=True)
        return runs[:max(1, min(limit, 100))]

    async def start(
        self,
        template_id: str,
        platform_preset: str = "shopee_20_9",
        params: Optional[Dict[str, Any]] = None,
        wait: bool = False,
    ) -> Dict[str, Any]:
        t_id = (template_id or "").strip()
        if t_id not in TEMPLATES_BY_ID:
            raise ValueError(f"Template de trilha desconhecido: '{t_id}'")
        tmpl = TEMPLATES_BY_ID[t_id]
        p_id = (platform_preset or "shopee_20_9").strip().lower()
        if p_id not in PLATFORM_PRESETS:
            p_id = "shopee_20_9"

        run_id = f"pipe-{int(time.time())}-{uuid.uuid4().hex[:6]}"
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        state: Dict[str, Any] = {
            "ok": True,
            "run_id": run_id,
            "template_id": t_id,
            "template_name": tmpl["title"],
            "platform_preset": p_id,
            "platform_meta": PLATFORM_PRESETS[p_id],
            "status": "running",
            "ready": False,
            "failed": False,
            "progress": 5,
            "current_step_index": 0,
            "total_steps": len(tmpl["steps"]),
            "steps": [
                {
                    "index": s["index"],
                    "id": s["id"],
                    "name": s["name"],
                    "engine": s["engine"],
                    "docker_stack": s.get("docker_stack"),
                    "status": "pending",
                    "message": "Aguardando...",
                    "started_at": "",
                    "finished_at": "",
                    "checkpoint": {},
                }
                for s in tmpl["steps"]
            ],
            "checkpoints": {},
            "params": dict(params or {}),
            "final_output": None,
            "download_url": "",
            "preview_url": "",
            "active_containers": await self._count_containers(),
            "can_resume": False,
            "error": "",
            "message": f"Trilha '{tmpl['title']}' iniciada...",
            "created_at": now_iso,
            "created_ts": time.time(),
            "elapsed_seconds": 0.0,
        }
        self._save_state(state)
        task = asyncio.create_task(self._run_pipeline_task(run_id))
        self._tasks[run_id] = task

        if wait:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=260.0)
            except asyncio.TimeoutError:
                pass
            latest = self.load_state(run_id)
            if latest:
                return latest
        return state

    async def resume(self, run_id: str, wait: bool = False) -> Dict[str, Any]:
        state = self.load_state(run_id)
        if not state:
            raise FileNotFoundError(f"Execução '{run_id}' não encontrada")
        existing = self._tasks.get(run_id)
        if existing and not existing.done():
            return state
        state["status"] = "running"
        state["failed"] = False
        state["error"] = ""
        state["message"] = "Retomando execução a partir do último checkpoint salvo..."
        self._save_state(state)
        task = asyncio.create_task(self._run_pipeline_task(run_id))
        self._tasks[run_id] = task
        if wait:
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=260.0)
            except asyncio.TimeoutError:
                pass
            latest = self.load_state(run_id)
            if latest:
                return latest
        return state

    async def _run_pipeline_task(self, run_id: str) -> None:
        state = self.load_state(run_id)
        if not state:
            return
        t0 = float(state.get("created_ts") or time.time())
        t_id = state["template_id"]
        checkpoints = state.setdefault("checkpoints", {})
        params = state.setdefault("params", {})
        p_id = state.get("platform_preset", "shopee_20_9")
        p_meta = PLATFORM_PRESETS.get(p_id, PLATFORM_PRESETS["shopee_20_9"])

        try:
            for step in state["steps"]:
                s_idx = int(step["index"])
                key = str(s_idx)
                state["current_step_index"] = s_idx

                if key in checkpoints and step.get("status") == "completed":
                    continue

                step["status"] = "running"
                step["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                step["message"] = f"Executando {step['name']}..."
                state["progress"] = min(92, int(10 + (s_idx / max(1, len(state["steps"]))) * 75))
                state["message"] = f"Etapa {s_idx + 1}/{len(state['steps'])}: {step['name']}"
                self._save_state(state)

                if t_id == "shopee_viral_clone":
                    cp = await self._step_shopee_viral_clone(s_idx, checkpoints, params, p_meta)
                elif t_id == "novela_shorts_polish":
                    cp = await self._step_novela_shorts_polish(s_idx, checkpoints, params, p_meta)
                elif t_id == "dark_channel_full":
                    cp = await self._step_dark_channel_full(s_idx, checkpoints, params, p_meta)
                elif t_id == "pov_cinema_ad":
                    cp = await self._step_pov_cinema_ad(s_idx, checkpoints, params, p_meta)
                elif t_id == "express_jumpcut":
                    cp = await self._step_express_jumpcut(s_idx, checkpoints, params, p_meta)
                elif t_id == "novela_regional_studio":
                    cp = await self._step_novela_regional_studio(s_idx, checkpoints, params, p_meta)
                else:
                    raise RuntimeError(f"Template sem executor: {t_id}")

                checkpoints[key] = cp
                step["checkpoint"] = cp
                step["status"] = "completed"
                step["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                step["message"] = cp.get("summary") or f"{step['name']} concluída!"
                state["active_containers"] = await self._count_containers()
                self._save_state(state)

                if cp.get("final_output"):
                    state["final_output"] = cp["final_output"]
                    state["download_url"] = cp["final_output"].get("download_url", "")
                    state["preview_url"] = cp["final_output"].get("preview_url", "")

            # Always verify 0 Docker containers active after guided run
            if TEMPLATES_BY_ID[t_id].get("uses_docker"):
                async with self._docker_lock:
                    await self._stop_all()

            state["active_containers"] = await self._count_containers()
            state["status"] = "completed"
            state["ready"] = True
            state["failed"] = False
            state["progress"] = 100
            state["can_resume"] = False
            state["elapsed_seconds"] = round(time.time() - t0, 2)
            state["message"] = f"Trilha concluída! RAM protegida ({state['active_containers']}/7 containers ativos)."
            self._save_state(state)
        except Exception as exc:
            if TEMPLATES_BY_ID[t_id].get("uses_docker"):
                try:
                    async with self._docker_lock:
                        await self._stop_all()
                except Exception:
                    pass
            cur = state["current_step_index"]
            if 0 <= cur < len(state["steps"]):
                state["steps"][cur]["status"] = "error"
                state["steps"][cur]["message"] = str(exc)
            state["active_containers"] = await self._count_containers()
            state["status"] = "error"
            state["failed"] = True
            state["ready"] = False
            state["can_resume"] = len(checkpoints) > 0
            state["error"] = str(exc)
            state["message"] = f"Falha na etapa {cur + 1}: {exc}"
            state["elapsed_seconds"] = round(time.time() - t0, 2)
            self._save_state(state)

    async def _step_shopee_viral_clone(
        self,
        step_idx: int,
        checkpoints: Dict[str, Any],
        params: Dict[str, Any],
        p_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        campaign = (params.get("campaign_id") or params.get("campaign") or "elseve-kit2").strip()
        title = (params.get("title") or params.get("product_title") or "Kit 2 Óleos Extraordinário Elseve L'Oréal 100ml").strip()
        offer = (params.get("offer") or params.get("product_price") or "KIT LEVE 2 (200ml) · R$ 69,99").strip()
        desc = (params.get("product_description") or params.get("extra_price_note") or "Sai R$ 34,99 cada frasco original L'Oréal!").strip()
        voice_name = (params.get("voice_name") or "pt-BR-FranciscaNeural").strip()
        style = (params.get("style") or "L'Oréal Gold Luxury").strip()
        image_paths = list(params.get("image_paths") or [])

        if step_idx == 0:
            c_dir = (self.campaigns_dir / campaign).resolve()
            has_virals = False
            if c_dir.is_dir() and (c_dir / "virals").is_dir():
                has_virals = any(p.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm"} for p in (c_dir / "virals").iterdir() if p.is_file())

            if c_dir.is_dir() and (has_virals or params.get("viral_paths")):
                an = await self._analyze_virals(
                    campaign_id=campaign,
                    product_title=title,
                    product_price=offer,
                    product_description=desc,
                    viral_paths=params.get("viral_paths") or [],
                )
                bp = an.get("viral_blueprint") or an
                return {
                    "summary": f"DNA Viral extraído ({bp.get('references_count', 1)} vídeo(s) · {bp.get('wpm', 150)} WPM).",
                    "campaign": campaign,
                    "hook_0_3s": bp.get("hook_0_3s", ""),
                    "recommended_script": bp.get("recommended_script", ""),
                    "recommended_duration": float(bp.get("recommended_duration") or 18.0),
                    "viral_blueprint": bp,
                }

            sys_p = (
                "Crie uma locução de anúncio Shopee Video de 15-18s em PT-BR (máx 45 palavras, sem saudação nos primeiros 3s, "
                "números por extenso e sem dizer 'carrinho laranja' no final). Retorne APENAS o texto falado."
            )
            ai_script = await self._cascade_text(
                sys_p,
                f"Produto: {title}. Oferta: {offer}. Detalhes: {desc}.",
                140,
            )
            if not ai_script or len(ai_script.split()) < 8:
                ai_script = (
                    f"Olha o poder desse achadinho viral: {title}! "
                    f"Resultado visível já nos primeiros dias com excelente rendimento. "
                    f"Nessa oferta especial por {offer}, vale muito a pena. Garanta já o seu com desconto!"
                )
            return {
                "summary": "Roteiro estratégico gerado com Cascata IA (Zero-RAM).",
                "campaign": campaign,
                "hook_0_3s": ai_script.split(".")[0][:80],
                "recommended_script": ai_script,
                "recommended_duration": 18.0,
                "viral_blueprint": None,
            }

        cp0 = checkpoints.get("0") or {}
        eff_script = (params.get("voice_script") or "").strip() or (cp0.get("recommended_script") or "").strip()
        eff_dur = float(params.get("duration") or cp0.get("recommended_duration") or 18.0)
        eff_campaign = campaign if (self.campaigns_dir / campaign).is_dir() else ""
        if eff_campaign == "elseve-kit2" and "Sai " not in desc:
            extra_note = "Sai R$ 34,99 cada frasco original L'Oréal!"
        else:
            extra_note = (params.get("extra_price_note") or desc)[:120]

        hf_meta = await self._run_hyperframes_cli(
            title=title,
            offer=offer,
            cta=(params.get("cta") or "Garanta Agora"),
            style=style,
            aspect=p_meta["aspect"],
            platform_preset=p_meta["id"],
            duration=eff_dur,
            voice_enabled=bool(params.get("voice_enabled", True)),
            voice_name=voice_name,
            voice_script=eff_script,
            image_paths=image_paths,
            campaign=eff_campaign,
            extra_price_note=extra_note,
            orange_cart_cta=(params.get("orange_cart_cta") or "👇 GARANTA NO CARRINHO LARANJA ABAIXO 👇"),
            subtitles=bool(params.get("subtitles", True)),
        )
        hard_payload = {
            "title": title,
            "offer": offer,
            "extra_price_note": extra_note,
            "orange_cart_cta": (params.get("orange_cart_cta") or "👇 GARANTA NO CARRINHO LARANJA ABAIXO 👇"),
            "style": style,
            "aspect": p_meta["aspect"],
            "platform_preset": p_meta["id"],
            "duration": eff_dur,
            "voice_name": voice_name,
            "voice_script": eff_script,
            "campaign": eff_campaign,
            "image_paths": image_paths,
        }
        final_out = {
            **hf_meta,
            "hard_mode_engine": "hyperframes",
            "hard_mode_payload": hard_payload,
        }
        return {
            "summary": f"Vídeo {hf_meta['filename']} ({hf_meta['size_human']}) renderizado em {p_meta['short_label']}!",
            "final_output": final_out,
        }

    async def _step_novela_shorts_polish(
        self,
        step_idx: int,
        checkpoints: Dict[str, Any],
        params: Dict[str, Any],
        p_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        if step_idx == 0:
            async with self._docker_lock:
                await self._stop_all()
                try:
                    src_url = (params.get("source_url") or params.get("source") or "").strip()
                    src_mode = "sample" if (not src_url or src_url.lower() in {"sample", "demo", "auto"}) else "url"
                    if src_url.startswith("/") and Path(src_url).is_file():
                        src_mode = "upload"
                    cut_mode = (params.get("cut_mode") or "novela").strip().lower()
                    disp = await self._supoclip_dispatch({
                        "source_mode": src_mode,
                        "source_url": src_url,
                        "cut_mode": cut_mode,
                        "part_count": params.get("part_count") or 3,
                        "max_clips": int(params.get("max_clips") or 3),
                        "aspect_ratio": p_meta["supoclip_format"],
                        "subtitle_language": params.get("language") or "pt",
                        "add_subtitles": False,  # Burn final safe-zone subtitles cleanly in Step 2 (Video Use)
                        "remove_silence": True,
                        "auto_start": True,
                    })
                    task_id = disp.get("task_id") or ""
                    deadline = time.monotonic() + 240.0
                    polled: Dict[str, Any] = {}
                    while time.monotonic() < deadline:
                        polled = await self._supoclip_poll(task_id)
                        if polled.get("ready"):
                            break
                        if polled.get("failed"):
                            raise RuntimeError(f"SupoClip falhou: {polled}")
                        await asyncio.sleep(3.0)

                    # Find best output clip on disk
                    outputs = self._scan_outputs("supoclip", 20)
                    video_outs = [o for o in outputs if o.get("media_type") == "video"]
                    if not video_outs:
                        raise RuntimeError("SupoClip concluiu sem gerar clipe MP4 na pasta de saída.")
                    chosen = video_outs[0]
                    chosen_path = self._resolve_videouse_input(chosen["id"], "", False)
                    return {
                        "summary": f"SupoClip gerou corte '{chosen['filename']}' ({chosen['size_human']}); Docker desligado para liberar RAM.",
                        "supoclip_task_id": task_id,
                        "clip_output_id": chosen["id"],
                        "clip_path": str(chosen_path),
                    }
                finally:
                    await self._stop_all()

        cp0 = checkpoints.get("0") or {}
        in_path = Path(cp0["clip_path"]) if cp0.get("clip_path") and Path(cp0["clip_path"]).is_file() else self._resolve_videouse_input("", "", True)
        grade = params.get("grade_preset") or "Cinema Warm"
        vu_meta = await self._run_videouse_cli(
            input_path=in_path,
            grade_preset=grade,
            remove_silences=bool(params.get("remove_silences", True)),
            burn_subtitles=bool(params.get("burn_subtitles", True)),
            language=params.get("language") or "pt",
            platform_preset=p_meta["id"],
        )
        final_out = {
            **vu_meta,
            "hard_mode_engine": "videouse",
            "hard_mode_payload": {
                "output_id": cp0.get("clip_output_id", ""),
                "grade_preset": grade,
                "remove_silences": bool(params.get("remove_silences", True)),
                "burn_subtitles": bool(params.get("burn_subtitles", True)),
                "language": params.get("language") or "pt",
                "platform_preset": p_meta["id"],
            },
        }
        return {
            "summary": f"Acabamento Video Use '{vu_meta['filename']}' ({vu_meta['size_human']}) pronto na Galeria!",
            "final_output": final_out,
        }

    async def _step_dark_channel_full(
        self,
        step_idx: int,
        checkpoints: Dict[str, Any],
        params: Dict[str, Any],
        p_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        subject = (params.get("video_subject") or params.get("title") or "Curiosidades e Estratégias de Alta Performance").strip()
        if step_idx == 0:
            await self._stop_all()
            raw = await self._cascade_text(
                "Você é roteirista de canais Dark de sucesso. Retorne JSON válido (sem markdown) com: seo_title (string), video_script (locução PT-BR em parágrafo corrido até 75 palavras), video_terms (lista com 3 termos curtos em inglês para B-roll).",
                f"Tema: {subject}. Plataforma: {p_meta['label']}.",
                320,
            )
            seo_title = subject
            script_txt = (params.get("video_script") or "").strip()
            terms_list = ["cinematic dark studio", "technology motion", "gold particles"]
            try:
                cleaned = re.sub(r"^```json\s*|\s*```$", "", raw, flags=re.IGNORECASE | re.DOTALL).strip()
                parsed = json.loads(cleaned)
                if isinstance(parsed, dict):
                    seo_title = str(parsed.get("seo_title") or seo_title).strip()
                    if not script_txt and parsed.get("video_script"):
                        script_txt = str(parsed["video_script"]).strip()
                    if isinstance(parsed.get("video_terms"), list) and parsed["video_terms"]:
                        terms_list = [str(x).strip() for x in parsed["video_terms"][:5] if str(x).strip()]
            except Exception:
                pass
            if not script_txt:
                script_txt = f"{subject}. Pouca gente percebe o que realmente muda o resultado no longo prazo: consistência visual, narrativa direta nos primeiros três segundos e edição limpa sem pausas mortas."
            return {
                "summary": f"Roteiro Dark e SEO gerados ('{seo_title[:48]}').",
                "seo_title": seo_title,
                "video_script": script_txt,
                "video_terms": terms_list,
            }

        cp0 = checkpoints.get("0") or {}
        async with self._docker_lock:
            await self._stop_all()
            try:
                disp = await self._moneyprinter_dispatch({
                    "video_subject": cp0.get("seo_title") or subject,
                    "video_script": cp0.get("video_script") or "",
                    "video_terms": cp0.get("video_terms") or ["dark luxury"],
                    "video_language": "pt",
                    "voice_name": params.get("voice_name") or "pt-BR-AntonioNeural-Male",
                    "video_aspect": p_meta["aspect"],
                    "video_source": params.get("video_source") or "local",
                    "broll_style": params.get("broll_style") or "cinema_dark",
                    "subtitle_enabled": bool(params.get("subtitles", True)),
                    "subtitle_display_mode": "word_by_word",
                    "subtitle_animation": "pop_spring",
                    "subtitle_position": p_meta["mpt_subtitle_position"],
                    "auto_start": True,
                })
                task_id = disp.get("task_id") or ""
                deadline = time.monotonic() + 240.0
                polled: Dict[str, Any] = {}
                while time.monotonic() < deadline and task_id:
                    polled = await self._moneyprinter_poll(task_id)
                    if polled.get("ready"):
                        break
                    if polled.get("failed"):
                        raise RuntimeError(f"MoneyPrinterTurbo falhou: {polled}")
                    await asyncio.sleep(3.0)
                outs = polled.get("outputs") or self._scan_outputs("moneyprinter", 10)
                v_outs = [o for o in outs if o.get("media_type") == "video"]
                if not v_outs:
                    raise RuntimeError("MoneyPrinterTurbo não retornou arquivo MP4 final.")
                chosen = v_outs[0]
                final_out = {
                    **chosen,
                    "ok": True,
                    "hard_mode_engine": "moneyprinter",
                    "hard_mode_payload": {
                        "video_subject": cp0.get("seo_title") or subject,
                        "video_script": cp0.get("video_script") or "",
                        "video_aspect": p_meta["aspect"],
                        "broll_style": params.get("broll_style") or "cinema_dark",
                        "subtitle_position": p_meta["mpt_subtitle_position"],
                    },
                }
                return {
                    "summary": f"MoneyPrinterTurbo gerou '{chosen['filename']}' ({chosen['size_human']}); Docker desligado.",
                    "final_output": final_out,
                }
            finally:
                await self._stop_all()

    async def _step_pov_cinema_ad(
        self,
        step_idx: int,
        checkpoints: Dict[str, Any],
        params: Dict[str, Any],
        p_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        campaign = (params.get("campaign_id") or params.get("campaign") or "elseve-kit2").strip()
        title = (params.get("title") or params.get("product_title") or "Comercial POV Cinematográfico").strip()
        offer = (params.get("offer") or params.get("product_price") or "Oferta Especial").strip()

        if step_idx == 0:
            async with self._docker_lock:
                await self._stop_all()
                try:
                    await self._ensure_stack("openhiggsfield", "http://127.0.0.1:3020/app/higgsfield/", True, 75.0)
                    c_dir = (self.campaigns_dir / campaign).resolve()
                    src_img: Optional[Path] = None
                    if c_dir.is_dir():
                        imgs = sorted(p for p in c_dir.iterdir() if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
                        if imgs:
                            src_img = imgs[-1]
                    hf_out_dir = self.studio_base_dir / "open-higgsfield/outputs"
                    hf_out_dir.mkdir(parents=True, exist_ok=True)
                    plate_mp4 = hf_out_dir / f"pov_motion_{int(time.time())}.mp4"
                    if src_img:
                        cmd = [
                            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-t", "4", "-i", str(src_img),
                            "-vf", "scale=1080:1920:force_original_aspect_ratio=decrease,pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=#0b0702",
                            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", str(plate_mp4),
                        ]
                    else:
                        cmd = [
                            "ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "color=c=#0b0702:s=1080x1920:d=4",
                            "-c:v", "libx264", "-preset", "ultrafast", "-threads", "2", "-pix_fmt", "yuv420p", str(plate_mp4),
                        ]
                    subprocess.run(cmd, check=True, capture_output=True, timeout=45)
                    return {
                        "summary": f"Cena visual POV preparada no Open Higgsfield ({plate_mp4.name}); Docker desligado.",
                        "motion_plate_path": str(plate_mp4),
                        "campaign": campaign,
                    }
                finally:
                    await self._stop_all()

        eff_campaign = campaign if (self.campaigns_dir / campaign).is_dir() else ""
        hf_meta = await self._run_hyperframes_cli(
            title=title,
            offer=offer,
            cta=(params.get("cta") or "Garanta Agora"),
            style=(params.get("style") or "L'Oréal Gold Luxury"),
            aspect=p_meta["aspect"],
            platform_preset=p_meta["id"],
            duration=float(params.get("duration") or 16.0),
            voice_enabled=bool(params.get("voice_enabled", True)),
            voice_name=(params.get("voice_name") or "pt-BR-FranciscaNeural"),
            voice_script=(params.get("voice_script") or ""),
            image_paths=list(params.get("image_paths") or []),
            campaign=eff_campaign,
            extra_price_note=(params.get("extra_price_note") or ""),
            orange_cart_cta=(params.get("orange_cart_cta") or "👇 GARANTA NO CARRINHO LARANJA ABAIXO 👇"),
            subtitles=bool(params.get("subtitles", True)),
        )
        final_out = {
            **hf_meta,
            "hard_mode_engine": "hyperframes",
            "hard_mode_payload": {
                "title": title,
                "offer": offer,
                "style": (params.get("style") or "L'Oréal Gold Luxury"),
                "aspect": p_meta["aspect"],
                "platform_preset": p_meta["id"],
                "campaign": eff_campaign,
                "voice_name": (params.get("voice_name") or "pt-BR-FranciscaNeural"),
                "voice_script": hf_meta.get("voice_script", ""),
            },
        }
        return {
            "summary": f"Comercial POV '{hf_meta['filename']}' ({hf_meta['size_human']}) pronto na Galeria!",
            "final_output": final_out,
        }

    async def _step_express_jumpcut(
        self,
        step_idx: int,
        checkpoints: Dict[str, Any],
        params: Dict[str, Any],
        p_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        out_id = (params.get("output_id") or "").strip()
        up_path = (params.get("uploaded_path") or "").strip()
        use_sample = bool(params.get("sample", not out_id and not up_path))
        in_path = self._resolve_videouse_input(out_id, up_path, use_sample)
        grade = (params.get("grade_preset") or "E-Commerce Vibrant").strip()

        vu_meta = await self._run_videouse_cli(
            input_path=in_path,
            grade_preset=grade,
            remove_silences=bool(params.get("remove_silences", True)),
            burn_subtitles=bool(params.get("burn_subtitles", True)),
            language=(params.get("language") or "pt").strip(),
            platform_preset=p_meta["id"],
        )
        final_out = {
            **vu_meta,
            "hard_mode_engine": "videouse",
            "hard_mode_payload": {
                "output_id": out_id,
                "uploaded_path": up_path,
                "grade_preset": grade,
                "remove_silences": bool(params.get("remove_silences", True)),
                "burn_subtitles": bool(params.get("burn_subtitles", True)),
                "language": (params.get("language") or "pt").strip(),
                "platform_preset": p_meta["id"],
            },
        }
        return {
            "summary": f"Edição expressa '{vu_meta['filename']}' ({vu_meta['size_human']}) salva em {p_meta['short_label']}!",
            "final_output": final_out,
        }

    async def generate_novela_script(
        self,
        *,
        theme: str,
        expressions: Optional[List[str]] = None,
        tone: str = "Comédia Leve",
        episode_badge: str = "🌵 NOVELINHA DO SERTÃO • EP. 01",
        visual_style: str = "stylized_3d_pixar",
        dramatic_structure: str = "viral_4_cuts",
    ) -> Dict[str, Any]:
        """Generate multi-character Northeast dialogue via AI cascade with v2.8 Dual-World & Viral 4-Cut pacing."""
        vstyle = _normalize_novela_visual_style(visual_style)
        dstruct = _normalize_novela_dramatic_structure(dramatic_structure)
        expr_list = [e.strip() for e in (expressions or []) if e and e.strip()]
        if not expr_list:
            expr_list = ["Oxente", "Morreu Maria Preá", "Botar Boneco", "Deixe de cunversa"]

        world_directive = (
            "ESTILO VISUAL: CINEMATOGRÁFICO REALISTA 8K LIVE-ACTION (pessoas reais em café/calçada nordestina iluminada, atuação contida de cinema/novela das 9). "
            if vstyle == "cinematic_live_action"
            else "ESTILO VISUAL: ANIMAÇÃO 3D PIXAR/CARTOON EXPRESSIVA (bodega nordestina colorida estilizada). "
        )
        structure_directive = (
            "ESTRUTURA OBRIGATÓRIA VIRAL 4 CORTES (v3.0):\n"
            "- Corte 1 (`attack_angry`, `lead_in_pause`: 0.0): Personagem A dispara provocação rápida (MÁX. 12 a 15 palavras, 2.0s a 3.4s, `over_shoulder`).\n"
            "- Corte 2 (`calm_inhale_comeback`, `lead_in_pause`: 0.12): Personagem B devolve uma invertida certeira (MÁX. 12 a 15 palavras, 2.0s a 3.4s, `reverse_over_shoulder`).\n"
            "- Corte 3 (`shock_what`, `lead_in_pause`: 0.0): Reação de espanto (MÁX. 12 palavras, `reaction_closeup`).\n"
            "- Corte 4 (`silent_shame_reaction`, `is_silent_reaction`: true, `silent_duration`: 2.0, `tts_text`: \"\", `subtitle_text`: \"\"): Close-up de reação de 2.0s voltando no Personagem A.\n"
            if dstruct == "viral_4_cuts"
            else "ESTRUTURA CLÁSSICA (3 CORTES RÁPIDOS): dialogue com 3 falas ágeis (máx. 12-15 palavras cada) alternando plano/contraplano.\n"
        )

        sys_prompt = (
            "Você é roteirista e diretor cinematográfico de micro-séries virais nordestinas para Reels/TikTok (10 a 15 segundos no total, corte seco plano/contraplano). "
            + world_directive
            + structure_directive
            + "Personagens fixos disponíveis: 'Dona Socorro', 'Seu Bastião', 'Enzo', 'Narrador'. "
            "REGRA DE DURAÇÃO & LEGENDA 100% FIEL: Cada fala deve ter NO MÁXIMO 12 a 15 palavras (2.0s a 3.4s). "
            "`subtitle_text` DEVE reproduzir EXATAMENTE 100% de todas as palavras de `tts_text` (PROIBIDO truncar ou resumir a 2ª frase!). "
            "REGRA CULTURAL INEGOCIÁVEL: é PROIBIDO usar gírias mineiras, paulistas ou gaúchas como 'trem', 'uai', 'sô', 'mano', 'meo', 'daora', 'guri', 'bah', 'tchê'. "
            "Use apenas vocabulário legítimo do Nordeste ('oxente', 'oxe', 'vixe', 'esse negócio', 'essa marmota', 'essa presepada', 'cabra', 'mangar', 'botar boneco', 'morreu Maria Preá', 'deixe de cunversa', 'arretado'). "
            "NUNCA alongue vogais repetidas ('Oxêêênte' é proibido; escreva 'Oxente!'). "
            "Responda EXCLUSIVAMENTE um JSON válido com as chaves:\n"
            "- title (string curta)\n"
            "- episode_badge (string)\n"
            "- setting (descrição curta em inglês do cenário externo iluminado com figurantes discretos ao fundo)\n"
            "- dictionary (array de 2 objetos {term, meaning} explicando as gírias usadas em português claro e engraçado)\n"
            "- dialogue (array de takes com: speaker, tts_text, subtitle_text, emotion [uma de: shocked, confused, laughing, proud, dramatic, shame, warm], "
            "camera_action [uma de: shake, whip_pan, zoom_in, dramatic_push], shot_framing [uma de: over_shoulder, reverse_over_shoulder, reaction_closeup], "
            "cut_role [attack_angry, calm_inhale_comeback, shock_what, silent_shame_reaction], lead_in_pause [float segundos], "
            "is_silent_reaction [boolean], silent_duration [float segundos], physical_action_en [ação física em inglês], expression_highlight [string])."
        )
        user_prompt = (
            f"Tema do episódio: {theme}\n"
            f"Expressões obrigatórias: {', '.join(expr_list)}\n"
            f"Tom de humor: {tone}\n"
            f"Selo do episódio: {episode_badge}\n"
            f"Estilo visual: {vstyle} | Estrutura: {dstruct}\n"
            "Entregue os cortes rigorosamente na cadência pedida."
        )
        raw_ai = await self._cascade_text(sys_prompt, user_prompt, 3200)
        parsed = _extract_json_block(raw_ai)
        fallback = build_fallback_novela_script(
            theme=theme,
            expressions=expr_list,
            tone=tone,
            visual_style=vstyle,
            dramatic_structure=dstruct,
        )
        if not parsed or not isinstance(parsed.get("dialogue"), list) or len(parsed["dialogue"]) < 2:
            fallback["source"] = "fallback_deterministic"
            return fallback

        cleaned_dialogue: List[Dict[str, Any]] = []
        default_framings = ["over_shoulder", "reverse_over_shoulder", "reaction_closeup", "reaction_closeup"]
        default_roles = ["attack_angry", "calm_inhale_comeback", "shock_what", "silent_shame_reaction"]
        for d_idx, row in enumerate(parsed["dialogue"][:6]):
            if not isinstance(row, dict):
                continue
            is_silent = bool(row.get("is_silent_reaction", False))
            if dstruct == "viral_4_cuts" and d_idx == 3 and (is_silent or not str(row.get("tts_text") or "").strip()):
                is_silent = True

            tts = "" if is_silent else _sanitize_northeast_text(str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or "").strip())
            sub_raw = "" if is_silent else _sanitize_northeast_text(str(row.get("subtitle_text") or tts).strip())
            if not is_silent and not tts:
                continue
            # Ensure subtitle_text never truncates spoken audio words
            sub = tts if (not sub_raw or len(sub_raw.split()) < len(tts.split())) else sub_raw

            framing = str(row.get("shot_framing") or default_framings[d_idx % len(default_framings)]).strip().lower()
            if framing not in ("over_shoulder", "reverse_over_shoulder", "reaction_closeup"):
                framing = default_framings[d_idx % len(default_framings)]

            cut_role = str(row.get("cut_role") or default_roles[min(d_idx, len(default_roles) - 1)]).strip().lower()
            if is_silent:
                cut_role = "silent_shame_reaction"
            elif dstruct == "viral_4_cuts" and d_idx == 1 and cut_role not in ("calm_inhale_comeback",):
                cut_role = "calm_inhale_comeback"

            try:
                lead_in = float(row.get("lead_in_pause") or 0.0)
            except (TypeError, ValueError):
                lead_in = 0.0
            if dstruct == "viral_4_cuts" and d_idx == 1:
                lead_in = 0.12 if (lead_in <= 0.0 or lead_in >= 0.35) else min(0.18, max(0.08, lead_in))

            try:
                silent_dur = float(row.get("silent_duration") or (2.0 if is_silent else 0.0))
            except (TypeError, ValueError):
                silent_dur = 2.0 if is_silent else 0.0
            if is_silent:
                silent_dur = max(1.8, min(2.2, silent_dur or 2.0))

            emotion = "shame" if is_silent else str(row.get("emotion") or "warm").strip().lower()
            cleaned_dialogue.append({
                "speaker": str(row.get("speaker") or "Dona Socorro").strip(),
                "tts_text": tts,
                "subtitle_text": sub,
                "emotion": emotion,
                "camera_action": "dramatic_push" if is_silent else str(row.get("camera_action") or "zoom_in").strip().lower(),
                "shot_framing": framing,
                "cut_role": cut_role,
                "lead_in_pause": round(lead_in, 2),
                "is_silent_reaction": is_silent,
                "silent_duration": round(silent_dur, 2),
                "physical_action_en": str(row.get("physical_action_en") or "").strip(),
                "expression_highlight": "" if is_silent else _sanitize_northeast_text(str(row.get("expression_highlight") or "").strip()),
            })
        if len(cleaned_dialogue) < 2:
            fallback["source"] = "fallback_deterministic"
            return fallback

        if dstruct == "viral_4_cuts":
            if len(cleaned_dialogue) >= 2:
                lp2 = float(cleaned_dialogue[1].get("lead_in_pause") or 0.0)
                cleaned_dialogue[1]["lead_in_pause"] = 0.12 if (lp2 <= 0.0 or lp2 >= 0.35) else round(min(0.18, max(0.08, lp2)), 2)
                cleaned_dialogue[1]["cut_role"] = "calm_inhale_comeback"
            if len(cleaned_dialogue) >= 3 and not any(x.get("is_silent_reaction") for x in cleaned_dialogue):
                cleaned_dialogue = cleaned_dialogue[:3]
                cleaned_dialogue.append({
                    "speaker": cleaned_dialogue[0]["speaker"],
                    "tts_text": "",
                    "subtitle_text": "",
                    "emotion": "shame",
                    "camera_action": "dramatic_push",
                    "shot_framing": "reaction_closeup",
                    "cut_role": "silent_shame_reaction",
                    "lead_in_pause": 0.0,
                    "is_silent_reaction": True,
                    "silent_duration": 2.0,
                    "physical_action_en": "freezes in silent mortified embarrassment, lowers gaze, swallows hard with sealed trembling lips and slumping shoulders",
                    "expression_highlight": "",
                })

        raw_dict = parsed.get("dictionary") if isinstance(parsed.get("dictionary"), list) else []
        cleaned_dict = [
            {"term": str(it.get("term") or "").strip(), "meaning": str(it.get("meaning") or "").strip()}
            for it in raw_dict
            if isinstance(it, dict) and str(it.get("term") or "").strip()
        ] or fallback["dictionary"]

        return {
            "title": str(parsed.get("title") or fallback["title"]).strip()[:90],
            "episode_badge": str(parsed.get("episode_badge") or episode_badge).strip()[:60],
            "visual_style": vstyle,
            "dramatic_structure": dstruct,
            "tone": tone,
            "setting": str(parsed.get("setting") or fallback["setting"]).strip()[:240],
            "dictionary": cleaned_dict[:3],
            "dialogue": cleaned_dialogue,
            "source": "cascade_ai",
        }

    async def _step_novela_regional_studio(
        self,
        step_idx: int,
        checkpoints: Dict[str, Any],
        params: Dict[str, Any],
        p_meta: Dict[str, Any],
    ) -> Dict[str, Any]:
        if step_idx == 0:
            raw_dlg = params.get("dialogue")
            theme = (params.get("theme") or "Enzo da capital confundindo gírias nordestinas na bodega").strip()
            raw_exprs = params.get("expressions")
            if isinstance(raw_exprs, str):
                expr_list = [x.strip() for x in raw_exprs.split(",") if x.strip()]
            elif isinstance(raw_exprs, list):
                expr_list = [str(x).strip() for x in raw_exprs if str(x).strip()]
            else:
                expr_list = ["Oxente", "Morreu Maria Preá", "Botar Boneco"]
            tone = (params.get("tone") or "Comédia Leve").strip()
            ep_badge = (params.get("episode_badge") or "🌵 NOVELINHA DO SERTÃO • EP. 01").strip()

            visual_style = _normalize_novela_visual_style(params.get("visual_style") or "stylized_3d_pixar")
            dramatic_structure = _normalize_novela_dramatic_structure(params.get("dramatic_structure") or "viral_4_cuts")
            if isinstance(raw_dlg, list) and len(raw_dlg) >= 1:
                fb_obj = build_fallback_novela_script(theme, expr_list, tone, visual_style, dramatic_structure)
                script_obj = {
                    "title": (params.get("title") or theme[:60]).strip(),
                    "episode_badge": ep_badge,
                    "visual_style": visual_style,
                    "dramatic_structure": dramatic_structure,
                    "tone": tone,
                    "setting": (params.get("setting") or fb_obj["setting"]),
                    "dictionary": params.get("dictionary") or fb_obj["dictionary"],
                    "dialogue": raw_dlg,
                    "source": "user_edited",
                }
            else:
                script_obj = await self.generate_novela_script(
                    theme=theme,
                    expressions=expr_list,
                    tone=tone,
                    episode_badge=ep_badge,
                    visual_style=visual_style,
                    dramatic_structure=dramatic_structure,
                )
            return {
                "summary": f"Roteiro '{script_obj['title']}' pronto ({len(script_obj['dialogue'])} takes · {visual_style} · {dramatic_structure}, fonte: {script_obj.get('source', 'ai')}).",
                **script_obj,
            }

        if not self._run_novela_cli:
            raise RuntimeError("Executor run_novela_cli_fn não configurado no orquestrador")

        cp0 = checkpoints.get("0") or {}
        visual_style = _normalize_novela_visual_style(params.get("visual_style") or cp0.get("visual_style") or "stylized_3d_pixar")
        dramatic_structure = _normalize_novela_dramatic_structure(params.get("dramatic_structure") or cp0.get("dramatic_structure") or "viral_4_cuts")

        clean_cinema = bool(params.get("clean_cinema_mode", True))
        subtitles_mode = str(params.get("subtitles_mode") or ("none" if clean_cinema else "minimal")).strip().lower()
        render_mode = str(params.get("render_mode") or "vps_preview").strip().lower()
        colab_engine = str(params.get("colab_engine") or "liveportrait_latentsync").strip().lower()
        accent_engine = str(params.get("accent_engine") or "nordestino_raiz").strip().lower()

        nov_meta = await self._run_novela_cli(
            title=cp0.get("title") or params.get("title") or "Causos do Sertão — Ep. 01",
            episode_badge=cp0.get("episode_badge") or params.get("episode_badge") or "🌵 NOVELINHA DO SERTÃO • EP. 01",
            visual_style=visual_style,
            dramatic_structure=dramatic_structure,
            tone=str(cp0.get("tone") or params.get("tone") or "Comédia Leve").strip(),
            platform_preset=p_meta["id"],
            setting=cp0.get("setting") or "",
            dictionary=cp0.get("dictionary") or [],
            dialogue=cp0.get("dialogue") or [],
            quality=str(params.get("quality") or "standard"),
            clean_cinema_mode=clean_cinema,
            subtitles_mode=subtitles_mode,
            render_mode=render_mode,
            colab_engine=colab_engine,
            accent_engine=accent_engine,
        )
        final_out = {
            **nov_meta,
            "hard_mode_engine": "novela",
            "hard_mode_payload": {
                "title": cp0.get("title") or "",
                "episode_badge": cp0.get("episode_badge") or "",
                "visual_style": visual_style,
                "dramatic_structure": dramatic_structure,
                "platform_preset": p_meta["id"],
                "dictionary": cp0.get("dictionary") or [],
                "dialogue": cp0.get("dialogue") or [],
                "clean_cinema_mode": clean_cinema,
                "subtitles_mode": subtitles_mode,
                "render_mode": render_mode,
                "colab_engine": colab_engine,
                "accent_engine": accent_engine,
            },
        }
        return {
            "summary": (
                f"Pack Google Flow Veo 3.1 pronto ({nov_meta.get('total_takes', 0)} takes, "
                f"{nov_meta.get('total_credits_lite', 0)} créd Lite)!"
                if render_mode == "flow_pack"
                else f"Episódio Clean Cinema '{nov_meta.get('filename')}' ({nov_meta.get('visual_style')}, {nov_meta.get('size_human', '')}) finalizado!"
            ),
            "final_output": final_out,
        }
