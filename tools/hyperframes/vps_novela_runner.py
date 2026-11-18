#!/usr/bin/env python3
"""VPS Novela Studio Runner — Zero-Idle-RAM Multi-Character Micro-Series Engine.

Produces 9:16 vertical Brazilian Northeast ("Nordestino") comedy micro-series & mini-novelas
in two selectable visual styles:
  - ``3d_cartoon``: Stylized 3D animated CG character art
  - ``cinematic_tv``: 35mm realistic live-action Brazilian television telenovela cinematography

Engine components:
  - Cast registry with per-character neural edge-tts voice, pitch, rate, signature badge colors,
    and Character Lock prompts for visual consistency across shots.
  - Dual phonetic dialogue layer (`tts_text` for expressive Northeast cadence -> TTS audio;
    `subtitle_text` for crisp, clean screen typography).
  - Live Image Generation cascade (NVIDIA NIM black-forest-labs/flux.1-dev + Gemini Image API)
    with CONTENT_FILTERED / integrity validation and bespoke SVG artistic fallback.
  - Platform-aware Safe-Zones (`tiktok_reels`, `yt_shorts`, `shopee_20_9`).
  - Native FFmpeg SFX synthesis (turn pops, comedic sting, 3.5s cliffhanger freeze-frame chord)
    mixed with 48kHz Stereo SoXR loudnorm speech audio + HyperFrames GSAP HTML/Chromium render.
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
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Runtime binaries & paths (VPS defaults with graceful fallbacks)
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
CACHE_ROOT = STUDIO_BASE / "cache/novela_assets"


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
# Default Cast Configuration (Voices + Character Locks)
# ---------------------------------------------------------------------------
CAST_REGISTRY: Dict[str, Dict[str, str]] = {
    "dona_socorro": {
        "label": "Dona Socorro",
        "role": "Matriarca dramatica e fofoqueira",
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
            "small pearl earrings, round tortoiseshell eyeglasses, silver hair tied in a neat bun, warm natural comedy poise"
        ),
    },
    "seu_bastiao": {
        "label": "Seu Bastiao",
        "role": "Dono da mercearia, sabio e debochado",
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
            "jovial 68-year-old countryside shopkeeper gentleman named Sebastiao wearing a woven light straw brimmed hat, "
            "open unbuttoned blue plaid shirt over a white tee, neat silver mustache, holding a small aluminum coffee cup, "
            "wisecracking half-smile"
        ),
        "lock_tv": (
            "humorous 68-year-old Brazilian countryside shopkeeper gentleman wearing a woven straw hat and open blue plaid "
            "cotton shirt over a crisp white tee, neat silver mustache, holding a metal coffee cup, amused squinting eyes"
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
            "dramatic goofy 22-year-old city young man named Enzo wearing a trendy oversized lavender hoodie, "
            "dark sunglasses pushed up onto wavy dark hair, holding a smartphone tightly, wide comedic startled eyes"
        ),
        "lock_tv": (
            "expressive 22-year-old young Brazilian city guy wearing an oversized pastel purple sweatshirt with sunglasses "
            "pushed up onto curly dark hair, holding a modern smartphone, bewildered comical facial expression"
        ),
    },
    "narrador": {
        "label": "Narrador",
        "role": "Contador de causos do sertao",
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
            "in Northeast Brazil with festive hanging colorful triangular flags and clay roof tiles, vibrant golden light, no text"
        ),
        "lock_tv": (
            "wide 35mm golden hour exterior establishing frame of a charming rustic cobblestone sidewalk corner shop in "
            "Northeast Brazil with warm painted walls, terracotta tiles, colorful wooden chairs on the porch, no text"
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

EMOTION_DIRECTIONS: Dict[str, Tuple[str, str]] = {
    "shocked": ("eyes wide open, jaw dropped, hands raised in comical alarm", "shake"),
    "confused": ("head tilted, squinting suspiciously, eyebrows knotted in puzzlement", "zoom_in"),
    "laughing": ("bursting out laughing warmly, shoulders shaking joyfully", "whip_pan"),
    "proud": ("chest puffed out, wise amused grin, wagging one finger playfully", "zoom_in"),
    "dramatic": ("hand over chest in hilarious telenovela gasping dramatization", "dramatic_push"),
    "warm": ("friendly welcoming smile, leaning forward mid-story", "zoom_in"),
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
    highlight_phrase: str
    audio_wav: Path
    speech_dur: float
    slot_start: float
    slot_dur: float
    shot_relpath: str
    asset_source: str


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
    slug = re.sub(r"[^a-z0-9]+", "_", clean).strip("_") or "narrador"
    return slug


def _get_character_spec(speaker_raw: str) -> Tuple[str, Dict[str, str]]:
    key = _normalize_speaker_key(speaker_raw)
    if key in CAST_REGISTRY:
        spec = dict(CAST_REGISTRY[key])
        if key == "seu_bastiao":
            spec["label"] = "Seu Bastião"
        return key, spec
    # Dynamic fallback cast member
    palette = [("#fb7185", "#ffffff"), ("#f97316", "#111827"), ("#22c55e", "#052e16")]
    bg, fg = palette[abs(hash(key)) % len(palette)]
    display = speaker_raw.strip() or "Personagem"
    return key, {
        "label": display,
        "role": "Participacao especial",
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
        "lock_3d": f"friendly expressive Brazilian countryside resident named {display} smiling warmly on a village sidewalk",
        "lock_tv": f"charismatic Brazilian town character named {display} outdoors in golden afternoon sunlight",
    }


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
    vault_file = STUDIO_BASE / "media-control-panel/data/vault.json"
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


def _probe_duration(media_path: Path) -> float:
    out = _run_checked([
        FFPROBE_BIN,
        "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        str(media_path),
    ], timeout=20).strip()
    try:
        return max(0.4, float(out))
    except ValueError:
        return 2.0


def _synthesize_turn_audio(
    tts_text: str,
    voice: str,
    rate: str,
    pitch: str,
    raw_mp3: Path,
    clean_wav: Path,
    lead_pad: float = 0.05,
    tail_pad: float = 0.22,
) -> float:
    """Synthesize edge-tts voice clip and normalize into 48kHz stereo WAV with micro padding."""
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

    speech_dur = _probe_duration(raw_mp3)
    # Trim ultra-long trailing silence softly if needed, keep crisp comedic pacing, encode 48kHz stereo PCM
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
    """Generate a vertical 9:16 portrait/scene image via NVIDIA NIM Flux API."""
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
        with urllib.request.urlopen(req, timeout=70) as resp:
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
        # Upscale smoothly to exact 1080x1920 crisp portrait canvas for consistent Chromium compositing
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
    setting: str,
    out_svg: Path,
) -> None:
    """Bespoke multi-layered procedural vector stage artwork (guaranteed zero black-screen fallback)."""
    is_3d = visual_style == "3d_cartoon"
    bg_top = "#0ea5e9" if is_3d else "#78350f"
    bg_mid = "#f59e0b" if is_3d else "#b45309"
    bg_bot = "#451a03" if is_3d else "#1c1917"
    shirt = spec.get("shirt_color", "#eab308")
    skin = spec.get("skin_tone", "#d8a47f")
    hair = spec.get("hair_color", "#94a3b8")
    label = html.escape(spec.get("label", "Personagem"))

    mouth_path = (
        "M 480 835 Q 540 890 600 835" if emotion in ("laughing", "warm", "proud")
        else "M 510 845 Q 540 875 570 845 Q 540 825 510 845" if emotion in ("shocked", "dramatic")
        else "M 495 848 Q 540 830 585 852"
    )
    prop_svg = ""
    if spec.get("prop") == "straw_hat":
        prop_svg = (
            '<ellipse cx="540" cy="495" rx="260" ry="44" fill="#eab308" stroke="#92400e" stroke-width="8"/>'
            '<path d="M 380 495 C 395 360 685 360 700 495 Z" fill="#facc15" stroke="#92400e" stroke-width="8"/>'
            '<rect x="385" y="468" width="310" height="22" rx="8" fill="#b91c1c"/>'
        )
    elif spec.get("prop") == "glasses":
        prop_svg = (
            '<circle cx="475" cy="705" r="46" fill="none" stroke="#451a03" stroke-width="10"/>'
            '<circle cx="605" cy="705" r="46" fill="none" stroke="#451a03" stroke-width="10"/>'
            '<line x1="521" y1="705" x2="559" y2="705" stroke="#451a03" stroke-width="10"/>'
        )
    elif spec.get("prop") == "phone_sunglasses":
        prop_svg = (
            '<rect x="440" y="545" width="84" height="36" rx="12" fill="#0f172a"/>'
            '<rect x="556" y="545" width="84" height="36" rx="12" fill="#0f172a"/>'
            '<line x1="524" y1="560" x2="556" y2="560" stroke="#0f172a" stroke-width="8"/>'
            '<rect x="705" y="885" width="96" height="164" rx="16" fill="#0f172a" stroke="#c084fc" stroke-width="6"/>'
            '<rect x="717" y="902" width="72" height="122" rx="8" fill="#38bdf8" opacity="0.75"/>'
        )

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
  <!-- Bodega village architecture silhouette -->
  <rect x="40" y="390" width="320" height="900" rx="12" fill="#fed7aa" opacity="0.28"/>
  <rect x="720" y="350" width="320" height="940" rx="12" fill="#bae6fd" opacity="0.24"/>
  <path d="M 0 1240 L 1080 1240 L 1080 1920 L 0 1920 Z" fill="#292524" opacity="0.82"/>
  <!-- Festive flags -->
  <polygon points="100,260 155,260 128,315" fill="#ef4444"/>
  <polygon points="210,275 265,275 238,330" fill="#fde047"/>
  <polygon points="820,275 875,275 848,330" fill="#22c55e"/>
  <polygon points="930,260 985,260 958,315" fill="#a855f7"/>
  <!-- Character Shoulders -->
  <path d="M 240 1320 C 260 1000 820 1000 840 1320 Z" fill="{shirt}" stroke="#0f172a" stroke-opacity="0.22" stroke-width="8"/>
  <!-- Neck & Head -->
  <rect x="485" y="885" width="110" height="140" rx="45" fill="{skin}"/>
  <circle cx="540" cy="700" r="185" fill="{skin}" stroke="#0f172a" stroke-opacity="0.16" stroke-width="6"/>
  <!-- Hair -->
  <path d="M 355 660 C 370 475 710 475 725 660 C 660 580 420 580 355 660 Z" fill="{hair}"/>
  <!-- Eyes -->
  <circle cx="475" cy="700" r="24" fill="#ffffff"/>
  <circle cx="605" cy="700" r="24" fill="#ffffff"/>
  <circle cx="478" cy="702" r="12" fill="#0f172a"/>
  <circle cx="608" cy="702" r="12" fill="#0f172a"/>
  <!-- Eyebrows -->
  <path d="M 432 648 Q 475 628 518 648" stroke="#1e293b" stroke-width="11" fill="none" stroke-linecap="round"/>
  <path d="M 562 648 Q 605 628 648 648" stroke="#1e293b" stroke-width="11" fill="none" stroke-linecap="round"/>
  <!-- Nose & Mouth -->
  <path d="M 540 718 Q 560 760 532 778" stroke="#9a5c3c" stroke-width="8" fill="none" stroke-linecap="round"/>
  <path d="{mouth_path}" stroke="#7f1d1d" stroke-width="11" fill="#ef4444" stroke-linecap="round"/>
  {prop_svg}
  <rect x="340" y="1350" width="400" height="58" rx="29" fill="#000000" opacity="0.45"/>
  <text x="540" y="1390" font-family="sans-serif" font-size="28" font-weight="800" fill="#ffffff" text-anchor="middle">{label}</text>
</svg>'''
    out_svg.write_text(svg, encoding="utf-8")


def _resolve_turn_visual(
    speaker_key: str,
    spec: Dict[str, str],
    emotion: str,
    visual_style: str,
    setting: str,
    assets_dir: Path,
    turn_idx: int,
    env_map: Dict[str, str],
    shot_cache: Dict[Tuple[str, str, str], Tuple[Path, str]],
) -> Tuple[str, str]:
    """Return relative asset path (inside HTML composition) + provenance label.

    Uses Character Lock + emotion cache per episode so recurring speakers share consistent identity
    or show varied emotion expressions without wasteful duplicate API calls.
    """
    is_3d = visual_style == "3d_cartoon"
    emo_bucket = "shocked" if emotion in ("shocked", "confused", "dramatic") else "warm"
    cache_key = (speaker_key, emo_bucket, visual_style)

    if cache_key in shot_cache:
        src_path, prov = shot_cache[cache_key]
        dst = assets_dir / f"shot_{turn_idx}{src_path.suffix}"
        shutil.copy2(src_path, dst)
        return f"assets/{dst.name}", prov

    style_prefix = (
        "Stylized 3D animated CG character art, smooth sculpted stylized features, expressive comedy cartoon eyes, "
        "soft warm subsurface scattering rim light, rich saturated colors, no text, no watermark"
        if is_3d
        else "Cinematic 35mm Brazilian television telenovela film still, realistic expressive acting, "
             "shallow depth of field f/1.8 bokeh, golden hour natural sunlight, rich color grading, no text, no watermark"
    )
    char_lock = spec["lock_3d"] if is_3d else spec["lock_tv"]
    emo_desc, _ = EMOTION_DIRECTIONS.get(emotion, EMOTION_DIRECTIONS["warm"])
    prompt = f"{style_prefix}. {char_lock}, {emo_desc}. Background setting: {setting}, vertical 9:16 medium shot."

    nv_key = env_map.get("NVIDIA_API_KEY") or env_map.get("NIM_API_KEY") or ""
    out_jpg = assets_dir / f"shot_{turn_idx}.jpg"
    ok, prov = _call_nvidia_flux_image(prompt, nv_key, out_jpg, "black-forest-labs/flux.1-dev")
    if not ok and prov == "content_filtered":
        # Safe rephrased retry without age/regional edge tokens
        safe_prompt = (
            f"{style_prefix}. Friendly cheerful Brazilian comedian character wearing {spec.get('shirt_color', 'colorful')} outfit, "
            f"{emo_desc}, sunny rustic corner store village porch backdrop, vertical 9:16 portrait."
        )
        ok, prov = _call_nvidia_flux_image(safe_prompt, nv_key, out_jpg, "black-forest-labs/flux.1-dev")

    if ok and out_jpg.exists():
        shot_cache[cache_key] = (out_jpg, f"nvidia:{prov}")
        return f"assets/{out_jpg.name}", f"nvidia:{prov}"

    # Deterministic illustrated SVG fallback (zero crashes, zero black screen)
    out_svg = assets_dir / f"shot_{turn_idx}.svg"
    _build_bespoke_svg_portrait(speaker_key, spec, visual_style, emotion, setting, out_svg)
    shot_cache[cache_key] = (out_svg, f"svg_fallback:{prov}")
    return f"assets/{out_svg.name}", f"svg_fallback:{prov}"


def _build_master_audio_mix(
    turns: List[TurnCompiled],
    freeze_start: float,
    freeze_dur: float,
    total_dur: float,
    work_dir: Path,
) -> Path:
    """Concatenate per-turn speech on timeline + layer synthesized comedy turn pops & cliffhanger sting."""
    raw_voice_track = work_dir / "voice_concat.wav"
    concat_list = work_dir / "voice_concat.txt"
    lines = []
    for t in turns:
        lines.append(f"file '{t.audio_wav}'")
    # Append trailing silence covering the 3.5s cliffhanger freeze-frame
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

    # Synthesize 2 lightweight playful SFX assets offline via ffmpeg lavfi
    pop_sfx = work_dir / "sfx_pop.wav"
    sting_sfx = work_dir / "sfx_novela_sting.wav"
    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-t", "0.14",
        "-i", "aevalsrc=0.14*sin(2*PI*(520+1600*t)*t)*exp(-22*t):s=48000:c=stereo",
        "-c:a", "pcm_s16le",
        str(pop_sfx),
    ], timeout=20)

    # Dramatic comedic telenovela suspense triad chord (A minor / E7 comedic cliffhanger swell)
    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-t", f"{max(1.8, freeze_dur - 0.1):.3f}",
        "-i", "aevalsrc=0.18*(0.55*sin(2*PI*220*t)+0.40*sin(2*PI*261.63*t)+0.35*sin(2*PI*329.63*t)+0.25*sin(2*PI*440*t))*(1-exp(-12*t))*exp(-0.55*t):s=48000:c=stereo",
        "-af", f"afade=t=in:st=0:d=0.05,afade=t=out:st={max(1.0, freeze_dur - 1.2):.2f}:d=1.0",
        "-c:a", "pcm_s16le",
        str(sting_sfx),
    ], timeout=20)

    # Layer subtle turn transition pops + cliffhanger sting onto voice track & normalize 48kHz Stereo SoXR
    filter_inputs: List[str] = ["-i", str(raw_voice_track)]
    filter_parts: List[str] = ["[0:a]aresample=48000:resampler=soxr,volume=1.0[vtrack]"]
    mix_labels: List[str] = ["[vtrack]"]
    in_idx = 1

    # Add subtle pop on character switches (skip t=0)
    prev_speaker = None
    for t in turns:
        if prev_speaker is not None and t.speaker_key != prev_speaker:
            delay_ms = max(0, int(round(t.slot_start * 1000)))
            filter_inputs.extend(["-i", str(pop_sfx)])
            lbl = f"[pop{in_idx}]"
            filter_parts.append(f"[{in_idx}:a]volume=0.22,adelay={delay_ms}|{delay_ms}{lbl}")
            mix_labels.append(lbl)
            in_idx += 1
        prev_speaker = t.speaker_key

    # Add dramatic cliffhanger novela sting at freeze_start
    freeze_ms = max(0, int(round(freeze_start * 1000)))
    filter_inputs.extend(["-i", str(sting_sfx)])
    sting_lbl = f"[sting{in_idx}]"
    filter_parts.append(f"[{in_idx}:a]volume=0.52,adelay={freeze_ms}|{freeze_ms}{sting_lbl}")
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
            lambda m: f'<span class="kw-pop" style="color:{accent_color};text-shadow:0 3px 16px rgba(0,0,0,0.95)">{m.group(0)}</span>',
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
    work_dir: Path,
) -> Path:
    """Generate HyperFrames compliant index.html + gsap timeline animations."""
    assets_dir = work_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    if GSAP_VENDOR.is_file():
        shutil.copy2(GSAP_VENDOR, assets_dir / "gsap.min.js")

    title = html.escape(str(cfg.get("title") or "Causos do Sertão — Episódio 01"))
    ep_badge = html.escape(str(cfg.get("episode_badge") or "🌵 NOVELINHA DO SERTÃO • EP. 01"))
    style_tag = "🎨 3D CARTOON" if visual_style == "3d_cartoon" else "🎬 NOVELA TV"
    dictionary_items: List[Dict[str, str]] = list(cfg.get("dictionary") or [])
    if not dictionary_items:
        dictionary_items = [
            {"term": "OXENTE!", "meaning": "Expressão de espanto, surpresa ou indignação"},
            {"term": "BOTAR BONECO", "meaning": "Fazer drama, escândalo ou manha sem necessidade"},
        ]

    safe_top = int(safe_zone["safe_top"])
    safe_bottom = int(safe_zone["safe_bottom"])
    safe_left = int(safe_zone["safe_left"])
    # Margin right keeps badges/cards clear of TikTok/Reels/Shopee sidebar icons (X >= 900)
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
        sub_html = _format_subtitle_html(t.subtitle_text, t.highlight_phrase, t.accent)

        # Pick reaction sticker text for emotional peaks
        sticker = html.escape((t.highlight_phrase or "").upper()[:26]) if t.highlight_phrase else ""
        sticker_html = (
            f'<div id="{react_id}" class="reaction-pill" style="background:{t.badge_bg};color:{t.badge_fg}">⚡ {sticker}!</div>'
            if sticker
            else f'<div id="{react_id}" class="reaction-pill" style="opacity:0"></div>'
        )

        # Pure CSS color overlay on top (NO CSS filter to obey HyperFrames media/render contract)
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

        # GSAP choreography anchored cleanly to timeline time `t.slot_start`
        ts = t.slot_start
        sd = max(0.5, t.slot_dur)
        if t.camera_action == "shake":
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{scale:1.03, x:0, rotation:0}}, '
                f'{{scale:1.10, x:-14, rotation:-1.2, duration:0.09, repeat:3, yoyo:true, ease:"power2.inOut"}}, {ts:.3f});'
                f'tl.to("#{shot_id}", {{scale:1.12, x:0, rotation:0, duration:{max(0.4, sd-0.38):.3f}, ease:"none"}}, {ts+0.36:.3f});'
            )
        elif t.camera_action == "whip_pan":
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{x:85, scale:1.10}}, {{x:0, scale:1.04, duration:0.26, ease:"power3.out"}}, {ts:.3f});'
                f'tl.to("#{shot_id}", {{scale:1.11, duration:{max(0.3, sd-0.26):.3f}, ease:"none"}}, {ts+0.26:.3f});'
            )
        elif t.camera_action == "dramatic_push":
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{scale:1.0}}, {{scale:1.16, duration:{sd:.3f}, ease:"power1.in"}}, {ts:.3f});'
            )
        else:
            gsap_steps.append(
                f'tl.fromTo("#{shot_id}", {{scale:1.01, y:8}}, {{scale:1.10, y:-8, duration:{sd:.3f}, ease:"none"}}, {ts:.3f});'
            )

        gsap_steps.append(
            f'tl.fromTo("#{badge_id}", {{x:-40, opacity:0}}, {{x:0, opacity:1, duration:0.22, ease:"back.out(1.7)"}}, {ts:.3f});'
            f'tl.fromTo("#{card_id}", {{y:28, opacity:0, scale:0.96}}, {{y:0, opacity:1, scale:1, duration:0.24, ease:"power3.out"}}, {ts+0.03:.3f});'
        )
        if sticker:
            gsap_steps.append(
                f'tl.fromTo("#{react_id}", {{scale:0.3, rotation:-12, opacity:0}}, '
                f'{{scale:1, rotation:-4, opacity:1, duration:0.28, ease:"back.out(2.1)"}}, {ts+0.12:.3f});'
            )

    # Final 3.5s Soap-Opera Freeze Frame + Dictionary Card
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
            <div id="cliffhanger-pill" class="cliffhanger-pill">
              📺 Continua no próximo episódio...
            </div>
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
  #root {{
    position: relative; width: 1080px; height: 1920px; overflow: hidden; background: #090b10;
  }}
  .scene-panel {{
    position: absolute; inset: 0; overflow: hidden;
  }}
  .cam-layer {{
    position: absolute; inset: 0; transform-origin: 50% 42%; will-change: transform;
  }}
  .portrait-img {{
    width: 100%; height: 100%; object-fit: cover; display: block;
  }}
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
  .top-ribbon {{
    position: absolute; display: flex; align-items: center; justify-content: space-between; gap: 12px; z-index: 5;
  }}
  .ep-pill {{
    background: rgba(15, 23, 42, 0.86); border: 2px solid rgba(250, 204, 21, 0.65);
    color: #fde047; padding: 10px 20px; border-radius: 999px;
    font-size: 24px; font-weight: 800; letter-spacing: 0.6px;
    box-shadow: 0 8px 24px rgba(0,0,0,0.45);
  }}
  .style-pill {{
    background: rgba(15, 23, 42, 0.86); border: 2px solid rgba(56, 189, 248, 0.55);
    color: #e0f2fe; padding: 10px 18px; border-radius: 999px;
    font-size: 22px; font-weight: 800;
  }}
  .ep-title-bar {{
    display: inline-block;
    background: rgba(10, 12, 18, 0.72); border-radius: 14px; padding: 10px 20px;
    font-size: 28px; font-weight: 800; color: #ffffff;
    text-shadow: 0 2px 10px rgba(0,0,0,0.9);
  }}
  .reaction-pill {{
    display: inline-block; padding: 14px 26px; border-radius: 20px;
    font-size: 32px; font-weight: 900; letter-spacing: 0.5px;
    border: 4px solid #ffffff; box-shadow: 0 12px 30px rgba(0,0,0,0.55);
  }}
  .lower-safe-stack {{
    position: absolute; display: flex; flex-direction: column; gap: 14px; z-index: 6;
  }}
  .speaker-row {{
    display: flex; align-items: center; gap: 12px; flex-wrap: wrap;
  }}
  .speaker-pill {{
    padding: 10px 22px; border-radius: 14px; font-size: 30px; font-weight: 900;
    letter-spacing: 0.4px; box-shadow: 0 8px 22px rgba(0,0,0,0.55);
    border: 3px solid rgba(255,255,255,0.85);
  }}
  .role-tag {{
    background: rgba(15, 23, 42, 0.84); color: #e2e8f0;
    padding: 8px 16px; border-radius: 12px; font-size: 22px; font-weight: 700;
    border: 1px solid rgba(255,255,255,0.2);
  }}
  .subtitle-box {{
    background: rgba(10, 12, 18, 0.90); border-radius: 20px; padding: 22px 26px;
    box-shadow: 0 16px 40px rgba(0,0,0,0.72);
    border-top: 1px solid rgba(255,255,255,0.16);
    border-right: 1px solid rgba(255,255,255,0.16);
    border-bottom: 1px solid rgba(255,255,255,0.16);
  }}
  .subtitle-text {{
    font-size: 40px; line-height: 1.24; font-weight: 800; color: #f8fafc;
    text-shadow: 0 2px 6px rgba(0,0,0,0.85);
  }}
  .kw-pop {{ font-weight: 900; text-decoration: underline rgba(253, 224, 71, 0.55) 4px; }}
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
    font-size: 30px; font-weight: 900; letter-spacing: 1px;
    border: 4px solid #fef08a; box-shadow: 0 12px 32px rgba(0,0,0,0.7);
  }}
  .dict-card {{
    width: 100%; background: rgba(15, 23, 42, 0.94); border: 4px solid #f59e0b;
    border-radius: 28px; padding: 28px 30px;
    box-shadow: 0 24px 60px rgba(0,0,0,0.85);
    display: flex; flex-direction: column; gap: 18px;
  }}
  .dict-header {{
    font-size: 36px; font-weight: 900; color: #fde047; text-align: center;
    padding-bottom: 12px; border-bottom: 2px solid rgba(245,158,11,0.35);
  }}
  .dict-row {{
    background: rgba(30, 41, 59, 0.85); border-radius: 18px; padding: 16px 20px;
    border-left: 8px solid #38bdf8;
  }}
  .dict-term {{ font-size: 30px; font-weight: 900; color: #38bdf8; margin-bottom: 6px; }}
  .dict-mean {{ font-size: 28px; line-height: 1.28; font-weight: 700; color: #f1f5f9; }}
  .cliffhanger-pill {{
    background: linear-gradient(90deg, #f59e0b, #ea580c); color: #ffffff;
    padding: 18px 34px; border-radius: 999px; font-size: 32px; font-weight: 900;
    border: 3px solid #fef08a; box-shadow: 0 16px 36px rgba(0,0,0,0.75);
    text-align: center;
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


def render_novela_episode(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Execute end-to-end multi-character micro-series production and return structured summary."""
    t_start_wall = time.time()
    visual_style = str(cfg.get("visual_style") or "3d_cartoon").strip().lower()
    if visual_style not in ("3d_cartoon", "cinematic_tv"):
        visual_style = "3d_cartoon"

    platform_preset = str(cfg.get("platform_preset") or "tiktok_reels").strip().lower()
    safe_zone = PLATFORM_SAFE_ZONES.get(platform_preset) or PLATFORM_SAFE_ZONES["tiktok_reels"]

    raw_dialogue: List[Dict[str, Any]] = list(cfg.get("dialogue") or [])
    if not raw_dialogue:
        raise ValueError("Campo 'dialogue' vazio: informe ao menos 1 fala de personagem.")

    setting = str(
        cfg.get("setting")
        or "sunny cobblestone sidewalk outside a traditional painted wooden country corner grocery shop in Northeast Brazil with festive triangular flags, warm late afternoon light"
    ).strip()

    freeze_dur = float(cfg.get("freeze_duration") or 3.5)
    freeze_dur = max(2.0, min(5.0, freeze_dur))
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
    shot_cache: Dict[Tuple[str, str, str], Tuple[Path, str]] = {}
    compiled_turns: List[TurnCompiled] = []
    cursor_t = 0.0

    for idx, raw_turn in enumerate(raw_dialogue):
        spk_raw = str(raw_turn.get("speaker") or "Narrador")
        spk_key, spec = _get_character_spec(spk_raw)
        subtitle_text = str(raw_turn.get("subtitle_text") or raw_turn.get("text") or "").strip()
        tts_text = str(raw_turn.get("tts_text") or subtitle_text).strip()
        if not subtitle_text:
            subtitle_text = tts_text
        if not tts_text:
            continue

        emotion = str(raw_turn.get("emotion") or "warm").strip().lower()
        _, default_cam = EMOTION_DIRECTIONS.get(emotion, EMOTION_DIRECTIONS["warm"])
        camera_action = str(raw_turn.get("camera_action") or default_cam).strip().lower()
        highlight_phrase = str(raw_turn.get("expression_highlight") or raw_turn.get("highlight") or "").strip()

        raw_mp3 = audio_dir / f"turn_{idx}.mp3"
        clean_wav = audio_dir / f"turn_{idx}.wav"
        slot_dur = _synthesize_turn_audio(
            tts_text=tts_text,
            voice=str(raw_turn.get("voice") or spec["voice"]),
            rate=str(raw_turn.get("rate") or spec["rate"]),
            pitch=str(raw_turn.get("pitch") or spec["pitch"]),
            raw_mp3=raw_mp3,
            clean_wav=clean_wav,
        )

        shot_relpath, asset_source = _resolve_turn_visual(
            speaker_key=spk_key,
            spec=spec,
            emotion=emotion,
            visual_style=visual_style,
            setting=setting,
            assets_dir=assets_dir,
            turn_idx=idx,
            env_map=env_map,
            shot_cache=shot_cache,
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
                highlight_phrase=highlight_phrase,
                audio_wav=clean_wav,
                speech_dur=slot_dur,
                slot_start=cursor_t,
                slot_dur=slot_dur,
                shot_relpath=shot_relpath,
                asset_source=asset_source,
            )
        )
        cursor_t += slot_dur

    if not compiled_turns:
        raise ValueError("Nenhuma fala valida encontrada em 'dialogue'.")

    freeze_start = cursor_t
    total_dur = freeze_start + freeze_dur

    # Build master audio mix (speech + pops + cliffhanger novela chord, 48kHz stereo loudnorm)
    final_mix_wav = _build_master_audio_mix(
        turns=compiled_turns,
        freeze_start=freeze_start,
        freeze_dur=freeze_dur,
        total_dur=total_dur,
        work_dir=work_dir,
    )

    # Generate HyperFrames composition HTML + verify with lint check
    _build_hyperframes_html(
        cfg=cfg,
        turns=compiled_turns,
        freeze_start=freeze_start,
        freeze_dur=freeze_dur,
        total_dur=total_dur,
        safe_zone=safe_zone,
        visual_style=visual_style,
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

    # Mux rendered HTML frames with 48kHz Stereo SoXR loudnorm mix
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

    keep_work = bool(cfg.get("keep_work_dir"))
    if not keep_work:
        shutil.rmtree(work_dir, ignore_errors=True)

    return {
        "ok": True,
        "output_path": str(out_path),
        "filename": out_path.name,
        "visual_style": visual_style,
        "platform_preset": platform_preset,
        "duration": round(total_dur, 2),
        "scenes": len(compiled_turns) + 1,
        "turns": len(compiled_turns),
        "speakers": sorted({t.speaker_label for t in compiled_turns}),
        "asset_sources": sorted({t.asset_source for t in compiled_turns}),
        "elapsed_sec": round(time.time() - t_start_wall, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="VPS Novela Studio Runner")
    parser.add_argument("--config", required=True, help="Path to episode JSON config")
    args = parser.parse_args()
    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))
    res = render_novela_episode(cfg)
    print(json.dumps(res, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        sys.exit(1)
