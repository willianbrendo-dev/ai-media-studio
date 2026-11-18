#!/usr/bin/env python3
"""Low-CPU Two-Clap Trigger Daemon & Voice Assistant Loop for OpenJarvis.

Monitors microphone transients (< 2% CPU via lightweight NumPy energy/peak
detection over 64 ms blocks), triggers on two sharp claps within 0.2s–0.8s,
plays a short confirmation chime, waits 0.3s, records with adaptive VAD,
transcribes with faster-whisper (tiny/int8/pt), runs OpenJarvis (with MCP
agent-desktop + studio-controller via Gateway http://localhost:9000/v1),
and speaks the reply via Edge-TTS through the active PipeWire/PulseAudio sink.
"""

from __future__ import annotations

import argparse
import collections
import gc
import io
import json
import logging
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Optional

# Enforce 2-core thread pooling on i5-4590T (4 logical threads) before importing NumPy/CTranslate2
os.environ["OMP_NUM_THREADS"] = "2"
os.environ["MKL_NUM_THREADS"] = "2"
os.environ["OPENBLAS_NUM_THREADS"] = "2"
os.environ["VECLIB_MAXIMUM_THREADS"] = "2"
os.environ["NUMEXPR_NUM_THREADS"] = "2"

import numpy as np
import sounddevice as sd

sys.path.insert(0, str(Path(__file__).resolve().parent))


class _FlushingStreamHandler(logging.StreamHandler):
    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        self.flush()


_LOG_FILE = Path.home() / ".openjarvis" / "logs" / "clap_listener.log"
_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
_handler = _FlushingStreamHandler(sys.stderr)
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
_file_handler = logging.FileHandler(_LOG_FILE, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
logging.basicConfig(level=logging.INFO, handlers=[_handler, _file_handler], force=True)
for _noisy in ("httpx", "httpcore", "huggingface_hub"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger("clap_listener")

VAULT_PATH = Path("/home/wbrendo/media-control-panel/vault.json")
OPENJARVIS_ENV_PATH = Path("/home/wbrendo/.openjarvis/.env")
DEFAULT_VPS_PANEL_URL = "https://187.77.202.176.sslip.io"
SAMPLE_RATE = 16000
BLOCK_SIZE = 1024  # 64 ms per frame (~15.6 callbacks/sec -> <1% CPU)
MIN_CLAP_GAP_S = 0.20
MAX_CLAP_GAP_S = 0.80
POST_BEEP_SETTLE_S = 0.30
MAX_RECORD_SECONDS = 8.0
SILENCE_STOP_SECONDS = 1.2
STARTUP_SILENCE_SECONDS = 4.5


def load_gateway_env() -> None:
    """Load ~/.openjarvis/.env first and ensure VPS Cascade Gateway + bearer token are active."""
    if OPENJARVIS_ENV_PATH.exists():
        try:
            for raw in OPENJARVIS_ENV_PATH.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
        except Exception:
            pass

    vps_url = (os.environ.get("VPS_PANEL_URL") or DEFAULT_VPS_PANEL_URL).rstrip("/")
    os.environ.setdefault("OPENAI_BASE_URL", f"{vps_url}/v1")
    os.environ.setdefault("VLLM_HOST", vps_url)

    vps_token = os.environ.get("VPS_PANEL_TOKEN", "").strip()
    if vps_token:
        os.environ["OPENAI_API_KEY"] = vps_token
        os.environ["VLLM_API_KEY"] = vps_token
        return

    if not os.environ.get("OPENAI_API_KEY") and VAULT_PATH.exists():
        try:
            vault = json.loads(VAULT_PATH.read_text(encoding="utf-8"))
            key = (
                str(vault.get("OPENAI_API_KEY", "")).strip()
                or str(vault.get("GEMINI_API_KEY", "")).strip()
                or "local-gateway-vault"
            )
            os.environ["OPENAI_API_KEY"] = key
            os.environ.setdefault("VLLM_API_KEY", key)
        except Exception:
            os.environ.setdefault("OPENAI_API_KEY", "local-gateway-vault")


def _active_pipewire_sink_label() -> str:
    """Return a concise description of the active PipeWire/PulseAudio default sink."""
    if shutil.which("wpctl"):
        try:
            res = subprocess.run(
                ["wpctl", "inspect", "@DEFAULT_AUDIO_SINK@"],
                capture_output=True,
                text=True,
                timeout=2,
                check=False,
            )
            for line in res.stdout.splitlines():
                if "node.description" in line or "node.name" in line:
                    return line.split("=", 1)[-1].strip().strip('"')
        except Exception:
            pass
    return "sink padrão do sistema"


_CHRT_ALLOWED: Optional[bool] = None
_NICE_ALLOWED: Optional[bool] = None


def _run_with_realtime_priority(base_cmd: list[str], player_name: str, timeout: int = 120) -> Optional[str]:
    """Run an audio player command attempting chrt -r 50, nice -n -10, and clean fallback."""
    global _CHRT_ALLOWED, _NICE_ALLOWED

    if shutil.which("chrt") and _CHRT_ALLOWED is not False:
        res = subprocess.run(
            ["chrt", "-r", "50", *base_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
            _CHRT_ALLOWED = True
            return f"chrt -r 50 {player_name}"
        _CHRT_ALLOWED = False

    if shutil.which("nice") and _NICE_ALLOWED is not False:
        res = subprocess.run(
            ["nice", "-n", "-10", *base_cmd],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
            err_low = (res.stderr or "").lower()
            if "permission denied" in err_low or "permissão negada" in err_low:
                _NICE_ALLOWED = False
                return f"{player_name} (RT-buffer 150ms)"
            _NICE_ALLOWED = True
            return f"nice -n -10 {player_name}"
        _NICE_ALLOWED = False

    res = subprocess.run(
        base_cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if res.returncode == 0:
        return f"{player_name} (RT-buffer 150ms)"
    logger.warning("[VOZ] %s retornou código %d: %s", player_name, res.returncode, res.stderr.strip())
    return None


def _ensure_48k_stereo_wav(wav_bytes: bytes) -> bytes:
    """Guarantee WAV buffer is 48,000 Hz Stereo 16-bit PCM using FFmpeg SoXR resampler if needed."""
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
            if wf.getframerate() == 48000 and wf.getnchannels() == 2 and wf.getsampwidth() == 2:
                return wav_bytes
    except Exception:
        pass

    ffmpeg_bin = str(Path.home() / ".local" / "bin" / "ffmpeg")
    if not os.path.isfile(ffmpeg_bin):
        ffmpeg_bin = shutil.which("ffmpeg") or ""
    if ffmpeg_bin:
        in_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with in_tmp:
                in_tmp.write(wav_bytes)
            out_tmp.close()
            res = subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    in_tmp.name,
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-af",
                    "volume=0.9,aresample=resampler=soxr",
                    "-c:a",
                    "pcm_s16le",
                    out_tmp.name,
                ],
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
            if res.returncode == 0 and os.path.getsize(out_tmp.name) > 44:
                return Path(out_tmp.name).read_bytes()
        finally:
            for p in (in_tmp.name, out_tmp.name):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return wav_bytes


def play_wav_bytes(wav_bytes: bytes, timeout: int = 180) -> str:
    """Play WAV bytes (48kHz Stereo SoXR) through the active PipeWire/PulseAudio default sink.

    Returns the player backend used (pw-play, paplay, ffplay, or sounddevice).
    """
    wav_48k = _ensure_48k_stereo_wav(wav_bytes)
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    try:
        with tmp:
            tmp.write(wav_48k)

        if shutil.which("pw-play"):
            used = _run_with_realtime_priority(
                ["pw-play", "--rate=48000", "--channels=2", "--latency=150ms", tmp.name],
                "pw-play",
                timeout=timeout,
            )
            if used:
                return used

        if shutil.which("paplay"):
            used = _run_with_realtime_priority(
                ["paplay", "--latency-msec=150", tmp.name],
                "paplay",
                timeout=timeout,
            )
            if used:
                return used

        if shutil.which("ffplay"):
            res = subprocess.run(
                ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", tmp.name],
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            if res.returncode == 0:
                return "ffplay"

        from openjarvis.speech.voice_io import play_wav

        play_wav(wav_48k)
        return "sounddevice"
    finally:
        try:
            os.unlink(tmp.name)
        except OSError:
            pass


def _to_speech_summary(text: str, max_chars: int = 440) -> str:
    """Convert rich Markdown console output into natural spoken PT-BR within max_chars."""
    import re

    cleaned = re.sub(r"```[\s\S]*?```", " comando disponível na tela. ", text or "")
    cleaned = re.sub(r"`([^`]*)`", r"\1", cleaned)
    cleaned = re.sub(r"!\[[^\]]*\]\([^\)]*\)", "", cleaned)
    cleaned = re.sub(r"https?://\S+", "", cleaned)
    cleaned = re.sub(r"/(?:home|opt|var|tmp)/\S+", "pasta do estúdio", cleaned)
    cleaned = re.sub(r"[#*•─│┌┐└┘📊🎬🖥️❓🚀1️⃣2️⃣3️⃣4️⃣✅]+", " ", cleaned)
    lines = [re.sub(r"\s+", " ", ln).strip(" -:") for ln in cleaned.splitlines()]
    joined = ". ".join(ln for ln in lines if ln)
    joined = re.sub(r"\.\s*\.", ".", joined).strip()
    if len(joined) <= max_chars:
        return joined
    cut = joined[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:")
    return f"{cut}. Os detalhes completos, perguntas e opções de execução estão na tela."


def play_confirmation_chime(sample_rate: int = 48000) -> str:
    """Play a brief dual-tone confirmation chime (48,000 Hz Stereo) via the active desktop audio sink."""
    t1 = np.linspace(0, 0.07, int(sample_rate * 0.07), endpoint=False)
    t2 = np.linspace(0, 0.10, int(sample_rate * 0.10), endpoint=False)
    env1 = np.hanning(len(t1))
    env2 = np.hanning(len(t2))
    tone1 = 0.22 * np.sin(2 * np.pi * 880.0 * t1) * env1
    tone2 = 0.25 * np.sin(2 * np.pi * 1318.5 * t2) * env2
    chime_f32 = np.concatenate([tone1, tone2])
    chime_i16 = np.clip(chime_f32 * 32767.0, -32768, 32767).astype(np.int16)
    chime_stereo = np.column_stack((chime_i16, chime_i16)).ravel()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(chime_stereo.tobytes())
    return play_wav_bytes(buf.getvalue())


def record_command_adaptive_vad(
    *,
    sample_rate: int = SAMPLE_RATE,
    block_size: int = BLOCK_SIZE,
    max_seconds: float = MAX_RECORD_SECONDS,
    silence_seconds: float = SILENCE_STOP_SECONDS,
    startup_silence_seconds: float = STARTUP_SILENCE_SECONDS,
) -> tuple[bytes, bool, float, float]:
    """Record speech using adaptive ambient noise calibration and VAD stop rules.

    Returns ``(wav_bytes, has_speech, duration_s, speech_threshold)``.
    """
    blocks_per_sec = sample_rate / block_size
    calib_blocks = max(3, int(round(0.25 * blocks_per_sec)))  # ~250ms ambient calibration
    silence_limit = max(2, int(round(silence_seconds * blocks_per_sec)))
    startup_limit = max(4, int(round(startup_silence_seconds * blocks_per_sec)))
    min_after_speech_blocks = max(3, int(round(0.8 * blocks_per_sec)))
    max_blocks = max(10, int(round(max_seconds * blocks_per_sec)))

    pre_roll: collections.deque[bytes] = collections.deque(maxlen=4)
    recorded_frames: list[bytes] = []
    ambient_samples: list[float] = []

    noise_rms = 600.0
    speech_threshold = 1200.0
    has_speech = False
    consecutive_speech = 0
    silence_count = 0
    blocks_since_speech = 0

    with sd.RawInputStream(
        samplerate=sample_rate,
        channels=1,
        dtype="int16",
        blocksize=block_size,
    ) as stream:
        for idx in range(max_blocks):
            raw, _ = stream.read(block_size)
            data = bytes(raw)
            arr = np.frombuffer(data, dtype=np.int16).astype(np.float32)
            rms = float(np.sqrt(np.mean(arr * arr))) if arr.size else 0.0

            if idx < calib_blocks:
                ambient_samples.append(rms)
                pre_roll.append(data)
                if idx == calib_blocks - 1:
                    noise_rms = float(np.median(ambient_samples))
                    speech_threshold = max(650.0, min(noise_rms * 1.85, 12000.0))
                continue

            if not has_speech:
                pre_roll.append(data)
                if rms > speech_threshold:
                    consecutive_speech += 1
                    if consecutive_speech >= 2:
                        has_speech = True
                        recorded_frames.extend(pre_roll)
                        silence_count = 0
                        blocks_since_speech = 0
                else:
                    consecutive_speech = 0
                    # Slowly track ambient floor while waiting for user speech.
                    noise_rms = 0.90 * noise_rms + 0.10 * rms
                    speech_threshold = max(650.0, min(noise_rms * 1.85, 12000.0))
                    if idx - calib_blocks >= startup_limit:
                        break
            else:
                recorded_frames.append(data)
                blocks_since_speech += 1
                if rms > speech_threshold * 0.88:
                    silence_count = 0
                else:
                    silence_count += 1
                    if (
                        blocks_since_speech >= min_after_speech_blocks
                        and silence_count >= silence_limit
                    ):
                        break

    pcm_bytes = b"".join(recorded_frames)
    duration_s = len(pcm_bytes) / (2 * sample_rate) if pcm_bytes else 0.0
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue(), has_speech, duration_s, speech_threshold


class TwoClapDetector:
    """Stateful impulse/transient detector for two claps within 0.2s–0.8s."""

    def __init__(
        self,
        *,
        peak_threshold: float = 0.18,
        spike_ratio: float = 4.0,
        min_gap_s: float = MIN_CLAP_GAP_S,
        max_gap_s: float = MAX_CLAP_GAP_S,
        cooldown_s: float = 2.0,
    ) -> None:
        self.peak_threshold = peak_threshold
        self.spike_ratio = spike_ratio
        self.min_gap_s = min_gap_s
        self.max_gap_s = max_gap_s
        self.cooldown_s = cooldown_s

        self._baseline_rms: float = 0.005
        self._first_clap_ts: Optional[float] = None
        self._last_impulse_ts: float = 0.0
        self._cooldown_until: float = 0.0

    def process_block(self, samples_int16: np.ndarray, now: Optional[float] = None) -> bool:
        """Analyze one audio frame (int16) and return True on a valid double clap."""
        ts = time.monotonic() if now is None else now
        if ts < self._cooldown_until:
            return False

        norm = samples_int16.astype(np.float32) * (1.0 / 32768.0)
        peak = float(np.max(np.abs(norm)))
        rms = float(np.sqrt(np.mean(norm * norm)))

        is_impulse = (
            peak >= self.peak_threshold
            and rms >= max(self._baseline_rms * self.spike_ratio, 0.02)
            and (ts - self._last_impulse_ts) >= 0.12
        )

        if not is_impulse:
            self._baseline_rms = 0.94 * self._baseline_rms + 0.06 * max(rms, 0.001)
            if self._first_clap_ts is not None and (ts - self._first_clap_ts) > self.max_gap_s:
                self._first_clap_ts = None
            return False

        self._last_impulse_ts = ts

        if self._first_clap_ts is None:
            self._first_clap_ts = ts
            logger.debug("[PALMAS] Primeira palma detectada (pico=%.3f, rms=%.4f)", peak, rms)
            return False

        gap = ts - self._first_clap_ts
        if self.min_gap_s <= gap <= self.max_gap_s:
            logger.info("[PALMAS] Duas palmas detectadas! (intervalo=%.2fs, pico=%.3f)", gap, peak)
            self._first_clap_ts = None
            self._cooldown_until = ts + self.cooldown_s
            return True

        if gap < self.min_gap_s:
            return False

        self._first_clap_ts = ts
        return False


class JarvisVoiceRuntime:
    """Lazy-cached STT, TTS, and OpenJarvis agent runtime with dynamic intent-based MCP filtering."""

    def __init__(self, voice_id: str = "pt-BR-AntonioNeural", enable_tts: bool = True) -> None:
        self.voice_id = voice_id
        self.enable_tts = enable_tts
        self._config: Any = None
        self._stt_model: Any = None
        self._tts: Any = None
        self._engine: Any = None
        self._model: str = ""
        self._agent: Any = None
        self._mcp_tools: list[Any] = []
        self._mcp_clients: list[Any] = []

    def _ensure_audio(self) -> None:
        from faster_whisper import WhisperModel
        from openjarvis.core.config import load_config
        from openjarvis.speech.edge_tts_backend import EdgeTTSBackend

        if self._config is None:
            self._config = load_config()

        if self._stt_model is None:
            speech = self._config.speech
            model_size = getattr(speech, "model", "tiny") or "tiny"
            device = getattr(speech, "device", "cpu") or "cpu"
            compute_type = getattr(speech, "compute_type", "int8") or "int8"
            logger.info("[OUVIDO] Carregando faster-whisper (%s, %s, %s, cpu_threads=2)...", model_size, device, compute_type)
            try:
                self._stt_model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=2,
                    num_workers=1,
                    local_files_only=True,
                )
            except Exception:
                self._stt_model = WhisperModel(
                    model_size,
                    device=device,
                    compute_type=compute_type,
                    cpu_threads=2,
                    num_workers=1,
                )

        if self._tts is None and self.enable_tts:
            self._tts = EdgeTTSBackend(default_voice=self.voice_id)

    STUDIO_MASTER_SYSTEM_PROMPT = """Você é o OpenJarvis, Operador Master e Diretor Estrategista Audiovisual do AI Media Studio (cliente leve Ubuntu + Gateway em Cascata e Control Panel na VPS em https://187.77.202.176.sslip.io).
Responda sempre em português do Brasil, de forma clara, estruturada e 100% fiel às capacidades reais do estúdio. Nunca invente ferramentas externas (como CapCut, Premiere, OpusClip ou Runway) nem cite arquivos que não tenham sido verificados nas ferramentas.

Domínio completo das 9 capacidades reais do estúdio (regra da VPS 8 GB RAM: máx. 1 stack Docker pesada ligada por vez; motores nativos consomem 0 MB RAM em repouso):
1. HyperFrames (Motor Nativo · 0 MB RAM ociosa): anúncios Motion Graphics HTML/Canvas/GSAP (5s–20s+) e modo Multi-Imagem POV com fotos reais (`/opt/ai-media-studio/campaigns/...`, parâmetros `campaign` / `image_paths`), ofertas Shopee/E-commerce com psicologia de divisão de preço, seta animada para Carrinho Laranja e locução PT-BR 48kHz SoXR (`L'Oréal Gold Luxury`, `Shopee Flash Sale`, `B2B Emerald`, `Cyber Neon`, `Minimal Luxury`).
2. MoneyPrinterTurbo (Docker `moneyprinter` · `/app/moneyprinter/`): vídeos faceless narrados com B-roll automático/local/Pexels/Pixabay, roteiro IA e legendas palavra por palavra (`pop_spring`), ideal para reviews/explainers/vídeos mais longos de produto.
3. SupoClip (Docker `supoclip` · `/app/supoclip/`): cortes de vídeos longos (podcasts, lives, aulas, novelas/séries) nos modos `novela` (`series` com partes sequenciais 1, 2, 3...), `highlights`, `viral`, `topic`, `recap` ou `trailer`.
4. Video Use (Motor Nativo · 0 MB RAM ociosa): pós-produção, limpeza automática de silêncios/hesitações (`faster-whisper` INT8 + FFmpeg), áudio 48kHz Estéreo SoXR, color grading (`Neutral Clean`, `Cinema Warm`, `E-Commerce Vibrant`) e legendas sincronizadas.
5. OpenFilm (Docker `openfilm` · `/app/openfilm/`): editor com timeline multipista, keyframes, motion blur e legendas estilizadas (`reels_viral`, `cinema`, `minimal`).
6. VoiceStudio (Docker `voicestudio` · `/app/voicestudio/`): locuções neurais PT-BR isoladas (Edge-TTS/eSpeak-ng, WAV/MP3 48kHz SoXR).
7. Open Higgsfield (Docker `openhiggsfield` · `/app/higgsfield/`): geração visual e motion AI cinematográfico para compor cenas/B-roll de produto.
8. AgentTube (Docker `agenttube` · `/app/agenttube/`): automação completa de canal do YouTube (roteiro, SEO e pipeline de publicação).
9. OpenSEO (Docker `openseo` · `/app/openseo/`): SEO semântico, títulos, palavras-chave e tags.

Regras obrigatórias de Direção Shopee Video Viral & POV + Protocolo Consultivo & MCP:
1. SHOPEE VIDEO VIRAL & POV DIRECTOR: Para anúncios de Shopee/TikTok Shop/Reels, siga sempre a estrutura vencedora de 15s–18s:
   - `0s–3s` Hook Visual Implacável (Antes vs. Depois real ou dor aguda; nunca comece com logo/saudação).
   - `3s–7s` Close POV & Benefícios Tangíveis (ex.: Controle de Frizz, Redução de Pontas Duplas, Proteção Térmica até 230°C, 1 Semana de Brilho com 6 Óleos Preciosos).
   - `7s–11s` Quebra de Objeção (ex.: "Nova Embalagem, Mesma Fórmula Original L'Oréal", sem pesar nos fios).
   - `11s–16s+` Ancoragem por divisão de preço (ex.: Kit com 2 por R$ 69,99 = R$ 34,99 cada) + badge CTA em `Y: 1110–1165px` ("Toque em Comprar Abaixo com Desconto!") apontando para o Card Branco flutuante da Shopee em `Y = 1180px`.
   - GABARITO SHOPEE SAFE-ZONE (1080×1920): `Y: 0–215px` livre (barra busca); `Y: 220–960px / X: 60–910px` palco de produto e preço; `Y: 985–1100px / X: 60–900px` corredor exclusivo de legendas (máx. 5–6 palavras/bloco, `MarginR 180px`); `Y: 1175–1920px` e `X > 910px` 100% livres (Card Branco, Carrinho Laranja, descrição e ícones).
   - REGRA ANTI-CONFUSÃO DE KITS: Filtre fotos que mostram a linha inteira de 4–6 produtos (shampoo/condicionador/máscara) e prints internos de afiliado quando anunciar um kit específico de 2 óleos!
2. CONSULTORIA & PLANEJAMENTO OBRIGATÓRIOS: Diante de qualquer pedido de criação, edição, corte, anúncio ou planejamento audiovisual (incluindo "qual o melhor caminho"), chame PRIMEIRO `studio_plan_production(goal=..., format_hint=..., inspect_media=True)`.
   - Com os dados reais retornados, apresente SEMPRE pelo nome exato TANTO a Ferramenta Principal (`primary`) QUANTO a Alternativa/Combo complementar (`alternative_or_combo`) de cada trilha retornada (por exemplo: compare `HyperFrames` vs `MoneyPrinterTurbo` / `Open Higgsfield` em vídeos de vendas Shopee; e `SupoClip` modo `novela` + acabamento no `Video Use` em cortes de vídeo longo); cite as mídias reais detectadas no PC/VPS (ou como subir via `scp` / `studio_stage_or_upload_video` / `sample`), faça 2 a 3 perguntas pontuais para fechar parâmetros ausentes e pergunte se prefere execução automática via API/MCP aqui com o Jarvis ou manual pela UI Web (`/app/<stack>/`).
   - NÃO ligue stacks nem dispare jobs pesados sem antes confirmar os parâmetros quando o pedido ainda estiver aberto/consultivo.
2. Só execute `studio_run_*`, `studio_control_stack` ou `studio_trigger_job` diretamente quando o usuário mandar explicitamente executar com parâmetros definidos ou confirmar após suas perguntas.
3. Para abrir sites no Chrome (ex: "abra o painel de afiliados da Shopee no Chrome"), chame `studio_open_url`. Para clicar/ler janelas abertas na tela, use `desktop_*`.
4. Use `webcam_look` SOMENTE quando o usuário pedir explicitamente para olhar pela câmera/webcam."""

    def _ensure_agent(self) -> None:
        import inspect
        import openjarvis.agents  # noqa: F401
        from openjarvis.core.config import load_config
        from openjarvis.core.events import EventBus
        from openjarvis.core.registry import AgentRegistry
        from openjarvis.engine import get_engine
        from openjarvis.intelligence import register_builtin_models
        from openjarvis.mcp.loader import load_mcp_tools_from_config
        from openjarvis.prompt.builder import SystemPromptBuilder

        if self._agent is not None:
            return

        if self._config is None:
            self._config = load_config()

        register_builtin_models()
        pref_engine = self._config.intelligence.preferred_engine or self._config.engine.default
        resolved = get_engine(self._config, pref_engine)
        if resolved is None:
            gw_host = os.environ.get("VLLM_HOST", DEFAULT_VPS_PANEL_URL)
            raise RuntimeError(f"Nenhum motor de inferência disponível (verifique {gw_host}/v1).")
        _, self._engine = resolved
        self._model = self._config.intelligence.default_model or "gemini-2.5-flash"

        mcp_tools, mcp_clients = load_mcp_tools_from_config(
            self._config.tools.mcp,
            config_dir=self._config._config_dir,
        )
        self._mcp_tools = mcp_tools
        self._mcp_clients = mcp_clients

        agent_key = self._config.agent.default_agent or "orchestrator"
        agent_cls = AgentRegistry.get(agent_key)
        bus = EventBus(record_history=False)

        kwargs: dict[str, Any] = {
            "bus": bus,
            "temperature": self._config.intelligence.temperature,
            "max_tokens": self._config.intelligence.max_tokens,
        }
        if getattr(agent_cls, "accepts_tools", False):
            kwargs["tools"] = mcp_tools
            kwargs["max_turns"] = self._config.agent.max_turns
            kwargs["interactive"] = True
            kwargs["confirm_callback"] = lambda _prompt: True
            kwargs["agent_id"] = agent_key

        if "prompt_builder" in inspect.signature(agent_cls.__init__).parameters:
            kwargs["prompt_builder"] = SystemPromptBuilder(
                agent_template=self.STUDIO_MASTER_SYSTEM_PROMPT,
                memory_files_config=self._config.memory_files,
                system_prompt_config=self._config.system_prompt,
                skill_index=[
                    (
                        "studio-controller",
                        "Studio Master Operator: domine as 9 ferramentas (HyperFrames, MoneyPrinterTurbo, SupoClip, Video Use, OpenFilm, VoiceStudio, Open Higgsfield, AgentTube, OpenSEO) e sempre chame studio_plan_production primeiro para pedidos criativos.",
                    )
                ],
            )

        self._agent = agent_cls(self._engine, self._model, **kwargs)
        logger.info(
            "[CEREBRO] OpenJarvis pronto (modelo=%s, agente=%s, pool_mcp=%d, modo=dynamic_intent, gateway=%s/v1).",
            self._model,
            agent_key,
            len(mcp_tools),
            os.environ.get("VLLM_HOST", DEFAULT_VPS_PANEL_URL),
        )

    def select_tools_for_intent(self, query: str) -> tuple[list[str], str]:
        """Select only the subset of MCP tool names matching the user's intent."""
        all_names = [t.spec.name for t in self._mcp_tools]
        low = (query or "").lower()
        selected: list[str] = []
        categories: list[str] = []

        vision_kw = (
            "câmera", "camera", "webcam", "webicão", "webicao",
            "segurando", "mostrando", "olhe", "olha", "vendo",
            "veja o que", "identifique",
        )
        desktop_kw = (
            "chrome", "navegador", "browser", "aba", "guia",
            "janela", "tela", "clica", "clicar", "clique",
            "digita", "digitar", "tecla", "atalho", "desktop",
            "abrir", "abra", "abre", "acessar", "acesse",
        )
        studio_kw = (
            "stack", "container", "contêiner", "conteiner", "docker",
            "estúdio", "estudio", "moneyprinter", "supoclip",
            "openfilm", "voicestudio", "openseo", "agenttube", "higgsfield", "openhiggsfield",
            "hyperframes", "video use", "videouse",
            "cascata", "cascade", "status", "log", "modo dev", "modo-dev",
            "skill", "ligar", "ligue", "liga", "desligar", "desligue",
            "desliga", "iniciar", "inicie", "parar", "pare", "reiniciar",
            "reinicie", "job", "via api", "pela api",
            "vps", "nuvem", "cloud", "servidor", "remoto", "remota",
            "saída", "saidas", "galeria", "download", "baixar",
            "vídeo", "video", "vídeos", "videos", "anúncio", "anuncio",
            "propaganda", "vendas", "produto", "shopee", "oferta", "elseve",
            "kit", "campanha", "foto", "fotos", "imagem", "imagens", "pov",
            "carrinho laranja", "tik tok shop", "tiktok shop",
            "criar", "editar", "edição", "edicao", "produção", "producao",
            "planejar", "estratégia", "estrategia", "melhor caminho", "qual ferramenta",
            "combo", "corte", "cortes", "cortar", "novela", "série", "serie",
            "continuação", "continuacao", "highlights", "virais", "viral",
            "shorts", "reels", "tiktok", "youtube", "canal", "seo", "roteiro",
            "upload", "enviar", "arquivo", "ssh", "scp",
            "locução", "locucao", "narração", "narracao", "voz", "dublagem",
            "áudio", "audio", "silêncio", "silencio", "silêncios", "silencios",
            "cor", "color", "legenda", "legendas", "motion", "animação", "animacao",
            "timeline", "b-roll", "broll",
        )

        if any(k in low for k in vision_kw):
            categories.append("visao_webcam")
            if "webcam_look" in all_names:
                selected.append("webcam_look")

        if any(k in low for k in desktop_kw) or ("afiliad" in low and any(v in low for v in ("abra", "abrir", "abre", "ir ", "vai ", "acesse", "painel"))):
            categories.append("desktop_gui")
            if "studio_open_url" in all_names:
                selected.append("studio_open_url")
            selected.extend(n for n in all_names if n.startswith("desktop_") and n not in selected)

        if any(k in low for k in studio_kw) or ("painel" in low and "afiliad" not in low):
            categories.append("studio_production")
            if "studio_plan_production" in all_names and "studio_plan_production" not in selected:
                selected.append("studio_plan_production")
            selected.extend(
                n for n in all_names
                if n.startswith("studio_") and n != "studio_open_url" and n not in selected
            )

        if not selected:
            return ["__none__"], "conversa_direta"

        return selected, "+".join(categories)

    def transcribe_wav(self, wav_bytes: bytes, language: str = "pt") -> str:
        self._ensure_audio()
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with tmp:
                tmp.write(wav_bytes)
            segments_iter, _info = self._stt_model.transcribe(
                tmp.name,
                language=language,
                beam_size=1,
                vad_filter=True,
                initial_prompt="Jarvis, olhe na câmera, o que estou segurando? Abra uma nova aba no Chrome no painel de afiliados da Shopee. Ligue ou desligue as stacks do estúdio.",
            )
            text = "".join(seg.text for seg in segments_iter).strip()
            return text
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            # Strict lazy-loading: immediately unload Whisper weights from RAM after transcription
            self._stt_model = None
            gc.collect()

    def ask_jarvis(self, query: str) -> tuple[str, list[str]]:
        from openjarvis.agents._stubs import AgentContext

        q_low = (query or "").lower()
        if any(p in q_low for p in ("teste de áudio", "teste de audio", "teste de som", "testar áudio", "testar audio")):
            logger.info("[CEREBRO] Auto-diagnóstico de áudio 48kHz SoXR acionado por voz.")
            return (
                "Teste de áudio concluído com sucesso. A síntese de voz está operando em quarenta e oito mil hertz estéreo com filtro SoXR de alta precisão, sem nenhum chiado.",
                [],
            )

        self._ensure_agent()
        selected_tools, category = self.select_tools_for_intent(query)
        active_count = 0 if selected_tools == ["__none__"] else len(selected_tools)
        logger.info(
            "[CEREBRO] Intent=%s (%d/%d tools carregadas: %s). Enviando ao Gateway VPS...",
            category,
            active_count,
            len(self._mcp_tools),
            ", ".join(selected_tools) if active_count > 0 else "nenhuma",
        )
        result = self._agent.run(query, context=AgentContext(tools=selected_tools))
        reply = (getattr(result, "content", "") or "").strip()
        tool_results = getattr(result, "tool_results", None) or []
        tool_names = [
            getattr(tr, "tool_name", "")
            for tr in tool_results
            if getattr(tr, "tool_name", "")
        ]
        if not reply and tool_results:
            for tr in reversed(tool_results):
                raw_tr = getattr(tr, "content", "") or ""
                try:
                    data_tr = json.loads(raw_tr)
                    if isinstance(data_tr, dict):
                        reply = str(
                            data_tr.get("description")
                            or data_tr.get("message")
                            or data_tr.get("error")
                            or ""
                        ).strip()
                        if reply:
                            break
                except Exception:
                    pass
        return reply or "Comando executado.", tool_names

    def speak_text(self, text: str) -> None:
        if not self.enable_tts or not text.strip():
            return
        spoken = _to_speech_summary(text.strip())
        if not spoken:
            return
        try:
            self._ensure_audio()
            sink_label = _active_pipewire_sink_label()
            logger.info("[VOZ] Sintetizando Edge-TTS (%s) para: %s", self.voice_id, spoken[:140])
            res = self._tts.synthesize(spoken, voice_id=self.voice_id, output_format="wav")
            if res.audio:
                timeout_s = max(90, int(res.duration_seconds) + 30)
                player = play_wav_bytes(res.audio, timeout=timeout_s)
                logger.info("[VOZ] Áudio reproduzido (%.2fs, %s -> %s).", res.duration_seconds, player, sink_label)
        except Exception as exc:
            logger.warning("[VOZ] Aviso ao reproduzir áudio TTS (resposta preservada): %s", exc)

    def handle_voice_turn(self) -> dict[str, Any]:
        beep_player = play_confirmation_chime()
        logger.info("[ESCUTA-ATIVA] Bipe Push-to-Talk emitido via %s. Aguardando %.2fs pós-bipe...", beep_player, POST_BEEP_SETTLE_S)
        time.sleep(POST_BEEP_SETTLE_S)

        logger.info("[OUVIDO] Ouvindo comando de voz em português (VAD adaptativo, máx %.1fs)...", MAX_RECORD_SECONDS)
        wav_bytes, has_speech, duration_s, threshold = record_command_adaptive_vad()

        if not has_speech:
            msg = f"Nenhuma fala detectada no microfone (limiar RMS={threshold:.0f})."
            logger.info("[OUVIDO] %s", msg)
            return {
                "ok": False,
                "has_speech": False,
                "duration_s": round(duration_s, 2),
                "transcript": "",
                "reply": msg,
                "tools_used": [],
            }

        logger.info("[OUVIDO] Captura concluída em %.2fs (limiar=%.0f). Transcrevendo (lazy-load)...", duration_s, threshold)
        if self._config is None:
            from openjarvis.core.config import load_config

            self._config = load_config()
        lang = getattr(self._config.speech, "language", "pt") or "pt"
        query = self.transcribe_wav(wav_bytes, language=lang)
        if not query:
            msg = "Áudio capturado, mas nenhuma frase inteligível foi reconhecida."
            logger.info("[OUVIDO] %s", msg)
            return {
                "ok": False,
                "has_speech": True,
                "duration_s": round(duration_s, 2),
                "transcript": "",
                "reply": msg,
                "tools_used": [],
            }

        logger.info("[OUVIDO] Transcrito (PT): \"%s\"", query)
        reply, tools_used = self.ask_jarvis(query)
        if tools_used:
            logger.info("[CEREBRO] Ferramentas MCP executadas: %s", ", ".join(tools_used))
        logger.info("[CEREBRO] Resposta: \"%s\"", reply)
        self.speak_text(reply)
        self.unload_idle_models()
        return {
            "ok": True,
            "has_speech": True,
            "duration_s": round(duration_s, 2),
            "transcript": query,
            "reply": reply,
            "tools_used": tools_used,
        }

    def handle_text_turn(self, query: str) -> dict[str, Any]:
        clean_q = (query or "").strip()
        if not clean_q:
            return {"ok": False, "transcript": "", "reply": "Comando vazio.", "tools_used": []}
        logger.info("[ESCUTA-ATIVA] Comando de texto recebido: \"%s\"", clean_q)
        reply, tools_used = self.ask_jarvis(clean_q)
        if tools_used:
            logger.info("[CEREBRO] Ferramentas MCP executadas: %s", ", ".join(tools_used))
        logger.info("[CEREBRO] Resposta: \"%s\"", reply)
        self.speak_text(reply)
        self.unload_idle_models()
        return {
            "ok": True,
            "transcript": clean_q,
            "reply": reply,
            "tools_used": tools_used,
        }

    def unload_idle_models(self) -> None:
        """Release heavy STT/TTS/Agent objects from RAM when idle to keep CPU < 1% and RAM minimal."""
        self._stt_model = None
        self._tts = None
        gc.collect()

    def close(self) -> None:
        self._stt_model = None
        self._tts = None
        self._agent = None
        for client in self._mcp_clients:
            try:
                client.close()
            except Exception:
                pass
        self._mcp_clients.clear()
        self._mcp_tools.clear()
        if self._engine is not None and hasattr(self._engine, "close"):
            try:
                self._engine.close()
            except Exception:
                pass
        self._engine = None
        gc.collect()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="OpenJarvis Push-to-Talk & Active Command Runner (Lazy-Loading, Zero Passive Mic Polling)"
    )
    parser.add_argument("--ptt", action="store_true", help="Executa 1 captura de voz sob demanda (Push-to-Talk) e encerra")
    parser.add_argument("--ask-text", default=None, help="Envia um comando de texto ativo ao Jarvis (com resposta em voz opcional)")
    parser.add_argument("--interactive", action="store_true", help="Inicia terminal interativo Push-to-Talk (ENTER para falar ou digite)")
    parser.add_argument("--json", action="store_true", help="Imprime saída estruturada em JSON no stdout")
    parser.add_argument("--voice", default="pt-BR-AntonioNeural", help="Voz Edge-TTS PT-BR")
    parser.add_argument("--no-tts", action="store_true", help="Desativa reprodução TTS")
    parser.add_argument("--once", action="store_true", help="Alias para --ptt (executa 1 escuta ativa e encerra)")
    parser.add_argument("--test-beep", action="store_true", help="Toca o som de confirmação 48kHz e sai")
    parser.add_argument("--test-audio", action="store_true", help="Executa auto-diagnóstico de síntese 48kHz Estéreo SoXR e reproduz frase de teste")
    args = parser.parse_args()

    load_gateway_env()

    if args.test_beep:
        player = play_confirmation_chime()
        print(f"Beep de confirmação executado via {player} ({_active_pipewire_sink_label()}).")
        return 0

    if args.test_audio:
        from openjarvis.speech.edge_tts_backend import EdgeTTSBackend

        phrase = "Jarvis, teste de áudio concluído. A voz está operando em quarenta e oito mil hertz estéreo com filtro SoXR de alta qualidade, sem chiado."
        tts = EdgeTTSBackend(default_voice=args.voice)
        res = tts.synthesize(phrase, voice_id=args.voice, output_format="wav")
        with wave.open(io.BytesIO(res.audio), "rb") as wf:
            ch = wf.getnchannels()
            sw = wf.getsampwidth() * 8
            sr = wf.getframerate()
            frames = wf.getnframes()
        player = play_wav_bytes(res.audio)
        logger.info("[VOZ] Auto-diagnóstico 48kHz Estéreo SoXR reproduzido via %s (%s).", player, _active_pipewire_sink_label())
        print(
            json.dumps(
                {
                    "ok": True,
                    "voice": args.voice,
                    "sample_rate_hz": sr,
                    "channels": ch,
                    "bit_depth": sw,
                    "frames": frames,
                    "duration_s": round(res.duration_seconds, 2),
                    "resampler": res.metadata.get("resampler", "soxr"),
                    "player": player,
                    "sink": _active_pipewire_sink_label(),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    runtime = JarvisVoiceRuntime(voice_id=args.voice, enable_tts=not args.no_tts)
    try:
        if args.ask_text is not None:
            out = runtime.handle_text_turn(args.ask_text)
            if args.json:
                print(json.dumps(out, ensure_ascii=False))
            else:
                print(out.get("reply", ""))
            return 0 if out.get("ok") else 1

        if args.interactive:
            logger.info("[ESCUTA-ATIVA] Modo Interativo Push-to-Talk iniciado (sem escuta passiva de palmas).")
            print("=== OpenJarvis · Modo Push-to-Talk / Comando Ativo ===")
            print("Pressione [ENTER] vazio para falar no microfone, digite seu comando em texto, ou 'sair' para encerrar.\n")
            while True:
                try:
                    user_in = input("Jarvis [ENTER=Voz / Texto] > ").strip()
                except (EOFError, KeyboardInterrupt):
                    print("\nEncerrando...")
                    break
                if user_in.lower() in ("sair", "exit", "quit", "q"):
                    break
                if not user_in:
                    out = runtime.handle_voice_turn()
                else:
                    out = runtime.handle_text_turn(user_in)
                print(f"\nJarvis: {out.get('reply', '')}\n")
            return 0

        # Default action: single on-demand Push-to-Talk turn (never a passive clap loop!)
        out = runtime.handle_voice_turn()
        if args.json:
            print(json.dumps(out, ensure_ascii=False))
        else:
            print(out.get("reply", ""))
        return 0 if out.get("ok") else 1
    finally:
        runtime.close()


if __name__ == "__main__":
    sys.exit(main())
