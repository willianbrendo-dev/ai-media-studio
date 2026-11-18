"""Microsoft Edge Neural TTS backend (lightweight, cloud-synthesized, zero local RAM)."""

from __future__ import annotations

import asyncio
import io
import os
import shutil
import subprocess
import tempfile
import threading
import wave
from pathlib import Path
from typing import Any, List

from openjarvis.core.registry import TTSRegistry
from openjarvis.speech.tts import TTSBackend, TTSResult

try:
    import edge_tts
except ImportError:
    edge_tts = None  # type: ignore[assignment]


_DEFAULT_VOICE = "pt-BR-AntonioNeural"
_PT_BR_VOICES = [
    "pt-BR-AntonioNeural",
    "pt-BR-FranciscaNeural",
    "pt-BR-ThalitaMultilingualNeural",
    "en-US-AndrewNeural",
    "en-US-AriaNeural",
]


def _speed_to_edge_rate(speed: float) -> str:
    """Convert numeric playback speed (e.g. 1.0, 1.15) to Edge-TTS rate string."""
    pct = int(round((float(speed) - 1.0) * 100))
    pct = max(-50, min(100, pct))
    return f"{pct:+d}%"


async def _fetch_edge_mp3(text: str, voice: str, rate: str) -> bytes:
    if edge_tts is None:
        raise RuntimeError("edge-tts package is not installed")
    last_exc: BaseException | None = None
    for attempt in range(3):
        try:
            communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
            chunks: list[bytes] = []
            async for chunk in communicate.stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    chunks.append(chunk["data"])
            audio = b"".join(chunks)
            if audio:
                return audio
        except BaseException as exc:  # noqa: BLE001
            last_exc = exc
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))
    if last_exc is not None:
        raise last_exc
    raise RuntimeError(f"Edge-TTS returned empty audio for voice={voice!r}")


def _espeak_fallback_48k_wav(text: str, target_rate: int = 48000, channels: int = 2) -> tuple[bytes, int, float]:
    """Offline fallback via espeak-ng + FFmpeg 48kHz Stereo SoXR if Edge-TTS DNS is temporarily unreachable."""
    espeak_bin = shutil.which("espeak-ng") or shutil.which("espeak")
    if not espeak_bin:
        raise RuntimeError("Neither Edge-TTS network nor espeak-ng is available")
    raw_res = subprocess.run(
        [espeak_bin, "-v", "pt-br", "-s", "165", "--stdout", text],
        capture_output=True,
        timeout=20,
        check=True,
    )
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if ffmpeg_bin:
        in_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with in_tmp:
                in_tmp.write(raw_res.stdout)
            out_tmp.close()
            subprocess.run(
                [
                    ffmpeg_bin,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    in_tmp.name,
                    "-ar",
                    str(target_rate),
                    "-ac",
                    str(channels),
                    "-af",
                    "volume=0.9,aresample=resampler=soxr",
                    "-c:a",
                    "pcm_s16le",
                    out_tmp.name,
                ],
                capture_output=True,
                timeout=20,
                check=False,
            )
            if os.path.getsize(out_tmp.name) > 44:
                wav_bytes = Path(out_tmp.name).read_bytes()
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    sr = wf.getframerate()
                    dur = wf.getnframes() / float(sr) if sr > 0 else 0.0
                return wav_bytes, sr, dur
        finally:
            for p in (in_tmp.name, out_tmp.name):
                try:
                    os.unlink(p)
                except OSError:
                    pass
    return raw_res.stdout, 22050, 1.0


def _run_coro_sync(coro: Any) -> bytes:
    """Execute an async coroutine safely from both sync and running-loop contexts."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result_box: list[bytes] = []
    err_box: list[BaseException] = []

    def _worker() -> None:
        try:
            result_box.append(asyncio.run(coro))
        except BaseException as exc:  # noqa: BLE001
            err_box.append(exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=120.0)
    if err_box:
        raise err_box[0]
    if not result_box:
        raise TimeoutError("Edge-TTS synthesis timed out")
    return result_box[0]


def _resolve_ffmpeg_bin() -> str:
    """Locate ffmpeg binary (with libsoxr support)."""
    local_bin = Path.home() / ".local" / "bin" / "ffmpeg"
    if local_bin.is_file() and os.access(local_bin, os.X_OK):
        return str(local_bin)
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return ""


def _mp3_to_wav(
    mp3_bytes: bytes,
    target_rate: int = 48000,
    channels: int = 2,
    volume: float = 0.9,
) -> tuple[bytes, int, float]:
    """Convert MP3 bytes to 48,000 Hz Stereo 16-bit WAV using FFmpeg + SoXR resampler (zero hiss/interpolation noise)."""
    ffmpeg_bin = _resolve_ffmpeg_bin()
    if ffmpeg_bin:
        in_tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        out_tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        try:
            with in_tmp:
                in_tmp.write(mp3_bytes)
            out_tmp.close()
            cmd = [
                ffmpeg_bin,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                in_tmp.name,
                "-ar",
                str(target_rate),
                "-ac",
                str(channels),
                "-af",
                f"volume={volume},aresample=resampler=soxr",
                "-c:a",
                "pcm_s16le",
                out_tmp.name,
            ]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=False)
            if res.returncode == 0 and os.path.getsize(out_tmp.name) > 44:
                wav_bytes = Path(out_tmp.name).read_bytes()
                with wave.open(io.BytesIO(wav_bytes), "rb") as wf:
                    sr = wf.getframerate()
                    nframes = wf.getnframes()
                    duration = nframes / float(sr) if sr > 0 else 0.0
                return wav_bytes, sr, duration
        finally:
            for p in (in_tmp.name, out_tmp.name):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # Fallback in-memory using PyAV (to_ndarray without plane stride padding) + libsoxr
    import av
    import numpy as np
    import soxr

    mono_chunks: list[np.ndarray] = []
    src_rate = 24000
    with av.open(io.BytesIO(mp3_bytes), format="mp3") as container:
        for frame in container.decode(audio=0):
            src_rate = int(frame.sample_rate or 24000)
            arr = frame.to_ndarray()
            if arr.ndim > 1:
                arr = arr.mean(axis=0)
            if np.issubdtype(arr.dtype, np.integer):
                arr = arr.astype(np.float32) / 32768.0
            else:
                arr = arr.astype(np.float32)
            mono_chunks.append(arr)

    mono_f32 = np.concatenate(mono_chunks) if mono_chunks else np.zeros(0, dtype=np.float32)
    if mono_f32.size > 0 and src_rate != target_rate:
        mono_f32 = soxr.resample(mono_f32, src_rate, target_rate, quality="VHQ")
    mono_f32 = np.clip(mono_f32 * float(volume), -1.0, 1.0)
    pcm_i16 = (mono_f32 * 32767.0).astype(np.int16)
    if channels == 2:
        pcm_i16 = np.column_stack((pcm_i16, pcm_i16)).ravel()

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(target_rate)
        wf.writeframes(pcm_i16.tobytes())
    duration = (len(pcm_i16) / channels) / float(target_rate) if target_rate > 0 else 0.0
    return buf.getvalue(), target_rate, duration


@TTSRegistry.register("edge_tts")
class EdgeTTSBackend(TTSBackend):
    """Neural TTS via Microsoft Edge Read Aloud (PT-BR Antonio/Francisca, zero RAM weight)."""

    backend_id = "edge_tts"

    def __init__(self, *, default_voice: str = _DEFAULT_VOICE) -> None:
        self._default_voice = default_voice or _DEFAULT_VOICE

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str = "",
        speed: float = 1.0,
        output_format: str = "mp3",
    ) -> TTSResult:
        if edge_tts is None:
            raise RuntimeError("edge-tts is not installed. Run: uv pip install edge-tts")

        voice = (voice_id or "").strip() or self._default_voice
        rate = _speed_to_edge_rate(speed)
        fmt = (output_format or "mp3").lower().lstrip(".")
        try:
            mp3_bytes = _run_coro_sync(_fetch_edge_mp3(text, voice, rate))
        except Exception:
            if fmt == "wav":
                wav_bytes, sr, duration = _espeak_fallback_48k_wav(text, target_rate=48000, channels=2)
                return TTSResult(
                    audio=wav_bytes,
                    format="wav",
                    duration_seconds=duration,
                    voice_id=voice,
                    sample_rate=sr,
                    metadata={"backend": "espeak_ng_fallback", "rate": rate, "channels": 2, "resampler": "soxr"},
                )
            raise

        if fmt == "wav":
            wav_bytes, sr, duration = _mp3_to_wav(mp3_bytes, target_rate=48000, channels=2, volume=0.9)
            return TTSResult(
                audio=wav_bytes,
                format="wav",
                duration_seconds=duration,
                voice_id=voice,
                sample_rate=sr,
                metadata={"backend": "edge_tts", "rate": rate, "channels": 2, "resampler": "soxr"},
            )

        return TTSResult(
            audio=mp3_bytes,
            format="mp3",
            duration_seconds=0.0,
            voice_id=voice,
            sample_rate=24000,
            metadata={"backend": "edge_tts", "rate": rate},
        )

    def available_voices(self) -> List[str]:
        return list(_PT_BR_VOICES)

    def health(self) -> bool:
        return edge_tts is not None


__all__ = ["EdgeTTSBackend"]
