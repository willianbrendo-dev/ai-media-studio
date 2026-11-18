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
            "cheerful expressive 65-year-old senior lady named Socorro wearing a bright yellow floral cotton apron "
            "over a teal blouse, small pearl earrings, round tortoiseshell eyeglasses resting on her nose, "
            "silver hair in a neat bun, playful animated eyebrows"
        ),
        "lock_tv": (
            "charismatic 65-year-old Brazilian senior lady wearing a bright yellow floral apron over a teal blouse, "
            "small pearl earrings, round tortoiseshell eyeglasses, silver hair tied in a neat bun, natural comedy poise"
        ),
        "veo_desc": (
            "Dona Socorro, an expressive 60-year-old Northeastern Brazilian matriarch wearing a bright yellow floral cotton apron "
            "over a teal blouse, pearl earrings, round tortoiseshell glasses, silver hair in a neat bun"
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
            "open unbuttoned blue plaid shirt over a white tee, neat silver mustache, holding a small aluminum coffee cup, "
            "wisecracking half-smile"
        ),
        "lock_tv": (
            "humorous 68-year-old Brazilian countryside shopkeeper gentleman wearing a woven straw hat and open blue plaid "
            "cotton shirt over a crisp white tee, neat silver mustache, holding a metal coffee cup, amused squinting eyes"
        ),
        "veo_desc": (
            "Seu Bastião, a witty 65-year-old countryside shopkeeper wearing a woven straw hat and open blue plaid shirt "
            "over a white cotton tee, neat silver mustache, holding a small aluminum coffee cup"
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
            "dramatic goofy 22-year-old city young man wearing a trendy oversized lavender hoodie, "
            "dark sunglasses pushed up onto wavy dark hair, holding a smartphone tightly, wide comedic startled eyes"
        ),
        "lock_tv": (
            "expressive 22-year-old young Brazilian city guy wearing an oversized pastel purple sweatshirt with sunglasses "
            "pushed up onto curly dark hair, holding a modern smartphone, bewildered comical facial expression"
        ),
        "veo_desc": (
            "Enzo, a confused 22-year-old city nephew wearing an oversized lavender hoodie with sunglasses pushed up onto "
            "dark curly hair, gripping his smartphone"
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
            "sunny colorful village cobblestone street corner outside a cozy wooden rustic countryside grocery shop "
            "in Northeast Brazil with festive hanging colorful triangular flags and clay roof tiles, golden light, no text"
        ),
        "lock_tv": (
            "wide 35mm golden hour exterior establishing frame of a charming rustic cobblestone sidewalk corner shop in "
            "Northeast Brazil with warm painted walls, terracotta tiles, wooden porch chairs, no text"
        ),
        "veo_desc": (
            "A lively village observer on the sunlit Northeastern Brazilian cobblestone corner sidewalk with bystanders watching"
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

EMOTION_DIRECTIONS: Dict[str, Tuple[str, str, str]] = {
    # (flux_desc [neutral/ready-to-speak mouth for Lip-Sync & Veo], default_cam, veo_acting)
    "shocked": (
        "eyes wide open in comic astonishment, eyebrows raised high, expressive face with lips gently closed ready to speak, hands raised energetically",
        "shake",
        "widens eyes in shock, throws both hands up energetically",
    ),
    "confused": (
        "head tilted, squinting suspiciously, eyebrows knotted in puzzlement, closed neutral lips ready to speak",
        "zoom_in",
        "blinks rapidly, tilts head sideways squinting in baffled city-kid panic",
    ),
    "laughing": (
        "warm amused eyes crinkling joyfully, relaxed closed-lip grin ready to speak, cheerful posture",
        "whip_pan",
        "bursts into hearty amused laughter with a teasing wink",
    ),
    "proud": (
        "chest puffed out, wise amused closed-lip smirk ready to speak, wagging one finger playfully",
        "zoom_in",
        "leans forward with a proud knowing smirk wagging one finger playfully",
    ),
    "dramatic": (
        "hand over chest in comical telenovela dramatization, expressive eyes, relaxed closed lips ready to speak",
        "dramatic_push",
        "puts hands on hips then dramatically gasps in pure soap-opera exaggeration",
    ),
    "warm": (
        "friendly welcoming smile with relaxed closed lips ready to speak, leaning forward mid-story",
        "zoom_in",
        "smiles warmly nodding animatedly mid-conversation",
    ),
}

SHOT_FRAMINGS: Dict[str, Dict[str, str]] = {
    "over_shoulder": {
        "label": "Plano · Over-the-Shoulder (Por cima do ombro)",
        "lens_mm": "35mm",
        "flux_cue": (
            "cinematic over-the-shoulder medium close-up framing looking slightly past the blurred foreground shoulder edge "
            "of another person on the lower-left frame, curious village onlookers softly out of focus behind"
        ),
        "veo_cue": (
            "Over-the-shoulder medium close-up camera shot (35mm lens) looking past the foreground listener's shoulder, "
            "background bystanders turning their heads subtly in reaction"
        ),
    },
    "reverse_over_shoulder": {
        "label": "Contraplano · Reverse Over-the-Shoulder",
        "lens_mm": "35mm",
        "flux_cue": (
            "reverse-angle over-the-shoulder medium close-up framing facing the opposite angle, soft blurred shoulder corner "
            "on lower-right foreground, character responding across the conversation"
        ),
        "veo_cue": (
            "Reverse-angle over-the-shoulder medium close-up (35mm lens) cutting sharply from the opposite conversational perspective"
        ),
    },
    "reaction_closeup": {
        "label": "Close-Up · Reação Dramática",
        "lens_mm": "50mm",
        "flux_cue": (
            "dramatic tight reaction close-up portrait framed from chest up, intense expressive face looking just off-lens, "
            "50mm crisp shallow bokeh"
        ),
        "veo_cue": (
            "Tight dramatic reaction close-up (50mm lens, shallow depth of field), sharp eye focus with natural handheld breathing"
        ),
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
        "lock_3d": f"friendly expressive Brazilian countryside resident named {display} on a village sidewalk",
        "lock_tv": f"charismatic Brazilian town character named {display} outdoors in golden afternoon sunlight",
        "veo_desc": f"{display}, a spirited Brazilian countryside character outdoors in warm natural daylight",
    }


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


def _call_nvidia_flux_image(
    prompt: str,
    api_key: str,
    out_jpg: Path,
    model: str = "black-forest-labs/flux.1-dev",
) -> Tuple[bool, str]:
    """Generate a clean vertical 9:16 portrait/scene image via NVIDIA NIM Flux API."""
    if not api_key:
        return False, "no_nvidia_key"
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
        with urllib.request.urlopen(req, timeout=75) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        arts = data.get("artifacts") or []
        if not arts:
            return False, "empty_artifacts"
        art = arts[0]
        finish = str(art.get("finishReason") or "SUCCESS").upper()
        if finish == "CONTENT_FILTERED":
            return False, "content_filtered"
        raw = base64.b64decode(art.get("base64") or "")
        if len(raw) < 20000:
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
        return True, model
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}"
    except Exception as exc:
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
    shot_cache: Dict[Tuple[str, str, str, str], Tuple[Path, str]],
    target_filename: Optional[str] = None,
    force_jpg: bool = False,
) -> Tuple[Path, str, str]:
    """Generate/resolve a clean 1080x1920 Shot-Reverse-Shot portrait image for one turn."""
    is_3d = visual_style == "3d_cartoon"
    emo_bucket = "shocked" if emotion in ("shocked", "confused", "dramatic") else "warm"
    cache_key = (speaker_key, emo_bucket, visual_style, shot_framing)

    base_name = target_filename or f"shot_{turn_idx}.jpg"
    if cache_key in shot_cache:
        src_path, prov = shot_cache[cache_key]
        suffix = ".jpg" if force_jpg else src_path.suffix
        dst = assets_dir / Path(base_name).with_suffix(suffix).name
        if src_path.suffix == ".svg" and force_jpg:
            _svg_to_clean_jpg(src_path, dst)
        else:
            shutil.copy2(src_path, dst)
        return dst, f"assets/{dst.name}", prov

    style_prefix = (
        "Stylized 3D animated CG feature-film frame, sculpted stylized characters, expressive comedy cartoon eyes, "
        "soft warm subsurface scattering rim light, rich saturated colors, no text, no subtitles, no watermark"
        if is_3d
        else "Cinematic 35mm live-action Brazilian television telenovela still frame, realistic expressive acting, "
             "shallow depth of field f/1.8 bokeh, golden hour natural sunlight, rich color grading, no text, no subtitles, no watermark"
    )
    char_lock = spec["lock_3d"] if is_3d else spec["lock_tv"]
    emo_desc, _, _ = EMOTION_DIRECTIONS.get(emotion, EMOTION_DIRECTIONS["warm"])
    framing_meta = SHOT_FRAMINGS.get(shot_framing, SHOT_FRAMINGS["over_shoulder"])
    action_clause = f", {physical_action_en}" if physical_action_en else ""
    prompt = (
        f"{style_prefix}. {framing_meta['flux_cue'].format(speaker_en=spec['label'])}. "
        f"{char_lock}, {emo_desc}{action_clause}. Natural expressive face, ready to speak with calm neutral closed lips (no frozen screaming open mouth). "
        f"Background setting: {setting}, vertical 9:16 portrait composition."
    )

    nv_key = env_map.get("NVIDIA_API_KEY") or env_map.get("NIM_API_KEY") or ""
    out_jpg = assets_dir / Path(base_name).with_suffix(".jpg").name
    ok, prov = _call_nvidia_flux_image(prompt, nv_key, out_jpg, "black-forest-labs/flux.1-dev")
    if not ok and prov == "content_filtered":
        safe_prompt = (
            f"{style_prefix}. Over-the-shoulder vertical 9:16 cinema shot of cheerful Brazilian comedian character wearing "
            f"{spec.get('shirt_color', 'colorful')} outfit, {emo_desc}, sunny village corner shop street background."
        )
        ok, prov = _call_nvidia_flux_image(safe_prompt, nv_key, out_jpg, "black-forest-labs/flux.1-dev")

    if ok and out_jpg.exists():
        shot_cache[cache_key] = (out_jpg, f"nvidia:{prov}")
        return out_jpg, f"assets/{out_jpg.name}", f"nvidia:{prov}"

    # Deterministic clean vector artwork fallback
    out_svg = assets_dir / Path(base_name).with_suffix(".svg").name
    _build_bespoke_svg_portrait(speaker_key, spec, visual_style, emotion, shot_framing, out_svg, clean_no_text=True)
    if force_jpg:
        try:
            _svg_to_clean_jpg(out_svg, out_jpg)
            out_svg.unlink(missing_ok=True)
            shot_cache[cache_key] = (out_jpg, f"svg_jpg_fallback:{prov}")
            return out_jpg, f"assets/{out_jpg.name}", f"svg_jpg_fallback:{prov}"
        except Exception:
            pass
    shot_cache[cache_key] = (out_svg, f"svg_fallback:{prov}")
    return out_svg, f"assets/{out_svg.name}", f"svg_fallback:{prov}"


def build_veo_take_prompt(
    speaker_spec: Dict[str, str],
    tts_text: str,
    visual_style: str,
    shot_framing: str,
    emotion: str,
    physical_action_en: str,
    setting: str,
) -> str:
    """Compose bilingual Veo 3.1 technical camera + acting prompt with exact Portuguese line."""
    is_3d = visual_style == "3d_cartoon"
    style_desc = (
        "Stylized 3D animated CG comedy film shot, sculpted expressive character lighting, rich colors"
        if is_3d
        else "Cinematic 35mm live-action Brazilian telenovela shot, natural golden-hour daylight"
    )
    framing_cue = SHOT_FRAMINGS.get(shot_framing, SHOT_FRAMINGS["over_shoulder"])["veo_cue"]
    _, _, default_acting = EMOTION_DIRECTIONS.get(emotion, EMOTION_DIRECTIONS["warm"])
    acting_en = (physical_action_en or "").strip() or default_acting
    veo_char = speaker_spec.get("veo_desc") or speaker_spec.get("lock_tv") or speaker_spec["label"]
    clean_speech = rve.sanitize_northeast_slang(tts_text).replace('"', "'").strip()
    return (
        f"{style_desc}, set on a {setting}. {framing_cue}. "
        f"{veo_char}, {acting_en}, starting from a relaxed neutral ready-to-speak expression and breaking into lively speech, "
        f"speaking in Brazilian Portuguese with an authentic, warm Northeastern Sertão accent "
        f"(sotaque nordestino arretado, fast energetic comedy cadence, open vowels, zero slow dragging): "
        f"\"{clean_speech}\" "
        f"Natural subtle handheld camera motion, authentic facial lip-sync, eye blinking and expressive gestures. "
        f"No subtitles, no text overlay, no watermarks, realistic lip-sync speech in Brazilian Portuguese."
    )


def generate_flow_veo_pack(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Generate Google Flow Veo 3.1 Production Pack (clean 1080x1920 Ingredients + bilingual prompts + credit math)."""
    t_start = time.time()
    visual_style = str(cfg.get("visual_style") or "3d_cartoon").strip().lower()
    if visual_style not in ("3d_cartoon", "cinematic_tv"):
        visual_style = "3d_cartoon"

    raw_dialogue: List[Dict[str, Any]] = list(cfg.get("dialogue") or [])
    if not raw_dialogue:
        raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala para gerar o Pack Veo 3.1.")

    title = str(cfg.get("title") or "Causos do Sertão — Episódio 01").strip()
    setting = str(
        cfg.get("setting")
        or "sunny rustic cobblestone sidewalk outside a colorful small countryside corner shop in Northeast Brazil with festive flags and bystanders watching"
    ).strip()

    pack_id = f"flow_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    pack_dir = Path(cfg.get("pack_dir") or (FLOW_PACKS_DIR / pack_id))
    pack_dir.mkdir(parents=True, exist_ok=True)

    env_map = _load_secrets_env()
    shot_cache: Dict[Tuple[str, str, str, str], Tuple[Path, str]] = {}
    takes: List[Dict[str, Any]] = []
    prev_speaker: Optional[str] = None

    for idx, raw_turn in enumerate(raw_dialogue):
        spk_raw = str(raw_turn.get("speaker") or "Narrador")
        spk_key, spec = _get_character_spec(spk_raw)
        subtitle_text = rve.sanitize_northeast_slang(str(raw_turn.get("subtitle_text") or raw_turn.get("text") or "").strip())
        tts_text = rve.sanitize_northeast_slang(str(raw_turn.get("tts_text") or subtitle_text).strip())
        if not tts_text:
            continue
        if not subtitle_text:
            subtitle_text = tts_text

        emotion = str(raw_turn.get("emotion") or "warm").strip().lower()
        shot_framing = _assign_shot_framing(
            idx,
            str(raw_turn.get("shot_framing") or ""),
            emotion,
            prev_speaker,
            spk_key,
        )
        physical_action_en = str(raw_turn.get("physical_action_en") or "").strip()

        ing_name = f"ingredient_take_{idx + 1:02d}.jpg"
        ing_path, _, prov = _resolve_turn_visual(
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
        )

        veo_prompt = build_veo_take_prompt(
            speaker_spec=spec,
            tts_text=tts_text,
            visual_style=visual_style,
            shot_framing=shot_framing,
            emotion=emotion,
            physical_action_en=physical_action_en,
            setting=setting,
        )
        words_cnt = max(2, len(tts_text.split()))
        est_sec = max(4, min(8, round(words_cnt / 2.3) + 2))
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
        "setting": setting,
        "total_takes": num_takes,
        "credit_unit_lite": 10,
        "credit_unit_fast": 20,
        "total_credits_lite": 10 * num_takes,
        "total_credits_fast": 20 * num_takes,
        "total_estimated_duration_sec": sum(t["estimated_duration_sec"] for t in takes),
        "takes": takes,
        "elapsed_sec": round(time.time() - t_start, 2),
    }
    (pack_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
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
                cmd = [
                    FFMPEG_BIN, "-y",
                    "-ss", f"{cut_s:.3f}", "-t", f"{seg_dur:.3f}", "-i", str(src_clip),
                    "-i", str(fitted_wav),
                    "-vf", v_filter,
                    "-map", "0:v:0", "-map", "1:a:0",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
                    "-shortest",
                    str(norm_clip),
                ]
            elif info["has_audio"]:
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
) -> Dict[str, Any]:
    """Send 1 take portrait + PT-BR WAV + acting prompt to remote Google Colab GPU Worker `/animate-take`."""
    endpoint = f"{colab_url.rstrip('/')}/animate-take"
    payload = {
        "image_b64": base64.b64encode(image_path.read_bytes()).decode("ascii"),
        "audio_b64": base64.b64encode(audio_wav.read_bytes()).decode("ascii"),
        "prompt": veo_prompt,
        "engine": colab_engine if colab_engine in ("echomimic_v3", "latentsync") else "echomimic_v3",
        "width": 1080,
        "height": 1920,
    }
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if colab_token:
        headers["X-Colab-Token"] = colab_token
    req = urllib.request.Request(endpoint, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=240) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok") or not data.get("video_b64"):
        raise RuntimeError(f"Colab /animate-take falhou: {data.get('error') or 'sem video_b64'}")
    out_mp4.write_bytes(base64.b64decode(data["video_b64"]))
    return data


def render_novela_episode(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Execute end-to-end multi-character micro-series production across v2.5 modes."""
    t_start_wall = time.time()
    render_mode = str(cfg.get("render_mode") or "vps_preview").strip().lower()
    if render_mode == "flow_pack":
        return generate_flow_veo_pack(cfg)

    visual_style = str(cfg.get("visual_style") or "3d_cartoon").strip().lower()
    if visual_style not in ("3d_cartoon", "cinematic_tv"):
        visual_style = "3d_cartoon"

    platform_preset = str(cfg.get("platform_preset") or "tiktok_reels").strip().lower()
    safe_zone = PLATFORM_SAFE_ZONES.get(platform_preset) or PLATFORM_SAFE_ZONES["tiktok_reels"]

    clean_cinema_mode = bool(cfg.get("clean_cinema_mode", True))
    subtitles_mode = str(cfg.get("subtitles_mode") or ("none" if clean_cinema_mode else "minimal")).strip().lower()
    if subtitles_mode not in ("none", "minimal"):
        subtitles_mode = "none" if clean_cinema_mode else "minimal"
    include_dictionary_endcard = bool(cfg.get("include_dictionary_endcard", False if clean_cinema_mode else True))

    raw_dialogue: List[Dict[str, Any]] = list(cfg.get("dialogue") or [])
    if not raw_dialogue:
        raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala de personagem.")

    setting = str(
        cfg.get("setting")
        or "sunny cobblestone sidewalk outside a painted wooden country corner grocery shop in Northeast Brazil with festive triangular flags and curious bystanders, warm afternoon sunlight"
    ).strip()

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

    env_map = _load_secrets_env()
    shot_cache: Dict[Tuple[str, str, str, str], Tuple[Path, str]] = {}
    compiled_turns: List[TurnCompiled] = []
    cursor_t = 0.0
    prev_speaker: Optional[str] = None

    for idx, raw_turn in enumerate(raw_dialogue):
        spk_raw = str(raw_turn.get("speaker") or "Narrador")
        spk_key, spec = _get_character_spec(spk_raw)
        subtitle_text = rve.sanitize_northeast_slang(str(raw_turn.get("subtitle_text") or raw_turn.get("text") or "").strip())
        tts_text = rve.sanitize_northeast_slang(str(raw_turn.get("tts_text") or subtitle_text).strip())
        if not tts_text:
            continue
        if not subtitle_text:
            subtitle_text = tts_text

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
        v_engine_used = str(tts_meta["voice_engine_used"])

        shot_abs_path, shot_relpath, asset_source = _resolve_turn_visual(
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
        colab_engine = str(cfg.get("colab_engine") or "echomimic_v3").strip().lower()

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
            )
            take_mp4 = work_dir / f"colab_take_{t.index:02d}.mp4"
            _call_colab_animate_take(
                colab_url=colab_url,
                colab_token=colab_token,
                image_path=t.shot_abs_path,
                audio_wav=t.audio_wav,
                veo_prompt=v_prompt,
                colab_engine=colab_engine,
                out_mp4=take_mp4,
            )
            t.animation_engine_used = f"tier1_colab:{colab_engine}"
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
            "clean_cinema_mode": clean_cinema_mode,
            "lipsync_enabled": True,
            "animation_engines_used": sorted({t.animation_engine_used for t in compiled_turns if t.animation_engine_used}),
            "voice_engines_used": sorted({t.voice_engine_used for t in compiled_turns}),
            "speakers": sorted({t.speaker_label for t in compiled_turns}),
            "asset_sources": sorted({f"{t.asset_source}+colab:{colab_engine}" for t in compiled_turns}),
            "elapsed_sec": round(time.time() - t_start_wall, 2),
        }

    # Mode 3 (Default v2.7): True Lip-Sync & Facial Motion Cascade via novela_lipsync_animator.py
    enable_lipsync = bool(cfg.get("enable_lipsync", True))
    if enable_lipsync:
        colab_url = str(cfg.get("colab_url") or env_map.get("COLAB_GPU_URL") or "").strip()
        colab_token = str(cfg.get("colab_token") or env_map.get("COLAB_GPU_TOKEN") or "").strip()
        colab_engine = str(cfg.get("colab_engine") or "echomimic_v3").strip().lower()
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
            )
            take_mp4 = work_dir / f"lipsync_take_{t.index:02d}.mp4"
            anim_meta = nla.animate_character_talking_take(
                image_path=t.shot_abs_path,
                audio_wav=t.audio_wav,
                out_mp4=take_mp4,
                prompt=v_prompt,
                emotion=t.emotion,
                camera_action=t.camera_action,
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
            lipsync_turns.append({
                "index": t.index,
                "speaker": t.speaker_label,
                "animation_engine": t.animation_engine_used,
                "voice_engine": t.voice_engine_used,
                "peak_mouth_open": t.lipsync_peak_open,
                "duration": anim_meta.get("duration", t.slot_dur),
            })
            if subtitles_mode == "minimal" and t.subtitle_text:
                ass_events.append((tl_t + 0.04, tl_t + max(0.3, float(anim_meta.get("duration") or t.slot_dur) - 0.04), t.subtitle_text))
            tl_t += float(anim_meta.get("duration") or t.slot_dur)

        # Concat animated clips seamlessly + optional minimal ASS + 48kHz SoXR final pass
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

        final_cmd = [FFMPEG_BIN, "-y", "-i", str(concat_mp4)]
        if vf_list:
            final_cmd.extend(["-vf", ",".join(vf_list), "-c:v", "libx264", "-preset", "fast", "-crf", "18"])
        else:
            final_cmd.extend(["-c:v", "copy"])
        final_cmd.extend([
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000:resampler=soxr",
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
            "voice_engines_used": sorted({t.voice_engine_used for t in compiled_turns}),
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
