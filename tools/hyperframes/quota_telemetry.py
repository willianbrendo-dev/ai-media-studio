#!/usr/bin/env python3
"""Shared Real-Time RPM / RPD / Cooldown Telemetry & Smart Quota Router (v2.9).

Persists sliding-window 60-second RPM timestamps, daily RPD counters (auto-reset on
YYYY-MM-DD rollover), HTTP 429 cooldowns, SHA-256 voice cache hits, and Gemini API
call savings across both the FastAPI gateway (`main.py`) and worker subprocesses
(`regional_voice_engine.py`, `vps_novela_runner.py`, `novela_lipsync_animator.py`).
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


STUDIO_BASE = Path(os.environ.get("STUDIO_BASE_DIR", "/opt/ai-media-studio"))
DEFAULT_TELEMETRY_FILE = Path(
    os.environ.get(
        "QUOTA_TELEMETRY_FILE",
        str(STUDIO_BASE / "media-control-panel/quota_telemetry.json"),
    )
)
LOCK_FILE = Path(os.environ.get("QUOTA_TELEMETRY_LOCK", "/tmp/ai_studio_quota_telemetry.lock"))

# Canonical Free/Pro Quota Catalog for UI Telemetry & Smart Routing
MODEL_SPECS: List[Dict[str, Any]] = [
    {
        "id": "nvidia/nemotron-3.5-lightning-30b-a3b",
        "provider": "nvidia-nim",
        "role": "Texto/Roteiro Titular",
        "specialty": "text",
        "rpm_limit": 40,
        "rpd_limit": 1000,
    },
    {
        "id": "nvidia/nemotron-3-ultra-550b-a55b",
        "provider": "nvidia-nim",
        "role": "Texto/Roteiro Deep",
        "specialty": "text",
        "rpm_limit": 40,
        "rpd_limit": 1000,
    },
    {
        "id": "moonshotai/kimi-k3.1-pro",
        "provider": "nvidia-nim",
        "role": "Texto/Roteiro NIM",
        "specialty": "text",
        "rpm_limit": 30,
        "rpd_limit": 1000,
    },
    {
        "id": "openrouter/nemotron-free",
        "provider": "openrouter",
        "role": "Texto Failover",
        "specialty": "text",
        "rpm_limit": 20,
        "rpd_limit": 500,
    },
    {
        "id": "gemini-2.5-flash-native-audio-latest",
        "provider": "google-gemini-live",
        "role": "Voz Nordestina Live API",
        "specialty": "voice",
        "rpm_limit": "Ilimitado",
        "tpm_limit": "1M",
        "rpd_limit": "Ilimitado",
    },
    {
        "id": "gemini-3.8-flash-tts",
        "provider": "google-gemini",
        "role": "Voz Nordestina REST Pool",
        "specialty": "voice",
        "rpm_limit": 3,
        "rpd_limit": 15,
    },
    {
        "id": "gemini-3.8-flash-lite-tts",
        "provider": "google-gemini",
        "role": "Voz Nordestina REST Pool",
        "specialty": "voice",
        "rpm_limit": 3,
        "rpd_limit": 15,
    },
    {
        "id": "gemini-2.5-flash-preview-tts",
        "provider": "google-gemini",
        "role": "Voz Nordestina REST Pool",
        "specialty": "voice",
        "rpm_limit": 3,
        "rpd_limit": 15,
    },
    {
        "id": "gemini-3.1-flash-tts-preview",
        "provider": "google-gemini",
        "role": "Voz Nordestina REST Pool",
        "specialty": "voice",
        "rpm_limit": 3,
        "rpd_limit": 15,
    },
    {
        "id": "gemini-2.5-pro-preview-tts",
        "provider": "google-gemini",
        "role": "Voz Nordestina REST Pool",
        "specialty": "voice",
        "rpm_limit": 3,
        "rpd_limit": 15,
    },
    {
        "id": "gemma-4-31b-it",
        "provider": "google-gemini",
        "role": "Reserva Gemini Alta Cota",
        "specialty": "text",
        "rpm_limit": 30,
        "rpd_limit": 1500,
    },
    {
        "id": "gemini-3.5-flash-lite",
        "provider": "google-gemini",
        "role": "Visão Multimodal / Texto Lite",
        "specialty": "vision",
        "rpm_limit": 15,
        "rpd_limit": 500,
    },
    {
        "id": "gemini-3.1-flash-lite",
        "provider": "google-gemini",
        "role": "Visão Multimodal / Texto Lite",
        "specialty": "vision",
        "rpm_limit": 15,
        "rpd_limit": 500,
    },
    {
        "id": "gemini-2.5-flash-lite",
        "provider": "google-gemini",
        "role": "Visão / Texto Contingência",
        "specialty": "vision",
        "rpm_limit": 10,
        "rpd_limit": 500,
    },
    {
        "id": "gemini-3.8-flash",
        "provider": "google-gemini",
        "role": "Texto Contingência",
        "specialty": "text",
        "rpm_limit": 5,
        "rpd_limit": 100,
    },
    {
        "id": "gemini-2.5-flash",
        "provider": "google-gemini",
        "role": "Último Fallback Gemini",
        "specialty": "text",
        "rpm_limit": 5,
        "rpd_limit": 20,
    },
    {
        "id": "black-forest-labs/flux.1-dev",
        "provider": "nvidia-nim",
        "role": "Imagem Base 1080x1920 NIM",
        "specialty": "image",
        "rpm_limit": 40,
        "rpd_limit": 1000,
    },
    {
        "id": "zerogpu-a10g-video",
        "provider": "huggingface-zerogpu",
        "role": "Vídeo Generativo & Lip-Sync GPU",
        "specialty": "video",
        "rpm_limit": 20,
        "rpd_limit": 300,
    },
    {
        "id": "colab-t4-gpu",
        "provider": "google-colab-t4",
        "role": "Vídeo Generativo & Lip-Sync GPU",
        "specialty": "video",
        "rpm_limit": 60,
        "rpd_limit": 1000,
    },
    {
        "id": "google-flow-veo31",
        "provider": "google-flow-veo31",
        "role": "Vídeo Generativo & Lip-Sync GPU",
        "specialty": "video",
        "rpm_limit": "Ilimitado",
        "rpd_limit": "Ilimitado",
    },
]

_SPEC_BY_ID: Dict[str, Dict[str, Any]] = {s["id"]: s for s in MODEL_SPECS}

_ALIASES: Dict[str, str] = {
    "nvidia/nemotron-3-ultra-550b-a55b:free": "openrouter/nemotron-free",
    "nvidia/nemotron-3.5-lightning:free": "openrouter/nemotron-free",
    "openrouter/free": "openrouter/nemotron-free",
    "models/gemini-2.5-flash-native-audio-latest": "gemini-2.5-flash-native-audio-latest",
    "gemini-3.1-flash-live-preview": "gemini-2.5-flash-native-audio-latest",
    "models/gemini-3.1-flash-live-preview": "gemini-2.5-flash-native-audio-latest",
    "gemini-3.8-live": "gemini-2.5-flash-native-audio-latest",
    "models/gemini-3.8-live": "gemini-2.5-flash-native-audio-latest",
    "black-forest-labs/flux.1-schnell": "black-forest-labs/flux.1-dev",
}

TTS_REST_POOL: List[str] = [
    "gemini-3.8-flash-tts",
    "gemini-3.8-flash-lite-tts",
    "gemini-2.5-flash-preview-tts",
    "gemini-3.1-flash-tts-preview",
    "gemini-2.5-pro-preview-tts",
]


def normalize_model_id(raw_id: str) -> str:
    clean = (raw_id or "").strip()
    if clean in _SPEC_BY_ID:
        return clean
    if clean in _ALIASES:
        return _ALIASES[clean]
    if clean.startswith("models/"):
        stripped = clean[7:]
        if stripped in _SPEC_BY_ID:
            return stripped
        if stripped in _ALIASES:
            return _ALIASES[stripped]
    return clean


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


class QuotaTelemetryTracker:
    """Thread/process-safe persistent RPM/RPD telemetry tracker."""

    def __init__(self, state_file: Optional[Path] = None, lock_file: Optional[Path] = None):
        self.state_file = Path(state_file) if state_file else DEFAULT_TELEMETRY_FILE
        self.lock_file = Path(lock_file) if lock_file else LOCK_FILE

    def _empty_state(self) -> Dict[str, Any]:
        return {
            "date": _today_str(),
            "active_by_specialty": {
                "text": "nvidia/nemotron-3.5-lightning-30b-a3b",
                "voice": "gemini-2.5-flash-native-audio-latest",
                "vision": "gemini-3.5-flash-lite",
                "image": "black-forest-labs/flux.1-dev",
                "video": "zerogpu-a10g-video",
            },
            "savings": {
                "saved_gemini_text_calls": 0,
                "saved_gemini_voice_cache_hits": 0,
                "saved_gemini_live_audio_calls": 0,
            },
            "rr_tts_cursor": 0,
            "stats": {},
            "updated_at": time.time(),
        }

    def _ensure_day_rollover(self, data: Dict[str, Any], now: float) -> Dict[str, Any]:
        today = _today_str()
        stats = data.setdefault("stats", {})
        for spec in MODEL_SPECS:
            mid = spec["id"]
            st = stats.setdefault(mid, {})
            st.setdefault("timestamps", [])
            st.setdefault("rpd_used", 0)
            st.setdefault("cache_hits", 0)
            st.setdefault("total_calls", 0)
            st.setdefault("cooldown_until", 0.0)
            st.setdefault("last_error_code", None)
            st.setdefault("last_error", "")
            st.setdefault("last_called_at", 0.0)
            # Prune 60s sliding RPM window
            st["timestamps"] = [float(t) for t in st["timestamps"] if float(t) >= now - 60.0]

        if data.get("date") != today:
            data["date"] = today
            for st in stats.values():
                st["rpd_used"] = 0
                st["cooldown_until"] = 0.0
                st["last_error_code"] = None
                st["last_error"] = ""
            data["savings"] = {
                "saved_gemini_text_calls": 0,
                "saved_gemini_voice_cache_hits": 0,
                "saved_gemini_live_audio_calls": 0,
            }
        data.setdefault("active_by_specialty", self._empty_state()["active_by_specialty"])
        data.setdefault("savings", self._empty_state()["savings"])
        data.setdefault("rr_tts_cursor", 0)
        return data

    def _with_state(self, mutator=None) -> Dict[str, Any]:
        now = time.time()
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.lock_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_file, "a+", encoding="utf-8") as lf:
            fcntl.flock(lf.fileno(), fcntl.LOCK_EX)
            try:
                if self.state_file.exists():
                    try:
                        data = json.loads(self.state_file.read_text(encoding="utf-8"))
                        if not isinstance(data, dict):
                            data = self._empty_state()
                    except Exception:
                        data = self._empty_state()
                else:
                    data = self._empty_state()

                data = self._ensure_day_rollover(data, now)
                if mutator is not None:
                    data = mutator(data, now)
                    data["updated_at"] = now
                    tmp = self.state_file.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    tmp.replace(self.state_file)
                elif not self.state_file.exists():
                    tmp = self.state_file.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                    tmp.replace(self.state_file)
                return data
            finally:
                fcntl.flock(lf.fileno(), fcntl.LOCK_UN)

    def record_call(
        self,
        model_id: str,
        *,
        ok: bool = True,
        status_code: int = 200,
        specialty: Optional[str] = None,
        cooldown_s: float = 0.0,
        saved_gemini: bool = False,
        live_audio_unlimited: bool = False,
        error: str = "",
    ) -> None:
        mid = normalize_model_id(model_id)

        def _mutate(data: Dict[str, Any], now: float) -> Dict[str, Any]:
            st = data["stats"].setdefault(mid, {
                "timestamps": [], "rpd_used": 0, "cache_hits": 0, "total_calls": 0,
                "cooldown_until": 0.0, "last_error_code": None, "last_error": "", "last_called_at": 0.0,
            })
            st["timestamps"].append(now)
            st["timestamps"] = [t for t in st["timestamps"] if t >= now - 60.0]
            st["rpd_used"] = int(st.get("rpd_used", 0)) + 1
            st["total_calls"] = int(st.get("total_calls", 0)) + 1
            st["last_called_at"] = now

            if ok:
                st["last_error_code"] = None
                st["last_error"] = ""
                st["cooldown_until"] = 0.0
                spec_key = specialty or (_SPEC_BY_ID.get(mid, {}).get("specialty"))
                if spec_key:
                    data["active_by_specialty"][spec_key] = mid
                if saved_gemini and spec_key == "text":
                    data["savings"]["saved_gemini_text_calls"] = int(data["savings"].get("saved_gemini_text_calls", 0)) + 1
                if live_audio_unlimited:
                    data["savings"]["saved_gemini_live_audio_calls"] = int(data["savings"].get("saved_gemini_live_audio_calls", 0)) + 1
            else:
                st["last_error_code"] = int(status_code)
                st["last_error"] = (error or "")[:160]
                if status_code == 429 or cooldown_s > 0:
                    eff_cd = cooldown_s if cooldown_s > 0 else 65.0
                    st["cooldown_until"] = max(float(st.get("cooldown_until", 0.0)), now + eff_cd)
            return data

        self._with_state(_mutate)

    def record_cache_hit(self, model_id: str = "gemini-2.5-flash-native-audio-latest", specialty: str = "voice") -> None:
        mid = normalize_model_id(model_id)

        def _mutate(data: Dict[str, Any], now: float) -> Dict[str, Any]:
            st = data["stats"].setdefault(mid, {
                "timestamps": [], "rpd_used": 0, "cache_hits": 0, "total_calls": 0,
                "cooldown_until": 0.0, "last_error_code": None, "last_error": "", "last_called_at": 0.0,
            })
            st["cache_hits"] = int(st.get("cache_hits", 0)) + 1
            if specialty:
                data["active_by_specialty"][specialty] = mid
            data["savings"]["saved_gemini_voice_cache_hits"] = int(data["savings"].get("saved_gemini_voice_cache_hits", 0)) + 1
            return data

        self._with_state(_mutate)

    def set_active(self, specialty: str, model_id: str) -> None:
        mid = normalize_model_id(model_id)

        def _mutate(data: Dict[str, Any], _now: float) -> Dict[str, Any]:
            data["active_by_specialty"][specialty] = mid
            return data

        self._with_state(_mutate)

    def _model_status_row(self, spec: Dict[str, Any], st: Dict[str, Any], now: float) -> Dict[str, Any]:
        rpm_used = len([t for t in st.get("timestamps", []) if float(t) >= now - 60.0])
        rpd_used = int(st.get("rpd_used", 0))
        rpm_lim = spec["rpm_limit"]
        rpd_lim = spec["rpd_limit"]
        cd_rem = max(0, int(round(float(st.get("cooldown_until", 0.0)) - now)))

        status = "ok"
        if isinstance(rpd_lim, int) and rpd_used >= rpd_lim:
            status = "exhausted_daily"
        elif cd_rem > 0:
            status = "cooldown"
        elif isinstance(rpm_lim, int) and rpm_used >= rpm_lim:
            status = "cooldown"
            cd_rem = max(cd_rem, 15)

        row: Dict[str, Any] = {
            "id": spec["id"],
            "provider": spec["provider"],
            "role": spec["role"],
            "specialty": spec["specialty"],
            "rpm_used": rpm_used,
            "rpm_limit": rpm_lim,
            "rpd_used": rpd_used,
            "rpd_limit": rpd_lim,
            "status": status,
            "cooldown_remaining_s": cd_rem,
            "last_error_code": st.get("last_error_code"),
            "cache_hits": int(st.get("cache_hits", 0)),
            "total_calls": int(st.get("total_calls", 0)),
        }
        if "tpm_limit" in spec:
            row["tpm_limit"] = spec["tpm_limit"]
        return row

    def is_available(self, model_id: str) -> bool:
        mid = normalize_model_id(model_id)
        spec = _SPEC_BY_ID.get(mid)
        if not spec:
            return True
        now = time.time()
        data = self._with_state(None)
        st = data.get("stats", {}).get(mid, {})
        row = self._model_status_row(spec, st, now)
        return row["status"] == "ok"

    def pick_round_robin_tts(self, pool: Optional[List[str]] = None) -> List[str]:
        """Return TTS REST models rotated & sorted so those with <3 RPM headroom come first cleanly."""
        target_pool = list(pool or TTS_REST_POOL)

        def _mutate_cursor(data: Dict[str, Any], _now: float) -> Dict[str, Any]:
            cur = int(data.get("rr_tts_cursor", 0))
            data["rr_tts_cursor"] = (cur + 1) % max(1, len(target_pool))
            return data

        data = self._with_state(_mutate_cursor)
        now = time.time()
        cur = int(data.get("rr_tts_cursor", 0))
        rotated = [target_pool[(cur + idx) % len(target_pool)] for idx in range(len(target_pool))]

        ready: List[Tuple[int, str]] = []
        backup: List[Tuple[int, str]] = []
        for idx, mid in enumerate(rotated):
            norm = normalize_model_id(mid)
            spec = _SPEC_BY_ID.get(norm, {"rpm_limit": 3, "rpd_limit": 15, "provider": "google-gemini", "role": "TTS", "specialty": "voice", "id": norm})
            row = self._model_status_row(spec, data.get("stats", {}).get(norm, {}), now)
            # Pre-emptively skip if >=2 calls used in last 60s when limit is 3 (avoid hitting 429!)
            rpm_lim = spec["rpm_limit"]
            rpd_lim = spec["rpd_limit"]
            if row["status"] == "ok" and (not isinstance(rpm_lim, int) or row["rpm_used"] < max(1, rpm_lim)):
                ready.append((row["rpm_used"], mid))
            elif not (isinstance(rpd_lim, int) and row["rpd_used"] >= rpd_lim) and row["cooldown_remaining_s"] == 0:
                backup.append((row["rpm_used"], mid))
        ready.sort(key=lambda x: x[0])
        backup.sort(key=lambda x: x[0])
        return [m for _, m in ready] + [m for _, m in backup]

    def snapshot(self) -> Dict[str, Any]:
        now = time.time()
        data = self._with_state(None)
        stats = data.get("stats", {})
        models_out: List[Dict[str, Any]] = []
        for spec in MODEL_SPECS:
            st = stats.get(spec["id"], {})
            models_out.append(self._model_status_row(spec, st, now))

        tts_rows = [m for m in models_out if m["id"] in TTS_REST_POOL]
        tts_rpm_used = sum(int(m["rpm_used"]) for m in tts_rows)
        tts_rpd_used = sum(int(m["rpd_used"]) for m in tts_rows)
        tts_rpd_lim = sum(int(m["rpd_limit"]) for m in tts_rows if isinstance(m["rpd_limit"], int))

        sav = data.get("savings", {})
        saved_text = int(sav.get("saved_gemini_text_calls", 0))
        saved_cache = int(sav.get("saved_gemini_voice_cache_hits", 0))
        saved_live = int(sav.get("saved_gemini_live_audio_calls", 0))

        return {
            "ok": True,
            "date": data.get("date", _today_str()),
            "updated_at": data.get("updated_at", now),
            "active_by_specialty": data.get("active_by_specialty", {}),
            "models": models_out,
            "tts_rest_pool": {
                "rpm_used": tts_rpm_used,
                "rpm_limit": 3 * len(TTS_REST_POOL),
                "rpd_used": tts_rpd_used,
                "rpd_limit": tts_rpd_lim,
            },
            "savings_summary": {
                "saved_gemini_text_calls": saved_text,
                "saved_gemini_voice_cache_hits": saved_cache,
                "saved_gemini_live_audio_calls": saved_live,
                "total_saved_calls": saved_text + saved_cache + saved_live,
            },
        }


_DEFAULT_TRACKER: Optional[QuotaTelemetryTracker] = None


def get_tracker() -> QuotaTelemetryTracker:
    global _DEFAULT_TRACKER
    if _DEFAULT_TRACKER is None:
        _DEFAULT_TRACKER = QuotaTelemetryTracker()
    return _DEFAULT_TRACKER
