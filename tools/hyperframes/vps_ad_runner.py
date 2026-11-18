#!/usr/bin/env python3
"""HyperFrames Motion Graphics & Multi-Image POV Shopee Ad runner for AI Media Studio VPS.

Supports:
1. Classic single-card typographic Motion Graphics ads (B2B Emerald, Shopee Flash Sale, etc.).
2. Multi-Image Real Product POV Ads (9:16 1080x1920 or 16:9), with:
   - Hook Antes/Depois (0s-3.5s, zero logo/greeting)
   - POV & Ken Burns product benefits showcase
   - Split formula / objection breaker showcase
   - Price-division anchoring + Animated Lower-Left Shopee Orange Cart CTA
   - Full audio-driven video duration (zero truncated speech) + 48kHz Stereo SoXR mastering
   - Central safe-zone synchronized captions parsed directly from Edge-TTS timestamps
"""

from __future__ import annotations

import argparse
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

HF_BIN = Path("/opt/ai-media-studio/tools/hyperframes/node_modules/.bin/hyperframes")
GSAP_VENDOR = Path("/opt/ai-media-studio/tools/hyperframes/vendor/gsap.min.js")
EDGE_TTS_BIN = Path("/opt/ai-media-studio/tools/video-use/.venv/bin/edge-tts")
CAMPAIGNS_ROOT = Path(os.environ.get("STUDIO_CAMPAIGNS_DIR", "/opt/ai-media-studio/campaigns"))

STYLE_THEMES: Dict[str, Dict[str, str]] = {
    "loreal_gold": {
        "name": "L'Oréal Gold Luxury",
        "bg": "#0b0702",
        "card": "rgba(26, 16, 5, 0.92)",
        "primary": "#f59e0b",
        "secondary": "#ee4d2d",
        "accent": "#fde68a",
        "badge_text": "#1b0d01",
        "ink": "#fffbeb",
        "muted": "#fed7aa",
        "badge_label": "ACHADINHO VIRAL · ORIGINAL L'ORÉAL",
    },
    "b2b_emerald": {
        "name": "B2B Emerald",
        "bg": "#040a07",
        "card": "rgba(10, 26, 19, 0.88)",
        "primary": "#10b981",
        "secondary": "#14b8a6",
        "accent": "#6ee7b7",
        "badge_text": "#032216",
        "ink": "#f8fafc",
        "muted": "#94a3b8",
        "badge_label": "SOLUCAO ENTERPRISE",
    },
    "shopee_flash": {
        "name": "Shopee Flash Sale",
        "bg": "#140603",
        "card": "rgba(38, 12, 6, 0.90)",
        "primary": "#ff5722",
        "secondary": "#fbbf24",
        "accent": "#fde047",
        "badge_text": "#1f0902",
        "ink": "#ffffff",
        "muted": "#fed7aa",
        "badge_label": "OFERTA RELAMPAGO",
    },
    "cyber_neon": {
        "name": "Cyber Neon",
        "bg": "#050614",
        "card": "rgba(13, 15, 38, 0.90)",
        "primary": "#22d3ee",
        "secondary": "#e879f9",
        "accent": "#a5f3fc",
        "badge_text": "#041620",
        "ink": "#f8fafc",
        "muted": "#cbd5e1",
        "badge_label": "LANCAMENTO TECH",
    },
    "minimal_luxury": {
        "name": "Minimal Luxury",
        "bg": "#0a0908",
        "card": "rgba(24, 21, 18, 0.90)",
        "primary": "#d4af37",
        "secondary": "#f3e5ab",
        "accent": "#fde68a",
        "badge_text": "#181204",
        "ink": "#fafaf9",
        "muted": "#d6d3d1",
        "badge_label": "EDICAO PREMIUM",
    },
}

STYLE_ALIASES: Dict[str, str] = {
    "l'oréal gold luxury": "loreal_gold",
    "loreal gold luxury": "loreal_gold",
    "l'oreal gold luxury": "loreal_gold",
    "loreal_gold": "loreal_gold",
    "b2b emerald": "b2b_emerald",
    "b2b_emerald": "b2b_emerald",
    "shopee flash sale": "shopee_flash",
    "shopee_flash": "shopee_flash",
    "cyber neon": "cyber_neon",
    "cyber_neon": "cyber_neon",
    "minimal luxury": "minimal_luxury",
    "minimal_luxury": "minimal_luxury",
}

VOICE_ALIASES: Dict[str, str] = {
    "pt-br-thalitaneural": "pt-BR-ThalitaMultilingualNeural",
    "pt-br-thalitamultilingualneural": "pt-BR-ThalitaMultilingualNeural",
    "pt-br-franciscaneural": "pt-BR-FranciscaNeural",
    "pt-br-antonioneural": "pt-BR-AntonioNeural",
    "pt-br-humbertoneural": "pt-BR-AntonioNeural",
}


def _resolve_theme(style: str) -> Tuple[str, Dict[str, str]]:
    slug = STYLE_ALIASES.get((style or "").strip().lower(), "b2b_emerald")
    return slug, STYLE_THEMES[slug]


def _resolve_voice(voice_name: str) -> str:
    raw = (voice_name or "").strip()
    if not raw:
        return "pt-BR-FranciscaNeural"
    return VOICE_ALIASES.get(raw.lower(), raw)


def _split_offer(offer: str) -> Tuple[str, str]:
    txt = (offer or "").strip()
    m = re.match(r"^(de\s+.+?)\s+(por\s+.+)$", txt, flags=re.IGNORECASE)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    return "", txt


def _build_ad_script(
    title: str,
    offer: str,
    cta: str,
    custom_script: str = "",
    has_images: bool = False,
) -> str:
    if custom_script and custom_script.strip():
        return custom_script.strip()
    parts = [title.strip(), offer.strip(), cta.strip()]
    deterministic = ". ".join(p.rstrip(".!") for p in parts if p) + "."
    if has_images:
        return deterministic
    try:
        with httpx.Client(timeout=5.0) as client:
            r = client.post(
                "http://127.0.0.1:9000/v1/chat/completions",
                json={
                    "model": "cascade-auto",
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "Você cria locuções publicitárias ultracurtas em português do Brasil. "
                                "Responda APENAS com 1 frase falada de no máximo 16 palavras, direta e persuasiva, sem aspas."
                            ),
                        },
                        {"role": "user", "content": f"Produto: {title}. Oferta: {offer}. CTA: {cta}."},
                    ],
                    "max_tokens": 50,
                    "temperature": 0.4,
                },
            )
            if r.status_code == 200:
                text = ((r.json().get("choices") or [{}])[0].get("message") or {}).get("content", "").strip().strip('"')
                if 3 <= len(text.split()) <= 26:
                    return text
    except Exception:
        pass
    return deterministic


def _filter_duplicate_cta_cues(
    cues: List[Dict[str, Any]],
    t_cta_start: float,
    cart_cta_text: str,
) -> List[Dict[str, Any]]:
    """Avoid showing simultaneous identical CTA captions right above the pulsing Scene 4 orange CTA pill."""
    cta_words = {
        w for w in re.findall(r"[a-zà-ÿ0-9]+", (cart_cta_text or "").lower())
        if len(w) >= 4
    }
    filtered: List[Dict[str, Any]] = []
    for c in cues:
        txt = str(c.get("text", "")).strip()
        low = txt.lower()
        c_end = float(c.get("end", 0.0))
        if c_end >= t_cta_start - 0.1:
            if any(p in low for p in ("carrinho laranja", "garanta no carrinho", "toque no carrinho", "toque em comprar")):
                continue
            cue_words = {w for w in re.findall(r"[a-zà-ÿ0-9]+", low) if len(w) >= 4}
            if cta_words and len(cue_words & cta_words) >= 3:
                continue
        filtered.append(c)
    return filtered


def _parse_srt_time(ts: str) -> float:
    m = re.match(r"(\d+):(\d+):(\d+)[,.](\d+)", ts.strip())
    if not m:
        return 0.0
    h, mnt, s, ms = int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))
    return h * 3600.0 + mnt * 60.0 + s + (ms / 1000.0)


def _split_long_cue(
    text: str,
    start: float,
    end: float,
    max_words: int = 5,
    max_chars: int = 34,
) -> List[Dict[str, Any]]:
    words = text.split()
    if len(words) <= max_words and len(text) <= max_chars:
        return [{"text": text, "start": start, "end": end}]
    chunks: List[str] = []
    cur: List[str] = []
    for w in words:
        cand = " ".join(cur + [w])
        if cur and (len(cur) >= max_words or len(cand) > max_chars):
            chunks.append(" ".join(cur))
            cur = [w]
        else:
            cur.append(w)
    if cur:
        chunks.append(" ".join(cur))
    total_chars = max(1, sum(len(c) for c in chunks))
    dur = max(0.3, end - start)
    t_cur = start
    out: List[Dict[str, Any]] = []
    for idx, ch in enumerate(chunks):
        part_dur = dur * (len(ch) / total_chars)
        t_next = end if idx == len(chunks) - 1 else (t_cur + part_dur)
        out.append({"text": ch, "start": t_cur, "end": t_next})
        t_cur = t_next
    return out


def _parse_srt_cues(srt_raw: str, audio_offset: float = 0.15) -> List[Dict[str, Any]]:
    raw_cues: List[Dict[str, Any]] = []
    for block in re.split(r"\r?\n\r?\n", srt_raw.strip()):
        lines = [ln.strip() for ln in block.splitlines() if ln.strip()]
        if len(lines) < 2:
            continue
        ts_line = next((ln for ln in lines if "-->" in ln), "")
        if not ts_line:
            continue
        t_a, t_b = ts_line.split("-->", 1)
        s = _parse_srt_time(t_a) + audio_offset
        e = _parse_srt_time(t_b) + audio_offset
        text_lines = [ln for ln in lines if "-->" not in ln and not ln.isdigit()]
        txt = re.sub(r"\s+", " ", " ".join(text_lines)).strip()
        if txt and e > s:
            raw_cues.extend(_split_long_cue(txt, s, e))

    # Eliminate tiny overlaps between sequential cues cleanly
    for idx in range(len(raw_cues) - 1):
        if raw_cues[idx]["end"] > raw_cues[idx + 1]["start"]:
            raw_cues[idx]["end"] = max(raw_cues[idx]["start"] + 0.15, raw_cues[idx + 1]["start"] - 0.02)
    return raw_cues


def _synth_voiceover(
    text: str,
    voice_name: str,
    out_mp3: Path,
    max_dur: Optional[float] = None,
    rate: str = "+12%",
) -> Tuple[float, List[Dict[str, Any]]]:
    resolved_voice = _resolve_voice(voice_name)
    cues: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="hf_tts_") as tmp:
        raw_mp3 = Path(tmp) / "raw.mp3"
        raw_vtt = Path(tmp) / "raw.vtt"
        subprocess.run(
            [
                str(EDGE_TTS_BIN),
                "--voice", resolved_voice,
                "--rate", rate,
                "--text", text,
                "--write-media", str(raw_mp3),
                "--write-subtitles", str(raw_vtt),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        raw_probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(raw_mp3)],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        raw_dur = max(0.5, float(raw_probe.stdout.strip() or "1.0"))
        target_clip = min(raw_dur, max(1.0, max_dur)) if max_dur is not None else raw_dur

        ff_cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(raw_mp3)]
        if max_dur is not None:
            ff_cmd.extend(["-t", f"{target_clip:.3f}"])
        fade_out_st = max(0.3, target_clip - 0.10)
        af = (
            f"afade=t=in:st=0:d=0.04,"
            f"afade=t=out:st={fade_out_st:.3f}:d=0.08,"
            f"loudnorm=I=-16:TP=-1.5:LRA=11,"
            f"aresample=resampler=soxr"
        )
        ff_cmd.extend(["-af", af, "-ar", "48000", "-ac", "2", "-c:a", "libmp3lame", "-b:a", "192k", str(out_mp3)])
        subprocess.run(ff_cmd, check=True, capture_output=True, text=True, timeout=60)

        if raw_vtt.exists():
            cues = _parse_srt_cues(raw_vtt.read_text(encoding="utf-8", errors="replace"), audio_offset=0.15)

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", str(out_mp3)],
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )
    out_dur = max(0.5, float(probe.stdout.strip() or "1.0"))
    if not cues:
        cues = _split_long_cue(text, 0.15, out_dur + 0.15, max_chars=44)
    return out_dur, cues


def _resolve_campaign_assets(
    image_paths: Optional[List[str]],
    campaign: str = "",
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Resolve and validate curated product images while enforcing Anti-Confusion exclusion metadata."""
    meta: Dict[str, Any] = {}
    cand_paths: List[Path] = []
    allowed_exts = {".jpg", ".jpeg", ".png", ".webp"}

    if campaign and campaign.strip():
        c_dir = (CAMPAIGNS_ROOT / campaign.strip()).resolve()
        if c_dir.is_relative_to(CAMPAIGNS_ROOT.resolve()) and c_dir.is_dir():
            c_json = c_dir / "campaign.json"
            if c_json.exists():
                try:
                    meta = json.loads(c_json.read_text(encoding="utf-8"))
                except Exception:
                    meta = {}
            if not image_paths:
                rec = meta.get("recommended_image_paths") or []
                if rec:
                    cand_paths = [Path(p) for p in rec]
                else:
                    excluded = {
                        str(e.get("filename") if isinstance(e, dict) else e)
                        for e in (meta.get("excluded_assets") or [])
                    }
                    cand_paths = sorted(
                        p for p in c_dir.iterdir()
                        if p.is_file() and p.suffix.lower() in allowed_exts and p.name not in excluded
                    )

    if image_paths:
        for raw_p in image_paths:
            p = Path(str(raw_p).strip())
            if p.is_file() and p.suffix.lower() in allowed_exts:
                # If campaign metadata exists in parent dir, load it cleanly for scene captions
                if not meta and (p.parent / "campaign.json").exists():
                    try:
                        meta = json.loads((p.parent / "campaign.json").read_text(encoding="utf-8"))
                    except Exception:
                        pass
                cand_paths.append(p)

    # Filter out any excluded files referenced accidentally unless explicitly forced with 1-item override
    excluded_names = {
        str(e.get("filename") if isinstance(e, dict) else e)
        for e in (meta.get("excluded_assets") or [])
    }
    if len(cand_paths) > 1 and excluded_names:
        filtered = [p for p in cand_paths if p.name not in excluded_names]
        if filtered:
            cand_paths = filtered

    seen: set[str] = set()
    resolved: List[Dict[str, Any]] = []
    roles = meta.get("asset_roles") or {}
    for idx, p in enumerate(cand_paths):
        rp = str(p.resolve())
        if rp in seen or not p.is_file():
            continue
        seen.add(rp)
        role_info = roles.get(p.name) or {}
        resolved.append({
            "src_path": p.resolve(),
            "asset_name": f"img_{idx:02d}{p.suffix.lower() or '.jpg'}",
            "orig_name": p.name,
            "role": role_info,
        })
    return resolved, meta


def _render_single_card_html(
    title: str,
    offer: str,
    cta: str,
    theme: Dict[str, str],
    width: int,
    height: int,
    duration: float,
    has_voice: bool,
    voice_dur: float,
    platform_preset: str = "shopee_20_9",
) -> str:
    is_vertical = height > width
    old_offer, main_offer = _split_offer(offer)
    title_esc = html.escape(title)
    old_esc = html.escape(old_offer)
    main_esc = html.escape(main_offer or "Condicao Especial")
    cta_esc = html.escape(cta or "Garanta Agora")
    badge_esc = html.escape(theme["badge_label"])

    is_shopee = is_vertical and (platform_preset or "shopee_20_9") == "shopee_20_9"
    title_size = "54px" if is_shopee else ("68px" if is_vertical else "64px")
    offer_size = "68px" if is_shopee else ("84px" if is_vertical else "78px")
    cta_size = "28px" if is_shopee else ("34px" if is_vertical else "32px")
    stage_pad = "250px 196px 725px 96px" if is_shopee else ("180px 160px 420px 80px" if is_vertical else "64px 96px")
    stage_justify = "flex-start" if is_vertical else "center"
    stage_gap = "26px" if is_shopee else ("34px" if is_vertical else "28px")
    hero_w = "100%" if is_shopee else ("92%" if is_vertical else "82%")
    clip_x = 96 if is_shopee else (80 if is_vertical else 0)
    clip_y = 235 if is_shopee else (160 if is_vertical else 0)
    clip_w = 788 if is_shopee else (840 if is_vertical else width)
    clip_h = 965 if is_shopee else (1300 if is_vertical else height)

    audio_tag = ""
    if has_voice:
        v_dur = min(duration - 0.1, max(0.5, voice_dur))
        audio_tag = f'<audio id="ad-voice" class="clip" src="assets/voiceover.mp3" data-start="0.15" data-duration="{v_dur:.2f}" data-track-index="1"></audio>'

    old_html = f'<div id="offer-old">{old_esc}</div>' if old_esc else ""

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={width}, height={height}" />
  <script src="assets/gsap.min.js"></script>
  <style>
    :root {{
      --bg: {theme["bg"]};
      --card: {theme["card"]};
      --primary: {theme["primary"]};
      --secondary: {theme["secondary"]};
      --accent: {theme["accent"]};
      --badge-text: {theme["badge_text"]};
      --ink: {theme["ink"]};
      --muted: {theme["muted"]};
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      background: var(--bg);
      font-family: "DejaVu Sans", "Noto Color Emoji", "Liberation Sans", Arial, sans-serif;
      color: var(--ink);
    }}
    #root {{
      position: relative;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: radial-gradient(circle at 22% 18%, rgba(255,255,255,0.05), transparent 55%), var(--bg);
    }}
    #fx-canvas {{
      position: absolute;
      inset: 0;
      width: {width}px;
      height: {height}px;
    }}
    .stage-wrap {{
      position: relative;
      z-index: 2;
      width: 100%;
      height: 100%;
      padding: {stage_pad};
      display: flex;
      flex-direction: column;
      justify-content: {stage_justify};
      align-items: center;
      text-align: center;
      gap: {stage_gap};
    }}
    .badge {{
      display: inline-flex;
      align-items: center;
      gap: 12px;
      padding: 12px 26px;
      border-radius: 999px;
      background: linear-gradient(135deg, var(--primary), var(--secondary));
      color: var(--badge-text);
      font-size: 20px;
      font-weight: 800;
      letter-spacing: 0.10em;
      box-shadow: 0 12px 38px rgba(0,0,0,0.45);
    }}
    .hero-card {{
      width: {hero_w};
      background: var(--card);
      border: 2px solid var(--primary);
      border-radius: 32px;
      padding: {"40px 32px" if is_vertical else "44px 60px"};
      box-shadow: 0 32px 90px rgba(0,0,0,0.65);
    }}
    .title {{
      font-size: {title_size};
      font-weight: 800;
      line-height: 1.08;
      letter-spacing: -0.02em;
      color: var(--ink);
      margin-bottom: {"24px" if is_vertical else "22px"};
    }}
    .offer-box {{
      padding: {"26px 22px" if is_vertical else "24px 40px"};
      border-radius: 24px;
      background: rgba(0, 0, 0, 0.45);
      border: 1.5px solid var(--secondary);
    }}
    #offer-old {{
      font-size: 28px;
      font-weight: 600;
      color: var(--muted);
      text-decoration: line-through;
      margin-bottom: 8px;
    }}
    #offer-main {{
      font-size: {offer_size};
      font-weight: 900;
      line-height: 1.05;
      color: var(--accent);
      letter-spacing: -0.02em;
    }}
    .cta-pill {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      max-width: 100%;
      padding: {"20px 36px" if is_vertical else "22px 54px"};
      border-radius: 20px;
      background: linear-gradient(135deg, var(--primary), var(--secondary));
      color: var(--badge-text);
      font-size: {cta_size};
      font-weight: 900;
      letter-spacing: 0.02em;
      box-shadow: 0 18px 46px rgba(0,0,0,0.55);
    }}
    .urgency-track {{
      width: {"84%" if is_vertical else "56%"};
      height: 10px;
      border-radius: 999px;
      background: rgba(255,255,255,0.12);
      overflow: hidden;
    }}
    #urgency-fill {{
      width: 100%;
      height: 100%;
      background: linear-gradient(90deg, var(--primary), var(--secondary));
      transform-origin: left center;
    }}
  </style>
</head>
<body>
  <div id="root" data-composition-id="promo" data-start="0" data-duration="{duration:.2f}" data-width="{width}" data-height="{height}" data-fps="30">
    <canvas id="fx-canvas" width="{width}" height="{height}"></canvas>
    <div class="stage-wrap">
      <div class="clip" data-start="0" data-duration="{duration:.2f}" data-track-index="0">
        <div id="badge-el" class="badge">{badge_esc}</div>
      </div>
      <div class="clip" data-start="0" data-duration="{duration:.2f}" data-track-index="0" style="width:100%;display:flex;justify-content:center;">
        <div id="hero-card" class="hero-card">
          <h1 id="title-el" class="title">{title_esc}</h1>
          <div id="offer-box" class="offer-box">
            {old_html}
            <div id="offer-main">{main_esc}</div>
          </div>
        </div>
      </div>
      <div class="clip" data-start="0" data-duration="{duration:.2f}" data-track-index="0">
        <div id="cta-el" class="cta-pill">{cta_esc}</div>
      </div>
      <div class="clip" data-start="0" data-duration="{duration:.2f}" data-track-index="0" style="width:100%;display:flex;justify-content:center;">
        <div class="urgency-track"><div id="urgency-fill"></div></div>
      </div>
    </div>
    {audio_tag}
  </div>
  <script>
    const canvas = document.getElementById("fx-canvas");
    const ctx = canvas.getContext("2d");
    const W = {width};
    const H = {height};
    const CLIP_X = {clip_x};
    const CLIP_Y = {clip_y};
    const CLIP_W = {clip_w};
    const CLIP_H = {clip_h};
    const PRIMARY = "{theme["primary"]}";
    const SECONDARY = "{theme["secondary"]}";
    const orbs = [];
    for (let i = 0; i < 18; i++) {{
      const angle = (i * 137.5 * Math.PI) / 180;
      orbs.push({{
        bx: ((i * 73) % 100) / 100,
        by: ((i * 41) % 100) / 100,
        r: 18 + (i % 5) * 14,
        dx: Math.cos(angle) * 65,
        dy: Math.sin(angle) * 85,
        c: i % 2 === 0 ? PRIMARY : SECONDARY,
      }});
    }}
    function drawBg(p) {{
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.beginPath();
      ctx.rect(CLIP_X, CLIP_Y, CLIP_W, CLIP_H);
      ctx.clip();
      ctx.strokeStyle = PRIMARY;
      ctx.globalAlpha = 0.08;
      ctx.lineWidth = 1.5;
      const step = 96;
      const shift = (p * 180) % step;
      for (let x = -step; x < W + step; x += step) {{
        ctx.beginPath(); ctx.moveTo(x + shift, 0); ctx.lineTo(x + shift, H); ctx.stroke();
      }}
      for (let y = -step; y < H + step; y += step) {{
        ctx.beginPath(); ctx.moveTo(0, y + shift * 0.5); ctx.lineTo(W, y + shift * 0.5); ctx.stroke();
      }}
      for (let i = 0; i < orbs.length; i++) {{
        const o = orbs[i];
        ctx.globalAlpha = 0.14;
        ctx.fillStyle = o.c;
        ctx.beginPath();
        ctx.arc(CLIP_X + o.bx * CLIP_W + Math.sin(p * Math.PI * 2 + i) * o.dx, CLIP_Y + o.by * CLIP_H + Math.cos(p * Math.PI * 2 + i * 0.7) * o.dy, o.r, 0, Math.PI * 2);
        ctx.fill();
      }}
      ctx.restore();
    }}
    const dur = {duration:.2f};
    const tl = gsap.timeline({{ paused: true, onUpdate: () => drawBg(tl.progress()) }});
    tl.fromTo("#badge-el", {{ y: -40, opacity: 0, scale: 0.85 }}, {{ y: 0, opacity: 1, scale: 1, duration: 0.45, ease: "back.out(1.7)" }}, 0.05)
      .fromTo("#hero-card", {{ y: 60, opacity: 0, scale: 0.92 }}, {{ y: 0, opacity: 1, scale: 1, duration: 0.65, ease: "power3.out" }}, 0.25)
      .fromTo("#offer-box", {{ scale: 0.88, opacity: 0 }}, {{ scale: 1, opacity: 1, duration: 0.55, ease: "back.out(1.5)" }}, 0.75)
      .fromTo("#cta-el", {{ y: 40, opacity: 0, scale: 0.9 }}, {{ y: 0, opacity: 1, scale: 1, duration: 0.5, ease: "power3.out" }}, 1.15)
      .to("#cta-el", {{ scale: 1.06, duration: 0.45, yoyo: true, repeat: 2, ease: "sine.inOut" }}, 2.0)
      .fromTo("#urgency-fill", {{ scaleX: 0.05 }}, {{ scaleX: 1.0, duration: Math.max(1.5, dur - 0.8), ease: "none" }}, 0.3)
      .to("#hero-card", {{ y: -8, duration: Math.max(0.4, dur - 2.5), ease: "sine.inOut" }}, 2.2)
      .to("#hero-card", {{ opacity: 0.96, duration: 0.2 }}, Math.max(0.2, dur - 0.25));
    drawBg(0);
    window.__timelines["promo"] = tl;
    tl.seek(0);
  </script>
</body>
</html>"""


def _render_multi_image_html(
    title: str,
    offer: str,
    cta: str,
    theme: Dict[str, str],
    width: int,
    height: int,
    duration: float,
    has_voice: bool,
    voice_dur: float,
    assets: List[Dict[str, Any]],
    cues: List[Dict[str, Any]],
    campaign_meta: Dict[str, Any],
    extra_price_note: str = "",
    orange_cart_cta: str = "",
    subtitles: bool = True,
    platform_preset: str = "shopee_20_9",
) -> str:
    """Render 4-Scene Multi-Image POV + Shopee 20:9 Anti-Crop Safe-Zone HTML composition."""
    n = len(assets)
    # Pick indices safely regardless of asset count (1..N)
    img_hook = f"assets/{assets[0]['asset_name']}"
    img_b1 = f"assets/{assets[min(1, n - 1)]['asset_name']}"
    img_b2 = f"assets/{assets[min(2, n - 1)]['asset_name']}"
    img_s1 = f"assets/{assets[min(3, n - 1)]['asset_name']}"
    img_s2 = f"assets/{assets[min(4, n - 1)]['asset_name']}"
    img_kit = f"assets/{assets[n - 1]['asset_name']}"

    # 4-Beat timings matched cleanly to 15s-18s Shopee POV rhythm
    t1_end = min(3.4, round(duration * 0.20, 2))
    t2_mid = round(t1_end + (duration * 0.15), 2)
    t2_end = min(8.2, round(duration * 0.47, 2))
    t3_end = min(11.6, round(duration * 0.67, 2))
    if t3_end >= duration - 2.4:
        t3_end = max(t2_end + 1.5, round(duration - 3.6, 2))

    title_esc = html.escape(title)
    offer_main_esc = html.escape(offer or "KIT LEVE 2 (200ml) · R$ 69,99")
    pricing = campaign_meta.get("pricing") or {}
    unit_note = extra_price_note.strip() or (
        f"Sai {pricing.get('unit_price', 'R$ 34,99')} cada frasco original L'Oréal!"
        if pricing.get("unit_price") or "69,99" in (offer or "")
        else html.escape(cta or "Oferta Relâmpago Shopee")
    )
    unit_esc = html.escape(unit_note)
    cart_cta_raw = orange_cart_cta.strip() or "👇 GARANTA NO CARRINHO LARANJA ABAIXO 👇"
    cart_cta_esc = html.escape(cart_cta_raw)

    is_vertical = height > width
    preset = (platform_preset or "shopee_20_9").strip().lower()
    if is_vertical:
        if preset in ("tiktok_reels", "yt_shorts"):
            sx, sw = 96, 788
            pill_y, prog_y = 200, 256
            stage_y, stage_h = 268, 560
            info_y, info_h = 842, 74
            price_y, price_h = 842, 150
            sub_y, sub_h = 1020, 92
            cta_y, cta_h = 1130, 58
        else:
            # Strict Shopee 20:9 Anti-Crop (X: 96..884, Y_top: 235, Y_stage: 300..850, Y_price: 865..1015, Y_sub: 1040..1132, Y_cta: 1145..1203; Y: 1210..1920 empty)
            sx, sw = 96, 788
            pill_y, prog_y = 235, 290
            stage_y, stage_h = 300, 550
            info_y, info_h = 865, 74
            price_y, price_h = 865, 150
            sub_y, sub_h = 1040, 92
            cta_y, cta_h = 1145, 58
        clip_y = pill_y
        clip_h = (cta_y + cta_h) - pill_y
    else:
        sx, sw = 120, 1680
        pill_y, prog_y = 40, 98
        stage_y, stage_h = 112, 620
        info_y, info_h = 746, 68
        price_y, price_h = 746, 136
        sub_y, sub_h = 896, 86
        cta_y, cta_h = 994, 58
        clip_y, clip_h = 24, 1032

    filtered_cues = _filter_duplicate_cta_cues(cues, t3_end, cart_cta_raw)

    audio_tag = ""
    if has_voice:
        v_dur = min(duration - 0.05, max(0.5, voice_dur))
        audio_tag = f'<audio id="ad-voice" class="clip" src="assets/voiceover.mp3" data-start="0.15" data-duration="{v_dur:.2f}" data-track-index="1"></audio>'

    cues_divs = []
    if subtitles and filtered_cues:
        for i, c in enumerate(filtered_cues):
            cues_divs.append(f'<div id="cue-{i}" class="caption-cue">{html.escape(c["text"])}</div>')
    cues_html = "\n          ".join(cues_divs)
    cues_json = json.dumps(filtered_cues, ensure_ascii=False)

    return f"""<!doctype html>
<html lang="pt-BR">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width={width}, height={height}" />
  <script src="assets/gsap.min.js"></script>
  <style>
    :root {{
      --bg: {theme["bg"]};
      --card: {theme["card"]};
      --primary: {theme["primary"]};
      --secondary: {theme["secondary"]};
      --accent: {theme["accent"]};
      --ink: {theme["ink"]};
    }}
    * {{ margin: 0; padding: 0; box-sizing: border-box; }}
    html, body {{
      width: {width}px;
      height: {height}px;
      overflow: hidden;
      background: var(--bg);
      font-family: "DejaVu Sans", "Noto Color Emoji", "Liberation Sans", Arial, sans-serif;
      color: var(--ink);
    }}
    #root {{
      position: relative;
      width: 100%;
      height: 100%;
      overflow: hidden;
      background: radial-gradient(circle at 50% 30%, rgba(245, 158, 11, 0.16), transparent 55%), var(--bg);
    }}
    #fx-canvas {{
      position: absolute;
      inset: 0;
      width: {width}px;
      height: {height}px;
      z-index: 1;
    }}
    .clip-stage {{
      position: relative;
      z-index: 2;
      width: 100%;
      height: 100%;
    }}
    .scene {{
      position: absolute;
      inset: 0;
      width: 100%;
      height: 100%;
      opacity: 0;
    }}
    #scene-1 {{
      opacity: 1;
    }}
    .ambient-bg {{
      position: absolute;
      inset: -40px;
      width: calc(100% + 80px);
      height: calc(100% + 80px);
      object-fit: cover;
      filter: blur(46px) brightness(0.30) saturate(1.25);
      transform: scale(1.08);
    }}
    /* Progresso em Y={prog_y}px, X={sx}..{sx+sw}px (Y=0..235px livre) */
    .top-progress {{
      position: absolute;
      top: {prog_y}px;
      left: {sx}px;
      width: {sw}px;
      height: 4px;
      border-radius: 999px;
      background: rgba(255,255,255,0.16);
      overflow: hidden;
      z-index: 10;
    }}
    #progress-fill {{
      width: 100%;
      height: 100%;
      background: linear-gradient(90deg, #fbbf24, #ee4d2d);
      transform-origin: left center;
    }}
    /* Badge superior em Y={pill_y}px, X={sx}..{sx+sw}px */
    .top-pill {{
      position: absolute;
      top: {pill_y}px;
      left: {sx}px;
      max-width: {sw}px;
      height: 50px;
      display: inline-flex;
      align-items: center;
      gap: 10px;
      padding: 8px 20px;
      border-radius: 999px;
      font-size: 20px;
      font-weight: 900;
      letter-spacing: 0.04em;
      text-transform: uppercase;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      box-shadow: 0 10px 28px rgba(0,0,0,0.55);
      z-index: 5;
    }}
    .pill-hook {{
      background: linear-gradient(135deg, #ee4d2d, #f59e0b);
      color: #fff;
      border: 2px solid #fde68a;
    }}
    .pill-gold {{
      background: linear-gradient(135deg, #f59e0b, #fbbf24);
      color: #1f1102;
      border: 2px solid #fffbeb;
    }}
    .pill-dark {{
      background: rgba(15, 9, 3, 0.90);
      color: #fde68a;
      border: 2px solid #f59e0b;
    }}
    /* Palco Principal [Y: {stage_y}px -> {stage_y+stage_h}px, X: {sx}px -> {sx+sw}px] */
    .hero-frame {{
      position: absolute;
      top: {stage_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {stage_h}px;
      border-radius: 28px;
      overflow: hidden;
      border: 3px solid rgba(245, 158, 11, 0.85);
      background: radial-gradient(circle at 50% 45%, #231607 0%, #0d0803 100%);
      box-shadow: 0 22px 60px rgba(0, 0, 0, 0.72), 0 0 32px rgba(245, 158, 11, 0.22);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .hero-img {{
      width: 100%;
      height: 100%;
      object-fit: contain;
      display: block;
      transform-origin: center center;
    }}
    /* Cena 4 Foto Container Travado em [Y: {stage_y}..{stage_y+stage_h}px, X: {sx}..{sx+sw}px] */
    .hero-frame-kit4 {{
      position: absolute;
      top: {stage_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {stage_h}px;
      border-radius: 28px;
      overflow: hidden;
      border: 3.5px solid #f59e0b;
      background: radial-gradient(circle at 50% 45%, #ffffff 60%, #fef3c7 100%);
      box-shadow: 0 24px 66px rgba(0, 0, 0, 0.75), 0 0 42px rgba(245, 158, 11, 0.36);
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 14px 20px;
    }}
    /* Card de Oferta/Preco da Cena 4 [Y: {price_y}px -> {price_y+price_h}px, X: {sx}px -> {sx+sw}px] */
    .price-banner-card {{
      position: absolute;
      top: {price_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {price_h}px;
      border-radius: 24px;
      padding: 12px 22px;
      background: linear-gradient(135deg, rgba(238, 77, 45, 0.98), rgba(180, 48, 12, 0.98));
      border: 3px solid #fde047;
      box-shadow: 0 18px 46px rgba(0, 0, 0, 0.65);
      display: flex;
      flex-direction: column;
      justify-content: center;
      overflow: hidden;
      z-index: 6;
    }}
    .price-title {{
      font-size: 18px;
      font-weight: 800;
      color: #fed7aa;
      letter-spacing: 0.03em;
      text-transform: uppercase;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
      margin-bottom: 3px;
    }}
    .price-main {{
      font-size: 32px;
      font-weight: 900;
      line-height: 1.08;
      color: #ffffff;
    }}
    .price-sub {{
      margin-top: 4px;
      font-size: 21px;
      font-weight: 900;
      color: #fde047;
    }}
    /* Split layout Cena 3 [Y: {stage_y}..{stage_y+stage_h}px, X: {sx}..{sx+sw}px] */
    .split-frame {{
      position: absolute;
      top: {stage_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {stage_h}px;
      display: flex;
      flex-direction: column;
      gap: 12px;
      overflow: hidden;
    }}
    .split-panel {{
      position: relative;
      flex: 1;
      border-radius: 24px;
      overflow: hidden;
      border: 2.5px solid #f59e0b;
      background: #140c04;
      box-shadow: 0 16px 40px rgba(0,0,0,0.65);
      display: flex;
      align-items: center;
      justify-content: center;
    }}
    .split-panel .split-bg {{
      position: absolute;
      inset: -24px;
      width: calc(100% + 48px);
      height: calc(100% + 48px);
      object-fit: cover;
      filter: blur(22px) brightness(0.55) saturate(1.2);
      z-index: 0;
    }}
    .split-panel .split-fg {{
      position: relative;
      z-index: 1;
      width: 100%;
      height: 100%;
      object-fit: contain;
    }}
    .split-tag {{
      position: absolute;
      left: 14px;
      bottom: 10px;
      padding: 6px 14px;
      border-radius: 10px;
      background: rgba(12, 7, 2, 0.90);
      border: 1.5px solid #fbbf24;
      color: #fde68a;
      font-size: 18px;
      font-weight: 800;
    }}
    /* Chips Cenas 1-3 [Y: {info_y}px -> {info_y+info_h}px, X: {sx}px -> {sx+sw}px] */
    .info-zone {{
      position: absolute;
      top: {info_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {info_h}px;
      display: flex;
      align-items: center;
      overflow: hidden;
      z-index: 4;
    }}
    .info-zone-s3 {{
      top: {info_y}px;
      height: {info_h}px;
    }}
    .benefit-row {{
      width: 100%;
      display: flex;
      flex-wrap: nowrap;
      gap: 8px;
      align-items: center;
      justify-content: space-between;
    }}
    .b-chip {{
      padding: 10px 14px;
      border-radius: 14px;
      background: rgba(18, 11, 3, 0.92);
      border: 2px solid #f59e0b;
      color: #fffbeb;
      font-size: 19px;
      font-weight: 800;
      box-shadow: 0 8px 20px rgba(0,0,0,0.45);
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    .b-chip-orange {{
      background: linear-gradient(135deg, #ee4d2d, #f97316);
      border-color: #fde68a;
      color: #fff;
    }}
    /* Corredor Unico de Legenda [Y: {sub_y}px -> {sub_y+sub_h}px, X: {sx}px -> {sx+sw}px] */
    #sub-box, #captions-layer {{
      position: absolute;
      top: {sub_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {sub_h}px;
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 8;
      pointer-events: none;
    }}
    .caption-cue {{
      position: absolute;
      max-width: {sw - 8}px;
      max-height: {sub_h - 4}px;
      padding: 10px 18px;
      border-radius: 16px;
      background: rgba(11, 7, 2, 0.92);
      border: 2px solid rgba(251, 191, 36, 0.85);
      box-shadow: 0 10px 26px rgba(0,0,0,0.65);
      color: #fffbeb;
      font-size: 29px;
      font-weight: 900;
      line-height: 1.18;
      text-align: center;
      overflow: hidden;
      opacity: 0;
      transform: translateY(8px) scale(0.96);
    }}
    /* Badge CTA Compacto Cena 4 [Y: {cta_y}px -> {cta_y+cta_h}px, X: {sx}px -> {sx+sw}px] */
    #cart-cta-zone {{
      position: absolute;
      top: {cta_y}px;
      left: {sx}px;
      width: {sw}px;
      height: {cta_h}px;
      z-index: 10;
      opacity: 0;
      display: flex;
      align-items: center;
      justify-content: center;
      overflow: hidden;
    }}
    .cart-pill {{
      width: {sw - 36}px;
      max-width: 100%;
      height: {max(44, cta_h - 6)}px;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      padding: 8px 18px;
      border-radius: 16px;
      background: linear-gradient(135deg, #ee4d2d, #ff7337);
      border: 2.5px solid #fde047;
      color: #ffffff;
      font-size: 22px;
      font-weight: 900;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }}
    /* Y: 1210px -> 1920px e X: 0..96px / 884..1080px ficam 100% VAZIOS */
  </style>
</head>
<body>
  <div id="root" data-composition-id="promo" data-start="0" data-duration="{duration:.2f}" data-width="{width}" data-height="{height}" data-fps="30">
    <canvas id="fx-canvas" width="{width}" height="{height}"></canvas>
    <div class="clip clip-stage" data-start="0" data-duration="{duration:.2f}" data-track-index="0">
      <div class="top-progress"><div id="progress-fill"></div></div>

      <!-- CENA 1: HOOK ANTES VS DEPOIS REAL (0s - {t1_end:.2f}s) -->
      <section id="scene-1" class="scene">
        <img class="ambient-bg" src="{img_hook}" alt="" />
        <div id="s1-pill" class="top-pill pill-hook">SEM RETOQUE · RESULTADO REAL</div>
        <div class="hero-frame">
          <img id="s1-img" class="hero-img" src="{img_hook}" alt="Antes e Depois Elseve" />
        </div>
        <div class="info-zone">
          <div id="s1-card" class="b-chip b-chip-orange" style="width:100%;text-align:center;font-size:20px;">
            Cabelo armado e com frizz? Olha essa mudança real!
          </div>
        </div>
      </section>

      <!-- CENA 2A: BENEFÍCIOS CLOSE POV ({t1_end:.2f}s - {t2_mid:.2f}s) -->
      <section id="scene-2a" class="scene">
        <img class="ambient-bg" src="{img_b1}" alt="" />
        <div class="top-pill pill-gold">ÓLEO EXTRAORDINÁRIO L'ORÉAL PARIS</div>
        <div class="hero-frame">
          <img id="s2a-img" class="hero-img" src="{img_b1}" alt="Benefícios Elseve" />
        </div>
        <div class="info-zone">
          <div class="benefit-row">
            <div id="s2-c1" class="b-chip">Zero Frizz</div>
            <div id="s2-c2" class="b-chip">Menos Pontas Duplas</div>
            <div id="s2-c3" class="b-chip b-chip-orange">Proteção Térmica 230°C</div>
          </div>
        </div>
      </section>

      <!-- CENA 2B: RESULTADO 1 SEMANA DE BRILHO ({t2_mid:.2f}s - {t2_end:.2f}s) -->
      <section id="scene-2b" class="scene">
        <img class="ambient-bg" src="{img_b2}" alt="" />
        <div class="top-pill pill-gold">1 SEMANA DE NUTRIÇÃO E BRILHO</div>
        <div class="hero-frame">
          <img id="s2b-img" class="hero-img" src="{img_b2}" alt="1 Semana de Nutrição" />
        </div>
        <div class="info-zone">
          <div class="benefit-row">
            <div class="b-chip">Toque Leve · Não Pesa nos Fios</div>
            <div class="b-chip b-chip-orange">Uso Universal</div>
          </div>
        </div>
      </section>

      <!-- CENA 3: SPLIT FÓRMULA 6 ÓLEOS + NOVA EMBALAGEM ({t2_end:.2f}s - {t3_end:.2f}s) -->
      <section id="scene-3" class="scene">
        <img class="ambient-bg" src="{img_s1}" alt="" />
        <div class="top-pill pill-dark">NOVA EMBALAGEM · MESMA FÓRMULA ORIGINAL</div>
        <div class="split-frame">
          <div id="s3-top" class="split-panel">
            <img class="split-bg" src="{img_s1}" alt="" />
            <img class="split-fg" src="{img_s1}" alt="6 Óleos de Flores Preciosas" />
            <div class="split-tag" style="z-index:2;">6 Óleos de Flores Preciosas · Nutre 10x Mais</div>
          </div>
          <div id="s3-bot" class="split-panel">
            <img class="split-bg" src="{img_s2}" alt="" />
            <img class="split-fg" src="{img_s2}" alt="Nova Embalagem Mesma Fórmula" />
            <div class="split-tag" style="z-index:2;">Original L'Oréal Paris · Nova Embalagem</div>
          </div>
        </div>
        <div class="info-zone info-zone-s3">
          <div class="b-chip" style="width:100%;text-align:center;">Autenticidade Garantida · Mesma Fórmula Consagrada</div>
        </div>
      </section>

      <!-- CENA 4: FOTO TRAVADA (300..850) + CARD DE OFERTA/PREÇO (865..1015) ({t3_end:.2f}s - {duration:.2f}s) -->
      <section id="scene-4" class="scene">
        <img class="ambient-bg" src="{img_kit}" alt="" />
        <div id="s4-pill" class="top-pill pill-hook">KIT COM 2 FRASCOS DE 100ML · L'ORÉAL</div>
        <div class="hero-frame-kit4">
          <img id="s4-img" class="hero-img" src="{img_kit}" alt="Kit 2 Óleos Elseve 100ml" />
        </div>
        <div id="s4-price" class="price-banner-card">
          <div class="price-title">{title_esc}</div>
          <div class="price-main">{offer_main_esc}</div>
          <div class="price-sub">{unit_esc}</div>
        </div>
      </section>

      <!-- CORREDOR EXCLUSIVO DE LEGENDA SHOPEE 20:9 (Y: {sub_y}..{sub_y+sub_h}px, X: {sx}..{sx+sw}px) -->
      <div id="sub-box">
        {cues_html}
      </div>

      <!-- BADGE CTA COMPACTO PULSANTE CENA 4 (Y: {cta_y}..{cta_y+cta_h}px, X: {sx}..{sx+sw}px) -->
      <div id="cart-cta-zone">
        <div id="cart-pill" class="cart-pill">
          <span>{cart_cta_esc}</span>
        </div>
      </div>
    </div>
    {audio_tag}
  </div>
  <script>
    const canvas = document.getElementById("fx-canvas");
    const ctx = canvas.getContext("2d");
    const W = {width};
    const H = {height};
    const CLIP_X = {sx};
    const CLIP_Y = {clip_y};
    const CLIP_W = {sw};
    const CLIP_H = {clip_h};
    const cues = {cues_json};

    const goldMotes = [];
    for (let i = 0; i < 20; i++) {{
      goldMotes.push({{
        bx: ((i * 53) % 100) / 100,
        by: ((i * 37) % 100) / 100,
        r: 4 + (i % 5) * 4,
        dx: ((i % 2 === 0 ? 1 : -1) * (25 + (i % 4) * 14)),
        dy: -90 - (i % 4) * 35,
      }});
    }}

    function drawGoldCanvas(p) {{
      ctx.clearRect(0, 0, W, H);
      ctx.save();
      ctx.beginPath();
      ctx.rect(CLIP_X, CLIP_Y, CLIP_W, CLIP_H);
      ctx.clip();
      for (let i = 0; i < goldMotes.length; i++) {{
        const m = goldMotes[i];
        const x = CLIP_X + m.bx * CLIP_W + Math.sin(p * Math.PI * 3 + i) * m.dx;
        const y = CLIP_Y + (((m.by * CLIP_H + p * m.dy * 2) % CLIP_H + CLIP_H) % CLIP_H);
        ctx.globalAlpha = 0.20 + (i % 3) * 0.08;
        ctx.fillStyle = i % 3 === 0 ? "#ee4d2d" : "#fbbf24";
        ctx.beginPath();
        ctx.arc(x, y, m.r, 0, Math.PI * 2);
        ctx.fill();
      }}
      ctx.restore();
    }}

    const dur = {duration:.2f};
    const t1End = {t1_end:.2f};
    const t2Mid = {t2_mid:.2f};
    const t2End = {t2_end:.2f};
    const t3End = {t3_end:.2f};

    const tl = gsap.timeline({{
      paused: true,
      onUpdate: () => drawGoldCanvas(tl.progress()),
    }});

    tl.fromTo("#progress-fill", {{ scaleX: 0.02 }}, {{ scaleX: 1.0, duration: dur, ease: "none" }}, 0);

    tl.set("#scene-1", {{ opacity: 1 }}, 0)
      .fromTo("#s1-img", {{ scale: 1.0 }}, {{ scale: 1.055, duration: t1End, ease: "sine.inOut" }}, 0)
      .fromTo("#s1-card", {{ y: 12, opacity: 0.9 }}, {{ y: 0, opacity: 1, duration: 0.35, ease: "back.out(1.6)" }}, 0.05)
      .to("#scene-1", {{ opacity: 0, duration: 0.28, ease: "power2.in" }}, t1End - 0.25);

    tl.to("#scene-2a", {{ opacity: 1, duration: 0.28, ease: "power2.out" }}, t1End - 0.22)
      .fromTo("#s2a-img", {{ scale: 1.05, x: -12 }}, {{ scale: 1.0, x: 10, duration: Math.max(0.8, t2Mid - t1End + 0.3), ease: "sine.inOut" }}, t1End - 0.15)
      .fromTo("#s2-c1", {{ y: 14, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.3, ease: "back.out(1.5)" }}, t1End + 0.10)
      .fromTo("#s2-c2", {{ y: 14, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.3, ease: "back.out(1.5)" }}, t1End + 0.45)
      .fromTo("#s2-c3", {{ y: 14, opacity: 0 }}, {{ y: 0, opacity: 1, duration: 0.3, ease: "back.out(1.5)" }}, t1End + 0.80)
      .to("#scene-2a", {{ opacity: 0, duration: 0.28, ease: "power2.in" }}, t2Mid - 0.22);

    tl.to("#scene-2b", {{ opacity: 1, duration: 0.28, ease: "power2.out" }}, t2Mid - 0.20)
      .fromTo("#s2b-img", {{ scale: 1.0, y: 10 }}, {{ scale: 1.05, y: -8, duration: Math.max(0.8, t2End - t2Mid + 0.25), ease: "sine.inOut" }}, t2Mid - 0.10)
      .to("#scene-2b", {{ opacity: 0, duration: 0.28, ease: "power2.in" }}, t2End - 0.22);

    tl.to("#scene-3", {{ opacity: 1, duration: 0.28, ease: "power2.out" }}, t2End - 0.20)
      .fromTo("#s3-top", {{ x: -40, opacity: 0.3 }}, {{ x: 0, opacity: 1, duration: 0.45, ease: "power3.out" }}, t2End - 0.10)
      .fromTo("#s3-bot", {{ x: 40, opacity: 0.3 }}, {{ x: 0, opacity: 1, duration: 0.45, ease: "power3.out" }}, t2End + 0.12)
      .to("#scene-3", {{ opacity: 0, duration: 0.28, ease: "power2.in" }}, t3End - 0.22);

    const s4Dur = Math.max(1.2, dur - t3End);
    tl.to("#scene-4", {{ opacity: 1, duration: 0.30, ease: "power2.out" }}, t3End - 0.20)
      .fromTo("#s4-img", {{ scale: 0.94 }}, {{ scale: 1.03, duration: s4Dur, ease: "power1.out" }}, t3End - 0.10)
      .fromTo("#s4-price", {{ y: 18, opacity: 0.2 }}, {{ y: 0, opacity: 1, duration: 0.40, ease: "power3.out" }}, t3End + 0.05)
      .fromTo("#cart-cta-zone", {{ y: 12, opacity: 0, scale: 0.96 }}, {{ y: 0, opacity: 1, scale: 1, duration: 0.36, ease: "back.out(1.6)" }}, t3End - 0.05)
      .to("#cart-pill", {{ scale: 1.018, duration: 0.38, yoyo: true, repeat: Math.max(2, Math.floor(s4Dur / 0.55)), ease: "sine.inOut" }}, t3End + 0.35);

    for (let i = 0; i < cues.length; i++) {{
      const c = cues[i];
      const elId = "#cue-" + i;
      const start = Math.max(0.02, c.start);
      const end = Math.min(dur - 0.05, c.end);
      if (end <= start) continue;
      tl.fromTo(elId, {{ opacity: 0, y: 8, scale: 0.96 }}, {{ opacity: 1, y: 0, scale: 1, duration: 0.12, ease: "power2.out" }}, start)
        .to(elId, {{ opacity: 0, y: -6, duration: 0.12, ease: "power2.in" }}, Math.max(start + 0.12, end - 0.10));
    }}

    drawGoldCanvas(0);
    window.__timelines["promo"] = tl;
    tl.seek(0);
  </script>
</body>
</html>"""


def render_ad(
    title: str,
    offer: str,
    cta: str,
    output_path: Path,
    style: str = "B2B Emerald",
    aspect: str = "9:16",
    duration: float = 8.0,
    voice_enabled: bool = True,
    voice_name: str = "pt-BR-AntonioNeural",
    voice_script: str = "",
    image_paths: Optional[List[str]] = None,
    campaign: str = "",
    extra_price_note: str = "",
    orange_cart_cta: str = "",
    subtitles: bool = True,
    quality: str = "draft",
    platform_preset: str = "shopee_20_9",
) -> Dict[str, Any]:
    t0 = time.monotonic()
    req_dur = max(5.0, min(30.0, float(duration)))
    assets, campaign_meta = _resolve_campaign_assets(image_paths, campaign)
    is_multi = len(assets) > 0

    if is_multi and (style or "").strip().lower() == "b2b emerald":
        style = "L'Oréal Gold Luxury"
    _, theme = _resolve_theme(style)

    preset_norm = (platform_preset or "shopee_20_9").strip().lower()
    if preset_norm == "yt_16_9":
        aspect = "16:9"
    is_vertical = (aspect or "9:16").strip() != "16:9"
    width, height = (1080, 1920) if is_vertical else (1920, 1080)

    if not HF_BIN.exists():
        raise RuntimeError("HyperFrames CLI não encontrado em /opt/ai-media-studio/tools/hyperframes")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="hf_comp_") as tmpdir:
        comp_dir = Path(tmpdir)
        assets_dir = comp_dir / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(GSAP_VENDOR, assets_dir / "gsap.min.js")

        for item in assets:
            shutil.copy2(item["src_path"], assets_dir / item["asset_name"])

        (comp_dir / "package.json").write_text(
            json.dumps({"name": "hf-ad", "version": "1.0.0", "private": True}, indent=2),
            encoding="utf-8",
        )
        (comp_dir / "hyperframes.json").write_text(
            json.dumps({"fps": 30, "width": width, "height": height}, indent=2),
            encoding="utf-8",
        )

        has_voice = False
        voice_dur = 0.0
        cues: List[Dict[str, Any]] = []
        final_script = ""
        dur = req_dur

        if voice_enabled and EDGE_TTS_BIN.exists():
            final_script = _build_ad_script(title, offer, cta, voice_script, has_images=is_multi)
            try:
                # In multi-image mode, NEVER truncate audio: expand video duration to hold the full voiceover!
                max_arg = None if is_multi else (req_dur - 0.25)
                voice_dur, cues = _synth_voiceover(
                    final_script,
                    voice_name,
                    assets_dir / "voiceover.mp3",
                    max_dur=max_arg,
                    rate="+12%",
                )
                has_voice = True
                if is_multi:
                    dur = max(req_dur, round(voice_dur + 0.75, 2))
            except Exception as exc:
                print(f"[WARN] TTS fallback: {exc}", flush=True)
                has_voice = False

        if is_multi:
            html_doc = _render_multi_image_html(
                title=title,
                offer=offer,
                cta=cta,
                theme=theme,
                width=width,
                height=height,
                duration=dur,
                has_voice=has_voice,
                voice_dur=voice_dur,
                assets=assets,
                cues=cues,
                campaign_meta=campaign_meta,
                extra_price_note=extra_price_note,
                orange_cart_cta=orange_cart_cta,
                subtitles=subtitles,
                platform_preset=preset_norm,
            )
        else:
            html_doc = _render_single_card_html(
                title=title,
                offer=offer,
                cta=cta,
                theme=theme,
                width=width,
                height=height,
                duration=dur,
                has_voice=has_voice,
                voice_dur=voice_dur,
                platform_preset=preset_norm,
            )

        (comp_dir / "index.html").write_text(html_doc, encoding="utf-8")

        check_res = subprocess.run(
            [str(HF_BIN), "check", "--json"],
            cwd=str(comp_dir),
            capture_output=True,
            text=True,
            timeout=45,
        )
        check_errors = []
        try:
            chk = json.loads(check_res.stdout or "{}")
            check_errors = [x for x in chk.get("errors", [])] if isinstance(chk, dict) else []
        except Exception:
            pass

        q_flag = quality if quality in ("draft", "looks", "delivery") else "draft"
        render_res = subprocess.run(
            [
                str(HF_BIN),
                "render",
                ".",
                "--quality", q_flag,
                "--fps", "30",
                "--skill", "hyperframes",
                "-o", str(output_path),
            ],
            cwd=str(comp_dir),
            capture_output=True,
            text=True,
            timeout=360,
        )
        if render_res.returncode != 0 or not output_path.exists():
            raise RuntimeError(
                f"Falha no render HyperFrames (code {render_res.returncode}): "
                f"{(render_res.stderr or render_res.stdout)[-500:]}"
            )

    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration,size", "-of", "json", str(output_path)],
        capture_output=True,
        text=True,
        timeout=20,
    )
    out_dur = dur
    try:
        out_dur = float(json.loads(probe.stdout)["format"]["duration"])
    except Exception:
        pass

    return {
        "ok": True,
        "output_path": str(output_path),
        "filename": output_path.name,
        "size_bytes": output_path.stat().st_size,
        "mode": "multi_image_pov" if is_multi else "single_card",
        "images_used": [x["orig_name"] for x in assets],
        "style": theme["name"],
        "aspect": "9:16" if is_vertical else "16:9",
        "platform_preset": preset_norm,
        "resolution": f"{width}x{height}",
        "duration": round(out_dur, 2),
        "audio_duration": round(voice_dur, 2),
        "voice_enabled": has_voice,
        "voice_name": _resolve_voice(voice_name) if has_voice else "",
        "voice_script": final_script,
        "check_errors": len(check_errors),
        "elapsed_seconds": round(time.monotonic() - t0, 2),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Render HyperFrames Single or Multi-Image POV Ad on VPS")
    ap.add_argument("--title", required=True)
    ap.add_argument("--offer", default="")
    ap.add_argument("--cta", default="Saiba Mais")
    ap.add_argument("--style", default="B2B Emerald")
    ap.add_argument("--aspect", default="9:16")
    ap.add_argument("--duration", type=float, default=8.0)
    ap.add_argument("--voice-enabled", action="store_true")
    ap.add_argument("--voice-name", default="pt-BR-AntonioNeural")
    ap.add_argument("--voice-script", default="")
    ap.add_argument("--image", action="append", default=[], help="Product image path (repeatable)")
    ap.add_argument("--images-json", default="", help="JSON array of image paths")
    ap.add_argument("--campaign", default="", help="Campaign folder slug under /opt/ai-media-studio/campaigns/")
    ap.add_argument("--extra-price-note", default="")
    ap.add_argument("--orange-cart-cta", default="")
    ap.add_argument("--no-subtitles", action="store_true")
    ap.add_argument("--quality", default="draft")
    ap.add_argument("--platform-preset", default="shopee_20_9")
    ap.add_argument("-o", "--output", type=Path, required=True)
    args = ap.parse_args()

    img_list: List[str] = list(args.image or [])
    if args.images_json and args.images_json.strip():
        try:
            loaded = json.loads(args.images_json)
            if isinstance(loaded, list):
                img_list.extend(str(x) for x in loaded if str(x).strip())
        except Exception:
            pass

    res = render_ad(
        title=args.title,
        offer=args.offer,
        cta=args.cta,
        output_path=args.output,
        style=args.style,
        aspect=args.aspect,
        duration=args.duration,
        voice_enabled=args.voice_enabled,
        voice_name=args.voice_name,
        voice_script=args.voice_script,
        image_paths=img_list,
        campaign=args.campaign,
        extra_price_note=args.extra_price_note,
        orange_cart_cta=args.orange_cart_cta,
        subtitles=not args.no_subtitles,
        quality=args.quality,
        platform_preset=args.platform_preset,
    )
    print(json.dumps(res, ensure_ascii=False))


if __name__ == "__main__":
    main()
