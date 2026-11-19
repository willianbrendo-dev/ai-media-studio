#!/usr/bin/env python3
"""Regional Accent & Voice Cloning Engine (v2.6).

Zero-Idle-RAM audio synthesis engine for authentic Brazilian Northeast ("Nordestino")
micro-series and regional comedy, supporting three engines cleanly:
  1. ``nordestino_raiz`` (Default): Gemini Native Audio TTS (``gemini-2.5-flash-preview-tts``)
     with authentic Sertão prosody director prompts and character voice mapping, converting
     24kHz ``s16le`` PCM output to 48kHz Stereo SoXR (loudnorm ``I=-16``). Transparently falls
     back to ``edge-tts`` + ``apply_nordestino_prosody()`` if Gemini quota fluctuates.
  2. ``f5_tts_clone``: Zero-Shot Voice Cloning via remote Colab GPU ``/tts-clone``
     (``F5-TTS-PT-BR``) using 6s-15s regional reference audio samples in
     ``/opt/ai-media-studio/voices/regional/``.
  3. ``seed_vc_guide``: Speech-to-Speech Voice Conversion via remote Colab GPU
     ``/voice-convert`` (``Seed-VC``) transferring the user's recorded microphone accent
     onto any target character timbre.
"""

from __future__ import annotations

import base64
import io
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
import uuid
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


import asyncio
import hashlib
import sys
import unicodedata

STUDIO_BASE = Path(os.environ.get("STUDIO_BASE_DIR", "/opt/ai-media-studio"))
VOICES_REGIONAL_DIR = STUDIO_BASE / "voices/regional"
VOICES_CACHE_DIR = STUDIO_BASE / "voices/cache"
PROFILES_META_PATH = VOICES_REGIONAL_DIR / "profiles.json"
EDGE_TTS_BIN = os.environ.get(
    "EDGE_TTS_BIN",
    "/opt/ai-media-studio/tools/video-use/.venv/bin/edge-tts",
)
FFMPEG_BIN = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE_BIN = shutil.which("ffprobe") or "ffprobe"
GEMINI_TTS_MODEL = "gemini-3.8-flash-tts"
GEMINI_TTS_MODELS: Tuple[str, ...] = (
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-pro-preview-tts",
)
GEMINI_LIVE_AUDIO_MODELS: Tuple[str, ...] = (
    "models/gemini-3.1-flash-live-preview",
    "models/gemini-2.5-flash-native-audio-latest",
    "models/gemini-3.8-live",
)
_LIVE_SAFE_VOICES = frozenset({"Kore", "Aoede", "Charon", "Fenrir", "Puck", "Zephyr", "Orus", "Leda"})
_LIVE_VOICE_FALLBACK_MAP = {
    "Kore": "Kore",
    "Aoede": "Aoede",
    "Charon": "Charon",
    "Fenrir": "Fenrir",
    "Puck": "Puck",
    "Zephyr": "Zephyr",
    "Orus": "Orus",
    "Leda": "Leda",
    "Sulafat": "Kore",
    "Despina": "Aoede",
    "Laomedeia": "Leda",
    "Achernar": "Fenrir",
    "Algenib": "Charon",
    "Enceladus": "Orus",
    "Umbriel": "Orus",
    "Gacrux": "Kore",
    "Iapetus": "Charon",
    "Rasalgethi": "Fenrir",
    "Schedar": "Orus",
}


def _get_qt():
    hf_dir = str(Path(__file__).resolve().parent)
    if hf_dir not in sys.path:
        sys.path.insert(0, hf_dir)
    try:
        from quota_telemetry import get_tracker
        return get_tracker()
    except Exception:
        return None


def _run_async(coro):
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, coro).result()
    except RuntimeError:
        return asyncio.run(coro)

NORDESTE_FAST_DIRECTOR_PROMPT = (
    "Atue em português brasileiro com sotaque nordestino autêntico do interior (Piauí/Ceará), "
    "ritmo RÁPIDO, vibrante, energético e espontâneo de comédia popular de calçada, "
    "fala ágil sem arrastar sílabas, entonação viva e natural."
)


# ---------------------------------------------------------------------------
# Authentic Character Regional Profiles & Gemini Voice Mappings
# ---------------------------------------------------------------------------
DEFAULT_REGIONAL_PROFILES: Dict[str, Dict[str, Any]] = {
    "dona_socorro": {
        "id": "dona_socorro",
        "label": "Dona Socorro",
        "region": "Interior do Ceará / Piauí",
        "role": "Matriarca dramática, desconfiada e expressiva",
        "gemini_voice": "Kore",
        "gemini_alt_voice": "Aoede",
        "edge_voice": "pt-BR-FranciscaNeural",
        "edge_rate": "+16%",
        "edge_pitch": "+5Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete Dona Socorro, senhora nordestina arretada, direta e afiada, com vogais abertas e reação imediata."
        ),
        "default_sample_text": "Oxente, minino! Tu veio botar boneco logo na minha calçada hoje?!",
    },
    "seu_bastiao": {
        "id": "seu_bastiao",
        "label": "Seu Bastião",
        "role": "Comerciante maduro, debochado e bom de prosa",
        "region": "Sertão do Piauí / Ceará / Pernambuco",
        "gemini_voice": "Charon",
        "gemini_alt_voice": "Fenrir",
        "edge_voice": "pt-BR-AntonioNeural",
        "edge_rate": "+14%",
        "edge_pitch": "-5Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete Seu Bastião, dono de bodega esperto, voz masculina madura, tirada rápida e riso debochado."
        ),
        "default_sample_text": "Hahaha! Deixe de cunversa, cabra leso! Aqui na bodega o negócio é arretado mesmo!",
    },
    "enzo": {
        "id": "enzo",
        "label": "Enzo",
        "role": "Sobrinho jovem confuso entre a capital e o sertão",
        "region": "Urbano / Nordestino Jovem",
        "gemini_voice": "Puck",
        "gemini_alt_voice": "Zephyr",
        "edge_voice": "pt-BR-AntonioNeural",
        "edge_rate": "+14%",
        "edge_pitch": "+6Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete Enzo, sobrinho jovem apressado e meio afobado tentando responder rápido com surpresa."
        ),
        "default_sample_text": "Tia Socorro, calma! Eu só tava pedindo um café sem frescura!",
    },
    "narrador_sertao": {
        "id": "narrador_sertao",
        "label": "Narrador do Sertão",
        "role": "Contador de causos musicais e irônicos",
        "region": "Cariri / Interior Nordestino",
        "gemini_voice": "Sulafat",
        "gemini_alt_voice": "Algenib",
        "edge_voice": "pt-BR-ThalitaMultilingualNeural",
        "edge_rate": "+14%",
        "edge_pitch": "-2Hz",
        "style_instruction": (
            f"{NORDESTE_FAST_DIRECTOR_PROMPT} "
            "Interprete um narrador ágil de causos nordestinos com ritmo dinâmico de repente e entusiasmo."
        ),
        "default_sample_text": "Eita lá! Mais um dia agitado na bodega antes da presepada começar!",
    },
    "noiva": {
        "id": "noiva",
        "label": "Noiva",
        "role": "Protagonista dramática intensa",
        "region": "Urbano / Drama Brasileiro",
        "gemini_voice": "Aoede",
        "gemini_alt_voice": "Kore",
        "edge_voice": "pt-BR-FranciscaNeural",
        "edge_rate": "+4%",
        "edge_pitch": "+1Hz",
        "style_instruction": "Atue em português brasileiro dramático realista, voz emotiva, firme e intensa.",
        "default_sample_text": "Você acha mesmo que eu não vi tudo acontecer bem na minha frente?!",
    },
    "sogra": {
        "id": "sogra",
        "label": "Sogra",
        "role": "Antagonista fria, elegante e manipuladora",
        "region": "Urbano / Drama Brasileiro",
        "gemini_voice": "Kore",
        "gemini_alt_voice": "Leda",
        "edge_voice": "pt-BR-ThalitaMultilingualNeural",
        "edge_rate": "-4%",
        "edge_pitch": "-3Hz",
        "style_instruction": "Atue com frieza elegante e cortante de antagonista dramática brasileira, calma e intimidadora.",
        "default_sample_text": "Calma querida... se você falar agora, quem perde tudo é a sua família.",
    },
    "noivo": {
        "id": "noivo",
        "label": "Noivo",
        "role": "Co-protagonista masculino sob pressão",
        "region": "Urbano / Drama Brasileiro",
        "gemini_voice": "Puck",
        "gemini_alt_voice": "Charon",
        "edge_voice": "pt-BR-AntonioNeural",
        "edge_rate": "+2%",
        "edge_pitch": "-2Hz",
        "style_instruction": "Atue em português brasileiro dramático realista com voz masculina expressiva e tensa.",
        "default_sample_text": "O quê?! Como você descobriu isso?!",
    },
}

SPEAKER_KEY_ALIASES: Dict[str, str] = {
    "dona_socorro": "dona_socorro",
    "dona socorro": "dona_socorro",
    "socorro": "dona_socorro",
    "seu_bastiao": "seu_bastiao",
    "seu bastiao": "seu_bastiao",
    "seu bastião": "seu_bastiao",
    "bastiao": "seu_bastiao",
    "bastião": "seu_bastiao",
    "enzo": "enzo",
    "narrador": "narrador_sertao",
    "narradora": "narrador_sertao",
    "narrador_sertao": "narrador_sertao",
    "noiva": "noiva",
    "esposa": "noiva",
    "noivo": "noivo",
    "marido": "noivo",
    "sogra": "sogra",
    "mae": "sogra",
    "mãe": "sogra",
}

EMOTION_ACTING_CUES_PT: Dict[str, str] = {
    "shocked": "Reação imediata de espanto real, respiração presa e voz firme.",
    "shock": "Choque súbito e impacto emocional, corte seco na voz.",
    "confused": "Tom perplexo e incrédulo de quem acabou de ser surpreendido.",
    "laughing": "Rindo de verdade com tirada rápida, alegre e debochada.",
    "proud": "Tom convencido e seguro, resposta na ponta da língua.",
    "dramatic": "Interpretação dramática de novela das 9, tensão visceral e pausas naturais.",
    "furious": "Raiva contida explodindo em acusação direta, articulação intensa e firme.",
    "angry": "Indignação firme e confrontadora, sem perder a clareza das palavras.",
    "cold_twist": "Frieza calculada e cortante, voz baixa mas letal que vira o jogo.",
    "betrayal": "Voz embargada por traição e decepção profunda, tensão dramática.",
    "panic": "Desespero e pânico urgente, respiração ofegante.",
    "shame": "Constrangimento e humilhação intensa, voz trêmula e baixa.",
    "whisper_tension": "Tom quase sussurrado, ameaçador e carregado de tensão.",
    "crying": "Voz embargada segurando o choro, emoção à flor da pele.",
    "warm": "Tom natural e caloroso de diálogo espontâneo.",
}

_AUDIO_GATE_WHISPER_SINGLETON = None


def _get_audio_gate_whisper():
    """Lazy-load CPU faster-whisper INT8 validator singleton (zero idle RAM until first check)."""
    global _AUDIO_GATE_WHISPER_SINGLETON
    if _AUDIO_GATE_WHISPER_SINGLETON is not None:
        return _AUDIO_GATE_WHISPER_SINGLETON
    try:
        from faster_whisper import WhisperModel
        _AUDIO_GATE_WHISPER_SINGLETON = WhisperModel(
            "base",
            device="cpu",
            compute_type="int8",
            cpu_threads=2,
        )
    except Exception:
        _AUDIO_GATE_WHISPER_SINGLETON = False
    return _AUDIO_GATE_WHISPER_SINGLETON


def _fold_pt_tokens(text: str) -> List[str]:
    norm = unicodedata.normalize("NFKD", (text or "").lower())
    ascii_txt = "".join(ch for ch in norm if not unicodedata.combining(ch))
    return re.findall(r"[a-z0-9]+", ascii_txt)


def _downsample_to_16k_mono(audio_mono, framerate: int):
    import numpy as np
    sr = int(framerate or 48000)
    if sr == 16000:
        return audio_mono.astype(np.float32, copy=False)
    if sr % 16000 == 0:
        factor = sr // 16000
        trim = len(audio_mono) - (len(audio_mono) % factor)
        if trim > 0:
            return audio_mono[:trim].reshape(-1, factor).mean(axis=1).astype(np.float32)
    try:
        from math import gcd
        from scipy.signal import resample_poly
        g = gcd(16000, sr)
        return resample_poly(audio_mono, 16000 // g, sr // g).astype(np.float32)
    except Exception:
        target_len = max(1, int(round(len(audio_mono) * 16000.0 / float(sr))))
        x_old = np.linspace(0.0, 1.0, len(audio_mono), endpoint=False)
        x_new = np.linspace(0.0, 1.0, target_len, endpoint=False)
        return np.interp(x_new, x_old, audio_mono).astype(np.float32)


def validate_generated_speech_wav(
    wav_path: Path | str,
    expected_text: str,
    min_rms_dbfs: float = -32.0,
    min_zcr: float = 0.025,
    min_word_overlap: float = 0.65,
    whisper_model: Any = "auto",
) -> Tuple[bool, str]:
    """Audio Quality Gate v4.0: blocks silence, low-frequency hums, and ASR hallucinations before caching."""
    import array

    try:
        import numpy as np  # type: ignore
    except ImportError:
        np = None

    w_path = Path(wav_path)
    if not w_path.is_file():
        return False, "arquivo WAV ausente"

    try:
        with wave.open(str(w_path), "rb") as wf:
            n_channels = max(1, wf.getnchannels())
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate() or 48000
            raw = wf.readframes(wf.getnframes())
    except Exception as exc:
        return False, f"falha ao ler WAV: {exc}"

    if sampwidth != 2 or not raw:
        return False, "formato PCM WAV inválido ou vazio"

    if np is not None:
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        if n_channels > 1:
            audio = audio.reshape(-1, n_channels).mean(axis=1)
        dur = len(audio) / float(framerate)
        if dur < 0.30:
            return False, f"áudio curto demais ({dur:.2f}s)"
        rms = float(np.sqrt(np.mean(np.square(audio)) + 1e-12))
        rms_dbfs = 20.0 * float(math.log10(rms))
        if rms_dbfs < min_rms_dbfs:
            return False, f"áudio baixo/mudo (RMS={rms_dbfs:.1f} dBFS < {min_rms_dbfs})"
        zcr = float(np.mean(np.abs(np.diff(np.signbit(audio)))))
        if zcr < min_zcr:
            return False, f"zumbido/ruído sem consoantes detectado (ZCR={zcr:.4f} < {min_zcr})"
    else:
        pcm = array.array("h")
        pcm.frombytes(raw)
        if n_channels > 1:
            mono = [
                sum(pcm[i + c] for c in range(n_channels)) / (32768.0 * n_channels)
                for i in range(0, len(pcm) - (len(pcm) % n_channels), n_channels)
            ]
        else:
            mono = [s / 32768.0 for s in pcm]
        dur = len(mono) / float(framerate)
        if dur < 0.30:
            return False, f"áudio curto demais ({dur:.2f}s)"
        rms = math.sqrt((sum(v * v for v in mono) / float(len(mono))) + 1e-12)
        rms_dbfs = 20.0 * math.log10(rms)
        if rms_dbfs < min_rms_dbfs:
            return False, f"áudio baixo/mudo (RMS={rms_dbfs:.1f} dBFS < {min_rms_dbfs})"
        crossings = sum(1 for i in range(1, len(mono)) if (mono[i] < 0.0) != (mono[i - 1] < 0.0))
        zcr = crossings / float(max(1, len(mono) - 1))
        if zcr < min_zcr:
            return False, f"zumbido/ruído sem consoantes detectado (ZCR={zcr:.4f} < {min_zcr})"
        return True, f"audio_gate_ok (RMS={rms_dbfs:.1f}dBFS, ZCR={zcr:.4f}, dur={dur:.2f}s)"

    # Ignore pure single-letter fillers unless total utterance is ultra-short (e.g. "O quê?!")
    raw_exp_tokens = _fold_pt_tokens(expected_text)
    exp_tokens = [w for w in raw_exp_tokens if len(w) >= 2]
    is_short_exclamation = len(raw_exp_tokens) <= 2
    model_obj = _get_audio_gate_whisper() if whisper_model == "auto" else whisper_model
    asr_note = ""
    if model_obj and model_obj is not False and (exp_tokens or is_short_exclamation):
        audio_16k = _downsample_to_16k_mono(audio, framerate)
        try:
            segments, _ = model_obj.transcribe(audio_16k, language="pt", vad_filter=False)
            transcribed = " ".join(getattr(seg, "text", "") for seg in segments).strip()
            got_tokens = _fold_pt_tokens(transcribed)
            if not got_tokens:
                return False, f"ASR detectou 0 palavras faladas (esperado '{expected_text[:40]}')"
            got_set = set(got_tokens)
            # Phonetic tolerance for ultra-short exclamations ("O quê?!" -> Whisper often outputs "Ok!" or "O que?")
            if is_short_exclamation and dur <= 2.2:
                asr_note = f", asr='{transcribed[:30]}'"
            else:
                hits = 0
                for ew in exp_tokens:
                    if ew in got_set or any((len(ew) >= 4 and len(gw) >= 4 and ew[:3] == gw[:3]) for gw in got_set):
                        hits += 1
                overlap = hits / float(len(exp_tokens))
                req_overlap = 0.50 if len(exp_tokens) <= 2 else min_word_overlap
                if overlap < req_overlap:
                    return False, f"ASR divergiu ({overlap:.0%} < {req_overlap:.0%}, lido='{transcribed[:60]}')"
                asr_note = f", overlap={overlap:.0%}"
        except Exception as asr_exc:
            return True, f"PASS_ACOUSTIC_ONLY (RMS={rms_dbfs:.1f}dBFS, ZCR={zcr:.4f}, asr_warn={asr_exc})"

    return True, f"PASS (RMS={rms_dbfs:.1f}dBFS, ZCR={zcr:.4f}{asr_note})"


def normalize_character_id(speaker_raw: str) -> str:
    clean = re.sub(r"\s+", " ", (speaker_raw or "").strip().lower())
    if clean in SPEAKER_KEY_ALIASES:
        return SPEAKER_KEY_ALIASES[clean]
    if "socorro" in clean:
        return "dona_socorro"
    if "bast" in clean:
        return "seu_bastiao"
    if "enzo" in clean:
        return "enzo"
    if "noiva" in clean or "esposa" in clean or "mulher" in clean:
        return "noiva"
    if "sogra" in clean or "mae" in clean or "mãe" in clean or "senhora" in clean:
        return "sogra"
    if "noivo" in clean or "marido" in clean or "pai" in clean or "homem" in clean:
        return "noivo"
    if "narr" in clean:
        return "narrador_sertao"
    return "narrador_sertao"


def sanitize_northeast_slang(text: str) -> str:
    """Enforce authentic Northeast (PI/CE/PE/BA) vocabulary and punchy non-dragged cadence.

    Blocks out-of-region slang ('trem', 'uai', 'sô', 'mano', 'meo', 'daora', 'guri', 'bah', 'tchê')
    and removes vowel-stretching spellings ('Oxêêênte' -> 'Oxente') that cause slow narration.
    """
    txt = (text or "").strip()
    if not txt:
        return ""

    # 1) Block out-of-region slang (Mineirês / Paulistês / Gauchês)
    replacements = [
        (r"\b(?:um|esse|aquele|o)\s+trem\b", lambda m: m.group(0).rsplit(None, 1)[0] + " negócio"),
        (r"\b(?:nessa|nesse|dessa|desse)\s+trem\b", "nesse negócio"),
        (r"\btrem\b", "negócio"),
        (r"\btrens\b", "negócios"),
        (r"\buai\b", "oxe"),
        (r"\bs[oô]\b", "visse"),
        (r"\bmano\b", "rapaz"),
        (r"\bmeo\b", "cabra"),
        (r"\bda\s*hora\b|\bdaora\b", "arretado"),
        (r"\bguri\b", "minino"),
        (r"\bguria\b", "minina"),
        (r"\bbah\b", "vixe"),
        (r"\btch[eê]\b", "oxente"),
    ]
    for pat, rep in replacements:
        txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    # 2) Collapse exaggerated repeated vowels (e.g. Oxêêênte -> Oxente, Viixe -> Vixe)
    # without breaking legitimate Portuguese double vowels like 'caatinga' or 'preencher'
    txt = re.sub(r"\b[oô]+x[eê]+n+t+e+\b", "Oxente", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bv[iî]+x[eê]+\b", "Vixe", txt, flags=re.IGNORECASE)
    txt = re.sub(r"([áéíóúâêîôûà])\1{1,}", r"\1", txt, flags=re.IGNORECASE)
    txt = re.sub(r"([aeiou])\1{2,}", r"\1", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\.{3,}", ",", txt)
    txt = re.sub(r"\s+", " ", txt).strip()
    return txt


def apply_nordestino_prosody(
    text: str,
    character: str = "dona_socorro",
    emotion: str = "warm",
    mode: str = "gemini",
) -> str:
    """Apply authentic Brazilian Northeast prosody without dragging syllables.

    - ``mode='gemini'`` enforces cultural sanitation, crisp colloquial contractions,
      and tight punctuation so Gemini Native TTS speaks fast and punchily.
    - ``mode='phonetic'`` injects subtle open-vowel cues ONLY for neural fallback engines.
    """
    txt = sanitize_northeast_slang(text)
    if not txt:
        return "Oxente!"

    char_id = normalize_character_id(character)
    if char_id != "enzo":
        gerund_rules = [
            (r"\b(procur)ando\b", r"\1ano"),
            (r"\b(bot)ando\b", r"\1ano"),
            (r"\b(olh)ando\b", r"\1ano"),
            (r"\b(fal)ando\b", r"\1ano"),
            (r"\b(chor)ando\b", r"\1ano"),
            (r"\b(esper)ando\b", r"\1ano"),
            (r"\b(faz)endo\b", r"\1eno"),
            (r"\b(diz)endo\b", r"\1eno"),
            (r"\b(v)endo\b", r"\1eno"),
            (r"\b(r)indo\b", r"\1ino"),
            (r"\b(v)indo\b", r"\1ino"),
        ]
        for pat, rep in gerund_rules:
            txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    colloq = [
        (r"\bpara\s+eu\b", "pr'eu" if mode == "gemini" else "pru eu"),
        (r"\bpara\b", "pra"),
        (r"\bestou\b", "tô"),
        (r"\bestá\b", "tá"),
        (r"\bmeu filho\b", "meu fí"),
        (r"\bmenino\b", "minino"),
        (r"\bsenhora\b", "sinhóra"),
        (r"\bconversa\b", "cunversa"),
        (r"\bvocê\b", "ocê"),
    ]
    for pat, rep in colloq:
        txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    # Crisp interjections (NO elongated vowels that drag Gemini speech!)
    txt = re.sub(r"\b[oô]xente\b", "Oxente", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\bvixe\s+maria\b", "Vixe Maria", txt, flags=re.IGNORECASE)
    txt = re.sub(r"\beita\b", "Eita", txt, flags=re.IGNORECASE)

    if mode == "phonetic":
        open_vowels = [
            (r"\bboneco\b", "bonéco"),
            (r"\bpre[aá]\b", "Preá"),
            (r"\bcaf[eé]\b", "café"),
            (r"\bn[aã]o\b", "num"),
        ]
        for pat, rep in open_vowels:
            txt = re.sub(pat, rep, txt, flags=re.IGNORECASE)

    txt = re.sub(r"([!?])\s*,", r"\1", txt)
    txt = re.sub(r"([!?])\1+", r"\1", txt)
    return re.sub(r"\s+", " ", txt).strip()


def _load_secrets_env() -> Dict[str, str]:
    env_map = dict(os.environ)
    # Vault (UI / /api/colab/config) takes precedence over static .env files
    for vf in (
        STUDIO_BASE / "media-control-panel/vault.json",
        STUDIO_BASE / "media-control-panel/data/vault.json",
    ):
        if vf.is_file():
            try:
                data = json.loads(vf.read_text(encoding="utf-8"))
                for k, val in data.items():
                    if isinstance(val, str) and val.strip() and not env_map.get(k):
                        env_map[k] = val.strip()
            except Exception:
                pass
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
                if k and not env_map.get(k):
                    env_map[k] = v.strip().strip("\"'")
    return env_map


def _run_checked(cmd: List[str], timeout: int = 120) -> str:
    proc = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd[:6])}\n{proc.stderr[-800:]}")
    return proc.stdout


def _probe_duration(path: Path) -> float:
    out = _run_checked([
        FFPROBE_BIN, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ], timeout=20).strip()
    try:
        return max(0.25, float(out))
    except ValueError:
        return 1.5


def _pcm_to_48k_stereo_wav(
    raw_pcm_bytes: bytes,
    src_rate: int,
    out_wav: Path,
    lead_pad: float = 0.02,
    tail_pad: float = 0.05,
    codec: str = "s16le",
    src_channels: int = 1,
    comedy_fast: bool = True,
) -> float:
    """Convert raw PCM (or RIFF WAV) stream into agile 48kHz Stereo SoXR WAV."""
    raw_pcm_bytes = raw_pcm_bytes[: len(raw_pcm_bytes) - (len(raw_pcm_bytes) % 2)]
    tmp_pcm = out_wav.with_suffix(".raw.pcm")
    tmp_pcm.write_bytes(raw_pcm_bytes)
    speed_chain = (
        "silenceremove=start_periods=1:start_duration=0.05:start_threshold=-38dB:"
        "stop_periods=-1:stop_duration=0.18:stop_threshold=-36dB,atempo=1.16,"
        if comedy_fast
        else ""
    )
    try:
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "lavfi", "-t", f"{lead_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-f", codec, "-ar", str(src_rate), "-ac", str(max(1, int(src_channels))), "-i", str(tmp_pcm),
            "-f", "lavfi", "-t", f"{tail_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-filter_complex",
            f"[1:a]{speed_chain}highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,"
            "aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo[sp];"
            "[0:a][sp][2:a]concat=n=3:v=0:a=1[outa]",
            "-map", "[outa]",
            "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
            str(out_wav),
        ], timeout=60)
    finally:
        tmp_pcm.unlink(missing_ok=True)
    return _probe_duration(out_wav)


def _normalize_media_to_48k_wav(
    src_audio: Path,
    out_wav: Path,
    lead_pad: float = 0.03,
    tail_pad: float = 0.09,
    pitch_shift_edge_fallback: bool = False,
) -> float:
    af_core = (
        "[1:a]highpass=f=75,acompressor=threshold=-16dB:ratio=2.5:attack=10:release=100,"
        "loudnorm=I=-16:TP=-1.5:LRA=11,"
        "aresample=48000:resampler=soxr,aformat=sample_fmts=s16:channel_layouts=stereo[sp]"
    )
    _run_checked([
        FFMPEG_BIN, "-y",
        "-f", "lavfi", "-t", f"{lead_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-i", str(src_audio),
        "-f", "lavfi", "-t", f"{tail_pad:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-filter_complex", f"{af_core};[0:a][sp][2:a]concat=n=3:v=0:a=1[outa]",
        "-map", "[outa]",
        "-c:a", "pcm_s16le", "-ar", "48000", "-ac", "2",
        str(out_wav),
    ], timeout=60)
    return _probe_duration(out_wav)


# ---------------------------------------------------------------------------
# Regional Voice Profile Bank (`/opt/ai-media-studio/voices/regional/`)
# ---------------------------------------------------------------------------

def ensure_regional_voice_bank() -> Dict[str, Any]:
    """Ensure regional voices directory and profiles.json exist on disk."""
    VOICES_REGIONAL_DIR.mkdir(parents=True, exist_ok=True)
    saved: Dict[str, Any] = {}
    if PROFILES_META_PATH.is_file():
        try:
            saved = json.loads(PROFILES_META_PATH.read_text(encoding="utf-8"))
        except Exception:
            saved = {}

    changed = False
    for cid, defaults in DEFAULT_REGIONAL_PROFILES.items():
        cdir = VOICES_REGIONAL_DIR / cid
        cdir.mkdir(parents=True, exist_ok=True)
        if cid not in saved or not isinstance(saved[cid], dict):
            saved[cid] = dict(defaults)
            changed = True
        else:
            for k, v in defaults.items():
                if k in ("style_instruction", "edge_rate", "edge_pitch", "default_sample_text"):
                    if saved[cid].get(k) != v:
                        saved[cid][k] = v
                        changed = True
                else:
                    saved[cid].setdefault(k, v)
        # Validate reference path still exists
        ref_rel = saved[cid].get("reference_file") or ""
        if ref_rel and not (VOICES_REGIONAL_DIR / ref_rel).is_file():
            saved[cid]["reference_file"] = ""
            saved[cid]["has_custom_reference"] = False
            changed = True

    if changed or not PROFILES_META_PATH.is_file():
        PROFILES_META_PATH.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    return saved


def list_regional_voice_profiles() -> List[Dict[str, Any]]:
    profiles = ensure_regional_voice_bank()
    out: List[Dict[str, Any]] = []
    for cid, p in profiles.items():
        ref_rel = str(p.get("reference_file") or "")
        ref_abs = (VOICES_REGIONAL_DIR / ref_rel) if ref_rel else None
        has_ref = bool(ref_abs and ref_abs.is_file())
        out.append({
            "id": cid,
            "label": p.get("label") or cid,
            "role": p.get("role") or "",
            "region": p.get("region") or "",
            "gemini_voice": p.get("gemini_voice") or "Kore",
            "edge_voice": p.get("edge_voice") or "pt-BR-FranciscaNeural",
            "style_instruction": p.get("style_instruction") or "",
            "default_sample_text": p.get("default_sample_text") or "",
            "has_reference": has_ref,
            "has_custom_reference": bool(p.get("has_custom_reference") and has_ref),
            "reference_file": ref_rel if has_ref else "",
            "reference_path": str(ref_abs) if has_ref else "",
            "reference_text": p.get("reference_text") or p.get("default_sample_text") or "",
            "reference_preview_url": (
                f"/api/voices/regional/sample/{cid}" if has_ref else ""
            ),
        })
    return out


def save_regional_voice_reference(
    character_id: str,
    raw_bytes: bytes,
    orig_filename: str,
    reference_text: str = "",
) -> Dict[str, Any]:
    """Save and normalize an uploaded regional reference voice sample (6s-15s clean WAV) for zero-shot cloning/VC."""
    cid = normalize_character_id(character_id) if character_id not in DEFAULT_REGIONAL_PROFILES else character_id
    profiles = ensure_regional_voice_bank()
    cdir = (VOICES_REGIONAL_DIR / cid).resolve()
    cdir.mkdir(parents=True, exist_ok=True)

    ext = Path(orig_filename or "sample.wav").suffix.lower()
    if ext not in (".wav", ".mp3", ".ogg", ".m4a", ".webm", ".flac"):
        ext = ".wav"
    tmp_in = cdir / f"upload_tmp_{uuid.uuid4().hex[:6]}{ext}"
    out_wav = cdir / f"reference_{cid}.wav"
    tmp_in.write_bytes(raw_bytes)
    try:
        dur_in = _probe_duration(tmp_in)
        if dur_in < 0.8:
            raise ValueError(f"Áudio de referência muito curto ({dur_in:.2f}s); envie entre 4s e 15s.")
        # Normalize to crisp 24kHz mono <= 15s (ideal for F5-TTS-PT-BR & Seed-VC)
        _run_checked([
            FFMPEG_BIN, "-y", "-i", str(tmp_in),
            "-t", "15",
            "-vn", "-af", "highpass=f=70,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=24000:resampler=soxr",
            "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
            str(out_wav),
        ], timeout=45)
    finally:
        tmp_in.unlink(missing_ok=True)

    final_dur = _probe_duration(out_wav)
    profiles[cid]["reference_file"] = f"{cid}/{out_wav.name}"
    profiles[cid]["has_custom_reference"] = True
    if reference_text.strip():
        profiles[cid]["reference_text"] = reference_text.strip()[:300]
    profiles[cid]["reference_duration_sec"] = round(final_dur, 2)
    PROFILES_META_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "character_id": cid,
        "reference_path": str(out_wav),
        "reference_file": f"{cid}/{out_wav.name}",
        "duration_sec": round(final_dur, 2),
        "reference_text": profiles[cid].get("reference_text") or profiles[cid].get("default_sample_text") or "",
        "reference_preview_url": f"/api/voices/regional/sample/{cid}",
    }


def _ensure_character_seed_reference(character_id: str, env_map: Dict[str, str]) -> Tuple[Optional[Path], str]:
    """Ensure a clean target timbre sample exists on disk for F5-TTS / Seed-VC (uses Gemini TTS if absent)."""
    profiles = ensure_regional_voice_bank()
    prof = profiles.get(character_id) or profiles["dona_socorro"]
    ref_rel = prof.get("reference_file") or ""
    if ref_rel and (VOICES_REGIONAL_DIR / ref_rel).is_file():
        return VOICES_REGIONAL_DIR / ref_rel, prof.get("reference_text") or prof.get("default_sample_text") or ""

    seed_wav = VOICES_REGIONAL_DIR / character_id / f"reference_{character_id}_seed.wav"
    if seed_wav.is_file() and seed_wav.stat().st_size > 2000:
        return seed_wav, prof.get("default_sample_text") or ""

    sample_text = prof.get("default_sample_text") or "Ôxente, rapaz! Agora sim tem sotaque nordestino de verdade!"
    gem_key = env_map.get("GEMINI_API_KEY") or env_map.get("GOOGLE_API_KEY") or ""
    if gem_key:
        try:
            pcm_bytes, srate, codec = _call_gemini_native_tts(
                text=sample_text,
                voice_name=prof.get("gemini_voice") or "Kore",
                style_prompt=prof.get("style_instruction") or "",
                emotion="warm",
                api_key=gem_key,
            )
            tmp_48k = seed_wav.with_suffix(".tmp48.wav")
            _pcm_to_48k_stereo_wav(pcm_bytes, srate, tmp_48k, lead_pad=0.04, tail_pad=0.08, codec=codec)
            _run_checked([
                FFMPEG_BIN, "-y", "-i", str(tmp_48k),
                "-ar", "24000", "-ac", "1", "-c:a", "pcm_s16le",
                str(seed_wav),
            ], timeout=30)
            tmp_48k.unlink(missing_ok=True)
            prof["reference_file"] = f"{character_id}/{seed_wav.name}"
            prof["reference_text"] = sample_text
            PROFILES_META_PATH.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), encoding="utf-8")
            return seed_wav, sample_text
        except Exception:
            pass
    return None, sample_text


# ---------------------------------------------------------------------------
# Engine Implementations: Gemini Native TTS / Edge Fallback / F5-TTS / Seed-VC
# ---------------------------------------------------------------------------

_EXHAUSTED_RPD_KEYS: set[Tuple[str, str]] = set()
_LAST_GEMINI_CALL_TS: float = 0.0


def _collect_gemini_keys(env_map: Dict[str, str]) -> List[str]:
    """Collect distinct Gemini API keys configured in env/vault in priority order."""
    out: List[str] = []
    raw_multi = str(env_map.get("GEMINI_API_KEYS") or "").strip()
    if raw_multi:
        for part in re.split(r"[,;\s]+", raw_multi):
            if part and part not in out:
                out.append(part)
    for kname in (
        "GEMINI_API_KEY",
        "GEMINI_API_KEY_2",
        "GEMINI_API_KEY_3",
        "GEMINI_API_KEY_4",
        "GOOGLE_API_KEY",
    ):
        val = str(env_map.get(kname) or "").strip()
        if val and val not in out:
            out.append(val)
    return out


def _extract_audio_from_gemini_resp(data: Dict[str, Any]) -> Tuple[bytes, int, str, int]:
    parts = ((data.get("candidates") or [{}])[0].get("content") or {}).get("parts") or []
    for p in parts:
        inline = p.get("inlineData") or {}
        if inline.get("data"):
            mime = str(inline.get("mimeType") or "audio/L16;codec=pcm;rate=24000")
            rate_m = re.search(r"rate=(\d+)", mime)
            srate = int(rate_m.group(1)) if rate_m else 24000
            raw = base64.b64decode(inline["data"])
            if len(raw) < 1000:
                raise RuntimeError("Gemini TTS retornou áudio vazio")
            if raw[:4] == b"RIFF" or "wav" in mime.lower():
                with wave.open(io.BytesIO(raw), "rb") as wf:
                    pcm = wf.readframes(wf.getnframes())
                    return pcm, int(wf.getframerate() or srate), "s16le", int(wf.getnchannels() or 1)
            return raw, srate, "s16le", 1
    raise RuntimeError("Resposta do Gemini TTS sem inlineData de áudio")


def _post_gemini_tts_payload(
    *,
    payload: Dict[str, Any],
    api_key: str,
    model: str = GEMINI_TTS_MODEL,
) -> Tuple[bytes, int, str, int]:
    global _LAST_GEMINI_CALL_TS
    gap = time.time() - _LAST_GEMINI_CALL_TS
    if gap < 0.45:
        time.sleep(0.45 - gap)
    _LAST_GEMINI_CALL_TS = time.time()
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=55) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return _extract_audio_from_gemini_resp(data)


def _call_gemini_native_tts(
    *,
    text: str,
    voice_name: str,
    style_prompt: str,
    emotion: str,
    api_key: str,
    model: str = GEMINI_TTS_MODEL,
) -> Tuple[bytes, int, str, int]:
    """Call Gemini Native TTS and return (raw_pcm_bytes, sample_rate, ffmpeg_codec, channels)."""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY não configurada")

    emo_cue = EMOTION_ACTING_CUES_PT.get(emotion, EMOTION_ACTING_CUES_PT["warm"])
    director_prompt = (
        f"{style_prompt} {emo_cue} "
        f"Leia exatamente a fala abaixo com cadência rápida, viva e naturalidade nordestina, sem arrastar palavras e sem comentários: "
        f"\"{text}\""
    )
    payload = {
        "contents": [{"parts": [{"text": director_prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {
                        "voiceName": voice_name or "Kore",
                    }
                }
            },
        },
    }
    return _post_gemini_tts_payload(payload=payload, api_key=api_key, model=model)


def _cache_key_for_turn(
    cid: str,
    gem_voice: str,
    emotion: str,
    eff_engine: str,
    processed_text: str,
    drama_pacing: bool = False,
) -> str:
    tag = "v4drama" if drama_pacing else "v29"
    raw = f"{tag}|{cid}|{gem_voice}|{emotion}|{eff_engine}|{processed_text}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_wav_duration_fast(wav_path: Path) -> float:
    import wave

    try:
        with wave.open(str(wav_path), "rb") as wf:
            fr = wf.getframerate() or 48000
            return wf.getnframes() / float(fr)
    except Exception:
        return _probe_duration(wav_path)


def _save_voice_cache(cache_key: str, src_wav: Path) -> None:
    if not cache_key or not src_wav.is_file():
        return
    try:
        VOICES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        dst = VOICES_CACHE_DIR / f"{cache_key}.wav"
        tmp = VOICES_CACHE_DIR / f".{cache_key}.{uuid.uuid4().hex[:6]}.wav.tmp"
        shutil.copyfile(src_wav, tmp)
        tmp.replace(dst)
    except Exception:
        pass


async def _live_audio_ws_exchange(
    *,
    api_key: str,
    model: str,
    live_voice: str,
    system_prompt: str,
    user_prompt: str,
) -> bytes:
    import websockets

    ws_url = (
        "wss://generativelanguage.googleapis.com/ws/"
        f"google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent?key={api_key}"
    )
    async with websockets.connect(ws_url, open_timeout=8, close_timeout=4, max_size=16 * 1024 * 1024) as ws:
        setup_msg = {
            "setup": {
                "model": model,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": live_voice,
                            }
                        }
                    },
                    "temperature": 0.2,
                },
                "systemInstruction": {
                    "parts": [{"text": system_prompt}],
                },
            }
        }
        await ws.send(json.dumps(setup_msg))
        await asyncio.wait_for(ws.recv(), timeout=10)

        turn_msg = {
            "clientContent": {
                "turns": [
                    {
                        "role": "user",
                        "parts": [{"text": user_prompt}],
                    }
                ],
                "turnComplete": True,
            }
        }
        await ws.send(json.dumps(turn_msg))

        pcm = bytearray()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            rem = max(0.5, deadline - time.monotonic())
            raw = await asyncio.wait_for(ws.recv(), timeout=min(12.0, rem))
            msg = json.loads(raw) if isinstance(raw, str) else json.loads(raw.decode("utf-8", errors="ignore"))
            sc = msg.get("serverContent") or {}
            mt = sc.get("modelTurn") or {}
            for part in mt.get("parts") or []:
                inline = part.get("inlineData") or {}
                b64 = inline.get("data")
                if b64:
                    pcm.extend(base64.b64decode(b64))
            if sc.get("turnComplete"):
                break
        return bytes(pcm)


def _call_gemini_live_audio_tts(
    *,
    env_map: Dict[str, str],
    text: str,
    style_inst: str,
    emo_cue: str,
    voice_name: str,
    models: Tuple[str, ...] = GEMINI_LIVE_AUDIO_MODELS,
    drama_pacing: bool = False,
) -> Tuple[bytes, int, str, int, str, str]:
    """Primary unlimited-RPM Gemini Live Audio TTS over BidiGenerateContent WebSocket."""
    keys = _collect_gemini_keys(env_map)
    if not keys:
        raise RuntimeError("Nenhuma GEMINI_API_KEY configurada")
    live_voice = _LIVE_VOICE_FALLBACK_MAP.get((voice_name or "Kore").strip(), "Kore")
    qt = _get_qt()

    tone_desc = (
        "Leia em voz alta com interpretação dramática brasileira realista e carga emocional EXATAMENTE "
        if drama_pacing
        else "Leia em voz alta com sotaque cearense autêntico, ritmo ágil e atuação expressiva EXATAMENTE "
    )
    system_prompt = (
        f"{style_inst} {emo_cue} "
        "Você é um sintetizador de voz (TTS) estritamente literal para dublagem brasileira. "
        f"{tone_desc}o texto entre aspas enviado pelo usuário. "
        "PROIBIDO responder, cumprimentar, confirmar ('claro', 'entendido'), explicar ou adicionar palavras."
    )
    user_prompt = f"\"{text}\""
    word_count = max(1, len(re.findall(r"\w+", text)))
    max_sane_sec = max(7.5, word_count * 1.15 + 3.0)

    last_err: Optional[Exception] = None
    for model in models:
        for key in keys:
            try:
                pcm = _run_async(
                    _live_audio_ws_exchange(
                        api_key=key,
                        model=model,
                        live_voice=live_voice,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                    )
                )
                raw_dur = len(pcm) / 48000.0
                if raw_dur < 0.35:
                    raise RuntimeError(f"{model} retornou áudio curto demais ({raw_dur:.2f}s)")
                if raw_dur > max_sane_sec:
                    raise RuntimeError(
                        f"{model} excedeu duração esperada ({raw_dur:.2f}s > {max_sane_sec:.2f}s para {word_count} palavras)"
                    )
                if qt is not None:
                    qt.record_call(model, ok=True, status_code=200, specialty="voice", live_audio_unlimited=True)
                return pcm, 24000, "s16le", 1, f"{model}|{live_voice}", live_voice
            except Exception as exc:
                last_err = exc
                if qt is not None:
                    qt.record_call(
                        model,
                        ok=False,
                        status_code=500,
                        specialty="voice",
                        cooldown_s=15.0,
                        error=str(exc)[:160],
                    )
    raise RuntimeError(f"Gemini Live Audio falhou: {last_err}")


def _call_gemini_resilient(
    *,
    env_map: Dict[str, str],
    build_payload_fn: Any,
    models: Tuple[str, ...] = GEMINI_TTS_MODELS,
) -> Tuple[bytes, int, str, int, str]:
    """Invoke Gemini REST TTS across Round-Robin pool & rotated keys with QuotaTelemetryTracker."""
    keys = _collect_gemini_keys(env_map)
    if not keys:
        raise RuntimeError("Nenhuma GEMINI_API_KEY configurada")

    qt = _get_qt()
    ordered_models = qt.pick_round_robin_tts(list(models)) if qt is not None else list(models)
    last_err: Optional[Exception] = None

    # Pass 1: zero-wait round-robin over models with RPM/cooldown headroom
    # Pass 2: short wait retry across non-RPD-exhausted models if Pass 1 hit transient limits
    for pass_idx in (0, 1):
        for model in ordered_models:
            if pass_idx == 0 and qt is not None and not qt.is_available(model):
                continue
            for key in keys:
                pair = (key[-6:], model)
                if pair in _EXHAUSTED_RPD_KEYS:
                    continue
                waits = (0.0,) if pass_idx == 0 else (2.5, 5.0)
                for wait_s in waits:
                    if wait_s > 0:
                        time.sleep(wait_s)
                    try:
                        payload = build_payload_fn(model)
                        pcm, sr, codec, ch = _post_gemini_tts_payload(payload=payload, api_key=key, model=model)
                        if qt is not None:
                            qt.record_call(model, ok=True, status_code=200, specialty="voice")
                        return pcm, sr, codec, ch, model
                    except urllib.error.HTTPError as exc:
                        err_body = ""
                        try:
                            err_body = exc.read().decode("utf-8", errors="ignore")
                        except Exception:
                            pass
                        last_err = RuntimeError(f"{model} HTTP {exc.code}: {err_body[:240]}")
                        low_err = err_body.lower()
                        is_rpd = exc.code == 429 and ("perday" in low_err or "requests per day" in low_err)
                        delay_m = re.search(r'"retryDelay"\s*:\s*"(\d+)s"', err_body)
                        cd_sec = 3600.0 if is_rpd else (float(delay_m.group(1)) + 2.0 if delay_m else (65.0 if exc.code == 429 else 0.0))
                        if qt is not None:
                            qt.record_call(
                                model,
                                ok=False,
                                status_code=exc.code,
                                specialty="voice",
                                cooldown_s=cd_sec,
                                error=err_body[:160],
                            )
                        if is_rpd:
                            _EXHAUSTED_RPD_KEYS.add(pair)
                            break
                        if exc.code == 400:
                            break
                        if pass_idx == 0 and exc.code in (429, 500, 502, 503, 504):
                            # Switch immediately to the next Round-Robin TTS model!
                            break
                        if exc.code in (429, 500, 502, 503, 504):
                            continue
                        break
                    except Exception as exc:
                        last_err = exc
                        if qt is not None:
                            qt.record_call(model, ok=False, status_code=500, specialty="voice", error=str(exc)[:160])
    raise RuntimeError(f"Todos os modelos/chaves Gemini TTS falharam: {last_err}")


def synthesize_gemini_multispeaker_dialogue(
    *,
    dialogue: List[Dict[str, Any]],
    out_wav: Path,
    lead_pad: float = 0.02,
    tail_pad: float = 0.05,
) -> Dict[str, Any]:
    """Synthesize a multi-character conversation in ONE Gemini Multi-Speaker Native TTS API call."""
    env_map = _load_secrets_env()
    profiles = ensure_regional_voice_bank()
    speaker_configs: List[Dict[str, Any]] = []
    seen_speakers: set[str] = set()
    script_lines: List[str] = []

    for row in dialogue[:8]:
        spk_raw = str(row.get("speaker") or "Dona Socorro").strip()
        cid = normalize_character_id(spk_raw)
        prof = profiles.get(cid) or profiles["dona_socorro"]
        spk_label = prof.get("label") or spk_raw
        txt = apply_nordestino_prosody(
            str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or ""),
            character=cid,
            emotion=str(row.get("emotion") or "warm"),
            mode="gemini",
        )
        if not txt:
            continue
        if spk_label not in seen_speakers:
            seen_speakers.add(spk_label)
            speaker_configs.append({
                "speaker": spk_label,
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": prof.get("gemini_voice") or "Kore"}},
            })
        script_lines.append(f"{spk_label}: {txt}")

    if not script_lines:
        raise ValueError("Diálogo vazio para síntese multi-speaker")

    prompt = f"{NORDESTE_FAST_DIRECTOR_PROMPT}\nInterpretando em ritmo rápido e cômico:\n" + "\n".join(script_lines)

    def _build_ms_payload(_model: str) -> Dict[str, Any]:
        return {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "multiSpeakerVoiceConfig": {"speakerVoiceConfigs": speaker_configs}
                },
            },
        }

    # Note: 3+ speakers work natively on gemini-3.1-flash-tts-preview
    ms_models = ("gemini-3.1-flash-tts-preview", "gemini-2.5-flash-preview-tts") if len(speaker_configs) <= 2 else ("gemini-3.1-flash-tts-preview",)
    pcm, srate, codec, ch, used_model = _call_gemini_resilient(
        env_map=env_map,
        build_payload_fn=_build_ms_payload,
        models=ms_models,
    )
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    dur = _pcm_to_48k_stereo_wav(
        pcm,
        srate,
        out_wav,
        lead_pad=lead_pad,
        tail_pad=tail_pad,
        codec=codec,
        src_channels=ch,
        comedy_fast=True,
    )
    return {
        "ok": True,
        "voice_engine_used": f"gemini_tts:multi_speaker:{used_model}",
        "gemini_model": used_model,
        "speakers": [s["speaker"] for s in speaker_configs],
        "duration": round(dur, 3),
        "wav_path": str(out_wav),
    }


def _synthesize_edge_regional_fallback(
    *,
    tts_text: str,
    character_id: str,
    emotion: str,
    profile: Dict[str, Any],
    out_wav: Path,
    lead_pad: float,
    tail_pad: float,
) -> float:
    """Apply aggressive phonetics + edge-tts when Gemini TTS is unreachable/rate-limited."""
    phonetic_text = apply_nordestino_prosody(tts_text, character=character_id, emotion=emotion, mode="phonetic")
    raw_mp3 = out_wav.with_suffix(".edge.mp3")
    voice = str(profile.get("edge_voice") or "pt-BR-FranciscaNeural")
    rate = str(profile.get("edge_rate") or "+10%")
    pitch = str(profile.get("edge_pitch") or "+0Hz")
    cmd = [
        EDGE_TTS_BIN,
        "--voice", voice,
        f"--rate={rate}",
        f"--pitch={pitch}",
        "--text", phonetic_text,
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
            time.sleep(0.6 * (attempt + 1))
    else:
        raise RuntimeError(f"edge-tts regional fallback falhou: {last_err}")

    try:
        return _normalize_media_to_48k_wav(raw_mp3, out_wav, lead_pad=lead_pad, tail_pad=tail_pad)
    finally:
        raw_mp3.unlink(missing_ok=True)


def _call_colab_voice_endpoint(
    *,
    endpoint_path: str,
    colab_url: str,
    colab_token: str,
    payload: Dict[str, Any],
    out_wav: Path,
    lead_pad: float,
    tail_pad: float,
) -> float:
    url = f"{colab_url.rstrip('/')}{endpoint_path}"
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if colab_token:
        headers["X-Colab-Token"] = colab_token
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=180) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not data.get("ok") or not data.get("audio_b64"):
        raise RuntimeError(f"Colab {endpoint_path} falhou: {data.get('error') or 'sem audio_b64'}")
    used_eng = str(data.get("engine") or "").lower()
    if "fallback" in used_eng or "copy" in used_eng:
        raise RuntimeError(f"Colab {endpoint_path} retornou fallback silencioso ({used_eng})")
    tmp_raw = out_wav.with_suffix(".colab_raw.wav")
    tmp_raw.write_bytes(base64.b64decode(data["audio_b64"]))
    try:
        return _normalize_media_to_48k_wav(tmp_raw, out_wav, lead_pad=lead_pad, tail_pad=tail_pad)
    finally:
        tmp_raw.unlink(missing_ok=True)


def synthesize_regional_turn(
    *,
    tts_text: str,
    speaker: str,
    emotion: str = "warm",
    accent_engine: str = "nordestino_raiz",
    accent_preset: str = "nordestino_raiz",
    out_wav: Path,
    guide_audio_path: Optional[Path] = None,
    colab_url: str = "",
    colab_token: str = "",
    lead_pad: float = 0.03,
    tail_pad: float = 0.09,
    voice_override: str = "",
    drama_pacing: bool = False,
    performance_direction: str = "",
) -> Dict[str, Any]:
    """Synthesize one dialogue turn in authentic Northeast prosody as 48kHz Stereo SoXR WAV."""
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    env_map = _load_secrets_env()
    profiles = ensure_regional_voice_bank()
    cid = normalize_character_id(speaker)
    prof = profiles.get(cid) or profiles["dona_socorro"]

    eff_engine = (accent_engine or accent_preset or "nordestino_raiz").strip().lower()
    if eff_engine not in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide"):
        eff_engine = "nordestino_raiz"

    eff_colab_url = (colab_url or env_map.get("COLAB_GPU_URL") or "").strip()
    eff_colab_tok = (colab_token or env_map.get("COLAB_GPU_TOKEN") or "").strip()
    processed_text = (
        sanitize_northeast_slang(tts_text)
        if drama_pacing
        else apply_nordestino_prosody(tts_text, character=cid, emotion=emotion, mode="gemini")
    )
    gem_voice = (voice_override or prof.get("gemini_voice") or "Kore").strip()

    # 0) Persistent SHA-256 Voice Cache (<5ms instant hit, ZERO API calls, protected by Acoustic Gate)
    cache_eligible = not (eff_engine == "seed_vc_guide" and guide_audio_path) and not (
        eff_engine == "f5_tts_clone" and eff_colab_url
    )
    cache_key = _cache_key_for_turn(cid, gem_voice, emotion, eff_engine, processed_text, drama_pacing=drama_pacing)
    cache_wav = VOICES_CACHE_DIR / f"{cache_key}.wav"
    if cache_eligible and cache_wav.is_file() and cache_wav.stat().st_size > 8000:
        dur_cached = _read_wav_duration_fast(cache_wav)
        ok_cache, cache_reason = validate_generated_speech_wav(
            cache_wav, expected_text=tts_text, whisper_model=None
        )
        if dur_cached > 0.35 and ok_cache:
            shutil.copyfile(cache_wav, out_wav)
            qt = _get_qt()
            if qt is not None:
                qt.record_cache_hit("gemini-2.5-flash-native-audio-latest", specialty="voice")
            return {
                "ok": True,
                "character_id": cid,
                "accent_engine_requested": eff_engine,
                "voice_engine_used": f"gemini_tts:{gem_voice}:cached",
                "gemini_model": "cache_sha256",
                "cache_hit": True,
                "cache_key": cache_key,
                "processed_text": processed_text,
                "duration": round(dur_cached, 3),
                "wav_path": str(out_wav),
            }
        # Purge corrupted cache file so it can never poison future episodes (e.g. Corte 3 hum bug)
        print(f"[AudioGate v4] Cache corrompido purgado ({cache_wav.name}): {cache_reason}", flush=True)
        cache_wav.unlink(missing_ok=True)

    # 1) Direct / Seed-VC Speech-to-Speech when user recorded a guide clip for this line
    if eff_engine == "seed_vc_guide" and guide_audio_path and Path(guide_audio_path).is_file():
        g_path = Path(guide_audio_path)
        target_wav, _ = _ensure_character_seed_reference(cid, env_map)
        if eff_colab_url and target_wav and target_wav.is_file():
            try:
                dur = _call_colab_voice_endpoint(
                    endpoint_path="/voice-convert",
                    colab_url=eff_colab_url,
                    colab_token=eff_colab_tok,
                    payload={
                        "guide_audio_b64": base64.b64encode(g_path.read_bytes()).decode("ascii"),
                        "target_timbre_b64": base64.b64encode(target_wav.read_bytes()).decode("ascii"),
                        "character_id": cid,
                    },
                    out_wav=out_wav,
                    lead_pad=lead_pad,
                    tail_pad=tail_pad,
                )
                return {
                    "ok": True,
                    "character_id": cid,
                    "accent_engine_requested": eff_engine,
                    "voice_engine_used": "seed_vc_colab",
                    "processed_text": processed_text,
                    "duration": round(dur, 3),
                    "wav_path": str(out_wav),
                }
            except Exception:
                pass
        # Fallback: normalize the user's real regional voice recording cleanly!
        dur = _normalize_media_to_48k_wav(g_path, out_wav, lead_pad=lead_pad, tail_pad=tail_pad)
        return {
            "ok": True,
            "character_id": cid,
            "accent_engine_requested": eff_engine,
            "voice_engine_used": "direct_user_guide_audio",
            "processed_text": processed_text,
            "duration": round(dur, 3),
            "wav_path": str(out_wav),
        }

    # 2) Zero-Shot F5-TTS-PT-BR Cloning via Colab GPU when configured
    if eff_engine == "f5_tts_clone" and eff_colab_url:
        ref_wav, ref_text = _ensure_character_seed_reference(cid, env_map)
        if ref_wav and ref_wav.is_file():
            try:
                dur = _call_colab_voice_endpoint(
                    endpoint_path="/tts-clone",
                    colab_url=eff_colab_url,
                    colab_token=eff_colab_tok,
                    payload={
                        "text": processed_text,
                        "ref_audio_b64": base64.b64encode(ref_wav.read_bytes()).decode("ascii"),
                        "ref_text": ref_text,
                        "character_id": cid,
                    },
                    out_wav=out_wav,
                    lead_pad=lead_pad,
                    tail_pad=tail_pad,
                )
                return {
                    "ok": True,
                    "character_id": cid,
                    "accent_engine_requested": eff_engine,
                    "voice_engine_used": "f5_tts_pt_br_colab",
                    "processed_text": processed_text,
                    "duration": round(dur, 3),
                    "wav_path": str(out_wav),
                }
            except Exception:
                pass

    # 3) Primary Engine (v2.9/v4.0): Gemini Live Audio -> Round-Robin REST Pool, both gated by Audio Quality Gate
    gem_keys = _collect_gemini_keys(env_map)
    if gem_keys:
        emo_cue = EMOTION_ACTING_CUES_PT.get(emotion, EMOTION_ACTING_CUES_PT["warm"])
        if performance_direction:
            emo_cue = f"{emo_cue} {performance_direction}".strip()
        if drama_pacing:
            style_inst = (
                f"Atuação dramática brasileira de micro-novela realista para {speaker}. "
                "Voz natural, intensa e expressiva, com ritmo dramático autêntico (sem pressa de desenho animado)."
            )
        else:
            style_inst = prof.get("style_instruction") or NORDESTE_FAST_DIRECTOR_PROMPT

        # 3A) Primary: Gemini Live Audio WebSocket (BidiGenerateContent, zero RPD/3-RPM bottleneck)
        try:
            pcm_bytes, srate, codec, src_ch, live_model_info, live_voice = _call_gemini_live_audio_tts(
                env_map=env_map,
                text=processed_text,
                style_inst=style_inst,
                emo_cue=emo_cue,
                voice_name=gem_voice,
                models=GEMINI_LIVE_AUDIO_MODELS,
                drama_pacing=drama_pacing,
            )
            dur = _pcm_to_48k_stereo_wav(
                pcm_bytes,
                srate,
                out_wav,
                lead_pad=lead_pad,
                tail_pad=tail_pad,
                codec=codec,
                src_channels=src_ch,
                comedy_fast=not drama_pacing,
            )
            ok_gate, gate_msg = validate_generated_speech_wav(out_wav, expected_text=tts_text, whisper_model="auto")
            if not ok_gate:
                raise RuntimeError(f"AudioGate v4 rejeitou Gemini Live ({gate_msg})")
            _save_voice_cache(cache_key, out_wav)
            return {
                "ok": True,
                "character_id": cid,
                "accent_engine_requested": eff_engine,
                "voice_engine_used": f"gemini_live:{live_voice}",
                "gemini_model": live_model_info.split("|")[0],
                "audio_gate": gate_msg,
                "cache_hit": False,
                "cache_key": cache_key,
                "processed_text": processed_text,
                "duration": round(dur, 3),
                "wav_path": str(out_wav),
            }
        except Exception as live_exc:
            print(f"[RVE LIVE INFO] Gemini Live indisponível/rejeitado para {cid}, acionando Pool REST: {live_exc}", flush=True)

        # 3B) Secondary: Round-Robin REST Pool across 5 Gemini TTS models
        def _build_turn_payload(model_name: str) -> Dict[str, Any]:
            # Prompt-capable 2.5/3.1 models take short acting direction; 3.8 raw TTS models take pure text ONLY!
            if "3.8" in (model_name or ""):
                prompt_text = processed_text
            else:
                cadence = "interpretação dramática intensa" if drama_pacing else "ritmo rápido nordestino"
                prompt_text = (
                    f"{style_inst} {emo_cue} "
                    f"Fale somente o texto entre aspas com {cadence}, sem comentários: "
                    f"\"{processed_text}\""
                )
            return {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "speechConfig": {
                        "voiceConfig": {
                            "prebuiltVoiceConfig": {
                                "voiceName": gem_voice,
                            }
                        }
                    },
                },
            }

        try:
            pcm_bytes, srate, codec, src_ch, used_model = _call_gemini_resilient(
                env_map=env_map,
                build_payload_fn=_build_turn_payload,
                models=GEMINI_TTS_MODELS,
            )
            dur = _pcm_to_48k_stereo_wav(
                pcm_bytes,
                srate,
                out_wav,
                lead_pad=lead_pad,
                tail_pad=tail_pad,
                codec=codec,
                src_channels=src_ch,
                comedy_fast=not drama_pacing,
            )
            ok_gate, gate_msg = validate_generated_speech_wav(out_wav, expected_text=tts_text, whisper_model="auto")
            if not ok_gate:
                raise RuntimeError(f"AudioGate v4 rejeitou Gemini REST ({gate_msg})")
            _save_voice_cache(cache_key, out_wav)
            return {
                "ok": True,
                "character_id": cid,
                "accent_engine_requested": eff_engine,
                "voice_engine_used": f"gemini_tts:{gem_voice}",
                "gemini_model": used_model,
                "audio_gate": gate_msg,
                "cache_hit": False,
                "cache_key": cache_key,
                "processed_text": processed_text,
                "duration": round(dur, 3),
                "wav_path": str(out_wav),
            }
        except Exception as gem_exc:
            # Log failure transparently before falling back ONLY when all Gemini keys/models failed
            print(f"[RVE WARNING] Gemini TTS falhou em todos os modelos para {cid}: {gem_exc}", flush=True)

    # 4) Offline / Unconfigured Key Edge-TTS Phonetic Fallback
    dur = _synthesize_edge_regional_fallback(
        tts_text=tts_text,
        character_id=cid,
        emotion=emotion,
        profile=prof,
        out_wav=out_wav,
        lead_pad=lead_pad,
        tail_pad=tail_pad,
    )
    ok_edge, edge_gate_msg = validate_generated_speech_wav(out_wav, expected_text=tts_text, whisper_model="auto")
    if not ok_edge:
        raise RuntimeError(f"AudioGate v4 rejeitou até o fallback Edge-TTS ({edge_gate_msg})")
    return {
        "ok": True,
        "character_id": cid,
        "accent_engine_requested": eff_engine,
        "voice_engine_used": "edge_tts_regional_fallback",
        "audio_gate": edge_gate_msg,
        "processed_text": apply_nordestino_prosody(tts_text, character=cid, emotion=emotion, mode="phonetic"),
        "duration": round(dur, 3),
        "wav_path": str(out_wav),
    }


def synthesize_dialogue_preview(
    *,
    dialogue: List[Dict[str, Any]],
    accent_engine: str = "nordestino_raiz",
    out_mp3: Path,
    colab_url: str = "",
    colab_token: str = "",
) -> Dict[str, Any]:
    """Synthesize rapid multi-character or single-line MP3 audio preview (48kHz Stereo SoXR)."""
    if not dialogue:
        raise ValueError("Diálogo vazio para preview de voz.")
    out_mp3.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(f"/tmp/reg_preview_{uuid.uuid4().hex[:8]}")
    tmp_dir.mkdir(parents=True, exist_ok=True)
    turn_metas: List[Dict[str, Any]] = []
    wavs: List[Path] = []
    try:
        for idx, row in enumerate(dialogue[:8]):
            txt = str(row.get("tts_text") or row.get("subtitle_text") or row.get("text") or "").strip()
            if not txt:
                continue
            wav_p = tmp_dir / f"prev_{idx:02d}.wav"
            g_raw = str(row.get("guide_audio_path") or "").strip()
            g_path = Path(g_raw) if g_raw and Path(g_raw).is_file() else None
            meta = synthesize_regional_turn(
                tts_text=txt,
                speaker=str(row.get("speaker") or "Dona Socorro"),
                emotion=str(row.get("emotion") or "warm").strip().lower(),
                accent_engine=accent_engine,
                out_wav=wav_p,
                guide_audio_path=g_path,
                colab_url=colab_url,
                colab_token=colab_token,
                lead_pad=0.02,
                tail_pad=0.07,
            )
            wavs.append(wav_p)
            turn_metas.append(meta)

        if not wavs:
            raise ValueError("Nenhuma fala válida para preview.")

        c_txt = tmp_dir / "concat.txt"
        c_txt.write_text("\n".join(f"file '{w}'" for w in wavs) + "\n", encoding="utf-8")
        _run_checked([
            FFMPEG_BIN, "-y",
            "-f", "concat", "-safe", "0", "-i", str(c_txt),
            "-af", "loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000:resampler=soxr",
            "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "48000", "-ac", "2",
            str(out_mp3),
        ], timeout=60)
        total_dur = _probe_duration(out_mp3)
        return {
            "ok": True,
            "audio_path": str(out_mp3),
            "filename": out_mp3.name,
            "duration": round(total_dur, 2),
            "turns_count": len(turn_metas),
            "engines_used": sorted({m["voice_engine_used"] for m in turn_metas}),
            "turns": turn_metas,
        }
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
