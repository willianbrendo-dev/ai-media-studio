#!/usr/bin/env python3
"""VPS Novela Studio Runner — Cinema Clean Engine v2.5.

Zero-Idle-RAM Multi-Character Micro-Series Engine supporting:
  1. Clean Cinema Storyboard Preview (`clean_cinema_mode=True` default):
     Full-bleed 1080x1920 Shot-Reverse-Shot / Over-the-Shoulder cinematography with fast
     dry cuts and zero cluttering overlay boxes (`subtitles_mode`: 'none' | 'minimal').
  2. Google Flow Veo 3.1 Production Pack (`generate_flow_veo_pack`):
     Generates clean 1080x1920 Shot-Reverse-Shot reference Ingredients + bilingual technical
     Veo 3.1 prompts (English camera direction + Brazilian Portuguese dialogue) + Flow Pro credit math.
  3. Automatic Take Stitcher (`stitch_novela_takes`):
     Uses faster-whisper INT8 word timestamps + FFmpeg silencedetect to trim only silent lead/tail
     from uploaded Google Flow / Colab MP4 takes without swallowing syllables, normalizes to
     1080x1920 @ 30fps + 48kHz Stereo SoXR (loudnorm I=-16), and optionally burns minimal captions.
  4. Google Colab GPU Bridge (`render_mode='colab_gpu'`):
     Sends each (Shot Ingredient + PT-BR WAV + Acting Prompt) to remote EchoMimicV3 / LatentSync
     FastAPI Cloudflare tunnel (`/animate-take`) and stitches returned lip-synced MP4 clips.
"""

from __future__ import annotations

import argparse
import base64
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_THIS_DIR = str(Path(__file__).resolve().parent)
if _THIS_DIR not in sys.path:
    sys.path.insert(0, _THIS_DIR)
import regional_voice_engine as rve  # noqa: E402
import novela_lipsync_animator as nla  # noqa: E402
import cinema_v4_director as cv4  # noqa: E402


def _resolve_cinema_engine(cfg: Optional[Dict[str, Any]] = None) -> str:
    raw = ""
    if isinstance(cfg, dict):
        raw = str(cfg.get("cinema_engine") or "").strip().lower()
    if not raw:
        raw = str(os.environ.get("NOVELA_CINEMA_ENGINE_DEFAULT", "v3.1")).strip().lower()
    return "v4" if raw in ("v4", "v4.0", "cinema_v4", "cinema") else "v3.1"


for _env_k, _env_v in (("OMP_NUM_THREADS", "2"), ("MKL_NUM_THREADS", "2"), ("CT2_NUM_THREADS", "2")):
    os.environ.setdefault(_env_k, _env_v)

SUPOCLIP_HF_CACHE = Path("/opt/ai-media-studio/supoclip/.cache/huggingface")
if SUPOCLIP_HF_CACHE.exists() and not os.environ.get("HF_HOME"):
    os.environ["HF_HOME"] = str(SUPOCLIP_HF_CACHE)

# ---------------------------------------------------------------------------
# Runtime binaries & paths
# ---------------------------------------------------------------------------
STUDIO_BASE = Path(os.environ.get("STUDIO_BASE_DIR", "/opt/ai-media-studio"))
HYPERFRAMES_DIR = Path(os.environ.get("HYPERFRAMES_DIR", str(STUDIO_BASE / "tools/hyperframes")))
HF_BIN = os.environ.get("HF_BIN", str(HYPERFRAMES_DIR / "node_modules/.bin/hyperframes"))
GSAP_VENDOR = Path(os.environ.get("GSAP_VENDOR", str(HYPERFRAMES_DIR / "vendor/gsap.min.js")))
EDGE_TTS_BIN = os.environ.get(
    "EDGE_TTS_BIN",
    "/opt/ai-media-studio/tools/video-use/.venv/bin/edge-tts",
)
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"
NOVELA_EXPORT_DIR = STUDIO_BASE / "exports/novela"
FLOW_PACKS_DIR = NOVELA_EXPORT_DIR / "flow_packs"

_WHISPER_CACHE: Dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Safe-Zone geometry per platform (1080x1920 vertical canvas)
# ---------------------------------------------------------------------------
PLATFORM_SAFE_ZONES: Dict[str, Dict[str, Any]] = {
    "tiktok_reels": {
        "label": "TikTok / Reels 9:16",
        "width": 1080,
        "height": 1920,
        "safe_top": 170,
        "safe_bottom": 1460,
        "safe_left": 56,
        "safe_right": 900,
    },
    "yt_shorts": {
        "label": "YouTube Shorts 9:16",
        "width": 1080,
        "height": 1920,
        "safe_top": 160,
        "safe_bottom": 1470,
        "safe_left": 56,
        "safe_right": 900,
    },
    "shopee_20_9": {
        "label": "Shopee Video 20:9 Anti-Crop",
        "width": 1080,
        "height": 1920,
        "safe_top": 225,
        "safe_bottom": 1150,
        "safe_left": 60,
        "safe_right": 890,
    },
}


# ---------------------------------------------------------------------------
# Cast Registry (Voices, Character Locks & Veo 3.1 Descriptions)
# ---------------------------------------------------------------------------
CAST_REGISTRY: Dict[str, Dict[str, str]] = {
    "dona_socorro": {
        "label": "Dona Socorro",
        "role": "Matriarca dramática e fofoqueira",
        "voice": "pt-BR-FranciscaNeural",
        "rate": "+16%",
        "pitch": "+6Hz",
        "badge_bg": "#f59e0b",
        "badge_fg": "#111827",
        "accent": "#fde047",
        "shirt_color": "#eab308",
        "skin_tone": "#d8a47f",
        "hair_color": "#cbd5e1",
        "prop": "glasses",
        "lock_3d": (
            "cheerful expressive 65-year-old senior Brazilian lady named Socorro wearing a bright yellow floral cotton apron "
            "over a teal blouse, small pearl earrings, thin-rimmed tortoiseshell eyeglasses resting on her nose, "
            "silver hair in a neat bun, arms relaxed naturally down at her sides"
        ),
        "lock_tv": (
            "photorealistic 62-year-old Brazilian woman named Socorro wearing a tasteful teal linen blouse and yellow apron, "
            "small pearl earrings, realistic thin eyeglasses, silver-streaked hair pinned back neatly, natural skin texture, "
            "calm posture with arms resting naturally down"
        ),
        "veo_desc": (
            "Dona Socorro, an expressive 60-year-old Northeastern Brazilian matriarch wearing a yellow floral apron "
            "over a teal blouse, pearl earrings, round tortoiseshell glasses, silver hair in a neat bun"
        ),
        "veo_live": (
            "a real 60-year-old Brazilian woman with expressive dark eyes, neat silver-streaked bun, thin eyeglasses, "
            "wearing a crisp teal linen blouse"
        ),
    },
    "seu_bastiao": {
        "label": "Seu Bastião",
        "role": "Dono da mercearia, sábio e debochado",
        "voice": "pt-BR-AntonioNeural",
        "rate": "+10%",
        "pitch": "-6Hz",
        "badge_bg": "#10b981",
        "badge_fg": "#052e16",
        "accent": "#6ee7b7",
        "shirt_color": "#2563eb",
        "skin_tone": "#c68b65",
        "hair_color": "#94a3b8",
        "prop": "straw_hat",
        "lock_3d": (
            "jovial 68-year-old countryside shopkeeper gentleman wearing a woven light straw brimmed hat, "
            "open unbuttoned blue plaid shirt over a white tee, "
            "neatly trimmed short gray mustache positioned above a clearly visible upper lip and expressive mouth, arms relaxed naturally down"
        ),
        "lock_tv": (
            "photorealistic 65-year-old Brazilian gentleman wearing a well-ironed open blue plaid cotton shirt over a white tee, "
            "neatly trimmed short gray mustache positioned above a clearly visible upper lip and expressive mouth, "
            "natural tanned skin pores and realistic wrinkles, arms resting comfortably down"
        ),
        "veo_desc": (
            "Seu Bastião, a witty 65-year-old countryside shopkeeper wearing a woven straw hat and open blue plaid shirt "
            "over a white cotton tee, neatly trimmed short gray mustache positioned above a clearly visible upper lip and expressive mouth"
        ),
        "veo_live": (
            "a real 65-year-old Brazilian man with weathered warm face, neatly trimmed short gray mustache positioned above a clearly visible upper lip and expressive mouth, wearing an open blue plaid linen shirt"
        ),
    },
    "enzo": {
        "label": "Enzo",
        "role": "Sobrinho da capital todo confuso",
        "voice": "pt-BR-AntonioNeural",
        "rate": "+4%",
        "pitch": "+8Hz",
        "badge_bg": "#a855f7",
        "badge_fg": "#ffffff",
        "accent": "#d8b4fe",
        "shirt_color": "#9333ea",
        "skin_tone": "#e0b494",
        "hair_color": "#1e293b",
        "prop": "phone_sunglasses",
        "lock_3d": (
            "22-year-old Brazilian city young man wearing a trendy oversized lavender hoodie, "
            "dark sunglasses pushed up onto wavy dark hair, arms resting calmly at his sides"
        ),
        "lock_tv": (
            "photorealistic 23-year-old young Brazilian urban man wearing a minimalist lavender casual crewneck sweater, "
            "natural dark curly hair, clear skin pores, calm relaxed posture with arms resting naturally down"
        ),
        "veo_desc": (
            "Enzo, a 22-year-old Brazilian city guy wearing an oversized lavender hoodie with sunglasses pushed up onto dark curly hair"
        ),
        "veo_live": (
            "a real 23-year-old Brazilian young man with curly dark hair in a clean lavender crewneck sweater"
        ),
    },
    "narrador": {
        "label": "Narrador",
        "role": "Contador de causos do sertão",
        "voice": "pt-BR-ThalitaMultilingualNeural",
        "rate": "+6%",
        "pitch": "-2Hz",
        "badge_bg": "#38bdf8",
        "badge_fg": "#082f49",
        "accent": "#bae6fd",
        "shirt_color": "#0284c7",
        "skin_tone": "#d2996c",
        "hair_color": "#0f172a",
        "prop": "village_wide",
        "lock_3d": (
            "charming Brazilian character standing calmly outdoors with arms relaxed down, warm golden sunlight, no text"
        ),
        "lock_tv": (
            "photorealistic Brazilian bystander in golden hour natural sunlight, calm relaxed arms down posture, 85mm shallow depth of field, no text"
        ),
        "veo_desc": (
            "A lively village observer on the sunlit Northeastern Brazilian cobblestone corner sidewalk with bystanders watching"
        ),
        "veo_live": (
            "a real Brazilian observer reacting on a sunlit urban cafe sidewalk with blurred passersby"
        ),
    },
}

SPEAKER_ALIASES: Dict[str, str] = {
    "dona socorro": "dona_socorro",
    "socorro": "dona_socorro",
    "dona_socorro": "dona_socorro",
    "seu bastiao": "seu_bastiao",
    "seu bastião": "seu_bastiao",
    "bastiao": "seu_bastiao",
    "bastião": "seu_bastiao",
    "seu_bastiao": "seu_bastiao",
    "enzo": "enzo",
    "narrador": "narrador",
    "narradora": "narrador",
}

def normalize_visual_style(raw: str) -> str:
    """Normalize visual style across v2.5/v2.8 names into 'cinematic_live_action' or 'stylized_3d_pixar'."""
    val = (raw or "stylized_3d_pixar").strip().lower()
    if val in ("cinematic_live_action", "cinematic_tv", "live_action", "realistic", "cinema_8k"):
        return "cinematic_live_action"
    return "stylized_3d_pixar"


def normalize_dramatic_structure(raw: str) -> str:
    """Normalize dramatic directing structure ('viral_4_cuts' or 'classic_3_cuts')."""
    val = (raw or "viral_4_cuts").strip().lower()
    if val in ("classic_3_cuts", "3_cuts", "classic"):
        return "classic_3_cuts"
    return "viral_4_cuts"


EMOTION_DIRECTIONS: Dict[str, Tuple[str, str, str]] = {
    # (flux_desc [STRICTLY positive closed-lips & arms-down phrasing], default_cam, veo_acting)
    "shocked": (
        "focused wide-eyed attentive gaze, subtly lifted eyebrows, firmly closed resting lips together, calm poised relaxed arms down",
        "shake",
        "widens eyes in shock and leans slightly forward with dynamic hand gesture",
    ),
    "confused": (
        "slight skeptical head tilt, inquisitive raised brow, composed lips gently closed together, relaxed lowered shoulders",
        "zoom_in",
        "blinks twice and tilts head sideways in puzzled disbelief",
    ),
    "laughing": (
        "crinkling warm eyes with confident knowing gaze, closed gentle relaxed lips, calm lowered arms",
        "whip_pan",
        "chuckles warmly with a knowing smirk and expressive shoulder movement",
    ),
    "proud": (
        "poised upright posture, calm confident expression, lips gently closed together, relaxed arms at sides",
        "zoom_in",
        "takes a calm breath, lifts chin with poise and delivers a sharp comeback",
    ),
    "dramatic": (
        "intense focused eyes, subtle furrowed brow, composed lips gently closed together, relaxed natural arms down",
        "dramatic_push",
        "tightens expression dramatically before reacting",
    ),
    "shame": (
        "humbled lowered gaze looking slightly downward, lips gently closed together in quiet embarrassment, relaxed lowered shoulders",
        "dramatic_push",
        "swallows hard silently, shoulders slump downward, brows knit in pure embarrassment with zero speech",
    ),
    "warm": (
        "friendly attentive gaze, relaxed lips gently closed together, calm posture with arms down",
        "zoom_in",
        "nods warmly and speaks with lively natural gestures",
    ),
    "happiness": (
        "bright expressive wide-open eyes with crisp catchlights, gentle pleasant smile with relaxed closed lips ready for dubbing, calm posture with arms down",
        "zoom_in",
        "smiles warmly with joyful lively head movement and bright eyes",
    ),
}

FRONTAL_SINGLE_SUBJECT_DIRECTIVE = (
    "single character only (strictly one person in frame, no second person, no foreground shoulder), "
    "facing the camera directly with direct frontal eye-contact into the lens, "
    "eyes wide open and bright with clear catchlights, warm expressive natural face"
)

SHOT_FRAMINGS: Dict[str, Dict[str, str]] = {
    "establishing": {
        "label": "Plano Aberto · Establishing Frontal",
        "lens_mm": "35mm",
        "flux_cue": f"medium-wide 35mm vertical 9:16 cinema shot of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Medium-wide 35mm cinema shot of speaker, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, natural handheld breathing",
    },
    "medium": {
        "label": "Plano Médio · Frontal Direto",
        "lens_mm": "85mm",
        "flux_cue": f"medium close-up 85mm vertical 9:16 cinema portrait of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Medium close-up 85mm cinema shot of speaker, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, natural handheld breathing",
    },
    "over_shoulder": {
        "label": "Plano · Frontal Limpo (Single-Subject)",
        "lens_mm": "85mm",
        "flux_cue": f"frontal 85mm medium close-up vertical 9:16 portrait of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Frontal 85mm medium close-up shot of speaker, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, natural handheld breathing",
    },
    "reverse_over_shoulder": {
        "label": "Contraplano · Frontal Limpo",
        "lens_mm": "85mm",
        "flux_cue": f"reverse-cut frontal 85mm medium close-up vertical 9:16 portrait of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Reverse-cut frontal 85mm medium close-up shot of speaker, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, cutting cleanly",
    },
    "close_up": {
        "label": "Close-Up · Frontal +14%",
        "lens_mm": "85mm",
        "flux_cue": f"tight 85mm close-up portrait framed from chest up of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Tight 85mm close-up shot, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, natural handheld breathing",
    },
    "reaction_closeup": {
        "label": "Close-Up · Reação Frontal",
        "lens_mm": "85mm",
        "flux_cue": f"tight 85mm reaction close-up portrait framed from chest up of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Tight reaction close-up shot, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, natural handheld breathing",
    },
    "reaction": {
        "label": "Reação Cômica · Frontal",
        "lens_mm": "85mm",
        "flux_cue": f"tight 85mm comic reaction close-up portrait framed from chest up of {{speaker_en}}, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}",
        "veo_cue": f"Tight comic reaction close-up shot, {FRONTAL_SINGLE_SUBJECT_DIRECTIVE}, lively smiling reaction",
    },
}


@dataclass
class TurnCompiled:
    index: int
    speaker_key: str
    speaker_label: str
    role: str
    badge_bg: str
    badge_fg: str
    accent: str
    tts_text: str
    subtitle_text: str
    emotion: str
    camera_action: str
    shot_framing: str
    physical_action_en: str
    highlight_phrase: str
    audio_wav: Path
    speech_dur: float
    slot_start: float
    slot_dur: float
    shot_relpath: str
    shot_abs_path: Path
    asset_source: str
    voice_engine_used: str = "nordestino_raiz"
    animation_engine_used: str = ""
    lipsync_peak_open: float = 0.0
    cut_role: str = "dialogue"
    lead_in_pause: float = 0.0
    is_silent_reaction: bool = False


def _normalize_speaker_key(raw: str) -> str:
    clean = re.sub(r"\s+", " ", (raw or "").strip().lower())
    if clean in SPEAKER_ALIASES:
        return SPEAKER_ALIASES[clean]
    if "socorro" in clean:
        return "dona_socorro"
    if "bast" in clean:
        return "seu_bastiao"
    if "enzo" in clean:
        return "enzo"
    if "narr" in clean:
        return "narrador"
    return re.sub(r"[^a-z0-9]+", "_", clean).strip("_") or "narrador"


def _get_character_spec(speaker_raw: str) -> Tuple[str, Dict[str, str]]:
    key = _normalize_speaker_key(speaker_raw)
    if key in CAST_REGISTRY:
        spec = dict(CAST_REGISTRY[key])
        if key == "seu_bastiao":
            spec["label"] = "Seu Bastião"
        return key, spec
    palette = [("#fb7185", "#ffffff"), ("#f97316", "#111827"), ("#22c55e", "#052e16")]
    bg, fg = palette[abs(hash(key)) % len(palette)]
    display = speaker_raw.strip() or "Personagem"
    return key, {
        "label": display,
        "role": "Participação especial",
        "voice": "pt-BR-FranciscaNeural" if display.lower().endswith("a") else "pt-BR-AntonioNeural",
        "rate": "+8%",
        "pitch": "+0Hz",
        "badge_bg": bg,
        "badge_fg": fg,
        "accent": "#fde047",
        "shirt_color": bg,
        "skin_tone": "#d6a07a",
        "hair_color": "#1e293b",
        "prop": "none",
        "lock_3d": f"friendly expressive Brazilian character named {display}, calm arms resting down",
        "lock_tv": f"photorealistic Brazilian human actor named {display}, natural skin texture, calm posture with arms down, 85mm cinema lens",
        "veo_desc": f"{display}, a spirited Brazilian character outdoors in warm natural daylight",
        "veo_live": f"{display}, a real charismatic Brazilian person in 8K live-action natural lighting",
    }


def compile_dramatic_cuts(
    raw_dialogue: List[Dict[str, Any]],
    dramatic_structure: str = "viral_4_cuts",
) -> List[Dict[str, Any]]:
    """Structure raw turns into either 4 viral dramatic cuts (0.12s Cut-2 pause + Cut-4 tone-matched reaction) or classic dialogue."""
    structure = normalize_dramatic_structure(dramatic_structure)
    turns = [dict(t) for t in (raw_dialogue or []) if isinstance(t, dict)]
    if not turns:
        return []

    def _is_react(row: Dict[str, Any]) -> bool:
        return bool(row.get("is_silent_reaction") or row.get("is_reaction"))

    def _sync_sub_full(row: Dict[str, Any]) -> None:
        if _is_react(row):
            return
        tts = rve.sanitize_northeast_slang(str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or "").strip())
        sub = rve.sanitize_northeast_slang(str(row.get("subtitle_text") or tts).strip())
        row["tts_text"] = tts
        # v3.0: Never let subtitle_text drop the second sentence/half of spoken dialogue
        row["subtitle_text"] = tts if (not sub or len(sub.split()) < len(tts.split())) else sub

    if structure == "classic_3_cuts":
        out = []
        for idx, t in enumerate(turns):
            if not str(t.get("tts_text") or t.get("subtitle_text") or t.get("text") or "").strip() and not _is_react(t):
                continue
            _sync_sub_full(t)
            t.setdefault("cut_role", "dialogue")
            t.setdefault("lead_in_pause", 0.0)
            t["is_silent_reaction"] = _is_react(t)
            out.append(t)
        return out

    # viral_4_cuts ("Quem Passou Vergonha"):
    # Cut 1: Attack (Speaker A) -> Cut 2: 0.12s quick beat + Comeback (Speaker B) -> Cut 3: Shock -> Cut 4: Reaction (Speaker A)
    spoken = [t for t in turns if not _is_react(t) and str(t.get("tts_text") or t.get("subtitle_text") or t.get("text") or "").strip()]
    silent_existing = next((t for t in turns if _is_react(t)), None)
    if not spoken:
        return turns

    cut1 = dict(spoken[0])
    _sync_sub_full(cut1)
    cut1["cut_role"] = str(cut1.get("cut_role") or "attack_angry")
    cut1["lead_in_pause"] = float(cut1.get("lead_in_pause") or 0.0)
    cut1["is_silent_reaction"] = False
    cut1.setdefault("shot_framing", "over_shoulder")

    result = [cut1]
    if len(spoken) >= 2:
        cut2 = dict(spoken[1])
        _sync_sub_full(cut2)
        cut2["cut_role"] = str(cut2.get("cut_role") or "calm_inhale_comeback")
        raw_lp = float(cut2.get("lead_in_pause") or 0.0)
        cut2["lead_in_pause"] = 0.12 if (raw_lp <= 0.0 or raw_lp >= 0.35) else round(min(0.18, max(0.08, raw_lp)), 2)
        cut2["is_silent_reaction"] = False
        cut2.setdefault("shot_framing", "reverse_over_shoulder")
        result.append(cut2)

    if len(spoken) >= 3:
        cut3 = dict(spoken[2])
        _sync_sub_full(cut3)
        cut3["cut_role"] = str(cut3.get("cut_role") or "shock_what")
        cut3["lead_in_pause"] = 0.0
        cut3["is_silent_reaction"] = False
        cut3.setdefault("shot_framing", "reaction_closeup")
        result.append(cut3)
    elif len(spoken) == 2:
        cut3 = {
            "speaker": "Enzo" if _normalize_speaker_key(str(cut1.get("speaker") or "")) != "enzo" else "Seu Bastião",
            "tts_text": "O quê?! Eita peste!",
            "subtitle_text": "O quê?! Eita peste!",
            "emotion": "shocked",
            "shot_framing": "reaction_closeup",
            "cut_role": "shock_what",
            "lead_in_pause": 0.0,
            "is_silent_reaction": False,
        }
        result.append(cut3)

    # Cut 4: End with Reaction Shot back on Cut 1's character!
    if silent_existing:
        cut4 = dict(silent_existing)
    else:
        cut4 = {
            "speaker": cut1.get("speaker") or "Dona Socorro",
            "tts_text": "",
            "subtitle_text": "",
            "emotion": "happiness",
            "camera_action": "dramatic_push",
            "shot_framing": "reaction",
        }
    cut4["cut_role"] = str(cut4.get("cut_role") or "silent_shame_reaction")
    cut4["is_silent_reaction"] = True
    cut4["reaction_phrase"] = str(cut4.get("reaction_phrase") or cut4.get("tts_text") or "").strip()
    cut4["tts_text"] = ""
    cut4["subtitle_text"] = ""
    cut4["lead_in_pause"] = 0.0
    cut4["silent_duration"] = float(cut4.get("silent_duration") or 2.0)
    cut4["emotion"] = str(cut4.get("emotion") or "happiness")
    cut4["shot_framing"] = str(cut4.get("shot_framing") or "reaction")
    result.append(cut4)
    return result


def _assign_shot_framing(
    turn_idx: int,
    raw_framing: str,
    emotion: str,
    prev_speaker: Optional[str],
    cur_speaker: str,
) -> str:
    clean = (raw_framing or "").strip().lower()
    if clean in SHOT_FRAMINGS:
        return clean
    if turn_idx == 0:
        return "over_shoulder"
    if emotion in ("dramatic", "shocked") and turn_idx >= 2:
        return "reaction_closeup"
    if prev_speaker and cur_speaker != prev_speaker:
        return "reverse_over_shoulder" if (turn_idx % 2 == 1) else "over_shoulder"
    cycle = ("over_shoulder", "reverse_over_shoulder", "reaction_closeup")
    return cycle[turn_idx % len(cycle)]


def _load_secrets_env() -> Dict[str, str]:
    env_map = dict(os.environ)
    for p in (
        STUDIO_BASE / "media-control-panel/.env",
        STUDIO_BASE / ".env",
        Path("/opt/ai-media-studio/media-control-panel/.env"),
    ):
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                s = line.strip()
                if not s or s.startswith("#") or "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                if k and k not in env_map:
                    env_map[k] = v.strip().strip("\"'")
    for vault_file in (
        STUDIO_BASE / "media-control-panel/vault.json",
        STUDIO_BASE / "media-control-panel/data/vault.json",
    ):
        if vault_file.is_file():
            try:
                vault = json.loads(vault_file.read_text(encoding="utf-8"))
                for k, val in vault.items():
                    if isinstance(val, str) and val.strip() and not env_map.get(k):
                        env_map[k] = val.strip()
            except Exception:
                pass
    return env_map


def _run_checked(cmd: List[str], cwd: Optional[Path] = None, timeout: int = 300) -> str:
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"Command failed (exit {proc.returncode}): {' '.join(cmd[:6])}\n"
            f"STDOUT: {proc.stdout[-800:]}\nSTDERR: {proc.stderr[-1000:]}"
        )
    return proc.stdout


def _probe_media(media_path: Path) -> Dict[str, Any]:
    out = _run_checked([
        FFPROBE_BIN,
        "-v", "error",
        "-show_format",
        "-show_streams",
        "-of", "json",
        str(media_path),
    ], timeout=25)
    data = json.loads(out)
    streams = data.get("streams") or []
    v_stream = next((s for s in streams if s.get("codec_type") == "video"), None)
    a_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)
    dur = float((data.get("format") or {}).get("duration") or 0.0)
    if dur <= 0 and v_stream:
        dur = float(v_stream.get("duration") or 0.0)
    w = int(v_stream.get("width", 1080)) if v_stream else 1080
    h = int(v_stream.get("height", 1920)) if v_stream else 1920
    return {
        "duration": max(0.3, dur),
        "width": w + (w % 2),
        "height": h + (h % 2),
        "has_audio": a_stream is not None,
    }


def _probe_duration(media_path: Path) -> float:
    return _probe_media(media_path)["duration"]


def _synthesize_turn_audio(
    tts_text: str,
    voice: str,
    rate: str,
    pitch: str,
    raw_mp3: Path,
    clean_wav: Path,
    lead_pad: float = 0.03,
    tail_pad: float = 0.09,
) -> float:
    """Synthesize edge-tts voice clip with rapid clean cinema padding into 48kHz stereo WAV."""
    clean_text = (tts_text or "...").strip()
    cmd = [
        EDGE_TTS_BIN,
        "--voice", voice,
        f"--rate={rate}",
        f"--pitch={pitch}",
        "--text", clean_text,
        "--write-media", str(raw_mp3),
    ]
    last_err: Optional[Exception] = None
    for attempt in range(3):
        try:
            _run_checked(cmd, timeout=45)
            if raw_mp3.exists() and raw_mp3.stat().st_size > 800:
                break
        except Exception as exc:
            last_err = exc
            time.sleep(0.8 * (attempt + 1))
    else:
        raise RuntimeError(f"edge-tts failed for '{clean_text[:40]}': {last_err}")

    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-t", f"{lead_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-i", str(raw_mp3),
        "-f", "lavfi", "-t", f"{tail_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex",
        "[1:a]aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo,highpass=f=75,acompressor=threshold=-16dB:ratio=2.5:attack=10:release=100[sp];"
        "[0:a][sp][2:a]concat=n=3:v=0:a=1[outa]",
        "-map", "[outa]",
        "-c:a", "pcm_s16le",
        str(clean_wav),
    ], timeout=60)
    return _probe_duration(clean_wav)


def _get_qt():
    hf_dir = str(Path(__file__).resolve().parent)
    if hf_dir not in sys.path:
        sys.path.insert(0, hf_dir)
    try:
        from quota_telemetry import get_tracker
        return get_tracker()
    except Exception:
        return None


def _call_nvidia_flux_image(
    prompt: str,
    api_key: str,
    out_jpg: Path,
    model: str = "black-forest-labs/flux.1-dev",
) -> Tuple[bool, str]:
    """Generate a clean vertical 9:16 portrait/scene image via NVIDIA NIM Flux API."""
    if not api_key:
        return False, "no_nvidia_key"
    qt = _get_qt()
    url = f"https://ai.api.nvidia.com/v1/genai/{model}"
    payload = {
        "prompt": prompt,
        "width": 768,
        "height": 1344,
        "steps": 22 if "dev" in model else 4,
    }
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=95) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        arts = data.get("artifacts") or []
        if not arts:
            if qt is not None:
                qt.record_call(model, ok=False, status_code=500, specialty="image", error="empty_artifacts")
            return False, "empty_artifacts"
        art = arts[0]
        finish = str(art.get("finishReason") or "SUCCESS").upper()
        if finish == "CONTENT_FILTERED":
            if qt is not None:
                qt.record_call(model, ok=False, status_code=400, specialty="image", error="content_filtered")
            return False, "content_filtered"
        raw = base64.b64decode(art.get("base64") or "")
        if len(raw) < 20000:
            if qt is not None:
                qt.record_call(model, ok=False, status_code=500, specialty="image", error=f"tiny_{len(raw)}")
            return False, f"tiny_image_{len(raw)}"
        tmp_raw = out_jpg.with_suffix(".raw.jpg")
        tmp_raw.write_bytes(raw)
        _run_checked([
            FFMPEG_BIN, "-y", "-i", str(tmp_raw),
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920",
            "-q:v", "2",
            str(out_jpg),
        ], timeout=30)
        tmp_raw.unlink(missing_ok=True)
        if qt is not None:
            qt.record_call(model, ok=True, status_code=200, specialty="image")
        return True, model
    except urllib.error.HTTPError as e:
        if qt is not None:
            qt.record_call(model, ok=False, status_code=e.code, specialty="image", error=f"http_{e.code}")
        return False, f"http_{e.code}"
    except Exception as exc:
        if qt is not None:
            qt.record_call(model, ok=False, status_code=500, specialty="image", error=type(exc).__name__)
        return False, f"err_{type(exc).__name__}"


def _build_bespoke_svg_portrait(
    speaker_key: str,
    spec: Dict[str, str],
    visual_style: str,
    emotion: str,
    shot_framing: str,
    out_svg: Path,
    clean_no_text: bool = True,
) -> None:
    """Clean procedural vector stage artwork (zero overlaid UI plaques when clean_no_text=True)."""
    is_3d = visual_style == "3d_cartoon"
    bg_top = "#0ea5e9" if is_3d else "#78350f"
    bg_mid = "#f59e0b" if is_3d else "#b45309"
    bg_bot = "#451a03" if is_3d else "#1c1917"
    shirt = spec.get("shirt_color", "#eab308")
    skin = spec.get("skin_tone", "#d8a47f")
    hair = spec.get("hair_color", "#94a3b8")

    # Shift subject offset subtly by shot_framing to mimic Shot-Reverse-Shot angle
    cx_shift = -48 if shot_framing == "over_shoulder" else (48 if shot_framing == "reverse_over_shoulder" else 0)
    cx = 540 + cx_shift

    mouth_path = (
        f"M {cx-60} 835 Q {cx} 890 {cx+60} 835" if emotion in ("laughing", "warm", "proud")
        else f"M {cx-30} 845 Q {cx} 875 {cx+30} 845 Q {cx} 825 {cx-30} 845" if emotion in ("shocked", "dramatic")
        else f"M {cx-45} 848 Q {cx} 830 {cx+45} 852"
    )
    prop_svg = ""
    if spec.get("prop") == "straw_hat":
        prop_svg = (
            f'<ellipse cx="{cx}" cy="495" rx="260" ry="44" fill="#eab308" stroke="#92400e" stroke-width="8"/>'
            f'<path d="M {cx-160} 495 C {cx-145} 360 {cx+145} 360 {cx+160} 495 Z" fill="#facc15" stroke="#92400e" stroke-width="8"/>'
            f'<rect x="{cx-155}" y="468" width="310" height="22" rx="8" fill="#b91c1c"/>'
        )
    elif spec.get("prop") == "glasses":
        prop_svg = (
            f'<circle cx="{cx-65}" cy="705" r="46" fill="none" stroke="#451a03" stroke-width="10"/>'
            f'<circle cx="{cx+65}" cy="705" r="46" fill="none" stroke="#451a03" stroke-width="10"/>'
            f'<line x1="{cx-19}" y1="705" x2="{cx+19}" y2="705" stroke="#451a03" stroke-width="10"/>'
        )
    elif spec.get("prop") == "phone_sunglasses":
        prop_svg = (
            f'<rect x="{cx-100}" y="545" width="84" height="36" rx="12" fill="#0f172a"/>'
            f'<rect x="{cx+16}" y="545" width="84" height="36" rx="12" fill="#0f172a"/>'
            f'<line x1="{cx-16}" y1="560" x2="{cx+16}" y2="560" stroke="#0f172a" stroke-width="8"/>'
        )

    shoulder_fg = ""
    if shot_framing == "over_shoulder":
        shoulder_fg = '<ellipse cx="110" cy="1760" rx="290" ry="340" fill="#1e293b" opacity="0.65"/>'
    elif shot_framing == "reverse_over_shoulder":
        shoulder_fg = '<ellipse cx="970" cy="1760" rx="290" ry="340" fill="#334155" opacity="0.65"/>'

    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1080 1920" width="1080" height="1920">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="{bg_top}"/>
      <stop offset="55%" stop-color="{bg_mid}"/>
      <stop offset="100%" stop-color="{bg_bot}"/>
    </linearGradient>
    <radialGradient id="sun" cx="50%" cy="32%" r="48%">
      <stop offset="0%" stop-color="#fef08a" stop-opacity="0.85"/>
      <stop offset="100%" stop-color="#f59e0b" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1080" height="1920" fill="url(#sky)"/>
  <circle cx="540" cy="600" r="560" fill="url(#sun)"/>
  <rect x="40" y="390" width="320" height="900" rx="12" fill="#fed7aa" opacity="0.28"/>
  <rect x="720" y="350" width="320" height="940" rx="12" fill="#bae6fd" opacity="0.24"/>
  <path d="M 0 1240 L 1080 1240 L 1080 1920 L 0 1920 Z" fill="#292524" opacity="0.82"/>
  <path d="M {cx-300} 1340 C {cx-280} 1000 {cx+280} 1000 {cx+300} 1340 Z" fill="{shirt}" stroke="#0f172a" stroke-opacity="0.22" stroke-width="8"/>
  <rect x="{cx-55}" y="885" width="110" height="140" rx="45" fill="{skin}"/>
  <circle cx="{cx}" cy="700" r="185" fill="{skin}" stroke="#0f172a" stroke-opacity="0.16" stroke-width="6"/>
  <path d="M {cx-185} 660 C {cx-170} 475 {cx+170} 475 {cx+185} 660 C {cx+120} 580 {cx-120} 580 {cx-185} 660 Z" fill="{hair}"/>
  <circle cx="{cx-65}" cy="700" r="24" fill="#ffffff"/>
  <circle cx="{cx+65}" cy="700" r="24" fill="#ffffff"/>
  <circle cx="{cx-62}" cy="702" r="12" fill="#0f172a"/>
  <circle cx="{cx+68}" cy="702" r="12" fill="#0f172a"/>
  <path d="M {cx-108} 648 Q {cx-65} 628 {cx-22} 648" stroke="#1e293b" stroke-width="11" fill="none" stroke-linecap="round"/>
  <path d="M {cx+22} 648 Q {cx+65} 628 {cx+108} 648" stroke="#1e293b" stroke-width="11" fill="none" stroke-linecap="round"/>
  <path d="M {cx} 718 Q {cx+20} 760 {cx-8} 778" stroke="#9a5c3c" stroke-width="8" fill="none" stroke-linecap="round"/>
  <path d="{mouth_path}" stroke="#7f1d1d" stroke-width="11" fill="#ef4444" stroke-linecap="round"/>
  {prop_svg}
  {shoulder_fg}
</svg>'''
    out_svg.write_text(svg, encoding="utf-8")


def _svg_to_clean_jpg(svg_path: Path, jpg_path: Path) -> None:
    _run_checked([
        FFMPEG_BIN, "-y",
        "-i", str(svg_path),
        "-vf", "scale=1080:1920",
        "-q:v", "2",
        str(jpg_path),
    ], timeout=25)


def _resolve_turn_visual(
    speaker_key: str,
    spec: Dict[str, str],
    emotion: str,
    shot_framing: str,
    physical_action_en: str,
    visual_style: str,
    setting: str,
    assets_dir: Path,
    turn_idx: int,
    env_map: Dict[str, str],
    shot_cache: Dict[Tuple[Any, ...], Tuple[Path, str]],
    target_filename: Optional[str] = None,
    force_jpg: bool = False,
    is_silent_reaction: bool = False,
) -> Tuple[Path, str, str, str]:
    """Generate/resolve a clean 1080x1920 portrait with v3.0 Identity & Set Continuity Lock."""
    style_norm = normalize_visual_style(visual_style)
    is_3d = style_norm == "stylized_3d_pixar"
    # v3.1 Frontal MoDA Continuity Lock: key ONLY on (speaker_key, style_norm) with fresh _v31 frontal portraits!
    master_cache_key = ("v31_frontal_moda_lock", speaker_key, style_norm)

    base_name = target_filename or f"novela_portrait_{speaker_key}_{turn_idx}_v31.jpg"
    if master_cache_key in shot_cache:
        src_path, prov = shot_cache[master_cache_key]
        suffix = ".jpg" if force_jpg else src_path.suffix
        dst = assets_dir / Path(base_name).with_suffix(suffix).name
        if src_path.suffix == ".svg" and force_jpg:
            _svg_to_clean_jpg(src_path, dst)
            return dst, f"assets/{dst.name}", f"{prov}+continuity_svg2jpg", ""
        if src_path.suffix != ".svg" and (shot_framing in ("reaction_closeup", "close_up", "reaction") or is_silent_reaction):
            dst = assets_dir / Path(base_name).with_suffix(".jpg").name
            _run_checked([
                FFMPEG_BIN, "-y", "-i", str(src_path),
                "-vf", "scale=1232:2190:flags=lanczos,crop=1080:1920:76:95",
                "-q:v", "2",
                str(dst),
            ], timeout=30)
            return dst, f"assets/{dst.name}", f"{prov}+continuity_closeup_14pct", ""
        shutil.copy2(src_path, dst)
        return dst, f"assets/{dst.name}", f"{prov}+continuity_reuse", ""

    style_prefix = (
        "Stylized 3D animated CG comedy film shot, theatrical feature CGI animation quality, sculpted expressive character lighting, rich warm colors, clean frame"
        if is_3d
        else "Photorealistic live-action 8K cinema shot, cinema camera 85mm f/1.8 lens, shallow depth of field, "
             "natural lighting, Brazilian television drama cinematography, clean frame"
    )
    char_lock = spec["lock_3d"] if is_3d else spec["lock_tv"]
    emo_key = "shame" if is_silent_reaction else emotion
    emo_desc, _, _ = EMOTION_DIRECTIONS.get(emo_key, EMOTION_DIRECTIONS["warm"])
    framing_meta = SHOT_FRAMINGS.get(shot_framing, SHOT_FRAMINGS["over_shoulder"])
    closed_mouth_mandate = (
        "composed neutral expression with lips gently closed together ready for dubbing, "
        "calm relaxed lowered shoulders and hands resting naturally down at sides"
    )
    prompt = (
        f"{char_lock}. {framing_meta['flux_cue'].format(speaker_en=spec['label'])}. "
        f"{style_prefix}. {emo_desc}, {closed_mouth_mandate}. "
        f"Background setting: {setting}, vertical 9:16 portrait composition."
    )

    nv_key = env_map.get("NVIDIA_API_KEY") or env_map.get("NIM_API_KEY") or ""
    out_jpg = assets_dir / Path(base_name).with_suffix(".jpg").name
    ok, prov = _call_nvidia_flux_image(prompt, nv_key, out_jpg, "black-forest-labs/flux.1-dev")
    if not ok and prov == "content_filtered":
        safe_prompt = (
            f"{char_lock}, {style_prefix}, solo medium close-up vertical 9:16 portrait, "
            f"{closed_mouth_mandate}, sunny countryside plaza."
        )
        ok, prov = _call_nvidia_flux_image(safe_prompt, nv_key, out_jpg, "black-forest-labs/flux.1-dev")
    if not ok:
        retry_prompt = (
            f"{style_prefix}. Vertical 9:16 portrait of {char_lock}, {closed_mouth_mandate}, {setting}."
            if prov != "content_filtered"
            else f"{style_prefix}. Vertical 9:16 portrait of friendly Brazilian character, {closed_mouth_mandate}, sunny plaza."
        )
        ok, prov = _call_nvidia_flux_image(retry_prompt, nv_key, out_jpg, "black-forest-labs/flux.1-schnell")

    if ok and out_jpg.exists():
        shot_cache[master_cache_key] = (out_jpg, f"nvidia:{prov}")
        return out_jpg, f"assets/{out_jpg.name}", f"nvidia:{prov}", prompt

    # Deterministic clean vector artwork fallback
    out_svg = assets_dir / Path(base_name).with_suffix(".svg").name
    _build_bespoke_svg_portrait(speaker_key, spec, "3d_cartoon" if is_3d else "cinematic_tv", emo_key, shot_framing, out_svg, clean_no_text=True)
    if force_jpg:
        try:
            _svg_to_clean_jpg(out_svg, out_jpg)
            out_svg.unlink(missing_ok=True)
            shot_cache[master_cache_key] = (out_jpg, f"svg_jpg_fallback:{prov}")
            return out_jpg, f"assets/{out_jpg.name}", f"svg_jpg_fallback:{prov}", prompt
        except Exception:
            pass
    shot_cache[master_cache_key] = (out_svg, f"svg_fallback:{prov}")
    return out_svg, f"assets/{out_svg.name}", f"svg_fallback:{prov}", prompt


def build_veo_take_prompt(
    speaker_spec: Dict[str, str],
    tts_text: str,
    visual_style: str,
    shot_framing: str,
    emotion: str,
    physical_action_en: str,
    setting: str,
    cut_role: str = "dialogue",
    lead_in_pause: float = 0.0,
    is_silent_reaction: bool = False,
    tone: str = "Comédia Leve",
) -> str:
    """Compose second-by-second bilingual Veo 3.1 technical camera + acting prompt matching episode tone."""
    style_norm = normalize_visual_style(visual_style)
    is_3d = style_norm == "stylized_3d_pixar"
    style_desc = (
        "Stylized 3D animated CG comedy film shot, theatrical 3D CGI animated feature comedy quality, sculpted expressive character lighting, rich colors"
        if is_3d
        else "Photorealistic live-action 8K cinema shot, ARRI Alexa Mini LF 85mm f/1.8 lens, shallow depth of field, "
             "natural skin pores and realistic lighting, dramatic Brazilian television drama cinematography, real human actors"
    )
    framing_cue = SHOT_FRAMINGS.get(shot_framing, SHOT_FRAMINGS["over_shoulder"])["veo_cue"]
    emo_key = "shame" if is_silent_reaction else emotion
    _, _, default_acting = EMOTION_DIRECTIONS.get(emo_key, EMOTION_DIRECTIONS["warm"])
    acting_en = (physical_action_en or "").strip() or default_acting
    veo_char = (
        speaker_spec.get("veo_desc")
        if is_3d
        else (speaker_spec.get("veo_live") or speaker_spec.get("lock_tv") or speaker_spec["label"])
    )

    if is_silent_reaction or cut_role == "silent_shame_reaction":
        low_t = (tone or "Comédia Leve").lower()
        is_drama = any(k in low_t for k in ("drama", "traiç", "traic", "vergonha", "tenso", "suspense", "choque"))
        if is_drama:
            return (
                f"{style_desc}, set on {setting}. {framing_cue}. "
                f"0.0s-2.0s: {veo_char} gasps in dramatic shock and mortified embarrassment, raising both hands to head and lowering gaze in disbelief, shoulders slumping. "
                f"No subtitles, no text overlay, no watermarks."
            )
        return (
            f"{style_desc}, set on {setting}. {framing_cue}. "
            f"0.0s-2.0s: {veo_char} breaks into an awkward sheepish smile and warm chuckle, raising one hand to scratch the back of head while shaking head in funny disbelief. "
            f"No subtitles, no text overlay, no watermarks."
        )

    clean_speech = rve.sanitize_northeast_slang(tts_text).replace('"', "'").strip()
    if lead_in_pause >= 0.08 or cut_role == "calm_inhale_comeback":
        timeline = (
            f"0.0s-{lead_in_pause:.2f}s: {veo_char} gives a quick natural beat and calm knowing nod before replying. "
            f"{lead_in_pause:.2f}s-3.6s: {acting_en}, speaking in Brazilian Portuguese with an authentic, warm Northeastern Sertão accent "
            f"(sotaque nordestino arretado, crisp energetic comeback cadence): \"{clean_speech}\" "
        )
    else:
        timeline = (
            f"0.0s-0.3s: {veo_char} transitions naturally from a relaxed closed-lip pose with quick subtle hand/shoulder emphasis. "
            f"0.3s-3.5s: {acting_en}, speaking in Brazilian Portuguese with an authentic, warm Northeastern Sertão accent "
            f"(sotaque nordestino arretado, fast energetic comedy cadence, open vowels, zero slow dragging): \"{clean_speech}\" "
        )

    return (
        f"{style_desc}, set on {setting}. {framing_cue}. "
        f"{timeline}"
        f"Blurred background passersby move subtly in depth, natural handheld camera breathing, authentic facial lip-sync and eye blinking. "
        f"No subtitles, no text overlay, no watermarks, realistic lip-sync speech in Brazilian Portuguese."
    )


def generate_flow_veo_pack(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Generate Google Flow Veo 3.1 Production Pack (dispatches to v4 Visual Bible OTS or v3.1 fallback)."""
    if _resolve_cinema_engine(cfg) == "v4":
        raw_in = list(cfg.get("dialogue") or [])
        if not raw_in:
            raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala para gerar o Pack Cinema v4.")
        env_map = _load_secrets_env()
        manifest = cv4.generate_flow_veo_pack_v4(
            cfg,
            compiled_dialogue=raw_in,
            flow_packs_dir=FLOW_PACKS_DIR,
            novela_export_dir=NOVELA_EXPORT_DIR,
            nvidia_key=(env_map.get("NVIDIA_API_KEY") or "").strip(),
        )
        qt = _get_qt()
        if qt is not None:
            qt.record_call("google-flow-veo31", ok=True, status_code=200, specialty="video")
        return manifest

    t_start = time.time()
    visual_style = normalize_visual_style(str(cfg.get("visual_style") or "stylized_3d_pixar"))
    dramatic_structure = normalize_dramatic_structure(str(cfg.get("dramatic_structure") or "viral_4_cuts"))

    raw_dialogue = compile_dramatic_cuts(list(cfg.get("dialogue") or []), dramatic_structure=dramatic_structure)
    if not raw_dialogue:
        raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala para gerar o Pack Veo 3.1.")

    title = str(cfg.get("title") or "Causos do Sertão — Episódio 01").strip()
    default_setting = (
        "sunlit contemporary Brazilian urban cafe sidewalk with warm bokeh depth and soft passersby"
        if visual_style == "cinematic_live_action"
        else "sunny rustic cobblestone sidewalk outside a colorful small countryside corner shop in Northeast Brazil with festive flags and bystanders watching"
    )
    setting = str(cfg.get("setting") or default_setting).strip()

    pack_id = f"flow_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    pack_dir = Path(cfg.get("pack_dir") or (FLOW_PACKS_DIR / pack_id))
    pack_dir.mkdir(parents=True, exist_ok=True)

    env_map = _load_secrets_env()
    shot_cache: Dict[Tuple[Any, ...], Tuple[Path, str]] = {}
    takes: List[Dict[str, Any]] = []
    prev_speaker: Optional[str] = None

    for idx, raw_turn in enumerate(raw_dialogue):
        spk_raw = str(raw_turn.get("speaker") or "Narrador")
        spk_key, spec = _get_character_spec(spk_raw)
        is_silent = bool(raw_turn.get("is_silent_reaction", False))
        cut_role = str(raw_turn.get("cut_role") or ("silent_shame_reaction" if is_silent else "dialogue")).strip().lower()
        lead_pause = float(raw_turn.get("lead_in_pause") or 0.0)
        subtitle_text = "" if is_silent else rve.sanitize_northeast_slang(str(raw_turn.get("subtitle_text") or raw_turn.get("text") or "").strip())
        tts_text = "" if is_silent else rve.sanitize_northeast_slang(str(raw_turn.get("tts_text") or subtitle_text).strip())
        if not tts_text and not is_silent:
            continue
        if not subtitle_text and not is_silent:
            subtitle_text = tts_text

        emotion = "shame" if is_silent else str(raw_turn.get("emotion") or "warm").strip().lower()
        shot_framing = _assign_shot_framing(
            idx,
            str(raw_turn.get("shot_framing") or ""),
            emotion,
            prev_speaker,
            spk_key,
        )
        physical_action_en = str(raw_turn.get("physical_action_en") or "").strip()

        ing_name = f"ingredient_take_{idx + 1:02d}.jpg"
        ing_path, _, prov, img_prompt = _resolve_turn_visual(
            speaker_key=spk_key,
            spec=spec,
            emotion=emotion,
            shot_framing=shot_framing,
            physical_action_en=physical_action_en,
            visual_style=visual_style,
            setting=setting,
            assets_dir=pack_dir,
            turn_idx=idx,
            env_map=env_map,
            shot_cache=shot_cache,
            target_filename=ing_name,
            force_jpg=True,
            is_silent_reaction=is_silent,
        )

        veo_prompt = build_veo_take_prompt(
            speaker_spec=spec,
            tts_text=tts_text,
            visual_style=visual_style,
            shot_framing=shot_framing,
            emotion=emotion,
            physical_action_en=physical_action_en,
            setting=setting,
            cut_role=cut_role,
            lead_in_pause=lead_pause,
            is_silent_reaction=is_silent,
        )
        words_cnt = max(2, len(tts_text.split())) if not is_silent else 0
        est_sec = max(2, min(4, round(float(raw_turn.get("silent_duration") or 2.0)))) if is_silent else max(3, min(7, round(words_cnt / 2.6 + lead_pause) + 1))
        framing_meta = SHOT_FRAMINGS[shot_framing]

        # Download relative path inside NOVELA_EXPORT_DIR (`exports/novela/...`)
        try:
            rel_inside_novela = ing_path.resolve().relative_to(NOVELA_EXPORT_DIR.resolve()).as_posix()
        except Exception:
            rel_inside_novela = f"flow_packs/{pack_id}/{ing_path.name}"

        from urllib.parse import quote
        q_rel = quote(rel_inside_novela, safe="")
        takes.append({
            "take_number": idx + 1,
            "cut_role": cut_role,
            "is_silent_reaction": is_silent,
            "lead_in_pause": round(lead_pause, 2),
            "speaker": spec["label"],
            "speaker_key": spk_key,
            "badge_bg": spec["badge_bg"],
            "emotion": emotion,
            "shot_framing": shot_framing,
            "shot_framing_label": framing_meta["label"],
            "lens_mm": framing_meta["lens_mm"],
            "estimated_duration_sec": est_sec,
            "tts_text": tts_text,
            "subtitle_text": subtitle_text,
            "image_prompt": img_prompt,
            "veo_prompt": veo_prompt,
            "ingredient_filename": ing_path.name,
            "ingredient_path": str(ing_path),
            "ingredient_relpath": rel_inside_novela,
            "ingredient_url": f"/api/outputs/download?stack=novela&root_idx=0&relpath={q_rel}&inline=1",
            "ingredient_download_url": f"/api/outputs/download?stack=novela&root_idx=0&relpath={q_rel}",
            "asset_source": prov,
            "credits_veo_lite": 10,
            "credits_veo_fast": 20,
        })
        prev_speaker = spk_key

    if not takes:
        raise ValueError("Nenhum take válido encontrado para o Pack Veo 3.1.")

    num_takes = len(takes)
    manifest = {
        "ok": True,
        "pack_id": pack_id,
        "title": title,
        "visual_style": visual_style,
        "dramatic_structure": dramatic_structure,
        "setting": setting,
        "prompt_rules": "v28_closed_mouth",
        "total_takes": num_takes,
        "credit_unit_lite": 10,
        "credit_unit_fast": 20,
        "total_credits_lite": 10 * num_takes,
        "total_credits_fast": 20 * num_takes,
        "total_estimated_duration_sec": sum(t["estimated_duration_sec"] for t in takes),
        "takes": takes,
        "elapsed_sec": round(time.time() - t_start, 2),
    }
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    (pack_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
    (pack_dir / "flow_production_pack.json").write_text(manifest_json, encoding="utf-8")
    qt = _get_qt()
    if qt is not None:
        qt.record_call("google-flow-veo31", ok=True, status_code=200, specialty="video")
    return manifest


def _build_master_audio_mix(
    turns: List[TurnCompiled],
    freeze_start: float,
    freeze_dur: float,
    total_dur: float,
    work_dir: Path,
    include_freeze_sting: bool = True,
) -> Path:
    """Concatenate per-turn speech + subtle scene cut pops normalized to 48kHz Stereo SoXR loudnorm."""
    raw_voice_track = work_dir / "voice_concat.wav"
    concat_list = work_dir / "voice_concat.txt"
    lines = [f"file '{t.audio_wav}'" for t in turns]

    if freeze_dur > 0:
        freeze_sil = work_dir / "freeze_sil.wav"
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-t", f"{freeze_dur:.3f}",
            "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-c:a", "pcm_s16le",
            str(freeze_sil),
        ], timeout=20)
        lines.append(f"file '{freeze_sil}'")
    concat_list.write_text("\n".join(lines) + "\n", encoding="utf-8")

    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "concat", "-safe", "0", "-i", str(concat_list),
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(raw_voice_track),
    ], timeout=60)

    pop_sfx = work_dir / "sfx_pop.wav"
    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-t", "0.10",
        "-i", "aevalsrc=0.10*sin(2*PI*(480+1400*t)*t)*exp(-28*t):s=48000:c=stereo",
        "-c:a", "pcm_s16le",
        str(pop_sfx),
    ], timeout=20)

    filter_inputs: List[str] = ["-i", str(raw_voice_track)]
    filter_parts: List[str] = ["[0:a]aresample=48000:resampler=soxr,volume=1.0[vtrack]"]
    mix_labels: List[str] = ["[vtrack]"]
    in_idx = 1

    prev_speaker = None
    for t in turns:
        if prev_speaker is not None and t.speaker_key != prev_speaker:
            delay_ms = max(0, int(round(t.slot_start * 1000)))
            filter_inputs.extend(["-i", str(pop_sfx)])
            lbl = f"[pop{in_idx}]"
            filter_parts.append(f"[{in_idx}:a]volume=0.16,adelay={delay_ms}|{delay_ms}{lbl}")
            mix_labels.append(lbl)
            in_idx += 1
        prev_speaker = t.speaker_key

    if include_freeze_sting and freeze_dur >= 1.5:
        sting_sfx = work_dir / "sfx_novela_sting.wav"
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-t", f"{max(1.5, freeze_dur - 0.1):.3f}",
            "-i", "aevalsrc=0.18*(0.55*sin(2*PI*220*t)+0.40*sin(2*PI*261.63*t)+0.35*sin(2*PI*329.63*t)+0.25*sin(2*PI*440*t))*(1-exp(-12*t))*exp(-0.55*t):s=48000:c=stereo",
            "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(0.8, freeze_dur - 1.2):.2f}:d=1.0",
            "-c:a", "pcm_s16le",
            str(sting_sfx),
        ], timeout=20)
        freeze_ms = max(0, int(round(freeze_start * 1000)))
        filter_inputs.extend(["-i", str(sting_sfx)])
        sting_lbl = f"[sting{in_idx}]"
        filter_parts.append(f"[{in_idx}:a]volume=0.48,adelay={freeze_ms}|{freeze_ms}{sting_lbl}")
        mix_labels.append(sting_lbl)

    filter_parts.append(
        f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0,"
        f"aresample=48000:resampler=soxr,"
        f"loudnorm=I=-16:TP=-1.5:LRA=11[mixout]"
    )

    final_mix = work_dir / "final_mix.wav"
    _run_checked([
        FFMPEG_BIN, "-y",
        *filter_inputs,
        "-filter_complex", ";".join(filter_parts),
        "-map", "[mixout]",
        "-t", f"{total_dur:.3f}",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(final_mix),
    ], timeout=90)
    return final_mix


def _format_subtitle_html(text: str, highlight: str, accent_color: str) -> str:
    safe_txt = html.escape(text.strip())
    if not highlight or not highlight.strip():
        return safe_txt
    target = html.escape(highlight.strip())
    pattern = re.compile(re.escape(target), re.IGNORECASE)
    if pattern.search(safe_txt):
        return pattern.sub(
            lambda m: f'<span class="kw-pop" style="color:{accent_color}">{m.group(0)}</span>',
            safe_txt,
            count=1,
        )
    return safe_txt


def _build_hyperframes_html(
    cfg: Dict[str, Any],
    turns: List[TurnCompiled],
    freeze_start: float,
    freeze_dur: float,
    total_dur: float,
    safe_zone: Dict[str, Any],
    visual_style: str,
    clean_cinema_mode: bool,
    subtitles_mode: str,
    include_dictionary_endcard: bool,
    work_dir: Path,
) -> Path:
    """Generate HyperFrames compliant index.html in Clean Cinema (100% full-bleed) or Classic Card mode."""
    assets_dir = work_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    if GSAP_VENDOR.is_file():
        shutil.copy2(GSAP_VENDOR, assets_dir / "gsap.min.js")

    title = html.escape(str(cfg.get("title") or "Causos do Sertão — Episódio 01"))
    ep_badge = html.escape(str(cfg.get("episode_badge") or "🌵 NOVELINHA DO SERTÃO • EP. 01"))
    style_tag = "🎨 3D CARTOON" if visual_style == "3d_cartoon" else "🎬 NOVELA TV"
    dictionary_items: List[Dict[str, str]] = list(cfg.get("dictionary") or []) or [
        {"term": "OXENTE!", "meaning": "Expressão de espanto, surpresa ou indignação"},
        {"term": "BOTAR BONECO", "meaning": "Fazer drama, escândalo ou manha sem necessidade"},
    ]

    safe_top = int(safe_zone["safe_top"])
    safe_bottom = int(safe_zone["safe_bottom"])
    safe_left = int(safe_zone["safe_left"])
    right_inset = max(72, 1080 - int(safe_zone["safe_right"]))
    caption_bottom_offset = 1920 - safe_bottom

    scenes_html: List[str] = []
    gsap_steps: List[str] = []

    for t in turns:
        sid = f"scene-{t.index}"
        shot_id = f"shot-{t.index}"
        card_id = f"card-{t.index}"
        badge_id = f"badge-{t.index}"
        react_id = f"react-{t.index}"
        sub_html = _format_subtitle_html(t.subtitle_text, t.highlight_phrase, "#fde047" if clean_cinema_mode else t.accent)

        if clean_cinema_mode:
            # CLEAN CINEMA MODE: 100% full-bleed image, NO dialog boxes, NO top ribbons, NO badges!
            minimal_cap_html = (
                f'<div id="{card_id}" class="minimal-caption-wrap" style="left:{safe_left}px;right:{right_inset}px;bottom:{caption_bottom_offset + 12}px;">'
                f'  <div class="minimal-caption-text">{sub_html}</div>'
                f'</div>'
                if subtitles_mode == "minimal"
                else ""
            )
            scenes_html.append(f"""
        <div id="{sid}" class="clip scene-panel" data-start="{t.slot_start:.3f}" data-duration="{t.slot_dur:.3f}" data-track-index="1" style="z-index:{10 + t.index};">
          <div id="{shot_id}" class="cam-layer">
            <img src="{t.shot_relpath}" alt="{html.escape(t.speaker_label)}" class="portrait-img" />
          </div>
          {minimal_cap_html}
        </div>""")
        else:
            sticker = html.escape((t.highlight_phrase or "").upper()[:26]) if t.highlight_phrase else ""
            sticker_html = (
                f'<div id="{react_id}" class="reaction-pill" style="background:{t.badge_bg};color:{t.badge_fg}">⚡ {sticker}!</div>'
                if sticker
                else f'<div id="{react_id}" class="reaction-pill" style="opacity:0"></div>'
            )
            tint_class = "tv-film-grade" if visual_style == "cinematic_tv" else "cartoon-vibrant-grade"
            scenes_html.append(f"""
        <div id="{sid}" class="clip scene-panel" data-start="{t.slot_start:.3f}" data-duration="{t.slot_dur:.3f}" data-track-index="1" style="z-index:{10 + t.index};">
          <div id="{shot_id}" class="cam-layer">
            <img src="{t.shot_relpath}" alt="{html.escape(t.speaker_label)}" class="portrait-img" />
            <div class="{tint_class}"></div>
            <div class="vignette-overlay"></div>
          </div>
          <div class="top-ribbon" style="top:{safe_top}px;left:{safe_left}px;right:{right_inset}px;">
            <span class="ep-pill">{ep_badge}</span>
            <span class="style-pill">{style_tag}</span>
          </div>
          <div style="position:absolute;top:{safe_top + 68}px;left:{safe_left}px;right:{right_inset}px;">
            <div class="ep-title-bar">{title}</div>
          </div>
          <div style="position:absolute;top:{safe_top + 148}px;right:{right_inset}px;">
            {sticker_html}
          </div>
          <div class="lower-safe-stack" style="left:{safe_left}px;right:{right_inset}px;bottom:{caption_bottom_offset}px;">
            <div id="{badge_id}" class="speaker-row">
              <span class="speaker-pill" style="background:{t.badge_bg};color:{t.badge_fg};">
                🎙️ {html.escape(t.speaker_label.upper())}
              </span>
              <span class="role-tag">{html.escape(t.role)}</span>
            </div>
            <div id="{card_id}" class="subtitle-box" style="border-left:10px solid {t.badge_bg};">
              <div class="subtitle-text">{sub_html}</div>
            </div>
          </div>
        </div>""")

        ts = t.slot_start
        sd = max(0.5, t.slot_dur)
        # Organic handheld shot-reverse-shot motion
        if t.camera_action == "shake":
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{scale:1.03, x:0, rotation:0}}, '
                f'{{scale:1.09, x:-10, rotation:-0.8, duration:0.08, repeat:3, yoyo:true, ease:"power2.inOut"}}, {ts:.3f});'
                f'tl.to("#{shot_id}", {{scale:1.11, x:0, rotation:0, duration:{max(0.3, sd-0.34):.3f}, ease:"none"}}, {ts+0.32:.3f});'
            )
        elif t.camera_action == "whip_pan":
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{x:55, scale:1.08}}, {{x:0, scale:1.03, duration:0.20, ease:"power3.out"}}, {ts:.3f});'
                f'tl.to("#{shot_id}", {{scale:1.09, duration:{max(0.25, sd-0.20):.3f}, ease:"none"}}, {ts+0.20:.3f});'
            )
        elif t.camera_action == "dramatic_push":
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{scale:1.0}}, {{scale:1.14, duration:{sd:.3f}, ease:"power1.in"}}, {ts:.3f});'
            )
        else:
            x_pan = -12 if t.shot_framing == "over_shoulder" else 12
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{scale:1.02, x:{x_pan}, y:5}}, {{scale:1.09, x:0, y:-5, duration:{sd:.3f}, ease:"none"}}, {ts:.3f});'
            )

        if clean_cinema_mode:
            if subtitles_mode == "minimal":
                gsap_steps.append(
                    f'tl.fromTo("#{card_id}", {{y:14, opacity:0}}, {{y:0, opacity:1, duration:0.16, ease:"power2.out"}}, {ts:.3f});'
                )
        else:
            gsap_steps.append(
                f'tl.fromTo("#{badge_id}", {{x:-40, opacity:0}}, {{x:0, opacity:1, duration:0.22, ease:"back.out(1.7)"}}, {ts:.3f});'
                f'tl.fromTo("#{card_id}", {{y:28, opacity:0, scale:0.96}}, {{y:0, opacity:1, scale:1, duration:0.24, ease:"power3.out"}}, {ts+0.03:.3f});'
            )
            if t.highlight_phrase:
                gsap_steps.append(
                    f'tl.fromTo("#{react_id}", {{scale:0.3, rotation:-12, opacity:0}}, '
                    f'{{scale:1, rotation:-4, opacity:1, duration:0.28, ease:"back.out(2.1)"}}, {ts+0.12:.3f});'
                )

    freeze_scene_html = ""
    if include_dictionary_endcard and freeze_dur >= 1.5:
        last_turn = turns[-1]
        dict_rows_html = ""
        for item in dictionary_items[:3]:
            term = html.escape(str(item.get("term") or item.get("word") or "").strip().upper())
            meaning = html.escape(str(item.get("meaning") or item.get("desc") or "").strip())
            if term:
                dict_rows_html += (
                    f'<div class="dict-row">'
                    f'  <div class="dict-term">📖 {term}</div>'
                    f'  <div class="dict-mean">{meaning}</div>'
                    f'</div>'
                )
        freeze_scene_html = f"""
        <div id="scene-freeze" class="clip scene-panel" data-start="{freeze_start:.3f}" data-duration="{freeze_dur:.3f}" data-track-index="1" style="z-index:{20 + len(turns)};">
          <div id="freeze-cam" class="cam-layer">
            <img src="{last_turn.shot_relpath}" alt="Freeze Frame" class="portrait-img" />
            <div class="freeze-sepia-tint"></div>
            <div class="freeze-letterbox-top"></div>
            <div class="freeze-letterbox-bot"></div>
          </div>
          <div id="freeze-card-wrap" class="freeze-center-safe" style="top:{safe_top + 45}px;bottom:{caption_bottom_offset + 20}px;left:{safe_left}px;right:{right_inset}px;">
            <div class="freeze-badge-top">⏸️ CONGELA A CENA!</div>
            <div class="dict-card">
              <div class="dict-header">🌵 DICIONÁRIO NORDESTINO</div>
              {dict_rows_html}
            </div>
            <div id="cliffhanger-pill" class="cliffhanger-pill">📺 Continua no próximo episódio...</div>
          </div>
        </div>"""
        gsap_steps.append(
            f'tl.fromTo("#freeze-cam", {{scale:1.02}}, {{scale:1.15, duration:{freeze_dur:.3f}, ease:"power2.out"}}, {freeze_start:.3f});'
            f'tl.fromTo("#freeze-card-wrap", {{scale:0.86, opacity:0}}, {{scale:1, opacity:1, duration:0.35, ease:"back.out(1.6)"}}, {freeze_start+0.08:.3f});'
            f'tl.fromTo("#cliffhanger-pill", {{scale:0.95}}, {{scale:1.05, duration:0.45, repeat:4, yoyo:true, ease:"sine.inOut"}}, {freeze_start+0.45:.3f});'
        )

    html_doc = f"""<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>{title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  html, body {{
    width: 1080px; height: 1920px; overflow: hidden;
    background: #090b10; color: #f8fafc;
    font-family: "Inter", "DejaVu Sans", "Liberation Sans", system-ui, sans-serif;
  }}
  #root {{ position: relative; width: 1080px; height: 1920px; overflow: hidden; background: #090b10; }}
  .scene-panel {{ position: absolute; inset: 0; overflow: hidden; }}
  .cam-layer {{ position: absolute; inset: 0; transform-origin: 50% 42%; will-change: transform; }}
  .portrait-img {{ width: 100%; height: 100%; object-fit: cover; display: block; }}
  .minimal-caption-wrap {{
    position: absolute; z-index: 8; text-align: center; pointer-events: none;
  }}
  .minimal-caption-text {{
    font-size: 38px; line-height: 1.22; font-weight: 900; color: #ffffff;
    -webkit-text-stroke: 3px #000000;
    paint-order: stroke fill;
    text-shadow: 0 3px 10px rgba(0,0,0,0.95);
  }}
  .kw-pop {{ font-weight: 900; }}
  .tv-film-grade {{
    position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(180,83,9,0.12) 0%, rgba(0,0,0,0) 40%, rgba(12,10,9,0.55) 100%);
  }}
  .cartoon-vibrant-grade {{
    position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(14,165,233,0.10) 0%, rgba(0,0,0,0) 42%, rgba(9,11,16,0.52) 100%);
  }}
  .vignette-overlay {{
    position: absolute; inset: 0;
    background: radial-gradient(circle at 50% 42%, rgba(0,0,0,0) 44%, rgba(5,7,12,0.72) 100%);
  }}
  .top-ribbon {{ position: absolute; display: flex; align-items: center; justify-content: space-between; gap: 12px; z-index: 5; }}
  .ep-pill {{
    background: rgba(15, 23, 42, 0.86); border: 2px solid rgba(250, 204, 21, 0.65);
    color: #fde047; padding: 10px 20px; border-radius: 999px; font-size: 24px; font-weight: 800;
  }}
  .style-pill {{
    background: rgba(15, 23, 42, 0.86); border: 2px solid rgba(56, 189, 248, 0.55);
    color: #e0f2fe; padding: 10px 18px; border-radius: 999px; font-size: 22px; font-weight: 800;
  }}
  .ep-title-bar {{
    display: inline-block; background: rgba(10, 12, 18, 0.72); border-radius: 14px;
    padding: 10px 20px; font-size: 28px; font-weight: 800; color: #ffffff;
  }}
  .reaction-pill {{
    display: inline-block; padding: 14px 26px; border-radius: 20px;
    font-size: 32px; font-weight: 900; border: 4px solid #ffffff;
  }}
  .lower-safe-stack {{ position: absolute; display: flex; flex-direction: column; gap: 14px; z-index: 6; }}
  .speaker-row {{ display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }}
  .speaker-pill {{
    padding: 10px 22px; border-radius: 14px; font-size: 30px; font-weight: 900;
    border: 3px solid rgba(255,255,255,0.85);
  }}
  .role-tag {{
    background: rgba(15, 23, 42, 0.84); color: #e2e8f0;
    padding: 8px 16px; border-radius: 12px; font-size: 22px; font-weight: 700;
  }}
  .subtitle-box {{
    background: rgba(10, 12, 18, 0.90); border-radius: 20px; padding: 22px 26px;
    border-top: 1px solid rgba(255,255,255,0.16);
  }}
  .subtitle-text {{ font-size: 40px; line-height: 1.24; font-weight: 800; color: #f8fafc; }}
  .freeze-sepia-tint {{
    position: absolute; inset: 0;
    background: linear-gradient(180deg, rgba(120,53,15,0.48) 0%, rgba(217,119,6,0.34) 45%, rgba(17,24,39,0.78) 100%);
  }}
  .freeze-letterbox-top {{ position: absolute; top: 0; left: 0; right: 0; height: 96px; background: rgba(0,0,0,0.92); }}
  .freeze-letterbox-bot {{ position: absolute; bottom: 0; left: 0; right: 0; height: 96px; background: rgba(0,0,0,0.92); }}
  .freeze-center-safe {{
    position: absolute; display: flex; flex-direction: column; justify-content: center; align-items: center; gap: 22px; z-index: 8;
  }}
  .freeze-badge-top {{
    background: #dc2626; color: #ffffff; padding: 12px 28px; border-radius: 999px;
    font-size: 30px; font-weight: 900; border: 4px solid #fef08a;
  }}
  .dict-card {{
    width: 100%; background: rgba(15, 23, 42, 0.94); border: 4px solid #f59e0b;
    border-radius: 28px; padding: 28px 30px; display: flex; flex-direction: column; gap: 18px;
  }}
  .dict-header {{
    font-size: 36px; font-weight: 900; color: #fde047; text-align: center;
    padding-bottom: 12px; border-bottom: 2px solid rgba(245,158,11,0.35);
  }}
  .dict-row {{
    background: rgba(30, 41, 59, 0.85); border-radius: 18px; padding: 16px 20px; border-left: 8px solid #38bdf8;
  }}
  .dict-term {{ font-size: 30px; font-weight: 900; color: #38bdf8; margin-bottom: 6px; }}
  .dict-mean {{ font-size: 28px; line-height: 1.28; font-weight: 700; color: #f1f5f9; }}
  .cliffhanger-pill {{
    background: linear-gradient(90deg, #f59e0b, #ea580c); color: #ffffff;
    padding: 18px 34px; border-radius: 999px; font-size: 32px; font-weight: 900;
    border: 3px solid #fef08a; text-align: center;
  }}
</style>
</head>
<body>
  <div id="root" data-composition-id="main" data-start="0" data-duration="{total_dur:.3f}" data-width="1080" data-height="1920" data-fps="30">
    {''.join(scenes_html)}
    {freeze_scene_html}
  </div>
  <script src="assets/gsap.min.js"></script>
  <script>
    window.__timelines = window.__timelines || {{}};
    const tl = gsap.timeline({{ paused: true }});
    {chr(10).join('    ' + step for step in gsap_steps)}
    window.__timelines["main"] = tl;
  </script>
</body>
</html>
"""
    index_path = work_dir / "index.html"
    index_path.write_text(html_doc, encoding="utf-8")
    return index_path


# ---------------------------------------------------------------------------
# Phase 2 & 3 Helpers: Intelligent Silence Trim Stitcher + Colab GPU Bridge
# ---------------------------------------------------------------------------

def _get_whisper_model() -> Any:
    if "base" not in _WHISPER_CACHE:
        from faster_whisper import WhisperModel
        _WHISPER_CACHE["base"] = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
            num_workers=1,
        )
    return _WHISPER_CACHE["base"]


def _detect_speech_window(clip_path: Path, raw_dur: float, work_dir: Path, idx: int) -> Tuple[float, float, List[Dict[str, Any]]]:
    """Find tight [start, end] speech bounds via faster-whisper INT8 (NumPy PCM) + silencedetect fallback."""
    import wave
    import numpy as np

    words_meta: List[Dict[str, Any]] = []
    wav_16k = work_dir / f"asr_{idx}.wav"
    try:
        _run_checked([
            FFMPEG_BIN, "-y", "-v", "error", "-i", str(clip_path),
            "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
            str(wav_16k),
        ], timeout=45)
        with wave.open(str(wav_16k), "rb") as wf:
            pcm_bytes = wf.readframes(wf.getnframes())
        audio_np = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) / 32768.0
        model = _get_whisper_model()
        segments, _ = model.transcribe(
            audio_np,
            language="pt",
            word_timestamps=True,
            vad_filter=True,
            beam_size=1,
        )
        for seg in segments:
            if getattr(seg, "words", None):
                for w in seg.words:
                    tok = str(getattr(w, "word", "") or "").strip()
                    if tok:
                        words_meta.append({
                            "word": tok,
                            "start": float(w.start),
                            "end": float(w.end),
                        })
            elif str(seg.text or "").strip():
                words_meta.append({"word": seg.text.strip(), "start": float(seg.start), "end": float(seg.end)})
    except Exception:
        words_meta = []

    if words_meta:
        first_t = min(w["start"] for w in words_meta)
        last_t = max(w["end"] for w in words_meta)
        s_t = max(0.0, first_t - 0.14)
        e_t = min(raw_dur, last_t + 0.16)
        if e_t - s_t >= min(raw_dur, 0.8):
            return round(s_t, 3), round(e_t, 3), words_meta

    # Fallback: FFmpeg silencedetect to trim leading & trailing dead air cleanly
    try:
        proc = subprocess.run(
            [FFMPEG_BIN, "-i", str(clip_path), "-af", "silencedetect=noise=-36dB:d=0.20", "-f", "null", "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=30,
        )
        stderr = proc.stderr
        sil_starts = [float(x) for x in re.findall(r"silence_start:\s*([0-9.]+)", stderr)]
        sil_ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", stderr)]
        s_t = 0.0
        e_t = raw_dur
        if sil_starts and sil_starts[0] < 0.25 and sil_ends:
            s_t = max(0.0, sil_ends[0] - 0.10)
        if sil_starts and sil_starts[-1] > s_t + 0.6 and (
            len(sil_starts) > len(sil_ends) or (sil_ends and sil_ends[-1] >= raw_dur - 0.30)
        ):
            e_t = min(raw_dur, sil_starts[-1] + 0.14)
        if e_t - s_t >= min(raw_dur, 0.8):
            return round(s_t, 3), round(e_t, 3), words_meta
    except Exception:
        pass

    return 0.0, round(raw_dur, 3), words_meta


def _format_ass_ts(sec: float) -> str:
    sec = max(0.0, sec)
    h = int(sec // 3600)
    m = int((sec % 3600) // 60)
    s = int(sec % 60)
    cs = int(round((sec - math.floor(sec)) * 100))
    if cs >= 100:
        cs = 99
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def stitch_novela_takes(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Trim silence lead/tail on ordered MP4 takes, normalize 1080x1920 30fps + 48kHz SoXR, optional minimal captions."""
    t_start = time.time()
    take_paths = [Path(str(p)) for p in (cfg.get("take_paths") or []) if str(p).strip()]
    if len(take_paths) < 1:
        raise ValueError("Informe ao menos 1 arquivo de take (.mp4) para costurar.")
    for p in take_paths:
        if not p.is_file():
            raise FileNotFoundError(f"Take não encontrado: {p}")

    platform_preset = str(cfg.get("platform_preset") or "tiktok_reels").strip().lower()
    safe_zone = PLATFORM_SAFE_ZONES.get(platform_preset) or PLATFORM_SAFE_ZONES["tiktok_reels"]
    subtitles_mode = str(cfg.get("subtitles_mode") or "none").strip().lower()
    trim_silence = bool(cfg.get("trim_silence", True))
    ambient_bed = bool(cfg.get("ambient_bed", False))
    redub_regional = bool(cfg.get("redub_with_regional_tts", False))
    redub_dialogue: List[Dict[str, Any]] = list(cfg.get("dialogue") or cfg.get("redub_dialogue") or [])
    accent_engine = str(cfg.get("accent_engine") or cfg.get("accent_preset") or "nordestino_raiz").strip().lower()
    env_map = _load_secrets_env()
    colab_url = str(cfg.get("colab_url") or env_map.get("COLAB_GPU_URL") or "").strip()
    colab_token = str(cfg.get("colab_token") or env_map.get("COLAB_GPU_TOKEN") or "").strip()
    redub_engines_used: List[str] = []
    redub_lipsync_engines: List[str] = []

    job_id = uuid.uuid4().hex[:8]
    work_dir = Path(f"/tmp/hf_stitch_{job_id}")
    work_dir.mkdir(parents=True, exist_ok=True)

    output_raw = cfg.get("output")
    if output_raw:
        out_path = Path(str(output_raw))
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", str(cfg.get("title") or "episodio-flow").lower()).strip("-")[:32] or "novela"
        out_path = NOVELA_EXPORT_DIR / f"novela_stitched_{slug}_{int(time.time())}_{job_id}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Cinema Engine v4 Stitcher: J/L-cut audio overlap, dynamic LRA >= 5 LU, zero -45dB dead air, Seed-VC/AudioGate
    if _resolve_cinema_engine(cfg) == "v4":
        try:
            tone = str(cfg.get("tone") or "Drama Tenso").strip()
            v4_cfg = {**cfg, "tone": tone}
            raw_dlg = redub_dialogue if redub_dialogue else [
                {"speaker": f"Personagem {idx + 1}", "tts_text": "", "is_silent_reaction": (idx == len(take_paths) - 1 and len(take_paths) >= 4)}
                for idx in range(len(take_paths))
            ]
            _, shot_specs = cv4.compile_cinema_v4_shots(v4_cfg, raw_dlg[:len(take_paths)])
            v4_clips: List[Path] = []
            v4_wavs: List[Optional[Path]] = []
            takes_metrics: List[Dict[str, Any]] = []
            raw_total_dur = 0.0
            for idx, src_clip in enumerate(take_paths):
                info = _probe_media(src_clip)
                raw_dur = info["duration"]
                raw_total_dur += raw_dur
                words_meta = []
                if trim_silence and info["has_audio"]:
                    cut_s, cut_e, words_meta = _detect_speech_window(src_clip, raw_dur, work_dir, idx)
                else:
                    cut_s, cut_e = 0.0, raw_dur
                seg_dur = max(0.45, cut_e - cut_s)
                trim_clip = work_dir / f"v4_trim_{idx:02d}.mp4"
                _run_checked([
                    FFMPEG_BIN, "-y", "-ss", f"{cut_s:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(src_clip),
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,fps=24,format=yuv420p",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                    "-af", "aresample=48000:resampler=soxr,aformat=sample_fmts=fltp:channel_layouts=stereo",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    str(trim_clip),
                ], timeout=120)
                v4_clips.append(trim_clip)

                # Extract native speech WAV (preserved 1:1 with original Flow/LTX lip-sync) or re-dub with AudioGate
                dlg_wav: Optional[Path] = work_dir / f"v4_dlg_{idx:02d}.wav"
                sp = shot_specs[idx] if idx < len(shot_specs) else None
                if redub_regional and sp and sp.tts_text:
                    meta_dub = rve.synthesize_regional_turn(
                        tts_text=sp.tts_text,
                        speaker=sp.speaker_label,
                        emotion=sp.semantic_emotion,
                        accent_engine=accent_engine,
                        out_wav=dlg_wav,
                        colab_url=colab_url,
                        colab_token=colab_token,
                        drama_pacing=cv4.tone_is_drama(tone),
                        performance_direction=sp.performance_direction_pt,
                    )
                    redub_engines_used.append(meta_dub["voice_engine_used"])
                elif info["has_audio"]:
                    _run_checked([
                        FFMPEG_BIN, "-y", "-i", str(trim_clip), "-vn",
                        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le", str(dlg_wav),
                    ], timeout=45)
                else:
                    dlg_wav = None

                # Keep target duration synchronized to real clip length (allowing natural L-cut overlap)
                if sp:
                    sp.target_duration_sec = round(_probe_duration(trim_clip), 2)
                v4_wavs.append(dlg_wav)
                takes_metrics.append({
                    "index": idx + 1,
                    "filename": src_clip.name,
                    "raw_duration": round(raw_dur, 2),
                    "cut_start": cut_s,
                    "cut_end": cut_e,
                    "final_duration": round(_probe_duration(trim_clip), 2),
                    "words_detected": len(words_meta),
                })

            st_v4 = cv4.stitch_cinema_v4_episode(
                clip_paths=v4_clips,
                dialogue_wavs=v4_wavs,
                shot_specs=shot_specs,
                out_mp4=out_path,
                tone=tone,
                subtitles_mode=subtitles_mode,
                safe_bottom=int(safe_zone["safe_bottom"]),
                safe_right=int(safe_zone["safe_right"]),
            )
            final_dur = float(st_v4["duration"])
            return {
                "ok": True,
                "cinema_engine": "v4",
                "output_path": str(out_path),
                "filename": out_path.name,
                "platform_preset": platform_preset,
                "subtitles_mode": subtitles_mode,
                "trim_silence": trim_silence,
                "takes_count": len(v4_clips),
                "raw_total_duration": round(raw_total_dur, 2),
                "duration": round(final_dur, 2),
                "trimmed_seconds": round(max(0.0, raw_total_dur - final_dur), 2),
                "has_l_cuts": st_v4["has_l_cuts"],
                "redubbed_with_regional_tts": redub_regional,
                "voice_engines_used": sorted(set(redub_engines_used)) or ["native_preserved"],
                "takes_metrics": takes_metrics,
                "elapsed_sec": round(time.time() - t_start, 2),
            }
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

    trimmed_clips: List[Path] = []
    takes_metrics: List[Dict[str, Any]] = []
    ass_events: List[Tuple[float, float, str]] = []
    raw_total_dur = 0.0
    timeline_cursor = 0.0

    try:
        for idx, src_clip in enumerate(take_paths):
            info = _probe_media(src_clip)
            raw_dur = info["duration"]
            raw_total_dur += raw_dur

            words_meta: List[Dict[str, Any]] = []
            if trim_silence and info["has_audio"]:
                cut_s, cut_e, words_meta = _detect_speech_window(src_clip, raw_dur, work_dir, idx)
            elif not info["has_audio"]:
                cut_s, cut_e = 0.0, raw_dur
            else:
                cut_s, cut_e = 0.0, raw_dur

            seg_dur = max(0.4, cut_e - cut_s)
            norm_clip = work_dir / f"norm_take_{idx:02d}.mp4"
            v_filter = "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,fps=30,format=yuv420p"

            if redub_regional:
                dlg_row = redub_dialogue[idx] if idx < len(redub_dialogue) and isinstance(redub_dialogue[idx], dict) else {}
                spoken_from_asr = " ".join(w["word"] for w in words_meta).strip()
                redub_txt = str(dlg_row.get("tts_text") or dlg_row.get("subtitle_text") or spoken_from_asr or "Ôxente, rapaz!").strip()
                redub_spk = str(dlg_row.get("speaker") or ["Dona Socorro", "Enzo", "Seu Bastião"][idx % 3])
                redub_emo = str(dlg_row.get("emotion") or "warm").strip().lower()
                g_raw = str(dlg_row.get("guide_audio_path") or "").strip()
                g_path = Path(g_raw) if g_raw and Path(g_raw).is_file() else None
                dub_wav = work_dir / f"redub_{idx:02d}.wav"
                dub_meta = rve.synthesize_regional_turn(
                    tts_text=redub_txt,
                    speaker=redub_spk,
                    emotion=redub_emo,
                    accent_engine=accent_engine,
                    out_wav=dub_wav,
                    guide_audio_path=g_path,
                    colab_url=colab_url,
                    colab_token=colab_token,
                    lead_pad=0.02,
                    tail_pad=0.04,
                )
                redub_engines_used.append(dub_meta["voice_engine_used"])
                dub_dur = max(0.3, float(dub_meta["duration"]))
                tempo = dub_dur / max(0.35, seg_dur)
                tempo_clamped = max(0.70, min(1.45, tempo))
                fitted_wav = work_dir / f"redub_fit_{idx:02d}.wav"
                _run_checked([
                    FFMPEG_BIN, "-y", "-i", str(dub_wav),
                    "-af", f"atempo={tempo_clamped:.3f},aresample=48000:resampler=soxr,apad=whole_dur={seg_dur:.3f}",
                    "-t", f"{seg_dur:.3f}",
                    "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
                    str(fitted_wav),
                ], timeout=60)
                raw_seg = work_dir / f"seg_raw_{idx:02d}.mp4"
                _run_checked([
                    FFMPEG_BIN, "-y",
                    "-ss", f"{cut_s:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(src_clip),
                    "-an", "-vf", v_filter,
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    str(raw_seg),
                ], timeout=120)

                lipsync_done = False
                # Tier 1C-Colab: /lipsync_video via T4 LatentSync 1.5 / MuseTalk 1.5
                if colab_url:
                    try:
                        hdrs = {"Accept": "application/json"}
                        if colab_token:
                            hdrs["X-Colab-Token"] = colab_token
                        hreq = urllib.request.Request(f"{colab_url.rstrip('/')}/health", headers=hdrs, method="GET")
                        with urllib.request.urlopen(hreq, timeout=2.5) as hr:
                            if hr.status == 200:
                                ls_req = urllib.request.Request(
                                    f"{colab_url.rstrip('/')}/lipsync_video",
                                    data=json.dumps({
                                        "video_b64": base64.b64encode(raw_seg.read_bytes()).decode("ascii"),
                                        "audio_b64": base64.b64encode(fitted_wav.read_bytes()).decode("ascii"),
                                        "engine": "latentsync_1.5",
                                        "width": 1080,
                                        "height": 1920,
                                    }).encode("utf-8"),
                                    headers={**hdrs, "Content-Type": "application/json"},
                                    method="POST",
                                )
                                with urllib.request.urlopen(ls_req, timeout=240) as lsr:
                                    ls_data = json.loads(lsr.read().decode("utf-8"))
                                if ls_data.get("ok") and ls_data.get("video_b64"):
                                    tmp_cb = work_dir / f"colab_ls_{idx:02d}.mp4"
                                    tmp_cb.write_bytes(base64.b64decode(ls_data["video_b64"]))
                                    _run_checked([
                                        FFMPEG_BIN, "-y", "-i", str(tmp_cb), "-i", str(fitted_wav),
                                        "-map", "0:v:0", "-map", "1:a:0",
                                        "-vf", v_filter,
                                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-shortest",
                                        str(norm_clip),
                                    ], timeout=120)
                                    tmp_cb.unlink(missing_ok=True)
                                    lipsync_done = True
                                    redub_lipsync_engines.append(f"colab:{ls_data.get('engine') or 'latentsync_1.5'}")
                                    qt = _get_qt()
                                    if qt is not None:
                                        qt.record_call("colab-t4-gpu", ok=True, status_code=200, specialty="video")
                    except Exception as cb_exc:
                        print(f"[STITCH WARN] Colab /lipsync_video take {idx+1} indisponível: {cb_exc}", flush=True)

                # Tier 1C-ZeroGPU: fffiloni/LatentSync cloud neural re-lipsync
                if not lipsync_done:
                    zg_ls = nla.zerogpu_lipsync_video(raw_seg, fitted_wav, norm_clip, env_map=env_map, width=1080, height=1920)
                    if zg_ls is not None and norm_clip.is_file():
                        lipsync_done = True
                        redub_lipsync_engines.append(str(zg_ls.get("engine") or "zerogpu:fffiloni/LatentSync"))

                if not lipsync_done:
                    print(f"[STITCH WARN] Re-lipsync GPU indisponível no take {idx+1}; aplicando apenas áudio PT-BR no clipe Flow Veo 3.1.", flush=True)
                    _run_checked([
                        FFMPEG_BIN, "-y",
                        "-i", str(raw_seg),
                        "-i", str(fitted_wav),
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "copy",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-shortest",
                        str(norm_clip),
                    ], timeout=120)
                    redub_lipsync_engines.append("audio_replace_only")
            else:
                if info["has_audio"]:
                    cmd = [
                        FFMPEG_BIN, "-y",
                        "-ss", f"{cut_s:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(src_clip),
                        "-vf", v_filter,
                        "-af", "aresample=48000:resampler=soxr,aformat=sample_fmts=fltp:channel_layouts=stereo",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        str(norm_clip),
                    ]
                else:
                    cmd = [
                        FFMPEG_BIN, "-y",
                        "-ss", f"{cut_s:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(src_clip),
                        "-f", "lavfi", "-t", f"{seg_dur:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                        "-vf", v_filter,
                        "-map", "0:v:0", "-map", "1:a:0",
                        "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                        "-shortest",
                        str(norm_clip),
                    ]
                _run_checked(cmd, timeout=180)
            actual_seg_dur = _probe_duration(norm_clip)
            trimmed_clips.append(norm_clip)

            # Group transcribed words into snappy 1-line captions shifted onto stitched timeline
            if subtitles_mode == "minimal" and words_meta:
                chunk: List[Dict[str, Any]] = []
                for w in words_meta:
                    chunk.append(w)
                    if len(chunk) >= 5 or w["word"].endswith((".", "!", "?", ",")):
                        c_s = max(0.0, chunk[0]["start"] - cut_s)
                        c_e = min(actual_seg_dur, chunk[-1]["end"] - cut_s)
                        txt = " ".join(x["word"] for x in chunk).strip()
                        if txt and c_e > c_s:
                            ass_events.append((timeline_cursor + c_s, timeline_cursor + c_e, txt))
                        chunk = []
                if chunk:
                    c_s = max(0.0, chunk[0]["start"] - cut_s)
                    c_e = min(actual_seg_dur, chunk[-1]["end"] - cut_s)
                    txt = " ".join(x["word"] for x in chunk).strip()
                    if txt and c_e > c_s:
                        ass_events.append((timeline_cursor + c_s, timeline_cursor + c_e, txt))

            takes_metrics.append({
                "index": idx + 1,
                "filename": src_clip.name,
                "raw_duration": round(raw_dur, 2),
                "cut_start": cut_s,
                "cut_end": cut_e,
                "final_duration": round(actual_seg_dur, 2),
                "words_detected": len(words_meta),
            })
            timeline_cursor += actual_seg_dur

        concat_mp4 = work_dir / "stitched_raw.mp4"
        c_list = work_dir / "clips.txt"
        c_list.write_text("\n".join(f"file '{p}'" for p in trimmed_clips) + "\n", encoding="utf-8")
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(c_list),
            "-c:v", "copy",
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(concat_mp4),
        ], timeout=120)

        # Build filter chain: loudness normalization (48kHz SoXR I=-16) + optional minimal ASS captions
        vf_filters: List[str] = []
        if subtitles_mode == "minimal" and ass_events:
            margin_v = max(80, 1920 - int(safe_zone["safe_bottom"]) + 20)
            margin_r = max(60, 1080 - int(safe_zone["safe_right"]))
            ass_path = work_dir / "minimal_caps.ass"
            ass_header = (
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding\n"
                f"Style: CleanMin,DejaVu Sans,42,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,2,56,{margin_r},{margin_v},1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            )
            ev_lines = [
                f"Dialogue: 0,{_format_ass_ts(s)},{_format_ass_ts(e)},CleanMin,,0,0,0,,{txt}"
                for s, e, txt in ass_events
            ]
            ass_path.write_text(ass_header + "\n".join(ev_lines) + "\n", encoding="utf-8")
            vf_filters.append(f"ass={ass_path}")

        af_filter = "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000:resampler=soxr"
        if ambient_bed:
            af_filter = (
                "highpass=f=65,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000:resampler=soxr"
            )

        final_cmd = [FFMPEG_BIN, "-y", "-i", str(concat_mp4)]
        if vf_filters:
            final_cmd.extend(["-vf", ",".join(vf_filters), "-c:v", "libx264", "-preset", "veryfast", "-crf", "17"])
        else:
            final_cmd.extend(["-c:v", "copy"])
        final_cmd.extend([
            "-af", af_filter,
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(out_path),
        ])
        _run_checked(final_cmd, timeout=180)

        final_dur = _probe_duration(out_path)
        return {
            "ok": True,
            "output_path": str(out_path),
            "filename": out_path.name,
            "platform_preset": platform_preset,
            "subtitles_mode": subtitles_mode,
            "trim_silence": trim_silence,
            "takes_count": len(trimmed_clips),
            "raw_total_duration": round(raw_total_dur, 2),
            "duration": round(final_dur, 2),
            "trimmed_seconds": round(max(0.0, raw_total_dur - final_dur), 2),
            "redubbed_with_regional_tts": redub_regional,
            "voice_engines_used": sorted(set(redub_engines_used)),
            "redub_lipsync_engines": sorted(set(redub_lipsync_engines)),
            "takes_metrics": takes_metrics,
            "elapsed_sec": round(time.time() - t_start, 2),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def _call_colab_animate_take(
    colab_url: str,
    colab_token: str,
    image_path: Path,
    audio_wav: Path,
    veo_prompt: str,
    colab_engine: str,
    out_mp4: Path,
    *,
    character_id: str = "",
    emotion: str = "happiness",
    cfg_scale: float = 1.25,
) -> Dict[str, Any]:
    """Send 1 take portrait + PT-BR WAV + acting prompt to remote Google Colab GPU Worker (`/animate_turn`)."""
    base = colab_url.rstrip("/")
    payload = {
        "image_b64": base64.b64encode(image_path.read_bytes()).decode("ascii"),
        "audio_b64": base64.b64encode(audio_wav.read_bytes()).decode("ascii"),
        "character_id": character_id or image_path.stem,
        "prompt": veo_prompt,
        "engine": colab_engine or "moda_livevasa_l4",
        "emotion": emotion or "happiness",
        "cfg_scale": float(cfg_scale or 1.25),
        "width": 1080,
        "height": 1920,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if colab_token:
        headers["X-Colab-Token"] = colab_token
    last_err = "sem resposta"
    for route in ("/animate_turn", "/animate-take", "/lipsync_video"):
        try:
            req = urllib.request.Request(f"{base}{route}", data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=240) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            if data.get("ok") and data.get("video_b64"):
                out_mp4.write_bytes(base64.b64decode(data["video_b64"]))
                return data
            last_err = str(data.get("error") or "sem video_b64")
        except Exception as exc:
            last_err = str(exc)[:180]
    raise RuntimeError(f"Colab /animate_turn falhou: {last_err}")


def _tone_is_dramatic(tone: str = "Comédia Leve") -> bool:
    low = (tone or "").strip().lower()
    return any(k in low for k in ("drama", "traiç", "traic", "vergonha", "tenso", "suspense", "choque", "sério", "serio"))


def _synthesize_silent_ambient_wav(out_wav: Path, dur_sec: float = 2.0) -> float:
    """Create natural -32 dB room-tone/street ambience WAV (eliminates -73 dB dead air)."""
    dur = max(0.08, float(dur_sec))
    _run_checked([
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anoisesrc=color=pink:amplitude=0.075:sample_rate=48000",
        "-af", "highpass=f=90,lowpass=f=2200,aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
        str(out_wav),
    ], timeout=30)
    return _probe_duration(out_wav)


def _create_master_ambience_bed_wav(out_wav: Path, dur_sec: float, target_lufs: float = -33.0) -> float:
    """Build a continuous -33 dBFS street/room-tone bed across the full episode so no 0.25s window falls below -40 dB."""
    dur = max(0.5, float(dur_sec))
    _run_checked([
        FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anoisesrc=color=pink:amplitude=0.085:sample_rate=48000",
        "-af", f"highpass=f=90,lowpass=f=2400,loudnorm=I={target_lufs:.1f}:TP=-6.0:LRA=4,aresample=48000:resampler=soxr",
        "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
        str(out_wav),
    ], timeout=45)
    return _probe_duration(out_wav)


def _synthesize_reaction_soundtrack_wav(
    out_wav: Path,
    dur_sec: float = 2.0,
    tone: str = "Comédia Leve",
    speaker: str = "Dona Socorro",
    reaction_text: str = "",
    env_map: Optional[Dict[str, str]] = None,
    work_dir: Optional[Path] = None,
) -> float:
    """Generate dynamic Cut 4 audio (-22 dB to -14 dB active peak): tone-matched SFX + micro vocal reaction + street bed."""
    dur = max(1.2, float(dur_sec))
    is_drama = _tone_is_dramatic(tone)
    tmp_dir = (work_dir / f"react_sfx_{uuid.uuid4().hex[:6]}") if work_dir else Path(f"/tmp/react_sfx_{uuid.uuid4().hex[:6]}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        bed_wav = tmp_dir / "bed.wav"
        sfx_wav = tmp_dir / "sfx.wav"
        _create_master_ambience_bed_wav(bed_wav, dur, target_lufs=-28.0)

        if is_drama:
            sfx_expr = "0.26*(sin(2*PI*(112-56*min(1,t/0.95))*t)+0.42*sin(2*PI*(224-112*min(1,t/0.95))*t))*exp(-1.7*t)"
        else:
            # Playful comic pizzicato chord (C5 -> E5 -> upward slide)
            sfx_expr = (
                "0.22*(sin(2*PI*523.25*t)*exp(-7.0*max(0,t-0.02))*between(t,0.02,0.55)"
                "+sin(2*PI*659.25*t)*exp(-5.5*max(0,t-0.16))*between(t,0.16,0.95)"
                "+0.65*sin(2*PI*(440+280*max(0,t-0.30))*t)*exp(-3.8*max(0,t-0.30))*between(t,0.30,1.60))"
            )
        _run_checked([
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-t", f"{dur:.3f}", "-i", f"aevalsrc=exprs='{sfx_expr}':s=48000:c=stereo",
            "-af", "loudnorm=I=-16:TP=-2:LRA=8,aresample=48000:resampler=soxr",
            "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            str(sfx_wav),
        ], timeout=30)

        vox_wav = tmp_dir / "react_vox.wav"
        vox_ok = False
        try:
            react_phrase = (reaction_text or "").strip() or ("Vixe Maria!" if is_drama else "Eita lasqueira!")
            rve.synthesize_regional_turn(
                tts_text=react_phrase,
                speaker=speaker,
                emotion="shocked" if is_drama else "laughing",
                accent_engine="nordestino_raiz",
                out_wav=vox_wav,
                lead_pad=0.01,
                tail_pad=0.04,
            )
            vox_ok = vox_wav.is_file() and vox_wav.stat().st_size > 4000
        except Exception:
            vox_ok = False

        if vox_ok:
            vox_fit = tmp_dir / "react_vox_fit.wav"
            vox_dur = _probe_duration(vox_wav)
            max_vox_slot = max(0.75, dur - 0.36)
            atempo = max(1.0, min(1.55, vox_dur / max_vox_slot))
            _run_checked([
                FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(vox_wav),
                "-af", f"atempo={atempo:.3f},loudnorm=I=-17:TP=-2:LRA=8,adelay=320|320,apad=whole_dur={dur:.3f}",
                "-t", f"{dur:.3f}", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                str(vox_fit),
            ], timeout=30)
            _run_checked([
                FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(bed_wav), "-i", str(sfx_wav), "-i", str(vox_fit),
                "-filter_complex", "[0:a][1:a][2:a]amix=inputs=3:duration=first:normalize=0,alimiter=limit=0.92[outa]",
                "-map", "[outa]", "-t", f"{dur:.3f}", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                str(out_wav),
            ], timeout=30)
        else:
            _run_checked([
                FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
                "-i", str(bed_wav), "-i", str(sfx_wav),
                "-filter_complex", "[0:a][1:a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.92[outa]",
                "-map", "[outa]", "-t", f"{dur:.3f}", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
                str(out_wav),
            ], timeout=30)
        return _probe_duration(out_wav)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _split_subtitle_events(start_t: float, end_t: float, text: str, max_chars: int = 42) -> List[Tuple[float, float, str]]:
    """Split subtitles > max_chars into 2 sequential timed ASS events so 100% of spoken audio is shown dynamically."""
    clean = re.sub(r"\s+", " ", (text or "").strip())
    if not clean or end_t <= start_t:
        return []
    if len(clean) <= max_chars or len(clean.split()) <= 4:
        return [(start_t, end_t, clean)]

    total = end_t - start_t
    if total < 0.75:
        return [(start_t, end_t, clean)]

    mid_char = len(clean) // 2
    best_split = -1
    best_dist = len(clean)
    # Prefer natural punctuation break near center
    for m in re.finditer(r"[,!?.;:]\s+", clean):
        idx = m.end() - 1
        words_l = clean[:idx].strip().split()
        words_r = clean[idx:].strip().split()
        if len(words_l) >= 2 and len(words_r) >= 2:
            dist = abs(idx - mid_char)
            if dist < best_dist:
                best_dist = dist
                best_split = idx

    if best_split != -1:
        p1 = clean[:best_split].strip()
        p2 = clean[best_split:].strip()
    else:
        words = clean.split()
        w_mid = max(2, len(words) // 2)
        p1 = " ".join(words[:w_mid]).strip()
        p2 = " ".join(words[w_mid:]).strip()

    if not p1 or not p2:
        return [(start_t, end_t, clean)]
    ratio = min(0.70, max(0.30, len(p1) / float(len(p1) + len(p2))))
    mid_t = start_t + total * ratio
    return [(start_t, mid_t, p1), (mid_t, end_t, p2)]


def _prepend_lead_silence_wav(src_wav: Path, lead_sec: float) -> float:
    """Prepend clean subtle lead silence (e.g. 0.45s Cut-2 breath pause) preserving 48kHz Stereo SoXR."""
    if lead_sec <= 0.02:
        return _probe_duration(src_wav)
    lead_wav = src_wav.with_suffix(".lead.wav")
    merged_wav = src_wav.with_suffix(".merged.wav")
    _synthesize_silent_ambient_wav(lead_wav, dur_sec=lead_sec)
    try:
        _run_checked([
            FFMPEG_BIN, "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(lead_wav), "-i", str(src_wav),
            "-filter_complex", "[0:a][1:a]concat=n=2:v=0:a=1,aresample=48000:resampler=soxr[outa]",
            "-map", "[outa]", "-ar", "48000", "-ac", "2", "-c:a", "pcm_s16le",
            str(merged_wav),
        ], timeout=45)
        shutil.move(str(merged_wav), str(src_wav))
    finally:
        lead_wav.unlink(missing_ok=True)
        merged_wav.unlink(missing_ok=True)
    return _probe_duration(src_wav)


def render_novela_episode(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Execute end-to-end multi-character micro-series production across v4 Cinema & v3.1 Fallback."""
    t_start_wall = time.time()
    render_mode = str(cfg.get("render_mode") or "vps_preview").strip().lower()
    if render_mode == "flow_pack":
        return generate_flow_veo_pack(cfg)

    if _resolve_cinema_engine(cfg) == "v4":
        raw_in = list(cfg.get("dialogue") or [])
        if not raw_in:
            raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala para render Cinema v4.")
        env_map = _load_secrets_env()
        tone = str(cfg.get("tone") or "Drama Tenso").strip()
        platform_preset = str(cfg.get("platform_preset") or "tiktok_reels").strip().lower()
        safe_zone = PLATFORM_SAFE_ZONES.get(platform_preset) or PLATFORM_SAFE_ZONES["tiktok_reels"]
        subtitles_mode = str(cfg.get("subtitles_mode") or "none").strip().lower()
        accent_engine = str(cfg.get("accent_engine") or cfg.get("accent_preset") or "nordestino_raiz").strip().lower()
        colab_url = str(cfg.get("colab_url") or env_map.get("COLAB_GPU_URL") or "").strip()
        colab_token = str(cfg.get("colab_token") or env_map.get("COLAB_GPU_TOKEN") or "").strip()

        bible, shots = cv4.compile_cinema_v4_shots(cfg, raw_in)
        job_id = uuid.uuid4().hex[:8]
        work_dir = Path(cfg.get("work_dir") or f"/tmp/hf_novela_v4_{job_id}")
        work_dir.mkdir(parents=True, exist_ok=True)
        out_raw = cfg.get("output")
        if out_raw:
            out_path = Path(str(out_raw))
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", bible.title.lower()).strip("-")[:32] or "novela"
            out_path = NOVELA_EXPORT_DIR / f"novela_v4_{slug}_{int(time.time())}_{job_id}.mp4"
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # Check if Colab L4 v4.0 full-scene async worker is online
        colab_v4_ready = False
        if colab_url:
            try:
                hdrs = {"Accept": "application/json"}
                if colab_token:
                    hdrs["X-Colab-Token"] = colab_token
                hreq = urllib.request.Request(f"{colab_url.rstrip('/')}/health", headers=hdrs, method="GET")
                with urllib.request.urlopen(hreq, timeout=3.0) as hr:
                    hdata = json.loads(hr.read().decode("utf-8"))
                colab_v4_ready = (
                    str(hdata.get("version", "")).startswith("4.0")
                    and bool(hdata.get("wangp_ready") or hdata.get("ltx2_ready") or hdata.get("longcat_ready"))
                )
            except Exception:
                colab_v4_ready = False

        try:
            assets = cv4.generate_v4_cinema_assets(bible, shots, work_dir, (env_map.get("NVIDIA_API_KEY") or "").strip())
            wavs: List[Optional[Path]] = []
            clips: List[Path] = []
            voice_engines: List[str] = []
            video_engines: List[str] = []

            for idx, shot in enumerate(shots):
                sa = assets["shot_assets"][idx]
                kf_path = Path(sa["image_path"]) if sa.get("image_path") else Path(assets["set_master_path"])
                w_path: Optional[Path] = None
                if shot.tts_text:
                    w_path = work_dir / f"v4_tts_{idx:02d}.wav"
                    meta_t = rve.synthesize_regional_turn(
                        tts_text=shot.tts_text,
                        speaker=shot.speaker_label,
                        emotion=shot.semantic_emotion,
                        accent_engine=accent_engine,
                        out_wav=w_path,
                        colab_url=colab_url,
                        colab_token=colab_token,
                        drama_pacing=bible.is_drama,
                        performance_direction=shot.performance_direction_pt,
                        lead_pad=0.04,
                        tail_pad=0.06,
                    )
                    voice_engines.append(meta_t["voice_engine_used"])
                    shot.target_duration_sec = round(max(0.85, min(4.6, float(meta_t["duration"]) - shot.l_cut_bleed_sec + shot.lead_in_pause_sec)), 2)
                wavs.append(w_path)

                seg_mp4 = work_dir / f"v4_clip_{idx:02d}.mp4"
                animated_v4 = False
                if colab_v4_ready and kf_path.is_file():
                    try:
                        hdrs = {"Accept": "application/json", "Content-Type": "application/json"}
                        if colab_token:
                            hdrs["X-Colab-Token"] = colab_token
                        ce_req = str(cfg.get("colab_engine") or "").strip().lower()
                        cb_backend = "ltx2_25_22B_distilled" if "ltx2" in ce_req else "auto"
                        payload_j = {
                            "image_b64": base64.b64encode(kf_path.read_bytes()).decode("ascii"),
                            "audio_b64": base64.b64encode(w_path.read_bytes()).decode("ascii") if (w_path and w_path.is_file()) else "",
                            "prompt": sa["video_prompt"],
                            "backend": cb_backend,
                            "duration_sec": shot.target_duration_sec,
                            "width": 720,
                            "height": 1280,
                            "fps": 24,
                        }
                        sreq = urllib.request.Request(
                            f"{colab_url.rstrip('/')}/jobs/animate_scene",
                            data=json.dumps(payload_j).encode("utf-8"),
                            headers=hdrs,
                            method="POST",
                        )
                        with urllib.request.urlopen(sreq, timeout=20) as sr:
                            sdata = json.loads(sr.read().decode("utf-8"))
                        jid = sdata.get("job_id")
                        deadline = time.time() + 420
                        while jid and time.time() < deadline:
                            time.sleep(3.0)
                            greq = urllib.request.Request(f"{colab_url.rstrip('/')}/jobs/{jid}", headers=hdrs, method="GET")
                            with urllib.request.urlopen(greq, timeout=15) as gr:
                                gdata = json.loads(gr.read().decode("utf-8"))
                            if gdata.get("status") == "completed" and gdata.get("video_b64"):
                                seg_mp4.write_bytes(base64.b64decode(gdata["video_b64"]))
                                animated_v4 = True
                                video_engines.append(f"colab_v4:{gdata.get('backend') or 'wan22'}")
                                break
                            if gdata.get("status") == "error":
                                raise RuntimeError(gdata.get("error") or "colab_v4_error")
                    except Exception as cb_v4_exc:
                        print(f"[CINEMA V4 WARN] Colab v4 full-scene take {idx+1} falhou ({cb_v4_exc}); usando storyboard animado.", flush=True)

                if not animated_v4:
                    # Storyboard camera motion preview (never falls back to v3.1 frontal head warp!)
                    dur = max(0.85, shot.target_duration_sec)
                    nfr = int(round(dur * 24))
                    _run_checked([
                        FFMPEG_BIN, "-y", "-loop", "1", "-i", str(kf_path),
                        "-vf", f"scale=1200:2134:flags=lanczos,zoompan=z='min(zoom+0.0028,1.14)':d={nfr}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s=1080x1920:fps=24,format=yuv420p",
                        "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "fast", "-crf", "18", str(seg_mp4),
                    ], timeout=90)
                    video_engines.append("storyboard_v4_motion_preview")
                clips.append(seg_mp4)

            st_out = cv4.stitch_cinema_v4_episode(
                clip_paths=clips,
                dialogue_wavs=wavs,
                shot_specs=shots,
                out_mp4=out_path,
                tone=tone,
                subtitles_mode=subtitles_mode,
                safe_bottom=int(safe_zone["safe_bottom"]),
                safe_right=int(safe_zone["safe_right"]),
            )
            return {
                "ok": True,
                "cinema_engine": "v4",
                "output_path": str(out_path),
                "filename": out_path.name,
                "render_mode": render_mode,
                "visual_style": bible.visual_style,
                "tone": bible.tone,
                "prompt_rules": "v4_cinema_ots_bible",
                "platform_preset": platform_preset,
                "subtitles_mode": subtitles_mode,
                "duration": st_out["duration"],
                "scenes": len(shots),
                "turns": len(shots),
                "speakers": sorted({s.speaker_label for s in shots}),
                "shot_framings": [s.shot_framing for s in shots],
                "has_l_cuts": st_out["has_l_cuts"],
                "voice_engines_used": sorted(set(voice_engines)),
                "animation_engines_used": sorted(set(video_engines)),
                "elapsed_sec": round(time.time() - t_start_wall, 2),
            }
        finally:
            if not bool(cfg.get("keep_work_dir")):
                shutil.rmtree(work_dir, ignore_errors=True)

    visual_style = normalize_visual_style(str(cfg.get("visual_style") or "stylized_3d_pixar"))
    dramatic_structure = normalize_dramatic_structure(str(cfg.get("dramatic_structure") or "viral_4_cuts"))

    platform_preset = str(cfg.get("platform_preset") or "tiktok_reels").strip().lower()
    safe_zone = PLATFORM_SAFE_ZONES.get(platform_preset) or PLATFORM_SAFE_ZONES["tiktok_reels"]

    clean_cinema_mode = bool(cfg.get("clean_cinema_mode", True))
    subtitles_mode = str(cfg.get("subtitles_mode") or ("none" if clean_cinema_mode else "minimal")).strip().lower()
    if subtitles_mode not in ("none", "minimal"):
        subtitles_mode = "none" if clean_cinema_mode else "minimal"
    include_dictionary_endcard = bool(cfg.get("include_dictionary_endcard", False if clean_cinema_mode else True))

    raw_dialogue = compile_dramatic_cuts(list(cfg.get("dialogue") or []), dramatic_structure=dramatic_structure)
    if not raw_dialogue:
        raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala de personagem.")

    default_setting = (
        "sunlit contemporary Brazilian urban cafe sidewalk with warm bokeh depth and soft passersby"
        if visual_style == "cinematic_live_action"
        else "sunny cobblestone sidewalk outside a painted wooden country corner grocery shop in Northeast Brazil with festive triangular flags and curious bystanders, warm afternoon sunlight"
    )
    setting = str(cfg.get("setting") or default_setting).strip()

    freeze_dur = float(cfg.get("freeze_duration") or (0.0 if not include_dictionary_endcard else 3.5))
    quality = str(cfg.get("quality") or "standard").strip().lower()
    if quality not in ("draft", "looks", "delivery", "standard", "high"):
        quality = "standard"

    job_id = uuid.uuid4().hex[:8]
    work_dir = Path(cfg.get("work_dir") or f"/tmp/hf_novela_{job_id}")
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)
    assets_dir = work_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    audio_dir = work_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    output_raw = cfg.get("output")
    if output_raw:
        out_path = Path(str(output_raw))
    else:
        slug = re.sub(r"[^a-z0-9]+", "-", str(cfg.get("title") or "novela").lower()).strip("-")[:32] or "novela"
        out_path = NOVELA_EXPORT_DIR / f"novela_{slug}_{visual_style}_{int(time.time())}_{job_id}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    episode_tone = str(cfg.get("tone") or "Comédia Leve").strip()
    env_map = _load_secrets_env()
    shot_cache: Dict[Tuple[Any, ...], Tuple[Path, str]] = {}
    compiled_turns: List[TurnCompiled] = []
    cursor_t = 0.0
    prev_speaker: Optional[str] = None

    for idx, raw_turn in enumerate(raw_dialogue):
        spk_raw = str(raw_turn.get("speaker") or "Narrador")
        spk_key, spec = _get_character_spec(spk_raw)
        is_silent = bool(raw_turn.get("is_silent_reaction", False) or raw_turn.get("is_reaction", False))
        cut_role = str(raw_turn.get("cut_role") or ("silent_shame_reaction" if is_silent else "dialogue")).strip().lower()
        lead_pause = float(raw_turn.get("lead_in_pause") or 0.0)
        tts_text = "" if is_silent else rve.sanitize_northeast_slang(str(raw_turn.get("tts_text") or raw_turn.get("subtitle_text") or raw_turn.get("text") or "").strip())
        if not tts_text and not is_silent:
            continue
        raw_sub = "" if is_silent else rve.sanitize_northeast_slang(str(raw_turn.get("subtitle_text") or tts_text).strip())
        subtitle_text = "" if is_silent else (tts_text if (not raw_sub or len(raw_sub.split()) < len(tts_text.split())) else raw_sub)

        if is_silent:
            emotion = str(raw_turn.get("emotion") or ("shame" if _tone_is_dramatic(episode_tone) else "happiness")).strip().lower()
        else:
            emotion = str(raw_turn.get("emotion") or "warm").strip().lower()
        _, default_cam, _ = EMOTION_DIRECTIONS.get(emotion, EMOTION_DIRECTIONS["warm"])
        camera_action = str(raw_turn.get("camera_action") or default_cam).strip().lower()
        shot_framing = _assign_shot_framing(
            idx,
            str(raw_turn.get("shot_framing") or ""),
            emotion,
            prev_speaker,
            spk_key,
        )
        physical_action_en = str(raw_turn.get("physical_action_en") or "").strip()
        highlight_phrase = str(raw_turn.get("expression_highlight") or raw_turn.get("highlight") or "").strip()

        clean_wav = audio_dir / f"turn_{idx}.wav"
        if is_silent:
            react_phrase_in = str(raw_turn.get("reaction_phrase") or raw_turn.get("tts_text") or "").strip()
            slot_dur = _synthesize_reaction_soundtrack_wav(
                clean_wav,
                dur_sec=float(raw_turn.get("silent_duration") or 2.0),
                tone=episode_tone,
                speaker=spk_raw,
                reaction_text=react_phrase_in,
                env_map=env_map,
                work_dir=work_dir,
            )
            v_engine_used = f"reaction_sfx_{'drama' if _tone_is_dramatic(episode_tone) else 'comedy'}"
        else:
            guide_raw = str(raw_turn.get("guide_audio_path") or "").strip()
            guide_path = Path(guide_raw) if guide_raw and Path(guide_raw).is_file() else None
            accent_engine = str(cfg.get("accent_engine") or cfg.get("accent_preset") or "nordestino_raiz").strip().lower()
            colab_url_eff = str(cfg.get("colab_url") or env_map.get("COLAB_GPU_URL") or "").strip()
            colab_tok_eff = str(cfg.get("colab_token") or env_map.get("COLAB_GPU_TOKEN") or "").strip()
            tts_meta = rve.synthesize_regional_turn(
                tts_text=tts_text,
                speaker=spk_raw,
                emotion=emotion,
                accent_engine=accent_engine,
                out_wav=clean_wav,
                guide_audio_path=guide_path,
                colab_url=colab_url_eff,
                colab_token=colab_tok_eff,
                lead_pad=0.02 if clean_cinema_mode else 0.04,
                tail_pad=0.05 if clean_cinema_mode else 0.14,
                voice_override=str(raw_turn.get("voice") or ""),
            )
            slot_dur = float(tts_meta["duration"])
            if lead_pause > 0.05:
                slot_dur = _prepend_lead_silence_wav(clean_wav, lead_pause)
            v_engine_used = str(tts_meta["voice_engine_used"])

        shot_abs_path, shot_relpath, asset_source, _ = _resolve_turn_visual(
            speaker_key=spk_key,
            spec=spec,
            emotion=emotion,
            shot_framing=shot_framing,
            physical_action_en=physical_action_en,
            visual_style=visual_style,
            setting=setting,
            assets_dir=assets_dir,
            turn_idx=idx,
            env_map=env_map,
            shot_cache=shot_cache,
            force_jpg=(render_mode == "colab_gpu"),
            is_silent_reaction=is_silent,
        )

        compiled_turns.append(
            TurnCompiled(
                index=idx,
                speaker_key=spk_key,
                speaker_label=spec["label"],
                role=spec["role"],
                badge_bg=spec["badge_bg"],
                badge_fg=spec["badge_fg"],
                accent=spec["accent"],
                tts_text=tts_text,
                subtitle_text=subtitle_text,
                emotion=emotion,
                camera_action=camera_action,
                shot_framing=shot_framing,
                physical_action_en=physical_action_en,
                highlight_phrase=highlight_phrase,
                audio_wav=clean_wav,
                speech_dur=slot_dur,
                slot_start=cursor_t,
                slot_dur=slot_dur,
                shot_relpath=shot_relpath,
                shot_abs_path=shot_abs_path,
                asset_source=asset_source,
                voice_engine_used=v_engine_used,
                cut_role=cut_role,
                lead_in_pause=lead_pause,
                is_silent_reaction=is_silent,
            )
        )
        cursor_t += slot_dur
        prev_speaker = spk_key

    if not compiled_turns:
        raise ValueError("Nenhuma fala válida encontrada em 'dialogue'.")

    # Mode 2: Remote Colab GPU Lip-Sync Bridge
    if render_mode == "colab_gpu":
        colab_url = str(cfg.get("colab_url") or env_map.get("COLAB_GPU_URL") or "").strip()
        if not colab_url:
            raise RuntimeError("COLAB_GPU_URL não configurada! Informe a URL do túnel Cloudflare do Google Colab.")
        colab_token = str(cfg.get("colab_token") or env_map.get("COLAB_GPU_TOKEN") or "").strip()
        colab_engine = str(cfg.get("colab_engine") or "liveportrait_latentsync").strip().lower()

        colab_clips: List[Path] = []
        for t in compiled_turns:
            _, spk_spec = _get_character_spec(t.speaker_label)
            v_prompt = build_veo_take_prompt(
                speaker_spec=spk_spec,
                tts_text=t.tts_text,
                visual_style=visual_style,
                shot_framing=t.shot_framing,
                emotion=t.emotion,
                physical_action_en=t.physical_action_en,
                setting=setting,
                cut_role=t.cut_role,
                lead_in_pause=t.lead_in_pause,
                is_silent_reaction=t.is_silent_reaction,
            )
            take_mp4 = work_dir / f"colab_take_{t.index:02d}.mp4"
            colab_meta = _call_colab_animate_take(
                colab_url=colab_url,
                colab_token=colab_token,
                image_path=t.shot_abs_path,
                audio_wav=t.audio_wav,
                veo_prompt=v_prompt,
                colab_engine=colab_engine,
                out_mp4=take_mp4,
                character_id=t.speaker_key,
                emotion=t.emotion,
                cfg_scale=(1.35 if t.is_silent_reaction else float(cfg.get("cfg_scale") or 1.25)),
            )
            t.animation_engine_used = str(colab_meta.get("engine") or f"tier1_colab:{colab_engine}")
            colab_clips.append(take_mp4)

        stitch_res = stitch_novela_takes({
            "take_paths": [str(p) for p in colab_clips],
            "platform_preset": platform_preset,
            "subtitles_mode": subtitles_mode,
            "trim_silence": False,
            "output": str(out_path),
        })
        if not bool(cfg.get("keep_work_dir")):
            shutil.rmtree(work_dir, ignore_errors=True)
        return {
            **stitch_res,
            "render_mode": "colab_gpu",
            "colab_engine": colab_engine,
            "visual_style": visual_style,
            "dramatic_structure": dramatic_structure,
            "clean_cinema_mode": clean_cinema_mode,
            "lipsync_enabled": True,
            "animation_engines_used": sorted({t.animation_engine_used for t in compiled_turns if t.animation_engine_used}),
            "voice_engines_used": sorted({t.voice_engine_used for t in compiled_turns if not t.is_silent_reaction}),
            "speakers": sorted({t.speaker_label for t in compiled_turns}),
            "asset_sources": sorted({f"{t.asset_source}+colab:{colab_engine}" for t in compiled_turns}),
            "elapsed_sec": round(time.time() - t_start_wall, 2),
        }

    # Mode 3 (Default v2.8): True Lip-Sync & Facial Motion Cascade via novela_lipsync_animator.py
    enable_lipsync = bool(cfg.get("enable_lipsync", True))
    if enable_lipsync:
        colab_url = str(cfg.get("colab_url") or env_map.get("COLAB_GPU_URL") or "").strip()
        colab_token = str(cfg.get("colab_token") or env_map.get("COLAB_GPU_TOKEN") or "").strip()
        colab_engine = str(cfg.get("colab_engine") or "liveportrait_latentsync").strip().lower()
        animated_clips: List[Path] = []
        lipsync_turns: List[Dict[str, Any]] = []
        ass_events: List[Tuple[float, float, str]] = []
        tl_t = 0.0

        for t in compiled_turns:
            _, spk_spec = _get_character_spec(t.speaker_label)
            v_prompt = build_veo_take_prompt(
                speaker_spec=spk_spec,
                tts_text=t.tts_text,
                visual_style=visual_style,
                shot_framing=t.shot_framing,
                emotion=t.emotion,
                physical_action_en=t.physical_action_en,
                setting=setting,
                cut_role=t.cut_role,
                lead_in_pause=t.lead_in_pause,
                is_silent_reaction=t.is_silent_reaction,
                tone=episode_tone,
            )
            take_mp4 = work_dir / f"lipsync_take_{t.index:02d}.mp4"
            anim_meta = nla.animate_character_talking_take(
                image_path=t.shot_abs_path,
                audio_wav=t.audio_wav,
                out_mp4=take_mp4,
                prompt=v_prompt,
                character_id=t.speaker_key,
                emotion=t.emotion,
                camera_action=t.camera_action,
                emotion_preset=t.cut_role,
                cfg_scale=(1.35 if t.is_silent_reaction else float(cfg.get("cfg_scale") or 1.25)),
                lead_in_pause=t.lead_in_pause,
                is_silent_reaction=t.is_silent_reaction,
                tone=episode_tone,
                colab_url=colab_url,
                colab_token=colab_token,
                colab_engine=colab_engine,
                env_map=env_map,
                width=1080,
                height=1920,
                fps=30,
            )
            t.animation_engine_used = str(anim_meta.get("animation_engine_used") or "local_viseme_30fps")
            t.lipsync_peak_open = float(anim_meta.get("peak_mouth_open") or 0.0)
            animated_clips.append(take_mp4)
            actual_turn_dur = float(anim_meta.get("duration") or t.slot_dur)
            lipsync_turns.append({
                "index": t.index,
                "speaker": t.speaker_label,
                "cut_role": t.cut_role,
                "is_silent_reaction": t.is_silent_reaction,
                "lead_in_pause": t.lead_in_pause,
                "animation_engine": t.animation_engine_used,
                "voice_engine": t.voice_engine_used,
                "peak_mouth_open": t.lipsync_peak_open,
                "duration": actual_turn_dur,
            })
            if subtitles_mode == "minimal" and t.subtitle_text and not t.is_silent_reaction:
                ev_start = tl_t + max(0.02, t.lead_in_pause)
                ev_end = tl_t + max(0.35, actual_turn_dur - 0.02)
                ass_events.extend(_split_subtitle_events(ev_start, ev_end, t.subtitle_text))
            tl_t += actual_turn_dur

        concat_list = work_dir / "lipsync_clips.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in animated_clips) + "\n", encoding="utf-8")
        concat_mp4 = work_dir / "lipsync_concat.mp4"
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(concat_list),
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(concat_mp4),
        ], timeout=120)

        vf_list: List[str] = []
        if subtitles_mode == "minimal" and ass_events:
            margin_v = max(80, 1920 - int(safe_zone["safe_bottom"]) + 20)
            margin_r = max(60, 1080 - int(safe_zone["safe_right"]))
            ass_path = work_dir / "lipsync_caps.ass"
            ass_header = (
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding\n"
                f"Style: CleanMin,DejaVu Sans,42,&H00FFFFFF,&H0000FFFF,&H00000000,&H00000000,-1,0,0,0,100,100,0,0,1,3,1,2,56,{margin_r},{margin_v},1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            )
            ev_lines = [f"Dialogue: 0,{_format_ass_ts(s)},{_format_ass_ts(e)},CleanMin,,0,0,0,,{txt}" for s, e, txt in ass_events]
            ass_path.write_text(ass_header + "\n".join(ev_lines) + "\n", encoding="utf-8")
            vf_list.append(f"ass={ass_path}")

        concat_dur = _probe_duration(concat_mp4)
        master_bed_wav = work_dir / "master_ambience_bed.wav"
        _create_master_ambience_bed_wav(master_bed_wav, concat_dur + 0.5, target_lufs=-33.0)

        final_cmd = [FFMPEG_BIN, "-y", "-i", str(concat_mp4), "-i", str(master_bed_wav)]
        audio_fc = "[0:a]loudnorm=I=-16:TP=-1.5:LRA=11[speech_a];[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[bed_a];[speech_a][bed_a]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[outa]"
        if vf_list:
            filter_complex = f"[0:v]{','.join(vf_list)}[outv];{audio_fc}"
            final_cmd.extend([
                "-filter_complex", filter_complex,
                "-map", "[outv]", "-map", "[outa]",
                "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            ])
        else:
            final_cmd.extend([
                "-filter_complex", audio_fc,
                "-map", "0:v:0", "-map", "[outa]",
                "-c:v", "copy",
            ])
        final_cmd.extend([
            "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            "-movflags", "+faststart",
            str(out_path),
        ])
        _run_checked(final_cmd, timeout=180)
        final_dur = _probe_duration(out_path)

        if not bool(cfg.get("keep_work_dir")):
            shutil.rmtree(work_dir, ignore_errors=True)

        return {
            "ok": True,
            "output_path": str(out_path),
            "filename": out_path.name,
            "render_mode": render_mode,
            "visual_style": visual_style,
            "dramatic_structure": dramatic_structure,
            "tone": episode_tone,
            "prompt_rules": "v31_frontal_moda_lock",
            "ass_events": [{"start": round(s, 2), "end": round(e, 2), "text": txt} for s, e, txt in ass_events],
            "platform_preset": platform_preset,
            "clean_cinema_mode": clean_cinema_mode,
            "subtitles_mode": subtitles_mode,
            "lipsync_enabled": True,
            "duration": round(final_dur, 2),
            "scenes": len(compiled_turns),
            "turns": len(compiled_turns),
            "speakers": sorted({t.speaker_label for t in compiled_turns}),
            "shot_framings": [t.shot_framing for t in compiled_turns],
            "accent_engine": str(cfg.get("accent_engine") or cfg.get("accent_preset") or "nordestino_raiz"),
            "voice_engines_used": sorted({t.voice_engine_used for t in compiled_turns if not t.is_silent_reaction}),
            "animation_engines_used": sorted({t.animation_engine_used for t in compiled_turns if t.animation_engine_used}),
            "lipsync_turns": lipsync_turns,
            "asset_sources": sorted({t.asset_source for t in compiled_turns}),
            "elapsed_sec": round(time.time() - t_start_wall, 2),
        }

    # Mode 3 (Default): Clean Cinema Storyboard Preview rendered locally via HyperFrames Chromium + 48kHz SoXR
    freeze_start = cursor_t
    total_dur = freeze_start + freeze_dur

    final_mix_wav = _build_master_audio_mix(
        turns=compiled_turns,
        freeze_start=freeze_start,
        freeze_dur=freeze_dur,
        total_dur=total_dur,
        work_dir=work_dir,
        include_freeze_sting=include_dictionary_endcard,
    )

    _build_hyperframes_html(
        cfg=cfg,
        turns=compiled_turns,
        freeze_start=freeze_start,
        freeze_dur=freeze_dur,
        total_dur=total_dur,
        safe_zone=safe_zone,
        visual_style=visual_style,
        clean_cinema_mode=clean_cinema_mode,
        subtitles_mode=subtitles_mode,
        include_dictionary_endcard=include_dictionary_endcard,
        work_dir=work_dir,
    )

    check_proc = subprocess.run(
        [HF_BIN, "check", str(work_dir), "--json"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=45,
    )
    if check_proc.returncode != 0:
        raise RuntimeError(f"HyperFrames check failed: {check_proc.stdout}\n{check_proc.stderr}")

    silent_mp4 = work_dir / "video_silent.mp4"
    _run_checked([
        HF_BIN, "render", str(work_dir),
        "-o", str(silent_mp4),
        "-f", "30",
        "-q", quality,
        "--low-memory-mode",
    ], cwd=work_dir, timeout=600)

    _run_checked([
        FFMPEG_BIN, "-y",
        "-i", str(silent_mp4),
        "-i", str(final_mix_wav),
        "-c:v", "copy",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        "-shortest",
        str(out_path),
    ], timeout=120)

    if not bool(cfg.get("keep_work_dir")):
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "ok": True,
        "output_path": str(out_path),
        "filename": out_path.name,
        "render_mode": render_mode,
        "visual_style": visual_style,
        "platform_preset": platform_preset,
        "clean_cinema_mode": clean_cinema_mode,
        "subtitles_mode": subtitles_mode,
        "duration": round(total_dur, 2),
        "scenes": len(compiled_turns) + (1 if include_dictionary_endcard else 0),
        "turns": len(compiled_turns),
        "speakers": sorted({t.speaker_label for t in compiled_turns}),
        "shot_framings": [t.shot_framing for t in compiled_turns],
        "accent_engine": str(cfg.get("accent_engine") or cfg.get("accent_preset") or "nordestino_raiz"),
        "voice_engines_used": sorted({t.voice_engine_used for t in compiled_turns}),
        "asset_sources": sorted({t.asset_source for t in compiled_turns}),
        "elapsed_sec": round(time.time() - t_start_wall, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="VPS Novela Studio Runner v2.5")
    parser.add_argument("--config", required=True, help="Path to JSON config")
    parser.add_argument("--mode", default="render", choices=["render", "flow_pack", "stitch"], help="Execution mode")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    if args.mode == "flow_pack":
        res = generate_flow_veo_pack(cfg)
    elif args.mode == "stitch":
        res = stitch_novela_takes(cfg)
    else:
        res = render_novela_episode(cfg)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
