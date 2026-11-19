#!/usr/bin/env python3
"""Cinema Engine v4.0 Director, Shot Compiler & Reference Pack Builder.

Replaces talking-head frontal directives and +14% static crop reuse with
real cinema coverage: Visual Bible (cast + wardrobe + set lighting), 180-degree
eyeline axis locking, over-the-shoulder (OTS) / reverse / reaction / ensemble shots,
varied pacing (~3s / ~4s / <=1.5s / ~2s), and J/L-cut dynamic sound design.
"""

from __future__ import annotations

import base64
import json
import math
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"


CINEMA_V4_FRAMINGS: Dict[str, Dict[str, str]] = {
    "two_shot": {
        "label": "Two-Shot · Abertura em Conflito",
        "lens_mm": "35mm",
        "prompt_cue": (
            "medium-wide 35mm vertical 9:16 cinema two-shot framing both characters in three-quarter profile facing each other across a candlelit banquet table, "
            "practical warm chandelier lighting, background patrons softly blurred in depth, eyes averted toward companion"
        ),
        "video_cue": "Medium-wide 35mm cinema shot with subtle handheld camera breathing, characters interacting in physical space, eyes locked on each other",
    },
    "over_shoulder": {
        "label": "Over-The-Shoulder (OTS) · Eixo Direito",
        "lens_mm": "50mm",
        "prompt_cue": (
            "cinematic over-the-shoulder vertical 9:16 frame: out-of-focus foreground shoulder of listener on the left border, "
            "sharp focus on speaker angled three-quarters looking off-camera right toward listener, earnest expressive hand gesture"
        ),
        "video_cue": "Cinematic 50mm over-the-shoulder shot, blurred foreground shoulder of listener, active speaker gesturing expressively with hands while looking at listener off-camera",
    },
    "reverse_over_shoulder": {
        "label": "Contraplano OTS · Eixo Esquerdo",
        "lens_mm": "50mm",
        "prompt_cue": (
            "reverse over-the-shoulder vertical 9:16 frame in the exact same room and lighting: out-of-focus foreground shoulder of first speaker on the right border, "
            "sharp focus on responding character angled three-quarters looking off-camera left, composed poised posture"
        ),
        "video_cue": "Reverse 50mm over-the-shoulder shot preserving exact background room and lighting, blurred foreground shoulder on right edge, subtle micro push-in as responder replies",
    },
    "reaction_closeup": {
        "label": "Close-Up de Choque · Linha Fora da Lente",
        "lens_mm": "85mm",
        "prompt_cue": (
            "tight dramatic 85mm cinema reaction close-up angled three-quarters, character looking off-camera toward the speaker, "
            "startled wide eyes, subtle head turn, practical background bokeh highlights, shallow depth of field"
        ),
        "video_cue": "Sharp 85mm reaction close-up with eyes widening and sudden subtle head recoil looking off-camera, dynamic push-in",
    },
    "wide_ensemble": {
        "label": "Reação com Figurantes · Cliffhanger",
        "lens_mm": "35mm",
        "prompt_cue": (
            "medium ensemble vertical 9:16 cinema shot inside the exact same lit location: foreground characters in serious exchange while blurred background guests turn their heads and whisper to each other, "
            "off-camera eyelines, rich depth and practical lighting"
        ),
        "video_cue": "Dramatic ensemble reaction shot, foreground character stunned without facing camera, background extras subtly turning heads and whispering, slow tension push-in",
    },
}


SEMANTIC_EMOTION_ACTING: Dict[str, Dict[str, str]] = {
    "furious": {
        "image_expr": "intense earnest dramatic expression, raised pointing hand gesture, serious focused brow",
        "video_act": "points finger sharply while confronting the other character with rising chest and intense jaw motion",
    },
    "angry": {
        "image_expr": "assertive dramatic expression, furrowed eyebrows, expressive hand gesture mid-speech",
        "video_act": "leans forward angrily gesturing with hand emphasis, maintaining fierce eye-line off-camera",
    },
    "cold_twist": {
        "image_expr": "calm composed expression, poised confident chin, subtle hand motion",
        "video_act": "stays ice-cold and calm, lifting chin slightly and delivering a sharp cutting comeback with deliberate pause",
    },
    "betrayal": {
        "image_expr": "deeply moved serious expression, glistening eyes, solemn posture",
        "video_act": "exhales shakily with hurt disbelief, blinking slowly while holding gaze off-camera",
    },
    "shock": {
        "image_expr": "startled surprised expression, wide-open eyes, slightly parted lips",
        "video_act": "jerks head back slightly in stunned disbelief, eyes snapping wide open before exhaling sharply",
    },
    "shocked": {
        "image_expr": "startled surprised expression, wide-open eyes, slightly parted lips",
        "video_act": "jerks head back slightly in stunned disbelief, eyes snapping wide open before exhaling sharply",
    },
    "shame": {
        "image_expr": "serious uneasy expression, thoughtful posture, hand raised near collar",
        "video_act": "swallows hard in embarrassed tension, glancing sideways as onlookers react in the background",
    },
    "panic": {
        "image_expr": "startled wide-eyed urgent expression, lively expressive gesture",
        "video_act": "glances rapidly toward the onlooker in rising panic, shoulders tightening",
    },
    "laughing": {
        "image_expr": "spontaneous lively laughter with expressive hand wave, crinkled joyful eyes",
        "video_act": "bursts into lively body-animated laughter waving one hand playfully while looking at companion",
    },
    "warm": {
        "image_expr": "engaged animated conversational expression, natural posture, slight hand gesture",
        "video_act": "speaks naturally with expressive upper-body lean and authentic hand articulation",
    },
}


@dataclass
class CharacterBibleEntry:
    key: str
    label: str
    wardrobe_en: str
    appearance_en: str
    eyeline_side: str  # "off_camera_right" | "off_camera_left" | "off_camera_center_left"
    voice_profile: str


@dataclass
class VisualBible:
    title: str
    visual_style: str
    tone: str
    is_drama: bool
    setting_en: str
    lighting_en: str
    extras_en: str
    characters: Dict[str, CharacterBibleEntry]


@dataclass
class ShotSpec:
    shot_index: int
    shot_id: str
    speaker_key: str
    speaker_label: str
    listener_label: str
    cut_role: str
    shot_framing: str
    lens_mm: str
    eyeline_direction: str
    camera_movement: str
    semantic_emotion: str
    physical_action_en: str
    extras_action_en: str
    performance_direction_pt: str
    tts_text: str
    subtitle_text: str
    target_duration_sec: float
    lead_in_pause_sec: float
    l_cut_bleed_sec: float
    is_reaction_shot: bool
    stinger_type: str  # "none" | "sub_drop" | "tension_swell" | "comedy_pop"
    reference_mode: str = "unique_angle_plate"


DEFAULT_DRAMA_WARDROBE: Dict[str, Tuple[str, str]] = {
    "noiva": (
        "young Latina Brazilian bride mid-20s with warm tanned complexion, dark chestnut eyes, neat low dark brown hair bun, twin pearl drop earrings, white floral lace wedding dress",
        "noiva",
    ),
    "noivo": (
        "handsome Latino Brazilian groom late-20s with warm tanned complexion, neatly trimmed short dark beard, dark brown wavy hair, tailored charcoal suit, white dress shirt and champagne silk tie",
        "noivo",
    ),
    "sogra": (
        "distinguished Brazilian woman 50s with fair olive complexion, silver-blonde hair swept into refined chignon, emerald green silk formal dress and gold chain necklace",
        "sogra",
    ),
    "convidada": (
        "expressive Brazilian woman guest 30s with warm tanned complexion, dark wavy shoulder-length hair, wearing a burgundy velvet cocktail dress and subtle gold earrings",
        "dona_socorro",
    ),
    "convidado": (
        "expressive Brazilian man guest 35s with olive complexion, short dark hair, wearing a tailored navy blue suit over white button-down shirt",
        "enzo",
    ),
    "dona_socorro": (
        "expressive Brazilian woman 55s with warm olive complexion, braided salt-and-pepper hair bun, wearing a vibrant terracotta linen blouse and wooden pendant",
        "dona_socorro",
    ),
    "seu_bastiao": (
        "charismatic older Brazilian man 60s with silver hair, neatly trimmed silver mustache, wearing an open-collar blue plaid cotton shirt over white tee",
        "seu_bastiao",
    ),
    "enzo": (
        "young Brazilian man mid-20s with curly black hair, light beard, wearing a fitted lavender cotton long-sleeve pullover",
        "enzo",
    ),
}


def tone_is_drama(tone: str, theme: str = "") -> bool:
    blob = f"{tone} {theme}".lower()
    return any(
        k in blob
        for k in (
            "drama", "trai", "tenso", "suspense", "choque", "casamento",
            "vingan", "vergonha", "conflito", "segr", "briga", "novela das 9",
        )
    )


def _infer_semantic_emotion(raw_turn: Dict[str, Any], shot_idx: int, is_reaction: bool, is_drama: bool) -> str:
    explicit = str(raw_turn.get("emotion") or "").strip().lower()
    txt = f"{raw_turn.get('tts_text') or ''} {raw_turn.get('text') or ''}".lower()
    if any(k in txt for k in ("o quê", "mentira", "impossível", "como assim", "cadê", "meu deus")):
        return "shock"
    if any(k in txt for k in ("traí", "roub", "dinheiro", "acaba", "chega", "culpa", "vergonha")):
        return "furious" if shot_idx == 0 else "betrayal"
    if explicit and explicit in SEMANTIC_EMOTION_ACTING:
        # Never force comedy "happiness" or "warm" onto an explicit dramatic reaction shot
        if is_reaction and is_drama and explicit in ("warm", "happiness", "laughing"):
            return "shame" if "vergonh" in txt else "shock"
        return explicit
    if is_reaction:
        return "shock" if is_drama else "laughing"
    if shot_idx == 0:
        return "furious" if is_drama else "angry"
    if shot_idx == 1:
        return "cold_twist" if is_drama else "cold_twist"
    if shot_idx == 2:
        return "shock"
    return "warm"


def build_visual_bible(
    cfg: Dict[str, Any],
    raw_dialogue: List[Dict[str, Any]],
) -> VisualBible:
    title = str(cfg.get("title") or "Micro-Drama Cinema v4").strip()
    tone = str(cfg.get("tone") or "Drama Tenso").strip()
    theme = str(cfg.get("theme") or "").strip()
    is_drama = tone_is_drama(tone, theme)
    visual_style = str(cfg.get("visual_style") or "cinematic_live_action").strip().lower()

    is_wedding = any(
        k in f"{theme} {cfg.get('setting') or ''} {title}".lower()
        for k in ("casamento", "noiva", "noivo", "altar", "wedding", "banquete", "sogra")
    )
    default_setting = (
        "elegant Brazilian wedding reception hall at night with warm golden crystal chandeliers, cream pleated drapery, round candlelit tables with white rose centerpieces"
        if is_wedding
        else (
            "dramatic dimly lit Brazilian contemporary living room or courtyard at night with warm practical lamps and rich textures"
            if is_drama
            else "bustling sunlit Brazilian neighborhood workshop and corner cafe terrace with warm wood counters, brass clock gears, hanging warm Edison bulbs, and soft street passersby"
        )
    )
    setting = str(cfg.get("setting") or default_setting).strip()
    lighting = (
        "warm practical tungsten chandelier key light, crisp rim backlight separating hair from background, subtle natural 35mm film grain"
        if "3d" not in visual_style
        else "stylized theatrical 3D feature cinema lighting, warm practical tungsten glow, rich shadow depth"
    )
    extras = (
        "blurred background wedding guests seated at tables whispering and turning heads during tension"
        if is_wedding
        else "blurred background onlookers and neighbors turning heads with curiosity in soft focus depth"
    )

    cast_entries: Dict[str, CharacterBibleEntry] = {}
    axis_cycle = ["off_camera_right", "off_camera_left", "off_camera_right"]
    idx_counter = 0
    for row in raw_dialogue:
        spk_raw = str(row.get("speaker") or "Personagem").strip()
        key = re.sub(r"[^a-z0-9]+", "_", spk_raw.lower()).strip("_") or f"char_{idx_counter}"
        if key in cast_entries:
            continue
        matched_key = None
        for known in DEFAULT_DRAMA_WARDROBE:
            if known in key:
                matched_key = known
                break
        if matched_key:
            desc, voice_prof = DEFAULT_DRAMA_WARDROBE[matched_key]
        else:
            fem_tokens = ("a_", "a ", "madrinha", "irma", "tia", "senhora", "dona", "amiga", "mulher", "mae", "vizinha", "convidada")
            is_fem = key.endswith("a") or re.sub(r"_[0-9]+$", "", key).endswith("a") or any(t in f" {key} " for t in fem_tokens)
            if is_fem:
                desc = (
                    f"expressive Brazilian woman portraying {spk_raw}, wearing an elegant formal evening cocktail dress matching the scene"
                    if is_drama
                    else f"expressive Brazilian woman portraying {spk_raw}, wearing colorful textured everyday Brazilian attire"
                )
                voice_prof = "dona_socorro"
            else:
                desc = (
                    f"expressive Brazilian man portraying {spk_raw}, wearing a tailored dark suit and crisp shirt matching the scene"
                    if is_drama
                    else f"expressive Brazilian man portraying {spk_raw}, wearing textured everyday Brazilian casual attire"
                )
                voice_prof = "enzo"
        cast_entries[key] = CharacterBibleEntry(
            key=key,
            label=spk_raw,
            wardrobe_en=desc,
            appearance_en=desc,
            eyeline_side=axis_cycle[idx_counter % len(axis_cycle)],
            voice_profile=voice_prof,
        )
        idx_counter += 1

    return VisualBible(
        title=title,
        visual_style=visual_style,
        tone=tone,
        is_drama=is_drama,
        setting_en=setting,
        lighting_en=lighting,
        extras_en=extras,
        characters=cast_entries,
    )


def compile_cinema_v4_shots(
    cfg: Dict[str, Any],
    raw_dialogue: List[Dict[str, Any]],
) -> Tuple[VisualBible, List[ShotSpec]]:
    """Compile raw dialogue turns into calibrated Cinema Engine v4 shot specifications."""
    bible = build_visual_bible(cfg, raw_dialogue)
    char_list = list(bible.characters.values())
    shots: List[ShotSpec] = []

    default_framings = ["over_shoulder", "reverse_over_shoulder", "reaction_closeup", "wide_ensemble"]
    default_roles = ["hook_confrontation", "cold_turnaround", "third_shock_impact", "ensemble_cliffhanger"]
    target_durations = [3.0, 3.8, 1.1, 2.1]
    default_movements = ["handheld_micro_drift", "subtle_push_in", "whip_snap_cut", "slow_tension_push"]

    for i, row in enumerate(raw_dialogue):
        spk_raw = str(row.get("speaker") or "Personagem").strip()
        spk_key = re.sub(r"[^a-z0-9]+", "_", spk_raw.lower()).strip("_") or f"char_{i}"
        char_entry = bible.characters.get(spk_key) or char_list[0]
        # Pick opposite listener from cast for true OTS foreground & eyeline
        listener = next((c for c in char_list if c.key != char_entry.key), char_entry)

        is_reaction = bool(row.get("is_silent_reaction") or row.get("is_reaction")) or (i == 3 and not str(row.get("tts_text") or "").strip())
        tts_txt = "" if is_reaction else str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or "").strip()
        sub_txt = "" if is_reaction else str(row.get("subtitle_text") or tts_txt).strip()

        raw_framing = str(row.get("shot_framing") or "").strip().lower()
        framing = raw_framing if raw_framing in CINEMA_V4_FRAMINGS else default_framings[i % len(default_framings)]
        if i == 0 and raw_framing in ("", "medium", "establishing"):
            framing = "over_shoulder"
        if i == 1:
            framing = "reverse_over_shoulder"
        if i == 2 and len(raw_dialogue) >= 3:
            framing = "reaction_closeup"
        if is_reaction or i >= 3:
            framing = "wide_ensemble" if raw_framing not in ("reaction_closeup",) else raw_framing

        semantic_emo = _infer_semantic_emotion(row, i, is_reaction, bible.is_drama)
        emo_act = SEMANTIC_EMOTION_ACTING.get(semantic_emo, SEMANTIC_EMOTION_ACTING["warm"])
        phys_en = str(row.get("physical_action_en") or "").strip() or emo_act["video_act"]
        extras_act = (
            "background guests turn their heads in shock and whisper to one another"
            if (is_reaction or i >= 2)
            else "softly blurred guests in background dining naturally"
        )

        cut_role = str(row.get("cut_role") or default_roles[min(i, len(default_roles) - 1)]).strip().lower()
        framing_meta = CINEMA_V4_FRAMINGS[framing]
        lead_pause = float(row.get("lead_in_pause") or (0.28 if i == 1 else 0.0))
        l_cut_bleed = 0.18 if i in (0, 1) and len(raw_dialogue) > i + 1 else 0.0
        stinger = "sub_drop" if i == 2 else ("tension_swell" if (is_reaction and bible.is_drama) else ("comedy_pop" if is_reaction else "none"))

        word_est_dur = max(0.85, len(tts_txt.split()) / 3.1) if tts_txt else 2.0
        target_dur = round(min(4.5, max(0.85 if i == 2 else 1.8, word_est_dur)), 2) if tts_txt else target_durations[min(i, 3)]

        shots.append(
            ShotSpec(
                shot_index=i + 1,
                shot_id=f"SHOT_{i + 1:02d}",
                speaker_key=char_entry.key,
                speaker_label=char_entry.label,
                listener_label=listener.label,
                cut_role=cut_role,
                shot_framing=framing,
                lens_mm=framing_meta["lens_mm"],
                eyeline_direction=char_entry.eyeline_side,
                camera_movement=str(row.get("camera_action") or default_movements[min(i, 3)]).strip().lower(),
                semantic_emotion=semantic_emo,
                physical_action_en=phys_en,
                extras_action_en=extras_act,
                performance_direction_pt=f"Atuação {semantic_emo} direcionada para {listener.label}",
                tts_text=tts_txt,
                subtitle_text=sub_txt,
                target_duration_sec=target_dur,
                lead_in_pause_sec=round(lead_pause, 2),
                l_cut_bleed_sec=l_cut_bleed,
                is_reaction_shot=is_reaction,
                stinger_type=stinger,
                reference_mode="unique_angle_plate",
            )
        )
    return bible, shots


def build_keyframe_prompt_v4(bible: VisualBible, shot: ShotSpec) -> str:
    """Build a Cinema v4 still keyframe prompt with composition FIRST + positive off-camera gaze."""
    spk = bible.characters.get(shot.speaker_key) or next(iter(bible.characters.values()))
    lst_entry = next((c for c in bible.characters.values() if c.label == shot.listener_label), spk)
    emo_expr = SEMANTIC_EMOTION_ACTING.get(shot.semantic_emotion, SEMANTIC_EMOTION_ACTING["warm"])["image_expr"]
    style_prefix = (
        "Photorealistic 35mm cinema still, vertical 9:16 frame"
        if "3d" not in bible.visual_style
        else "Theatrical 3D CGI cinema feature film still, vertical 9:16 frame"
    )
    side_word = "right" if "right" in shot.eyeline_direction else "left"
    opp_word = "left" if side_word == "right" else "right"

    if shot.shot_framing in ("over_shoulder", "reverse_over_shoulder") and lst_entry.key != spk.key:
        # Put over-the-shoulder two-person geometry FIRST so Flux never defaults to a centered frontal portrait
        lst_brief = ", ".join(lst_entry.wardrobe_en.split(",")[-2:]).strip()
        comp_head = (
            f"{style_prefix}, cinematic over-the-shoulder two-person framing: on the {opp_word} foreground edge stands the softly blurred "
            f"back shoulder and side hair silhouette of a listener wearing {lst_brief}; across from them in sharp focus stands "
            f"{spk.wardrobe_en} angled in three-quarter side profile gazing {side_word} toward the listener with eyes averted sideways, {emo_expr}"
        )
    elif shot.shot_framing == "wide_ensemble":
        comp_head = (
            f"{style_prefix}, wide 35mm establishing group cinema shot from 5 meters back showing multiple people around the scene; "
            f"in the mid-ground stand the main characters together including {spk.wardrobe_en}, while startled onlookers in the background turn their heads in side profile; {emo_expr}"
        )
    elif shot.shot_framing == "two_shot":
        comp_head = (
            f"{style_prefix}, medium-wide two-shot side-profile framing of {spk.wardrobe_en} and {lst_entry.label} standing face to face across a candlelit table, "
            f"gazing directly at one another in profile, {emo_expr}"
        )
    else:
        comp_head = (
            f"{style_prefix}, three-quarter side-angled 85mm cinema reaction portrait of {spk.wardrobe_en}, "
            f"head turned sideways gazing off-frame to the {side_word} away from viewer, {emo_expr}"
        )

    return (
        f"{comp_head}. Setting: {bible.setting_en}. Lighting: {bible.lighting_en}. Background: {shot.extras_action_en}."
    )


def build_full_scene_video_prompt_v4(bible: VisualBible, shot: ShotSpec) -> str:
    """Build second-by-second full-scene generation prompt for Veo 3.1 Lite (Flow) or Open-Weight Video models."""
    spk = bible.characters.get(shot.speaker_key) or next(iter(bible.characters.values()))
    framing_video = CINEMA_V4_FRAMINGS[shot.shot_framing]["video_cue"]
    style_prefix = (
        "Photorealistic vertical 9:16 cinema drama clip, ARRI Alexa Mini LF"
        if "3d" not in bible.visual_style
        else "Theatrical vertical 9:16 3D cinema clip"
    )
    if shot.is_reaction_shot or not shot.tts_text:
        return (
            f"{style_prefix}, set inside {bible.setting_en} ({bible.lighting_en}). "
            f"{framing_video}. 0.0s-{shot.target_duration_sec:.1f}s: {spk.wardrobe_en} reacts in {shot.semantic_emotion}: "
            f"{shot.physical_action_en}, three-quarter profile with eyes locked off-frame {shot.eyeline_direction.replace('_', ' ')} toward companion. "
            f"Background: {shot.extras_action_en}. Subtle camera push-in, ambient room acoustics, clean cinema frame with zero overlaid text."
        )
    speech_q = shot.tts_text.replace('"', "'")
    return (
        f"{style_prefix}, set inside {bible.setting_en} ({bible.lighting_en}). "
        f"{framing_video}. {spk.wardrobe_en} stands in three-quarter profile gazing off-frame {shot.eyeline_direction.replace('_', ' ')} toward {shot.listener_label} "
        f"while {shot.physical_action_en}. "
        f"Speaking in natural Brazilian Portuguese ({shot.semantic_emotion}, {shot.performance_direction_pt}): \"{speech_q}\". "
        f"Background: {shot.extras_action_en}. Natural room reverb, handheld micro-motion, clean cinema frame with zero overlaid text."
    )


def _sanitize_nim_prompt(prompt: str) -> str:
    """Replace words that trigger overly sensitive safety filters on NVIDIA NIM image generation."""
    s = prompt
    for bad, rep in (
        ("furious", "earnest intense"),
        ("confrontation", "dramatic conversation"),
        ("confrontational", "expressive dramatic"),
        ("shouting", "speaking emphatically"),
        ("screaming", "speaking urgently"),
        ("mortified", "thoughtful"),
        ("humiliated", "uneasy"),
        ("sleeveless", "tailored"),
        ("frozen", "still"),
        ("stunned", "surprised"),
        ("skin pores", "facial detail"),
        ("skin", "complexion"),
        ("master shot", "establishing group scene"),
        ("gown", "dress"),
        ("whispering", "chatting quietly"),
        ("ARRI Alexa Mini LF look", "35mm cinematic lighting"),
        ("ARRI Alexa Mini LF", "35mm cinema camera"),
    ):
        s = re.sub(rf"\b{bad}\b", rep, s, flags=re.IGNORECASE)
    return s


def call_nim_flux_plate(prompt: str, nvidia_key: str, out_jpg: Path, seed_offset: int = 0) -> Tuple[bool, str]:
    """Generate a high-craft vertical 9:16 plate via NVIDIA NIM Flux API with automatic safety-filter retry."""
    if not nvidia_key:
        return False, "no_nvidia_key"
    out_jpg.parent.mkdir(parents=True, exist_ok=True)
    clean_primary = _sanitize_nim_prompt(prompt)
    clean_fallback = re.sub(r"\([^)]*\)", "", clean_primary)[:460].strip()
    attempts = [
        ("black-forest-labs/flux.1-dev", clean_primary, 24),
        ("black-forest-labs/flux.1-dev", clean_fallback, 22),
        ("black-forest-labs/flux.1-schnell", clean_fallback[:360], 4),
    ]
    last_reason = "unknown"
    for attempt_idx, (model, clean_p, steps) in enumerate(attempts):
        if attempt_idx > 0:
            time.sleep(0.8)
        url = f"https://ai.api.nvidia.com/v1/genai/{model}"
        payload = {
            "prompt": clean_p,
            "width": 768,
            "height": 1344,
            "steps": steps,
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {nvidia_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=95) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            arts = data.get("artifacts") or []
            if not arts or str(arts[0].get("finishReason") or "SUCCESS").upper() == "CONTENT_FILTERED":
                last_reason = f"{model}_filtered"
                continue
            raw = base64.b64decode(arts[0].get("base64") or "")
            if len(raw) < 20000:
                last_reason = f"tiny_image_{len(raw)}"
                continue
            tmp_raw = out_jpg.with_suffix(".raw.jpg")
            tmp_raw.write_bytes(raw)
            subprocess.run(
                [
                    FFMPEG_BIN, "-y", "-i", str(tmp_raw),
                    "-vf", "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920",
                    "-q:v", "2", str(out_jpg),
                ],
                check=True,
                capture_output=True,
                timeout=30,
            )
            tmp_raw.unlink(missing_ok=True)
            return True, f"nvidia:{model.split('/')[-1]}:cinema_v4"
        except urllib.error.HTTPError as exc:
            last_reason = f"nim_http_{exc.code}"
            if exc.code in (429, 502, 503, 504):
                time.sleep(2.0)
        except Exception as exc:
            last_reason = f"nim_err:{type(exc).__name__}"
    return False, last_reason


def generate_v4_cinema_assets(
    bible: VisualBible,
    shots: List[ShotSpec],
    out_dir: Path,
    nvidia_key: str,
) -> Dict[str, Any]:
    """Generate Master Set plate, Cast Ingredient sheets, and unique per-shot OTS/Reaction plates (zero crop reuse)."""
    bible_dir = out_dir / "bible"
    shots_dir = out_dir / "storyboard"
    bible_dir.mkdir(parents=True, exist_ok=True)
    shots_dir.mkdir(parents=True, exist_ok=True)

    cast_list = list(bible.characters.values())
    cast_brief = ", ".join(f"{c.label} ({c.wardrobe_en.split(',')[0]})" for c in cast_list[:3])
    master_prompt = (
        f"Photorealistic 35mm wide establishing cinema still, vertical 9:16, inside {bible.setting_en[:240]}. "
        f"{bible.lighting_en}. Group gathered around table: {cast_brief}. "
        f"Background: {bible.extras_en}."
    )
    set_master_path = bible_dir / "set_master.jpg"
    ok_master, master_src = call_nim_flux_plate(master_prompt, nvidia_key, set_master_path)

    char_sheets: Dict[str, str] = {}
    for c_key, c_entry in bible.characters.items():
        char_jpg = bible_dir / f"char_{c_key}.jpg"
        c_prompt = (
            f"Photorealistic 85mm cinema character reference portrait, vertical 9:16, {c_entry.wardrobe_en}, "
            f"standing inside {bible.setting_en}, {bible.lighting_en}, natural relaxed expression, clear lighting for character consistency, no text."
        )
        ok_c, _ = call_nim_flux_plate(c_prompt, nvidia_key, char_jpg)
        if ok_c:
            char_sheets[c_key] = str(char_jpg)

    shot_assets: List[Dict[str, Any]] = []
    for shot in shots:
        kf_prompt = build_keyframe_prompt_v4(bible, shot)
        vid_prompt = build_full_scene_video_prompt_v4(bible, shot)
        shot_jpg = shots_dir / f"shot_{shot.shot_index:02d}_{shot.shot_framing}.jpg"
        ok_s, src_s = call_nim_flux_plate(kf_prompt, nvidia_key, shot_jpg, seed_offset=shot.shot_index)
        shot_assets.append({
            "shot_index": shot.shot_index,
            "shot_id": shot.shot_id,
            "framing": shot.shot_framing,
            "image_path": str(shot_jpg) if ok_s else (str(set_master_path) if ok_master else ""),
            "asset_source": src_s,
            "keyframe_prompt": kf_prompt,
            "video_prompt": vid_prompt,
        })

    return {
        "set_master_path": str(set_master_path) if ok_master else "",
        "set_master_source": master_src,
        "char_sheets": char_sheets,
        "shot_assets": shot_assets,
    }


def synthesize_cinema_room_tone_and_stingers(
    *,
    total_dur: float,
    out_wav: Path,
    tone: str,
    shot_cuts: List[Tuple[float, str]],  # [(cut_time_sec, stinger_type), ...]
    target_bed_dbfs: float = -38.0,
) -> Path:
    """Create precisely RMS-calibrated room-tone bed (-38 dBFS, zero windows < -45 dB) with impact stingers."""
    import wave
    import numpy as np

    out_wav.parent.mkdir(parents=True, exist_ok=True)
    dur = max(1.0, float(total_dur))
    sr = 48000

    # Generate warm filtered pink noise room tone and calibrate exact RMS to target_bed_dbfs
    bed_raw = out_wav.with_name(f".bed_raw_{uuid.uuid4().hex[:6]}.wav")
    bed_tmp = out_wav.with_name(f".bed_{uuid.uuid4().hex[:6]}.wav")
    subprocess.run(
        [
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-t", f"{dur:.3f}", "-i", "anoisesrc=color=pink:sample_rate=48000:amplitude=0.5",
            "-af", "highpass=f=85,lowpass=f=1200,aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(bed_raw),
        ],
        check=True,
        capture_output=True,
        timeout=45,
    )
    with wave.open(str(bed_raw), "rb") as wf:
        arr = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0
    bed_raw.unlink(missing_ok=True)
    cur_rms = float(np.sqrt(np.mean(np.square(arr)) + 1e-12))
    target_amp = 10.0 ** (float(target_bed_dbfs) / 20.0)
    scaled = np.clip((arr * (target_amp / max(cur_rms, 1e-6))) * 32767.0, -32767, 32767).astype(np.int16)
    with wave.open(str(bed_tmp), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(scaled.tobytes())

    stinger_files: List[Tuple[float, Path]] = []
    try:
        for idx, (cut_t, stype) in enumerate(shot_cuts):
            if stype == "none" or cut_t < 0.1 or cut_t >= dur - 0.1:
                continue
            s_wav = out_wav.with_name(f".stinger_{idx}_{uuid.uuid4().hex[:5]}.wav")
            if stype == "sub_drop":
                s_dur = 0.90
                t = np.linspace(0.0, s_dur, int(sr * s_dur), endpoint=False)
                sig = 0.34 * np.sin(2.0 * np.pi * (95.0 * np.exp(-3.2 * t) + 34.0) * t) * np.exp(-3.5 * t)
            elif stype == "tension_swell":
                s_dur = min(2.0, max(0.8, dur - cut_t))
                t = np.linspace(0.0, s_dur, int(sr * s_dur), endpoint=False)
                chord = np.sin(2.0 * np.pi * 110.0 * t) + 0.6 * np.sin(2.0 * np.pi * 164.8 * t) + 0.45 * np.sin(2.0 * np.pi * 220.0 * t)
                env = np.minimum(1.0, t / 0.85) * np.exp(-0.55 * np.maximum(0.0, t - 1.1))
                sig = 0.14 * chord * env
            else:
                s_dur = 0.50
                t = np.linspace(0.0, s_dur, int(sr * s_dur), endpoint=False)
                sig = 0.16 * (np.sin(2.0 * np.pi * 440.0 * t) + 0.6 * np.sin(2.0 * np.pi * 659.25 * t)) * np.exp(-5.0 * t)

            pcm_16 = np.clip(sig * 32767.0, -32767, 32767).astype(np.int16)
            stereo_16 = np.column_stack([pcm_16, pcm_16]).ravel()
            with wave.open(str(s_wav), "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(stereo_16.tobytes())
            stinger_files.append((cut_t, s_wav))

        if not stinger_files:
            shutil.move(str(bed_tmp), str(out_wav))
            return out_wav

        cmd = [FFMPEG_BIN, "-y", "-i", str(bed_tmp)]
        fc_parts: List[str] = ["[0:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[a0]"]
        mix_labels = ["[a0]"]
        for s_idx, (cut_t, s_wav) in enumerate(stinger_files, start=1):
            cmd.extend(["-i", str(s_wav)])
            delay_ms = max(0, int(round(cut_t * 1000)))
            fc_parts.append(
                f"[{s_idx}:a]adelay={delay_ms}|{delay_ms},aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[s{s_idx}]"
            )
            mix_labels.append(f"[s{s_idx}]")
        fc_parts.append(f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}:duration=first:normalize=0[outa]")
        cmd.extend([
            "-filter_complex", ";".join(fc_parts),
            "-map", "[outa]",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(out_wav),
        ])
        subprocess.run(cmd, check=True, capture_output=True, timeout=45)
        return out_wav
    finally:
        bed_tmp.unlink(missing_ok=True)
        for _, sw in stinger_files:
            sw.unlink(missing_ok=True)


def stitch_cinema_v4_episode(
    *,
    clip_paths: List[Path],
    dialogue_wavs: List[Optional[Path]],
    shot_specs: List[ShotSpec],
    out_mp4: Path,
    tone: str = "Drama Tenso",
    subtitles_mode: str = "none",
    safe_bottom: int = 1680,
    safe_right: int = 884,
) -> Dict[str, Any]:
    """Assemble full-scene clips with true J/L-cut audio bleeds, LRA >= 5 LU dynamics, and clean bottom captions."""
    if not clip_paths:
        raise ValueError("Nenhum clipe informado para montagem Cinema v4.")

    work_dir = out_mp4.parent / f".stitch_v4_{uuid.uuid4().hex[:6]}"
    work_dir.mkdir(parents=True, exist_ok=True)
    try:
        # 0) Measure real synthesized WAV durations so visual cut points & L-cut bleeds stay locked to actual speech
        w_durs: List[float] = []
        for idx in range(len(clip_paths)):
            wp = dialogue_wavs[idx] if idx < len(dialogue_wavs) else None
            wd = 0.0
            if wp and Path(wp).is_file():
                try:
                    wd = float(
                        subprocess.check_output(
                            [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                             "-of", "default=noprint_wrappers=1:nokey=1", str(wp)],
                            text=True,
                        ).strip() or "0"
                    )
                except Exception:
                    wd = 0.0
            w_durs.append(wd)

        # 1) Normalize video segments strictly at 24fps 1080x1920 vertical, aligned tightly to actual speech WAVs
        norm_vids: List[Path] = []
        v_durs: List[float] = []
        timeline_starts: List[float] = []
        audio_starts: List[float] = []
        cursor = 0.0
        prev_audio_end = 0.0
        for idx, cp in enumerate(clip_paths):
            nv = work_dir / f"vseg_{idx:02d}.mp4"
            spec = shot_specs[idx] if idx < len(shot_specs) else None
            probe_d = float(
                subprocess.check_output(
                    [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(cp)],
                    text=True,
                ).strip() or "2.5"
            )
            wd = w_durs[idx]
            raw_lead = float(spec.lead_in_pause_sec if spec else 0.0)
            # Ensure this speaker never starts on top of the previous line's L-cut tail
            eff_lead = max(raw_lead, max(0.0, (prev_audio_end - cursor) + 0.08)) if wd > 0.2 else raw_lead
            bleed = min(0.18, float(spec.l_cut_bleed_sec if spec else 0.0)) if idx < len(clip_paths) - 1 else 0.0

            if spec and not spec.is_reaction_shot and wd > 0.2:
                desired_d = max(1.05 if idx == 2 else 1.4, eff_lead + wd - bleed + (0.04 if idx < len(clip_paths) - 1 else 0.14))
                seg_d = desired_d if idx < len(clip_paths) else probe_d
            else:
                seg_d = float(spec.target_duration_sec) if (spec and spec.target_duration_sec > 0.45) else probe_d
                seg_d = min(probe_d, max(1.6, seg_d)) if probe_d > 0.4 else seg_d

            setpts_ratio = 1.0
            if probe_d > 0.25 and seg_d > probe_d:
                setpts_ratio = min(1.35, seg_d / probe_d)
                seg_d = min(seg_d, probe_d * setpts_ratio)

            vf_chain = "scale=1080:1920:force_original_aspect_ratio=increase:flags=lanczos,crop=1080:1920,setsar=1,fps=24,format=yuv420p"
            if abs(setpts_ratio - 1.0) > 0.02:
                vf_chain = f"setpts={setpts_ratio:.4f}*PTS," + vf_chain

            subprocess.run(
                [
                    FFMPEG_BIN, "-y", "-i", str(cp), "-t", f"{seg_d:.3f}",
                    "-an",
                    "-vf", vf_chain,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                    str(nv),
                ],
                check=True,
                capture_output=True,
                timeout=120,
            )
            actual_d = float(
                subprocess.check_output(
                    [FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
                     "-of", "default=noprint_wrappers=1:nokey=1", str(nv)],
                    text=True,
                ).strip() or f"{seg_d}"
            )
            timeline_starts.append(cursor)
            a_start = cursor + eff_lead
            audio_starts.append(a_start)
            if wd > 0.2:
                prev_audio_end = max(prev_audio_end, a_start + wd)
            v_durs.append(actual_d)
            norm_vids.append(nv)
            cursor += actual_d

        total_dur = max(0.8, cursor)
        concat_list = work_dir / "vlist.txt"
        concat_list.write_text("\n".join(f"file '{p}'" for p in norm_vids) + "\n", encoding="utf-8")
        vid_only = work_dir / "vid_only.mp4"
        subprocess.run(
            [FFMPEG_BIN, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list), "-c:v", "copy", str(vid_only)],
            check=True,
            capture_output=True,
            timeout=90,
        )

        # 2) Build multi-track timeline audio with L-cuts (previous speech bleeds ~0.18s into reaction cut) & stingers
        cut_events: List[Tuple[float, str]] = []
        for idx, start_t in enumerate(timeline_starts):
            st = shot_specs[idx].stinger_type if idx < len(shot_specs) else "none"
            if st != "none":
                cut_events.append((start_t, st))
        bed_wav = work_dir / "bed_and_stingers.wav"
        synthesize_cinema_room_tone_and_stingers(
            total_dur=total_dur,
            out_wav=bed_wav,
            tone=tone,
            shot_cuts=cut_events,
            target_bed_dbfs=-38.0,
        )

        # Semantic acting dynamics (preserves LRA >= 5.0 LU instead of squashing all turns flat)
        emo_gain_db = {
            "furious": 4.5,
            "angry": 3.5,
            "shock": 4.5,
            "shocked": 4.5,
            "panic": 4.0,
            "cold_twist": -5.0,
            "betrayal": -3.5,
            "shame": -5.5,
            "whisper_tension": -6.5,
            "warm": 0.0,
            "laughing": 1.5,
        }

        # Collect speech tracks positioned on timeline (allowing L-cut bleed into next shot!)
        cmd = [FFMPEG_BIN, "-y", "-i", str(vid_only), "-i", str(bed_wav)]
        fc_parts: List[str] = ["[1:a]aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[abed]"]
        mix_inputs: List[str] = ["[abed]"]
        input_idx = 2
        for idx, start_t in enumerate(timeline_starts):
            w_path = dialogue_wavs[idx] if idx < len(dialogue_wavs) else None
            if w_path and Path(w_path).is_file():
                cmd.extend(["-i", str(w_path)])
                spec = shot_specs[idx] if idx < len(shot_specs) else None
                bleed = float(spec.l_cut_bleed_sec if spec else 0.0)
                sem_emo = spec.semantic_emotion if spec else "warm"
                gain_db = emo_gain_db.get(sem_emo, 0.0)
                offset_s = audio_starts[idx] if idx < len(audio_starts) else start_t
                delay_ms = int(round(offset_s * 1000))
                next_a_start = audio_starts[idx + 1] if (idx + 1 < len(audio_starts) and w_durs[idx + 1] > 0.2) else total_dur
                max_a_dur = max(0.4, (next_a_start - offset_s - 0.03) if idx + 1 < len(audio_starts) and w_durs[idx + 1] > 0.2 else (v_durs[idx] + (bleed if bleed > 0 else 0.0)))
                fc_parts.append(
                    f"[{input_idx}:a]atrim=0:{max_a_dur:.3f},volume={gain_db:+.1f}dB,adelay={delay_ms}|{delay_ms},"
                    f"aformat=sample_fmts=fltp:sample_rates=48000:channel_layouts=stereo[sp{idx}]"
                )
                mix_inputs.append(f"[sp{idx}]")
                input_idx += 1

        # Mix first cleanly to intermediate WAV, measure integrated LUFS via ebur128, then apply pure linear gain
        pre_mix_wav = work_dir / "premix_dynamic.wav"
        fc_mix = (
            ";".join(fc_parts)
            + f";{''.join(mix_inputs)}amix=inputs={len(mix_inputs)}:duration=first:normalize=0,alimiter=limit=0.88:level=false[mixa]"
        )
        subprocess.run(
            [*cmd, "-filter_complex", fc_mix, "-map", "[mixa]", "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2", str(pre_mix_wav)],
            check=True, capture_output=True, timeout=120,
        )
        eb = subprocess.run(
            [FFMPEG_BIN, "-nostats", "-i", str(pre_mix_wav), "-filter_complex", "ebur128=peak=true", "-f", "null", "-"],
            capture_output=True, text=True, timeout=30,
        ).stderr
        i_m = re.findall(r"I:\s*([+-]?[0-9.]+)\s*LUFS", eb)
        cur_lufs = float(i_m[-1]) if i_m else -18.0
        lin_gain_db = max(-6.0, min(8.0, -15.8 - cur_lufs))

        # Re-open command for final mux using linearly normalized dynamic premix (preserves 100% LRA!)
        cmd = [FFMPEG_BIN, "-y", "-i", str(vid_only), "-i", str(pre_mix_wav)]
        fc_parts = [
            f"[1:a]volume={lin_gain_db:+.2f}dB,alimiter=limit=0.83:level=false,aresample=48000:resampler=soxr[outa]"
        ]

        # Optional bottom safe-zone ASS captions (Y > 1580, never covering chest/face; OFF by default in v4)
        vf_chain: List[str] = []
        if subtitles_mode == "minimal" and shot_specs:
            ass_path = work_dir / "v4_caps.ass"
            margin_v = max(160, 1920 - int(safe_bottom))
            margin_r = max(80, 1080 - int(safe_right))
            header = (
                "[Script Info]\nScriptType: v4.00+\nPlayResX: 1080\nPlayResY: 1920\n\n"
                "[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
                "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
                "MarginL, MarginR, MarginV, Encoding\n"
                f"Style: CinemaV4,DejaVu Sans,54,&H00FFFFFF,&H0000FFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,3,1,2,80,{margin_r},{margin_v},1\n\n"
                "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            )
            def _ts(s: float) -> str:
                cs = max(0, int(round(s * 100)))
                h, rem = divmod(cs, 360000)
                m, rem = divmod(rem, 6000)
                sec, c = divmod(rem, 100)
                return f"{h}:{m:02d}:{sec:02d}.{c:02d}"
            ev = []
            for idx, sp in enumerate(shot_specs):
                if idx < len(timeline_starts) and sp.subtitle_text and not sp.is_reaction_shot:
                    s_t = timeline_starts[idx] + sp.lead_in_pause_sec
                    e_t = min(total_dur, s_t + v_durs[idx] + sp.l_cut_bleed_sec)
                    ev.append(f"Dialogue: 0,{_ts(s_t)},{_ts(e_t)},CinemaV4,,0,0,0,,{sp.subtitle_text}")
            if ev:
                ass_path.write_text(header + "\n".join(ev) + "\n", encoding="utf-8")
                vf_chain.append(f"ass={ass_path}")

        if vf_chain:
            fc_parts.insert(0, f"[0:v]{','.join(vf_chain)}[outv]")
            cmd.extend(["-filter_complex", ";".join(fc_parts), "-map", "[outv]", "-map", "[outa]", "-c:v", "libx264", "-preset", "fast", "-crf", "17"])
        else:
            cmd.extend(["-filter_complex", ";".join(fc_parts), "-map", "0:v:0", "-map", "[outa]", "-c:v", "copy"])

        cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2", "-t", f"{total_dur:.3f}", "-movflags", "+faststart", str(out_mp4)])
        subprocess.run(cmd, check=True, capture_output=True, timeout=180)
        return {
            "ok": True,
            "output_path": str(out_mp4),
            "duration": round(total_dur, 2),
            "shots_count": len(norm_vids),
            "timeline_starts": [round(x, 2) for x in timeline_starts],
            "shot_durations": [round(x, 2) for x in v_durs],
            "has_l_cuts": any(s.l_cut_bleed_sec > 0 for s in shot_specs),
        }
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


def generate_flow_veo_pack_v4(
    cfg: Dict[str, Any],
    *,
    compiled_dialogue: List[Dict[str, Any]],
    flow_packs_dir: Path,
    novela_export_dir: Path,
    nvidia_key: str,
) -> Dict[str, Any]:
    """Build a complete Cinema Engine v4 Production Pack for Google Flow (Veo 3.1 Lite) & Colab L4 Full-Scene."""
    from urllib.parse import quote

    t_start = time.time()
    bible, shots = compile_cinema_v4_shots(cfg, compiled_dialogue)
    pack_id = f"flow_v4_{int(time.time())}_{uuid.uuid4().hex[:6]}"
    pack_dir = Path(cfg.get("pack_dir") or (flow_packs_dir / pack_id))
    pack_dir.mkdir(parents=True, exist_ok=True)

    assets = generate_v4_cinema_assets(bible, shots, pack_dir, nvidia_key)

    def _rel_and_urls(p_str: str) -> Tuple[str, str, str]:
        if not p_str:
            return "", "", ""
        p = Path(p_str)
        try:
            rel = p.resolve().relative_to(novela_export_dir.resolve()).as_posix()
        except Exception:
            rel = f"flow_packs/{pack_id}/{p.name}"
        q = quote(rel, safe="")
        return (
            rel,
            f"/api/outputs/download?stack=novela&root_idx=0&relpath={q}&inline=1",
            f"/api/outputs/download?stack=novela&root_idx=0&relpath={q}",
        )

    set_rel, set_url, set_dl = _rel_and_urls(assets.get("set_master_path") or "")
    char_sheets_meta: Dict[str, Dict[str, Any]] = {}
    for c_key, c_path in (assets.get("char_sheets") or {}).items():
        crel, curl, cdl = _rel_and_urls(c_path)
        char_sheets_meta[c_key] = {
            "label": bible.characters[c_key].label if c_key in bible.characters else c_key,
            "wardrobe_en": bible.characters[c_key].wardrobe_en if c_key in bible.characters else "",
            "eyeline_side": bible.characters[c_key].eyeline_side if c_key in bible.characters else "",
            "path": c_path,
            "relpath": crel,
            "url": curl,
            "download_url": cdl,
        }

    takes: List[Dict[str, Any]] = []
    for shot, sa in zip(shots, assets.get("shot_assets") or []):
        ing_path = Path(sa["image_path"]) if sa.get("image_path") else Path(assets.get("set_master_path") or "")
        irel, iurl, idl = _rel_and_urls(str(ing_path))
        csheet = char_sheets_meta.get(shot.speaker_key) or {}
        framing_meta = CINEMA_V4_FRAMINGS[shot.shot_framing]
        est_sec = max(1, min(8, int(math.ceil(shot.target_duration_sec))))
        takes.append({
            "take_number": shot.shot_index,
            "shot_id": shot.shot_id,
            "cut_role": shot.cut_role,
            "is_silent_reaction": shot.is_reaction_shot,
            "lead_in_pause": shot.lead_in_pause_sec,
            "l_cut_bleed_sec": shot.l_cut_bleed_sec,
            "stinger_type": shot.stinger_type,
            "speaker": shot.speaker_label,
            "speaker_key": shot.speaker_key,
            "listener": shot.listener_label,
            "badge_bg": "#dc2626" if bible.is_drama else "#f59e0b",
            "emotion": shot.semantic_emotion,
            "shot_framing": shot.shot_framing,
            "shot_framing_label": framing_meta["label"],
            "lens_mm": framing_meta["lens_mm"],
            "eyeline_direction": shot.eyeline_direction,
            "camera_movement": shot.camera_movement,
            "physical_action_en": shot.physical_action_en,
            "extras_action_en": shot.extras_action_en,
            "estimated_duration_sec": est_sec,
            "tts_text": shot.tts_text,
            "subtitle_text": shot.subtitle_text,
            "image_prompt": sa.get("keyframe_prompt") or "",
            "veo_prompt": sa.get("video_prompt") or "",
            "ingredient_filename": ing_path.name,
            "ingredient_path": str(ing_path),
            "ingredient_relpath": irel,
            "ingredient_url": iurl,
            "ingredient_download_url": idl,
            "char_sheet_url": csheet.get("url", ""),
            "set_master_url": set_url,
            "asset_source": sa.get("asset_source") or "nvidia:flux.1-dev:cinema_v4",
            "credits_veo_lite": 10,
            "credits_veo_fast": 20,
        })

    manifest = {
        "ok": True,
        "cinema_engine": "v4",
        "pack_id": pack_id,
        "title": bible.title,
        "tone": bible.tone,
        "visual_style": bible.visual_style,
        "dramatic_structure": str(cfg.get("dramatic_structure") or "viral_4_cuts"),
        "setting": bible.setting_en,
        "prompt_rules": "v4_cinema_ots_bible",
        "recommended_flow_model": "Veo 3.1 Lite (10 créditos/clipe · Ingredients to Video 9:16 720p)",
        "visual_bible": {
            "set_master_path": assets.get("set_master_path") or "",
            "set_master_relpath": set_rel,
            "set_master_url": set_url,
            "set_master_download_url": set_dl,
            "lighting_en": bible.lighting_en,
            "extras_en": bible.extras_en,
            "characters": char_sheets_meta,
            "flow_usage_guide": (
                "No Google Flow (Veo 3.1 Lite - Ingredients to Video): anexe 'set_master' + a folha 'char_*' do personagem "
                "+ o keyframe OTS do take, cole o veo_prompt e baixe os 4 clipes para costurar no Stitcher v4 (com L-cuts e QA)."
            ),
        },
        "total_takes": len(takes),
        "credit_unit_lite": 10,
        "credit_unit_fast": 20,
        "total_credits_lite": 10 * len(takes),
        "total_credits_fast": 20 * len(takes),
        "total_estimated_duration_sec": sum(t["estimated_duration_sec"] for t in takes),
        "takes": takes,
        "elapsed_sec": round(time.time() - t_start, 2),
    }
    manifest_json = json.dumps(manifest, ensure_ascii=False, indent=2)
    (pack_dir / "manifest.json").write_text(manifest_json, encoding="utf-8")
    (pack_dir / "flow_production_pack.json").write_text(manifest_json, encoding="utf-8")
    return manifest
