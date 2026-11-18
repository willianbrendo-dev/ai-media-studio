#!/usr/bin/env python3
"""Local Ecosystem MCP Server (studio_controller) for OpenJarvis.

Exposes structured tools over stdio MCP to control Docker stacks, inspect the
AI Cascade Gateway, trigger stack API jobs via localhost:9000 / local ports,
toggle modo-dev, and run on-demand webcam vision.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

for _tvar in (
    "OMP_NUM_THREADS",
    "MKL_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "VECLIB_MAXIMUM_THREADS",
):
    os.environ.setdefault(_tvar, "2")

sys.path.insert(0, str(Path(__file__).resolve().parent))

import httpx
from mcp.server.mcpserver import MCPServer

logging.basicConfig(stream=sys.stderr, level=logging.WARNING)
logger = logging.getLogger("studio_controller")

PANEL_BASE = "http://127.0.0.1:9000"
OPENJARVIS_ENV_FILE = Path("/home/wbrendo/.openjarvis/.env")

STACK_ALIASES: Dict[str, str] = {
    "moneyprinter": "moneyprinter",
    "moneyprinterturbo": "moneyprinter",
    "money-printer-turbo": "moneyprinter",
    "supoclip": "supoclip",
    "openfilm": "openfilm",
    "agenttube": "agenttube",
    "youtube-automation-agent": "agenttube",
    "openhiggsfield": "openhiggsfield",
    "open-higgsfield": "openhiggsfield",
    "higgsfield": "openhiggsfield",
    "voicestudio": "voicestudio",
    "voice-studio": "voicestudio",
    "openseo": "openseo",
    "open-seo": "openseo",
}

STACK_API_PORTS: Dict[str, int] = {
    "supoclip": 8000,
    "moneyprinter": 8080,
    "openfilm": 3005,
    "agenttube": 3010,
    "openhiggsfield": 3020,
    "voicestudio": 3030,
    "openseo": 3040,
    "panel": 9000,
}

mcp = MCPServer("studio-controller")


def _read_dotenv_vars() -> Dict[str, str]:
    vals: Dict[str, str] = {}
    if OPENJARVIS_ENV_FILE.exists():
        try:
            for raw in OPENJARVIS_ENV_FILE.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                vals[k.strip()] = v.strip().strip('"').strip("'")
        except Exception:
            pass
    for k in ("VPS_PANEL_URL", "VPS_PANEL_TOKEN", "VPS_PANEL_USER", "VPS_PANEL_PASS", "STUDIO_DEFAULT_TARGET"):
        if os.environ.get(k):
            vals[k] = os.environ[k].strip()
    return vals


def _resolve_target_conn(target: str = "vps") -> tuple[str, str, Dict[str, str], Optional[tuple[str, str]]]:
    """Return (normalized_target, base_url, headers, basic_auth_tuple)."""
    env = _read_dotenv_vars()
    norm = (target or "").strip().lower()
    if not norm or norm == "auto":
        norm = env.get("STUDIO_DEFAULT_TARGET", "vps").strip().lower()
    if norm in ("vps", "cloud", "nuvem", "remote", "remoto"):
        vps_url = env.get("VPS_PANEL_URL", "https://187.77.202.176.sslip.io").rstrip("/")
        if not vps_url:
            raise RuntimeError("VPS_PANEL_URL não configurada em ~/.openjarvis/.env")
        headers: Dict[str, str] = {}
        auth: Optional[tuple[str, str]] = None
        token = env.get("VPS_PANEL_TOKEN", "")
        user = env.get("VPS_PANEL_USER", "")
        pw = env.get("VPS_PANEL_PASS", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
            headers["X-Studio-Token"] = token
        if user and pw and not token:
            auth = (user, pw)
        return "vps", vps_url, headers, auth
    return "local", PANEL_BASE, {}, None


def _resolve_stack_id(name: str) -> str:
    cleaned = name.strip().lower().replace(" ", "")
    return STACK_ALIASES.get(cleaned, name.strip().lower())


@mcp.tool()
def studio_list_stacks(target: str = "vps") -> str:
    """List all 7 Docker media stacks and runtime status from the Control Panel (defaults to 'vps').

    Args:
        target: 'vps' (Hostinger cloud server, default) or 'local'.
    """
    try:
        norm_target, base_url, headers, auth = _resolve_target_conn(target)
        with httpx.Client(timeout=15.0, headers=headers, auth=auth) as client:
            proj_resp = client.get(f"{base_url}/api/projects")
            proj_resp.raise_for_status()
            projects = proj_resp.json().get("projects", [])
            jarvis_data: Dict[str, Any] = {}
            try:
                j_resp = client.get(f"{base_url}/api/openjarvis/status")
                if j_resp.status_code == 200:
                    jarvis_data = j_resp.json()
            except Exception:
                pass
        summary = [
            {
                "id": p.get("id"),
                "name": p.get("name"),
                "status": p.get("status"),
                "http_ready": p.get("http_ready"),
                "running_containers": p.get("running_containers"),
                "total_containers": p.get("total_containers"),
                "ui_port": p.get("ui_port"),
                "api_port": p.get("api_port"),
            }
            for p in projects
        ]
        return json.dumps(
            {
                "ok": True,
                "target": norm_target,
                "panel_url": base_url,
                "stack_count": len(summary),
                "stacks": summary,
                "openjarvis": jarvis_data,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "target": target, "error": f"Falha ao consultar painel ({target}): {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_control_stack(stack_name: str, action: str, target: str = "vps") -> str:
    """Start, stop, or restart a Docker stack (or 'all') on cloud VPS (default) or local PC.

    Args:
        stack_name: One of moneyprinter, supoclip, openfilm, voicestudio, openseo, agenttube, openhiggsfield, or 'all'.
        action: One of 'start', 'stop', 'restart'.
        target: 'vps' (cloud server, default) or 'local'.
    """
    act = action.strip().lower()
    if act not in ("start", "stop", "restart"):
        return json.dumps({"ok": False, "error": "action deve ser 'start', 'stop' ou 'restart'."})

    stack_id = _resolve_stack_id(stack_name)
    try:
        norm_target, base_url, headers, auth = _resolve_target_conn(target)
        with httpx.Client(timeout=130.0, headers=headers, auth=auth) as client:
            if stack_id in ("all", "todas", "todos"):
                if act == "stop":
                    resp = client.post(f"{base_url}/api/projects/stop-all")
                elif act == "start":
                    resp = client.post(f"{base_url}/api/projects/start-all")
                else:
                    return json.dumps({"ok": False, "error": "Para 'all', use action='start' ou 'stop'."})
            else:
                resp = client.post(
                    f"{base_url}/api/projects/{stack_id}/action",
                    json={"action": act},
                )
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "target": norm_target, "status_code": resp.status_code, "detail": data}, ensure_ascii=False)
            data["target"] = norm_target
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "target": target, "error": f"Erro ao controlar stack '{stack_id}' ({target}): {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_cascade_status(live_check: bool = False, target: str = "vps") -> str:
    """Check AI Cascade Gateway (Claude -> Gemini -> NVIDIA NIM -> OpenRouter) and CPU/RAM telemetry on VPS (default) or local PC.

    Args:
        live_check: If true, runs live probes on all providers.
        target: 'vps' (cloud server, default) or 'local'.
    """
    try:
        norm_target, base_url, headers, auth = _resolve_target_conn(target)
        with httpx.Client(timeout=40.0, headers=headers, auth=auth) as client:
            sys_data = client.get(f"{base_url}/api/system").json()
            cascade_data = client.get(f"{base_url}/api/ai/cascade-status").json()
            diag_data = None
            infra_data = None
            skills_diag = None
            if live_check:
                diag_resp = client.post(f"{base_url}/api/diagnostics/all")
                if diag_resp.status_code == 200:
                    full_diag = diag_resp.json()
                    diag_data = full_diag.get("ai_cascade")
                    infra_data = full_diag.get("infra")
                    skills_diag = full_diag.get("skills_diag")
        return json.dumps(
            {
                "ok": True,
                "target": norm_target,
                "panel_url": base_url,
                "system": sys_data,
                "cascade": cascade_data,
                "live_diagnostic": diag_data,
                "infra_diagnostic": infra_data,
                "skills_diagnostic": skills_diag,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "target": target, "error": f"Erro ao consultar cascata/telemetria ({target}): {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_get_stack_logs(stack_name: str, target: str = "vps") -> str:
    """Fetch recent Docker Compose logs for a media stack on VPS (default) or local PC.

    Args:
        stack_name: Stack identifier (e.g. 'moneyprinter', 'supoclip', 'voicestudio').
        target: 'vps' (default) or 'local'.
    """
    stack_id = _resolve_stack_id(stack_name)
    try:
        norm_target, base_url, headers, auth = _resolve_target_conn(target)
        with httpx.Client(timeout=20.0, headers=headers, auth=auth) as client:
            resp = client.get(f"{base_url}/api/projects/{stack_id}/logs")
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "target": norm_target, "detail": data}, ensure_ascii=False)
            data["target"] = norm_target
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "target": target, "error": f"Erro ao ler logs de '{stack_id}' ({target}): {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_list_outputs(stack_name: str = "all", target: str = "vps") -> str:
    """List rendered videos, cuts, and audio outputs available in the Outputs Gallery for download.

    Args:
        stack_name: 'all' or specific stack ('moneyprinter', 'voicestudio', 'supoclip', 'openfilm', 'agenttube').
        target: 'vps' (cloud server, default) or 'local'.
    """
    stack_id = "all" if stack_name.strip().lower() in ("all", "todas", "todos", "") else _resolve_stack_id(stack_name)
    try:
        norm_target, base_url, headers, auth = _resolve_target_conn(target)
        with httpx.Client(timeout=20.0, headers=headers, auth=auth) as client:
            resp = client.get(f"{base_url}/api/outputs", params={"stack": stack_id, "limit": 25})
            resp.raise_for_status()
            data = resp.json()
        items = [
            {
                "stack": x.get("stack"),
                "filename": x.get("filename"),
                "size": x.get("size_human"),
                "type": x.get("media_type"),
                "created": x.get("mtime_iso"),
                "download_url": f"{base_url}{x.get('download_url', '')}",
            }
            for x in data.get("items", [])[:15]
        ]
        return json.dumps(
            {
                "ok": True,
                "target": norm_target,
                "count": data.get("count", len(items)),
                "total_size": data.get("total_size_human", "0 B"),
                "items": items,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "target": target, "error": f"Erro ao listar saídas ({target}): {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_trigger_job(
    stack_name: str,
    endpoint_path: str,
    method: str = "POST",
    payload_json: str = "{}",
    target: str = "vps",
) -> str:
    """Trigger an HTTP API job directly on a media stack on VPS (default) or local PC without clicking UI elements.

    Args:
        stack_name: Target stack ('moneyprinter', 'supoclip', 'openfilm', 'voicestudio', 'openseo', 'agenttube', 'openhiggsfield', or 'panel').
        endpoint_path: Endpoint path starting with '/', e.g. '/api/v1/videos' or '/api/tts'.
        method: HTTP method ('GET' or 'POST').
        payload_json: JSON string payload for POST requests.
        target: 'vps' (default, routes safely through authenticated VPS /proxy endpoint) or 'local'.
    """
    stack_id = _resolve_stack_id(stack_name)
    port = STACK_API_PORTS.get(stack_id)
    if port is None:
        return json.dumps(
            {"ok": False, "error": f"Stack desconhecida '{stack_name}'. Válidas: {sorted(STACK_API_PORTS.keys())}"}
        )

    path = endpoint_path if endpoint_path.startswith("/") else f"/{endpoint_path}"
    http_method = method.strip().upper()
    if http_method not in ("GET", "POST", "PUT", "DELETE"):
        return json.dumps({"ok": False, "error": f"Método HTTP não suportado: {http_method}"})

    try:
        body: Optional[Dict[str, Any]] = json.loads(payload_json) if payload_json.strip() else {}
    except json.JSONDecodeError as exc:
        return json.dumps({"ok": False, "error": f"payload_json inválido: {exc}"})

    try:
        norm_target, base_url, headers, auth = _resolve_target_conn(target)
        if norm_target == "vps":
            with httpx.Client(timeout=60.0, headers=headers, auth=auth) as client:
                if stack_id == "panel":
                    resp = client.request(http_method, f"{base_url}{path}", json=body if http_method != "GET" else None)
                else:
                    resp = client.post(
                        f"{base_url}/api/projects/{stack_id}/proxy",
                        json={"method": http_method if http_method in ("GET", "POST") else "POST", "path": path, "port_kind": "api", "payload": body},
                    )
            try:
                resp_data = resp.json()
            except Exception:
                resp_data = resp.text[:2000]
            return json.dumps({"ok": resp.status_code < 400, "target": "vps", "status_code": resp.status_code, "response": resp_data}, ensure_ascii=False, indent=2)

        url = f"http://127.0.0.1:{port}{path}"
        with httpx.Client(timeout=60.0) as client:
            if http_method == "GET":
                resp = client.get(url, params=body if isinstance(body, dict) else None)
            elif http_method == "DELETE":
                resp = client.delete(url)
            else:
                resp = client.request(http_method, url, json=body)
        try:
            resp_data = resp.json()
        except Exception:
            resp_data = resp.text[:2000]
        return json.dumps(
            {
                "ok": resp.status_code < 400,
                "target": "local",
                "url": url,
                "status_code": resp.status_code,
                "response": resp_data,
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps(
            {
                "ok": False,
                "target": target,
                "error": f"Falha ao executar job em '{stack_id}' ({target}): {exc}. Confirme se a stack está ligada com studio_control_stack.",
            },
            ensure_ascii=False,
        )


@mcp.tool()
def studio_modo_dev(action: str = "status", sync_vps: bool = True) -> str:
    """Check or toggle Modo Dev engineering skills (on, off, toggle, status) locally on wbrendo-pc and sync with the Hostinger VPS."""
    act = (action or "status").strip().lower()
    if act in ("ativar", "enable", "true", "ligar"):
        act = "on"
    elif act in ("desativar", "disable", "false", "desligar", "off", "repouso"):
        act = "off"

    modo_bin = Path("/home/wbrendo/.local/bin/modo-dev")
    if not modo_bin.exists():
        return json.dumps({"ok": False, "error": "~/.local/bin/modo-dev não encontrado."}, ensure_ascii=False)

    try:
        st_cur = subprocess.run([str(modo_bin), "status", "--json"], capture_output=True, text=True, check=True)
        local_data = json.loads(st_cur.stdout)

        if act == "toggle":
            act = "off" if local_data.get("active") else "on"

        local_msg = ""
        if act in ("on", "off"):
            res = subprocess.run([str(modo_bin), act], capture_output=True, text=True, check=True)
            local_msg = res.stdout.strip()
            st_after = subprocess.run([str(modo_bin), "status", "--json"], capture_output=True, text=True, check=True)
            local_data = json.loads(st_after.stdout)
        elif act != "status":
            return json.dumps({"ok": False, "error": "action deve ser 'status', 'on', 'off' ou 'toggle'."})

        vps_info: Dict[str, Any] = {"synced": False, "skipped": not sync_vps}
        if sync_vps:
            try:
                _, base_url, headers, auth = _resolve_target_conn("vps")
                with httpx.Client(timeout=15.0, headers=headers, auth=auth) as client:
                    if act in ("on", "off"):
                        r_vps = client.post(
                            f"{base_url}/api/skills/dev-mode",
                            json={"enable": (act == "on"), "action": act},
                        )
                    else:
                        r_vps = client.get(f"{base_url}/api/skills/dev-mode")
                    if r_vps.status_code < 400:
                        vj = r_vps.json()
                        vps_info = {
                            "synced": True,
                            "active": bool(vj.get("active", vj.get("enabled"))),
                            "vault_total": vj.get("vault_total", vj.get("dev_skills_count")),
                            "linked_dev_count": vj.get("linked_dev_count", vj.get("dev_count")),
                        }
                    else:
                        vps_info = {"synced": False, "status_code": r_vps.status_code, "error": r_vps.text[:200]}
            except Exception as vexc:
                vps_info = {"synced": False, "error": str(vexc)}

        in_sync = bool(vps_info.get("synced")) and (bool(local_data.get("active")) == bool(vps_info.get("active")))
        return json.dumps(
            {
                "ok": True,
                "action": act,
                **local_data,
                "local": local_data,
                "vps": vps_info,
                "vps_synced": bool(vps_info.get("synced")),
                "in_sync": in_sync,
                "message": local_msg or f"Modo Dev local={'ativo' if local_data.get('active') else 'repouso'}, VPS={'ativo' if vps_info.get('active') else 'repouso'}",
            },
            ensure_ascii=False,
            indent=2,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Erro ao consultar/alternar modo-dev híbrido: {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_open_url(url: str) -> str:
    """Open a URL or web shortcut (such as Shopee Affiliates, VPS Control Panel, or any website) directly in a new Google Chrome tab in 1 step.

    Args:
        url: Full URL (https://...) or shortcut ('shopee', 'afiliados', 'painel', 'vps', 'youtube').
    """
    raw = (url or "").strip()
    low = raw.lower()
    env_vals = _read_dotenv_vars()
    vps_url = env_vals.get("VPS_PANEL_URL", "").strip() or "https://187.77.202.176.sslip.io"
    shortcuts = {
        "shopee": "https://affiliate.shopee.com.br/dashboard",
        "shopee afiliados": "https://affiliate.shopee.com.br/dashboard",
        "afiliados": "https://affiliate.shopee.com.br/dashboard",
        "afiliados shopee": "https://affiliate.shopee.com.br/dashboard",
        "painel": vps_url,
        "painel vps": vps_url,
        "vps": vps_url,
        "nuvem": vps_url,
        "estudio": vps_url,
        "estúdio": vps_url,
        "youtube": "https://www.youtube.com",
    }
    if low in shortcuts:
        target_url = shortcuts[low]
    elif "shopee" in low and ("afiliad" in low or "filhad" in low or "dashboard" in low or "partner" in low):
        target_url = "https://affiliate.shopee.com.br/dashboard"
    elif not raw.startswith(("http://", "https://")):
        target_url = f"https://{raw}"
    else:
        target_url = raw

    browser_bin = (
        shutil.which("google-chrome")
        or shutil.which("google-chrome-stable")
        or shutil.which("xdg-open")
    )
    if not browser_bin:
        return json.dumps({"ok": False, "error": "Navegador Google Chrome não encontrado no sistema."}, ensure_ascii=False)

    try:
        subprocess.Popen(
            [browser_bin, target_url],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return json.dumps(
            {
                "ok": True,
                "url": target_url,
                "browser": Path(browser_bin).name,
                "message": f"Nova aba aberta no Google Chrome com sucesso em {target_url}.",
            },
            ensure_ascii=False,
        )
    except Exception as exc:
        return json.dumps({"ok": False, "url": target_url, "error": f"Erro ao abrir navegador: {exc}"}, ensure_ascii=False)


def _fast_scan_local_media(query: str = "", limit: int = 15, max_depth: int = 2, include_images: bool = False) -> list[dict[str, Any]]:
    """Scan local media directories quickly using bounded os.walk (typically < 30ms)."""
    q = (query or "").strip().lower()
    search_dirs = [
        Path.home() / "Videos",
        Path.home() / "Vídeos",
        Path.home() / "Downloads",
        Path.home() / "Desktop",
    ]
    if include_images:
        search_dirs.extend([
            Path.home() / "Imagens",
            Path.home() / "Pictures",
        ])
    exts = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".mp3", ".wav"}
    if include_images:
        exts.update({".jpg", ".jpeg", ".png", ".webp"})
    skip_dirs = {"node_modules", ".venv", "venv", "__pycache__", ".git", ".cache", "site-packages"}
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    for base_dir in search_dirs:
        if not base_dir.is_dir():
            continue
        base_depth = len(base_dir.parts)
        try:
            for root_str, dirnames, filenames in os.walk(base_dir):
                cur_depth = len(Path(root_str).parts) - base_depth
                dirnames[:] = sorted(
                    d for d in dirnames
                    if not d.startswith(".") and d not in skip_dirs and cur_depth < max_depth
                )
                for fname in sorted(filenames):
                    p = Path(root_str) / fname
                    if p.suffix.lower() not in exts:
                        continue
                    full_str = str(p)
                    if full_str in seen:
                        continue
                    if q and q not in fname.lower() and q not in full_str.lower():
                        continue
                    seen.add(full_str)
                    try:
                        st = p.stat()
                    except OSError:
                        continue
                    found.append({
                        "path": full_str,
                        "filename": fname,
                        "ext": p.suffix.lower(),
                        "size_mb": round(st.st_size / (1024 * 1024), 2),
                        "mtime": st.st_mtime,
                        "scp_to_supoclip": f'scp "{full_str}" root@187.77.202.176:/opt/ai-media-studio/supoclip/clips/',
                        "scp_to_moneyprinter": f'scp "{full_str}" root@187.77.202.176:/opt/ai-media-studio/MoneyPrinterTurbo/storage/local_videos/',
                    })
                    if len(found) >= limit * 2:
                        break
                if len(found) >= limit * 2:
                    break
        except Exception:
            continue

    found.sort(key=lambda x: x.get("mtime", 0), reverse=True)
    for item in found[:limit]:
        item.pop("mtime", None)
    return found[:limit]


def _inspect_vps_studio_snapshot() -> dict[str, Any]:
    """Fetch VPS telemetry, recent Gallery outputs, campaigns, and active stack states (< 1s)."""
    _, base_url, headers, auth = _resolve_target_conn("vps")
    results: dict[str, Any] = {"telemetry": {}, "projects": [], "outputs": [], "campaigns": [], "errors": []}

    try:
        # Single keep-alive HTTP/1.1 session reuses the TLS handshake (~0.4-0.8s total)
        with httpx.Client(timeout=5.0, headers=headers, auth=auth) as client:
            r_sys = client.get(f"{base_url}/api/system")
            if r_sys.status_code == 200:
                results["telemetry"] = r_sys.json()
            else:
                results["errors"].append(f"telemetry:HTTP {r_sys.status_code}")

            r_out = client.get(f"{base_url}/api/outputs", params={"stack": "all", "limit": 15})
            if r_out.status_code == 200:
                results["outputs"] = r_out.json().get("items", [])
            else:
                results["errors"].append(f"outputs:HTTP {r_out.status_code}")

            r_cmp = client.get(f"{base_url}/api/campaigns")
            if r_cmp.status_code == 200:
                results["campaigns"] = r_cmp.json().get("campaigns", [])

            if int((results["telemetry"] or {}).get("active_containers") or 0) > 0:
                r_prj = client.get(f"{base_url}/api/projects")
                if r_prj.status_code == 200:
                    results["projects"] = r_prj.json().get("projects", [])
    except Exception as exc:
        results["errors"].append(str(exc))

    return results


_STUDIO_CAPABILITIES_CATALOG: dict[str, dict[str, str]] = {
    "hyperframes": {
        "name": "HyperFrames",
        "kind": "Motor Nativo (0 MB RAM ociosa)",
        "best_for": "Anúncios Motion Graphics 5s–15s em HTML/Canvas/GSAP com cartela de preço, oferta/CTA Shopee e locução PT-BR neural",
        "execution_api": "POST /api/quick/hyperframes (MCP: studio_run_hyperframes)",
        "execution_ui": "Aba Estúdio Rápido -> HyperFrames",
    },
    "moneyprinter": {
        "name": "MoneyPrinterTurbo",
        "kind": "Stack Docker (`moneyprinter` · ligar sob demanda)",
        "best_for": "Vídeos faceless narrados (15s–90s+) com B-roll automático/local/Pexels/Pixabay, roteiro IA e legendas palavra por palavra (`pop_spring`)",
        "execution_api": "POST /api/quick/moneyprinter (MCP: studio_run_moneyprinter)",
        "execution_ui": "/app/moneyprinter/ (UI Streamlit) ou Estúdio Rápido -> MoneyPrinterTurbo",
    },
    "supoclip": {
        "name": "SupoClip",
        "kind": "Stack Docker (`supoclip` · ligar sob demanda)",
        "best_for": "Cortar vídeos longos (podcasts, lives, aulas, filmes/novelas) em partes sequenciais (`novela`/`series`) ou clipes de retenção (`highlights`, `viral`)",
        "execution_api": "POST /api/quick/supoclip + /api/quick/supoclip/upload (MCP: studio_run_supoclip)",
        "execution_ui": "/app/supoclip/ (UI Next.js) ou Estúdio Rápido -> SupoClip",
    },
    "videouse": {
        "name": "Video Use",
        "kind": "Motor Nativo (0 MB RAM ociosa)",
        "best_for": "Limpar silêncios e hesitações (`faster-whisper` INT8 + FFmpeg), normalizar/crossfade 48kHz SoXR, color grading e queimar legendas pós-corte",
        "execution_api": "POST /api/quick/videouse + /api/quick/videouse/upload (MCP: studio_run_videouse)",
        "execution_ui": "Aba Estúdio Rápido -> Video Use",
    },
    "openfilm": {
        "name": "OpenFilm",
        "kind": "Stack Docker (`openfilm` · ligar sob demanda)",
        "best_for": "Editor com timeline multipista assistida por IA, keyframes, motion blur e legendas estilizadas combinando clipes da Galeria",
        "execution_api": "POST /api/quick/openfilm (MCP: studio_control_stack / studio_trigger_job)",
        "execution_ui": "/app/openfilm/",
    },
    "voicestudio": {
        "name": "VoiceStudio",
        "kind": "Stack Docker (`voicestudio` · ligar sob demanda, CPU-only)",
        "best_for": "Locução neural PT-BR isolada (Edge-TTS/eSpeak-ng) com ajuste de timbre, velocidade, tom e masterização 48kHz SoXR",
        "execution_api": "POST /api/quick/voicestudio",
        "execution_ui": "/app/voicestudio/",
    },
    "openhiggsfield": {
        "name": "Open Higgsfield",
        "kind": "Stack Docker (`openhiggsfield` · ligar sob demanda)",
        "best_for": "Geração visual e motion AI avançado (cenas estilizadas de produto, avatar/B-roll cinematográfico)",
        "execution_api": "Start stack via studio_control_stack('openhiggsfield', 'start')",
        "execution_ui": "/app/higgsfield/",
    },
    "agenttube": {
        "name": "AgentTube",
        "kind": "Stack Docker (`agenttube` · ligar sob demanda)",
        "best_for": "Automação completa de canal do YouTube (ideação, roteiro longo, metadados e fluxo de publicação)",
        "execution_api": "Start stack via studio_control_stack('agenttube', 'start')",
        "execution_ui": "/app/agenttube/",
    },
    "openseo": {
        "name": "OpenSEO",
        "kind": "Stack Docker (`openseo` · ligar sob demanda)",
        "best_for": "SEO semântico, pesquisa de palavras-chave, títulos persuasivos, tags e descrições para YouTube e e-commerce",
        "execution_api": "Start stack via studio_control_stack('openseo', 'start')",
        "execution_ui": "/app/openseo/",
    },
    "novela_studio": {
        "name": "Novela Studio · Cinema Clean, Flow Veo 3.1 & Colab GPU",
        "kind": "Motor Nativo v2.5 (0 MB RAM ociosa · Trilha 6)",
        "best_for": "Criar micro-séries multi-personagem estilo plano/contraplano 100% limpo (`clean_cinema_mode=True`) via 3 motores: (1) Pack Google Flow Veo 3.1 (Ingredients 1080x1920 + Prompts Bilíngues + Costurador de Takes), (2) Colab GPU Lip-Sync (EchoMimicV3/LatentSync) ou (3) Preview Rápido VPS.",
        "execution_api": "POST /api/quick/novela/flow-pack + /stitch-takes (redub_with_regional_tts) + /preview-voice + /api/quick/novela + /api/colab/status · MCP: studio_preview_regional_voice / studio_generate_flow_pack / studio_stitch_novela_takes / studio_run_novela",
        "execution_ui": "Aba Estúdio Rápido -> Modo Guiado (Trilha 6) ou Modo Hard -> Novela Studio",
    },
}


def _recommend_studio_tracks(goal: str, format_hint: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """Classify creative/production goals across all Studio tools and build strategic recommendations + discovery questions."""
    text = f"{goal} {format_hint}".lower()
    tracks: list[dict[str, Any]] = []
    questions: list[str] = []

    is_novela_create = any(k in text for k in (
        "novelinha", "causo", "nordest", "dona socorro", "bastião", "bastiao",
        "enzo", "micro-série", "micro-serie", "personagem", "personagens",
        "diálogo", "dialogo", "3d_cartoon", "cinematic_tv", "veo", "google flow",
        "echomimic", "latentsync", "colab", "plano e contraplano", "costurar takes",
    )) or (("novela" in text or "série" in text or "serie" in text) and not any(c in text for c in ("cortar", "fatiar", "vídeo longo", "video longo", "supoclip")))
    is_ad_promo = any(k in text for k in (
        "shopee", "anúncio", "anuncio", "vendas", "venda", "oferta", "promo",
        "desconto", "produto", "e-commerce", "ecommerce", "loja", "afiliado",
        "propaganda", "motion", "hyperframes", "faceless", "moneyprinter",
    ))
    is_long_cut = any(k in text for k in (
        "cortar", "corte", "cortes", "fatiar", "vídeo longo", "video longo", "longo",
        "podcast", "live", "highlights", "virais", "viral", "supoclip", "partes",
    ))

    if is_novela_create:
        tracks.append({
            "track": "Novela Studio v2.5 · Cinema Clean (Google Flow Veo 3.1 / Colab GPU / VPS Preview)",
            "primary": {
                **_STUDIO_CAPABILITIES_CATALOG["novela_studio"],
                "why": "Direção cinematográfica Shot-Reverse-Shot (plano/contraplano por cima do ombro) 100% limpa sem cartões poluindo o rosto. Gera Pack Veo 3.1 (Ingredients + prompts EN/PT + créditos 10/20), costura takes com corte seco via faster-whisper INT8 e integra lip-sync remoto no Google Colab GPU (EchoMimicV3/LatentSync).",
                "recommended_params": {"render_mode": "flow_pack", "visual_style": "3d_cartoon", "clean_cinema_mode": True, "subtitles_mode": "none", "platform_preset": "tiktok_reels"},
            },
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["supoclip"],
                "combo_note": "Se já tiver vídeo longo pronto para fatiar em partes, use SupoClip; para unir os clipes curtos de 6s–8s gerados no Google Flow Veo 3.1 com ritmo de corte seco, use `studio_stitch_novela_takes`.",
            },
        })
        questions.extend([
            "Qual motor prefere: (1) Pack Google Flow Veo 3.1 + Costurador de Takes, (2) Colab GPU Lip-Sync (EchoMimicV3/LatentSync) ou (3) Preview Rápido VPS Clean Cinema?",
            "Qual estilo visual (`3d_cartoon` vs `cinematic_tv`), tema/gírias e quer tela 100% limpa sem texto (`none`) ou legenda minimalista 1 linha (`minimal`)?",
        ])
    is_cleanup_polish = any(k in text for k in (
        "silêncio", "silencio", "silêncios", "silencios", "limpar", "jump-cut",
        "hesitação", "hesitacoes", "cor", "color", "grading", "tratar cor",
        "videouse", "video use", "pós-produção", "pos-producao", "legendar",
    ))
    is_voice = any(k in text for k in ("voz", "locução", "locucao", "narração", "narracao", "dublagem", "voicestudio"))
    is_timeline = any(k in text for k in ("timeline", "multipista", "openfilm", "keyframe", "motion blur", "montagem"))
    is_ai_visual = any(k in text for k in ("higgsfield", "avatar", "cena cinematográfica", "b-roll ia", "gerar cena"))
    is_yt_seo = any(k in text for k in ("youtube", "canal", "agenttube", "openseo", "seo", "palavras-chave", "tags"))

    if is_ad_promo:
        tracks.append({
            "track": "Criação de Anúncio / Vídeo de Vendas (E-Commerce / Shopee)",
            "primary": {
                **_STUDIO_CAPABILITIES_CATALOG["hyperframes"],
                "why": "Gera anúncios rápidos de alta conversão (5s–15s) com cartela animada (preço 'De R$ X por R$ Y', preset `Shopee Flash Sale`), CTA pulsando e locução neural PT-BR sem gastar RAM Docker em repouso.",
                "recommended_params": {"style": "Shopee Flash Sale", "aspect": "9:16", "duration": 8, "voice_enabled": True},
            },
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["moneyprinter"],
                "combo_note": "Alternativa para vídeo explicativo/review mais longo (30s–90s) com B-roll e legenda palavra por palavra (`pop_spring`). Combo avançado: gerar cena visual do produto no Open Higgsfield (`/app/higgsfield/`) ou tratar ritmo/cor final no Video Use (`E-Commerce Vibrant`).",
            },
        })
        questions.extend([
            "Para o vídeo de vendas: qual é o nome do produto, preço/desconto (ex: 'De R$ 129 por R$ 49,90') e CTA desejado?",
            "Você prefere um anúncio visual curto de impacto (5s–15s no HyperFrames) ou um vídeo faceless narrado com B-roll de produto (30s–60s no MoneyPrinterTurbo)? Qual formato (`9:16` vertical Shopee/Reels ou `16:9` horizontal)?",
        ])

    if is_long_cut:
        cut_mode = "novela" if any(w in text for w in ("novela", "série", "serie", "partes", "continuação", "continuacao", "episódio", "episodio")) else "highlights"
        tracks.append({
            "track": "Corte de Vídeo Longo em Partes (Modo Novela / Série / Clipes)",
            "primary": {
                **_STUDIO_CAPABILITIES_CATALOG["supoclip"],
                "why": f"Especialista em fatiar vídeos longos. No modo `{cut_mode}` (`series`), mantém a ordem narrativa sequencial com ganchos de continuação ('Parte 1, Parte 2...') ou extrai os momentos virais.",
                "recommended_params": {"cut_mode": cut_mode, "output_format": "vertical", "part_count": "auto", "burn_subtitles": True, "subtitle_language": "pt"},
            },
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["videouse"],
                "combo_note": "Combo ideal após o corte no SupoClip: rodar os clipes no Video Use (0 MB RAM ociosa) para remover silêncios residuais com `faster-whisper` INT8, normalizar áudio em 48kHz SoXR e aplicar color grading (`Cinema Warm` ou `Neutral Clean`).",
            },
        })
        questions.extend([
            "Para os cortes no SupoClip: o vídeo longo está no seu PC (`~/Videos`/`~/Downloads`), em uma URL, já na Galeria da VPS, ou quer usar nosso vídeo demo `sample` de 135s?",
            "Quantas partes deseja no estilo Novela (`auto` ou 2 a 10), em qual proporção (`9:16` vertical ou `16:9` horizontal) e quer aplicar limpeza de silêncio + cor no Video Use depois?",
        ])

    if is_cleanup_polish and not is_long_cut:
        tracks.append({
            "track": "Limpeza de Silêncios, Áudio 48kHz, Cor & Legendas",
            "primary": {
                **_STUDIO_CAPABILITIES_CATALOG["videouse"],
                "why": "Remove respirações/silêncios automaticamente via `faster-whisper` INT8, normaliza áudio com crossfade 48kHz Estéreo SoXR, aplica color grading (`Neutral Clean`, `Cinema Warm`, `E-Commerce Vibrant`) e grava legendas sincronizadas com 0 MB RAM ociosa.",
            },
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["openfilm"],
                "combo_note": "Se quiser recompor manualmente em timeline multipista, cortes finos ou motion blur após a limpeza, abra no OpenFilm (`/app/openfilm/`).",
            },
        })
        questions.append("Qual preset de cor você prefere (`Neutral Clean`, `Cinema Warm` ou `E-Commerce Vibrant`) e qual arquivo da Galeria/PC vamos processar?")

    if is_voice and not is_ad_promo:
        tracks.append({
            "track": "Locução & Síntese de Voz Neural PT-BR",
            "primary": _STUDIO_CAPABILITIES_CATALOG["voicestudio"],
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["videouse"],
                "combo_note": "Depois da locução pronta, você pode unificar com vídeo/imagem no HyperFrames ou OpenFilm.",
            },
        })
        questions.append("Qual o roteiro falado, voz preferida (`pt-BR-AntonioNeural` ou `pt-BR-FranciscaNeural`) e ritmo (`rate`/`pitch`)?")

    if is_timeline:
        tracks.append({
            "track": "Edição em Timeline Multipista com IA",
            "primary": _STUDIO_CAPABILITIES_CATALOG["openfilm"],
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["videouse"],
                "combo_note": "Ideal combinar clipes pré-cortados no SupoClip ou tratados no Video Use direto na timeline do OpenFilm.",
            },
        })

    if is_ai_visual and not is_ad_promo:
        tracks.append({
            "track": "Geração Visual & Motion AI Cinematográfico",
            "primary": _STUDIO_CAPABILITIES_CATALOG["openhiggsfield"],
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["hyperframes"],
                "combo_note": "Exporte a cena gerada para compor oferta/CTA no HyperFrames ou editar na timeline do OpenFilm.",
            },
        })

    if is_yt_seo:
        tracks.append({
            "track": "Crescimento de Canal YouTube & SEO",
            "primary": _STUDIO_CAPABILITIES_CATALOG["agenttube"],
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["openseo"],
                "combo_note": "Combine OpenSEO (`/app/openseo/`) para pesquisa semântica/tags + MoneyPrinterTurbo ou AgentTube (`/app/agenttube/`) para produção e publicação.",
            },
        })
        questions.append("O foco é otimizar título/tags/SEO no OpenSEO ou automatizar roteiro/upload de canal completo no AgentTube?")

    if not tracks:
        tracks.append({
            "track": "Estratégia Audiovisual Sob Medida",
            "primary": _STUDIO_CAPABILITIES_CATALOG["hyperframes"],
            "alternative_or_combo": {
                **_STUDIO_CAPABILITIES_CATALOG["moneyprinter"],
                "combo_note": "Se envolver vídeo já gravado para cortar, use SupoClip (`/app/supoclip/`); se envolver limpeza/cor, use Video Use; se for montagem em timeline, use OpenFilm.",
            },
        })
        questions.extend([
            "Você já possui um vídeo bruto para cortar/editar ou vamos criar do zero (motion ad curto ou vídeo narrado)?",
            "Qual formato final deseja (`9:16` Reels/TikTok/Shorts/Shopee ou `16:9` YouTube/Desktop) e duração aproximada?",
        ])

    return tracks, questions[:3]


@mcp.tool()
def studio_plan_production(
    goal: str,
    format_hint: str = "",
    inspect_media: bool = True,
) -> str:
    """Analyze a creative/production audiovisual goal against all 9 Studio tools, inspect local/VPS media and free VPS RAM in <1s, and return a strategic plan with primary/combo tools, discovery questions, and execution options (API vs UI).

    ALWAYS invoke this tool FIRST before starting stacks or triggering jobs when the user asks to create, edit, cut, polish, or plan any video/audio workflow.

    Args:
        goal: User's creative objective (e.g. 'criar anúncio para Shopee e cortar vídeo longo estilo novela').
        format_hint: Optional aspect/duration/channel hint (e.g. '9:16', '5 partes', 'YouTube').
        inspect_media: True (default) to scan local folders + VPS Galeria and VPS RAM/stacks live.
    """
    import time as _time

    t0 = _time.monotonic()
    local_files: list[dict[str, Any]] = []
    vps_snap: dict[str, Any] = {"telemetry": {}, "projects": [], "outputs": [], "campaigns": [], "errors": []}

    want_images = any(k in f"{goal} {format_hint}".lower() for k in (
        "shopee", "tiktok", "produto", "anúncio", "anuncio", "kit", "foto", "imagem", "elseve", "campanha", "pov"
    ))

    if inspect_media:
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            f_local = pool.submit(_fast_scan_local_media, "", 16, 2, want_images)
            f_vps = pool.submit(_inspect_vps_studio_snapshot)
            try:
                local_files = f_local.result(timeout=2.0)
            except Exception:
                local_files = []
            try:
                vps_snap = f_vps.result(timeout=4.5)
            except Exception as exc:
                vps_snap["errors"].append(str(exc))

    tel = vps_snap.get("telemetry") or {}
    projects = vps_snap.get("projects") or []
    vps_outputs = vps_snap.get("outputs") or []
    vps_campaigns = vps_snap.get("campaigns") or []
    active_stacks = [
        {"id": p.get("id"), "name": p.get("name"), "status": p.get("status")}
        for p in projects
        if p.get("status") not in ("stopped", "standby", None) or (p.get("running_containers") or 0) > 0
    ]

    tracks, discovery_questions = _recommend_studio_tracks(goal, format_hint)
    elapsed_ms = round((_time.monotonic() - t0) * 1000, 1)

    shopee_director_blueprint = None
    if want_images:
        shopee_director_blueprint = {
            "framework": "Shopee Video Viral & POV Director (15s–18s · 9:16 1080x1920)",
            "structure": [
                {"slot": "0s–3s", "role": "Hook Visual Implacável", "rule": "Abrir direto com Antes vs. Depois real ou dor aguda (ex: cabelo armado/frizz). NUNCA começar com logo ou saudação."},
                {"slot": "3s–7s", "role": "POV & Benefícios Tangíveis", "rule": "Close do produto com selos animados: Controle de Frizz, Redução de Pontas Duplas, Proteção Térmica 230°C e 1 Semana de Brilho (6 Óleos Preciosos)."},
                {"slot": "7s–11s", "role": "Quebra de Objeção", "rule": "Nova Embalagem, Mesma Fórmula Original L'Oréal — Nutre 10x mais sem pesar nos fios."},
                {"slot": "11s–16s+", "role": "Ancoragem de Preço & CTA Carrinho Laranja", "rule": "Psicologia da divisão (ex: Kit com 2 por R$ 69,99 = R$ 34,99 cada) + seta animada no canto inferior esquerdo apontando para o Carrinho Laranja."},
            ],
            "anti_confusion_rule": "Em anúncios de Kit (ex: Kit 2 Óleos), filtrar fotos que mostram a linha inteira com Shampoo/Condicionador/Máscara (4–6 produtos) e prints internos de afiliado.",
            "shopee_safe_zone_1080x1920": {
                "top_dead_zone_y": "0–215px (Barra Pesquisar + status bar Android — sem títulos/fotos)",
                "product_stage": "Y: 220–960px, X: 60–910px (título top:220px, palco 285–960px com banner de preço Cena 4 integrado na base)",
                "subtitle_corridor": "Y: 985–1100px, X: 60–900px (máx. 5–6 palavras/bloco, MarginR 180px livre dos ícones)",
                "cta_pointer": "Y: 1110–1165px, X: 60px ('Toque em Comprar Abaixo com Desconto!') apontando para o Card Branco da Shopee em Y=1180px",
                "bottom_reserved_app_zone": "Y: 1175–1920px e X: 910–1080px (Y>=1020px) 100% livres (Card Branco, Carrinho Laranja, @descrição e ícones)",
            },
            "audio_and_safe_zone_rule": "Locução feminina PT-BR persuasiva (`pt-BR-FranciscaNeural` ou `pt-BR-ThalitaMultilingualNeural`, rate `+12%`) em 48kHz Estéreo SoXR ajustando a duração do vídeo ao áudio sem cortar palavras; legendas estritamente em Y: 985–1100px.",
        }

    upload_cmd_hint = (
        local_files[0]["scp_to_supoclip"]
        if local_files
        else 'scp ~/Videos/seu_video.mp4 root@187.77.202.176:/opt/ai-media-studio/supoclip/clips/'
    )

    seen_vps_gallery: set[tuple[str, str]] = set()
    unique_vps_gallery: list[dict[str, Any]] = []
    for it in vps_outputs:
        key = (str(it.get("filename") or ""), str(it.get("size_human") or ""))
        if key in seen_vps_gallery:
            continue
        seen_vps_gallery.add(key)
        unique_vps_gallery.append({
            "id": it.get("id"),
            "stack": it.get("stack"),
            "filename": it.get("filename"),
            "size": it.get("size_human"),
            "media_type": it.get("media_type"),
        })
        if len(unique_vps_gallery) >= 8:
            break

    plan = {
        "ok": True,
        "elapsed_ms": elapsed_ms,
        "goal": goal,
        "format_hint": format_hint,
        "vps_environment": {
            "ram_available_gb": tel.get("ram_available_gb"),
            "ram_total_gb": tel.get("ram_total_gb"),
            "cpu_percent": tel.get("cpu_percent"),
            "disk_free_gb": tel.get("disk_free_gb"),
            "active_containers": tel.get("active_containers", 0),
            "active_docker_stacks": active_stacks,
            "ram_safety_rule": "VPS com 8 GB RAM: manter no máximo 1 stack Docker pesada ligada por vez (`0/7` em standby). HyperFrames e Video Use consomem 0 MB RAM ociosa.",
        },
        "media_inventory": {
            "local_pc_count": len(local_files),
            "local_pc_files": local_files[:8],
            "vps_gallery_count": len(unique_vps_gallery),
            "vps_gallery_recent": unique_vps_gallery,
            "upload_command_scp": upload_cmd_hint,
            "mcp_upload_helper": "studio_stage_or_upload_video(local_path='~/Videos/seu_video.mp4', stack_name='supoclip')",
        },
        "vps_campaigns": vps_campaigns,
        "shopee_director_blueprint": shopee_director_blueprint,
        "recommended_tracks": tracks,
        "all_9_studio_capabilities": list(_STUDIO_CAPABILITIES_CATALOG.keys()),
        "discovery_questions": discovery_questions,
        "execution_choices": {
            "option_api_mcp": "Automático via API/MCP aqui com o Jarvis (eu configuro os parâmetros, controlo 1 stack por vez e entrego o MP4 pronto na Galeria).",
            "option_web_ui": "Manual pela Interface Web na VPS (`/app/supoclip/`, `/app/moneyprinter/`, `/app/openfilm/`, etc. ou aba Estúdio Rápido).",
        },
        "consultative_rule": "Responda comparando ferramenta principal vs alternativa/combo, cite as mídias e RAM reais detectadas, faça as perguntas de descoberta e pergunte se prefere execução via API/MCP ou UI Web antes de ligar contêineres.",
    }
    return json.dumps(plan, ensure_ascii=False, indent=2)


@mcp.tool()
def studio_scan_media(query: str = "", target: str = "both") -> str:
    """Scan local PC folders (~/Videos, ~/Downloads, ~/Desktop) and VPS media storage for video/audio files, returning exact paths and ready SSH/SCP commands.

    Args:
        query: Optional filename or keyword filter.
        target: 'local', 'vps', or 'both' (default).
    """
    local_files = _fast_scan_local_media(query=query, limit=20, max_depth=3) if target in ("local", "both") else []

    vps_outputs = []
    vps_supoclip = {}
    if target in ("vps", "both"):
        try:
            _, base_url, headers, auth = _resolve_target_conn("vps")
            with httpx.Client(timeout=15.0, headers=headers, auth=auth) as client:
                r_out = client.get(f"{base_url}/api/outputs", params={"stack": "all", "limit": 15})
                if r_out.status_code == 200:
                    vps_outputs = r_out.json().get("items", [])
                r_sc = client.get(f"{base_url}/api/v1/clips")
                if r_sc.status_code == 200:
                    vps_supoclip = r_sc.json()
        except Exception:
            pass

    return json.dumps(
        {
            "ok": True,
            "local_media_count": len(local_files),
            "local_media": local_files,
            "vps_gallery_count": len(vps_outputs),
            "vps_gallery": [
                {"stack": x.get("stack"), "filename": x.get("filename"), "size": x.get("size_human"), "relpath": x.get("relpath")}
                for x in vps_outputs[:10]
            ],
            "supoclip_cut_modes": vps_supoclip.get("cut_modes", []),
            "ssh_quick_hint": "Se o vídeo estiver no PC local, use studio_stage_or_upload_video(local_path=...) ou envie via scp para root@187.77.202.176:/opt/ai-media-studio/supoclip/clips/.",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def studio_stage_or_upload_video(
    local_path: str = "",
    stack_name: str = "supoclip",
    use_vps_demo_if_missing: bool = True,
) -> str:
    """Upload a local video file from the PC to the VPS (via HTTPS API or SCP) or stage a 135s VPS sample video if no local file exists.

    Args:
        local_path: Path to local video file (e.g. '~/Videos/aula.mp4'). Leave empty to auto-stage VPS sample video.
        stack_name: 'supoclip' or 'moneyprinter'.
        use_vps_demo_if_missing: If true and local_path is empty/not found, generates and stages a 135s sample video on the VPS.
    """
    stack_id = _resolve_stack_id(stack_name)
    _, base_url, headers, auth = _resolve_target_conn("vps")
    expanded = Path(os.path.expanduser(local_path.strip())) if local_path and local_path.strip() else None

    if expanded and expanded.is_file():
        try:
            if stack_id == "supoclip":
                with httpx.Client(timeout=180.0, headers=headers, auth=auth) as client:
                    with expanded.open("rb") as fh:
                        resp = client.post(
                            f"{base_url}/api/quick/supoclip/upload",
                            files={"video": (expanded.name, fh, "video/mp4")},
                        )
                    resp.raise_for_status()
                    data = resp.json()
                return json.dumps(
                    {
                        "ok": True,
                        "mode": "uploaded_local_file",
                        "local_path": str(expanded),
                        "video_path": data.get("video_path"),
                        "message": f"Arquivo local '{expanded.name}' enviado com sucesso para o SupoClip na VPS!",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            else:
                remote_dir = "/opt/ai-media-studio/MoneyPrinterTurbo/storage/local_videos/"
                subprocess.run(
                    ["scp", "-o", "BatchMode=yes", str(expanded), f"root@187.77.202.176:{remote_dir}"],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=90,
                )
                return json.dumps(
                    {
                        "ok": True,
                        "mode": "scp_to_vps",
                        "local_path": str(expanded),
                        "vps_path": f"{remote_dir}{expanded.name}",
                        "message": f"Arquivo '{expanded.name}' transferido via SCP para MoneyPrinterTurbo na VPS!",
                    },
                    ensure_ascii=False,
                    indent=2,
                )
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"Falha ao enviar '{expanded}': {exc}"}, ensure_ascii=False)

    if use_vps_demo_if_missing and stack_id == "supoclip":
        try:
            with httpx.Client(timeout=90.0, headers=headers, auth=auth) as client:
                resp = client.post(f"{base_url}/api/quick/supoclip/upload?sample=1")
                resp.raise_for_status()
                data = resp.json()
            return json.dumps(
                {
                    "ok": True,
                    "mode": "vps_demo_sample",
                    "video_path": data.get("video_path"),
                    "filename": data.get("filename"),
                    "scp_hint": "Para usar um vídeo próprio do PC: scp ~/Videos/seu_video.mp4 root@187.77.202.176:/opt/ai-media-studio/supoclip/clips/",
                    "message": data.get("message", "Vídeo demo de 135s preparado na VPS!"),
                },
                ensure_ascii=False,
                indent=2,
            )
        except Exception as exc:
            return json.dumps({"ok": False, "error": f"Erro ao preparar vídeo demo no SupoClip: {exc}"}, ensure_ascii=False)

    return json.dumps(
        {
            "ok": False,
            "error": f"Arquivo '{local_path}' não encontrado localmente.",
            "scp_command": "scp ~/Videos/seu_video.mp4 root@187.77.202.176:/opt/ai-media-studio/supoclip/clips/",
        },
        ensure_ascii=False,
        indent=2,
    )


@mcp.tool()
def studio_run_supoclip(
    source: str = "sample",
    cut_mode: str = "novela",
    output_format: str = "vertical",
    subtitle_language: str = "pt",
    burn_subtitles: bool = True,
    part_count: str = "auto",
    max_clips: int = 5,
    instructions: str = "",
) -> str:
    """Dispatch an intelligent video cutting job on SupoClip in the VPS with full support for Novela (series continuation), Highlights, or Viral Shorts.

    Args:
        source: Video URL (https://...), 'upload://...', local PC path ('~/Videos/...'), or 'sample' (uses 135s VPS demo video).
        cut_mode: 'novela' (Estilo Novela com Continuação), 'highlights' (Melhores Momentos), 'viral' (Cortes Curtos Virais), 'topic', 'recap', or 'trailer'.
        output_format: 'vertical' (9:16), 'square' (1:1), or 'horizontal' (16:9).
        subtitle_language: 'pt', 'en', 'es', or 'auto'.
        burn_subtitles: True to burn subtitles into the video.
        part_count: 'auto' or '2'..'10' (for 'novela' mode).
        max_clips: Max clips (1..10) for highlights/viral modes.
        instructions: Custom AI director instructions.
    """
    _, base_url, headers, auth = _resolve_target_conn("vps")
    src_val = (source or "sample").strip()
    source_mode = "url"

    if src_val.lower() in ("sample", "demo", "auto", ""):
        source_mode = "sample"
        src_val = "sample"
    else:
        local_candidate = Path(os.path.expanduser(src_val))
        if local_candidate.is_file():
            up_res = json.loads(studio_stage_or_upload_video(str(local_candidate), "supoclip", False))
            if not up_res.get("ok"):
                return json.dumps(up_res, ensure_ascii=False, indent=2)
            src_val = up_res["video_path"]
            source_mode = "upload"

    payload = {
        "source_url": src_val,
        "source_mode": source_mode,
        "cut_mode": cut_mode.strip().lower(),
        "output_format": output_format,
        "processing_mode": "fast",
        "subtitle_language": subtitle_language,
        "add_subtitles": burn_subtitles,
        "part_count": part_count,
        "max_clips": max_clips,
        "instructions": instructions,
        "auto_start": True,
    }
    try:
        with httpx.Client(timeout=120.0, headers=headers, auth=auth) as client:
            resp = client.post(f"{base_url}/api/quick/supoclip", json=payload)
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": data}, ensure_ascii=False, indent=2)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao acionar SupoClip na VPS: {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_run_moneyprinter(
    video_subject: str,
    video_script: str = "",
    video_terms: str = "",
    voice_name: str = "pt-BR-AntonioNeural-Male",
    video_aspect: str = "9:16",
    video_source: str = "auto",
    broll_style: str = "grid_emerald",
    subtitle_display_mode: str = "word_by_word",
    subtitle_animation: str = "pop_spring",
) -> str:
    """Dispatch a full MoneyPrinterTurbo faceless/Shopee video job on the VPS with voice, B-roll style, keywords, and dynamic subtitles.

    Args:
        video_subject: Product or topic of the video.
        video_script: Optional narration script (if empty, AI generates it).
        video_terms: Optional comma-separated B-roll search keywords.
        voice_name: Neural voice (e.g. 'pt-BR-AntonioNeural-Male', 'pt-BR-FranciscaNeural-Female').
        video_aspect: '9:16', '16:9', or '1:1'.
        video_source: 'auto', 'local', 'pexels', or 'pixabay'.
        broll_style: 'grid_emerald', 'cinema_dark', 'cyber_cyan', or 'warm_amber'.
        subtitle_display_mode: 'word_by_word' or 'sentence'.
        subtitle_animation: 'pop_spring' or 'none'.
    """
    _, base_url, headers, auth = _resolve_target_conn("vps")
    payload = {
        "video_subject": video_subject,
        "video_script": video_script,
        "video_terms": video_terms,
        "voice_name": voice_name,
        "video_aspect": video_aspect,
        "video_source": video_source,
        "broll_style": broll_style,
        "subtitle_enabled": True,
        "subtitle_display_mode": subtitle_display_mode,
        "subtitle_animation": subtitle_animation,
        "auto_start": True,
    }
    try:
        with httpx.Client(timeout=90.0, headers=headers, auth=auth) as client:
            resp = client.post(f"{base_url}/api/quick/moneyprinter", json=payload)
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": data}, ensure_ascii=False, indent=2)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao acionar MoneyPrinterTurbo na VPS: {exc}"}, ensure_ascii=False)


def _poll_quick_task(client: httpx.Client, url: str, timeout_s: float = 120.0) -> Dict[str, Any]:
    import time as _time
    deadline = _time.monotonic() + timeout_s
    last_data: Dict[str, Any] = {}
    while _time.monotonic() < deadline:
        resp = client.get(url)
        if resp.status_code >= 400:
            return {"ok": False, "status_code": resp.status_code, "detail": resp.text[:400]}
        last_data = resp.json()
        if last_data.get("ready") or last_data.get("failed"):
            return last_data
        _time.sleep(2.5)
    return last_data


@mcp.tool()
def studio_run_videouse(
    source: str = "sample",
    grade_preset: str = "Neutral Clean",
    remove_silences: bool = True,
    burn_subtitles: bool = True,
    language: str = "pt",
    wait_for_completion: bool = True,
    target: str = "vps",
) -> str:
    """Clean silences/hesitations, apply 48kHz Stereo SoXR fades, color grade, and burn subtitles using zero-cost Video Use (faster-whisper INT8 + FFmpeg).

    Args:
        source: 'sample' (demo video), a Galeria output_id or filename, or a local PC path ('~/Videos/clip.mp4').
        grade_preset: 'Neutral Clean', 'Cinema Warm', or 'E-Commerce Vibrant'.
        remove_silences: True to automatically cut dead silences and fillers.
        burn_subtitles: True to burn synchronized output-timeline subtitles.
        language: 'pt', 'en', 'es', or 'auto'.
        wait_for_completion: True to poll until the finished MP4 is ready in Galeria & Downloads.
        target: 'vps' (default) or 'local'.
    """
    _, base_url, headers, auth = _resolve_target_conn(target)
    src_val = (source or "sample").strip()
    output_id = ""
    uploaded_path = ""
    use_sample = src_val.lower() in ("sample", "demo", "auto", "")

    try:
        with httpx.Client(timeout=120.0, headers=headers, auth=auth) as client:
            if not use_sample:
                local_cand = Path(os.path.expanduser(src_val))
                if local_cand.is_file():
                    with local_cand.open("rb") as fh:
                        up_r = client.post(
                            f"{base_url}/api/quick/videouse/upload",
                            files={"video": (local_cand.name, fh, "video/mp4")},
                        )
                    up_r.raise_for_status()
                    uploaded_path = up_r.json().get("video_path", "")
                elif ":" in src_val and src_val.count(":") >= 2:
                    output_id = src_val
                else:
                    outs_r = client.get(f"{base_url}/api/outputs?limit=150")
                    outs_r.raise_for_status()
                    items = outs_r.json().get("items", [])
                    match = next(
                        (
                            it for it in items
                            if it.get("media_type") == "video"
                            and src_val.lower() in f"{it.get('filename', '')} {it.get('relpath', '')}".lower()
                        ),
                        None,
                    )
                    if not match:
                        return json.dumps({"ok": False, "error": f"Vídeo '{src_val}' não encontrado localmente nem na Galeria."}, ensure_ascii=False)
                    output_id = match["id"]

            resp = client.post(
                f"{base_url}/api/quick/videouse",
                json={
                    "output_id": output_id,
                    "uploaded_path": uploaded_path,
                    "sample": use_sample and not output_id and not uploaded_path,
                    "grade_preset": grade_preset,
                    "remove_silences": remove_silences,
                    "burn_subtitles": burn_subtitles,
                    "language": language,
                    "wait": False,
                },
            )
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": data}, ensure_ascii=False, indent=2)

            if wait_for_completion and data.get("task_id"):
                done = _poll_quick_task(client, f"{base_url}/api/quick/videouse/task/{data['task_id']}", timeout_s=120.0)
                return json.dumps(done, ensure_ascii=False, indent=2)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao acionar Video Use: {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_run_hyperframes(
    title: str,
    offer: str = "",
    cta: str = "Garanta o Seu Agora",
    style: str = "L'Oréal Gold Luxury",
    aspect: str = "9:16",
    duration: float = 16.0,
    voice_enabled: bool = True,
    voice_name: str = "pt-BR-FranciscaNeural",
    voice_script: str = "",
    image_paths: str = "",
    campaign: str = "",
    extra_price_note: str = "",
    orange_cart_cta: str = "",
    subtitles: bool = True,
    wait_for_completion: bool = True,
    target: str = "vps",
) -> str:
    """Render a Single or Multi-Image POV Motion Graphics Promo Ad MP4 using HyperFrames (HTML/Canvas/GSAP + Chromium Headless) and synchronized PT-BR 48kHz SoXR voiceover.

    Args:
        title: Product or campaign headline.
        offer: Price or offer highlight (e.g. 'KIT LEVE 2 (200ml) · R$ 69,99').
        cta: Call to action button label.
        style: 'L\'Oréal Gold Luxury', 'Shopee Flash Sale', 'B2B Emerald', 'Cyber Neon', or 'Minimal Luxury'.
        aspect: '9:16' (Vertical) or '16:9' (Horizontal).
        duration: Target minimum duration in seconds (5 to 30; automatically expanded if full narration is longer).
        voice_enabled: True to synthesize PT-BR neural narration without truncating audio.
        voice_name: Edge-TTS voice name (e.g. 'pt-BR-FranciscaNeural', 'pt-BR-ThalitaMultilingualNeural', 'pt-BR-AntonioNeural').
        voice_script: Optional custom narration script.
        image_paths: Optional JSON array or comma-separated VPS image paths (e.g. from /opt/ai-media-studio/campaigns/elseve-kit2/).
        campaign: Optional VPS campaign slug (e.g. 'elseve-kit2') to load curated non-excluded images automatically.
        extra_price_note: Optional price-division psychology highlight (e.g. 'Sai R$ 34,99 cada frasco!').
        orange_cart_cta: Optional lower-left Shopee orange cart prompt (e.g. 'Toque no Carrinho Laranja Abaixo!').
        subtitles: True to render dynamic captions in the central safe zone.
        wait_for_completion: True to poll until the finished MP4 is ready in Galeria & Downloads.
        target: 'vps' (default) or 'local'.
    """
    _, base_url, headers, auth = _resolve_target_conn(target)
    parsed_images: list[str] = []
    if image_paths and image_paths.strip():
        raw_ip = image_paths.strip()
        if raw_ip.startswith("["):
            try:
                loaded = json.loads(raw_ip)
                if isinstance(loaded, list):
                    parsed_images = [str(x).strip() for x in loaded if str(x).strip()]
            except Exception:
                pass
        if not parsed_images:
            parsed_images = [p.strip() for p in raw_ip.split(",") if p.strip()]

    try:
        with httpx.Client(timeout=210.0, headers=headers, auth=auth) as client:
            resp = client.post(
                f"{base_url}/api/quick/hyperframes",
                json={
                    "title": title,
                    "offer": offer,
                    "cta": cta,
                    "style": style,
                    "aspect": aspect,
                    "duration": max(5.0, min(30.0, float(duration))),
                    "voice_enabled": voice_enabled,
                    "voice_name": voice_name,
                    "voice_script": voice_script,
                    "image_paths": parsed_images,
                    "campaign": campaign.strip(),
                    "extra_price_note": extra_price_note.strip(),
                    "orange_cart_cta": orange_cart_cta.strip(),
                    "subtitles": subtitles,
                    "quality": "draft",
                    "wait": False,
                },
            )
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": data}, ensure_ascii=False, indent=2)

            if wait_for_completion and data.get("task_id"):
                done = _poll_quick_task(client, f"{base_url}/api/quick/hyperframes/task/{data['task_id']}", timeout_s=240.0)
                return json.dumps(done, ensure_ascii=False, indent=2)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao acionar HyperFrames: {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_analyze_viral_references(
    campaign_id: str,
    product_title: str = "",
    product_price: str = "",
    product_description: str = "",
    viral_video_paths: str = "",
    target: str = "vps",
) -> str:
    """Reverse-engineer 1 or more Shopee viral reference MP4 videos (faster-whisper INT8 + scene cuts + WPM + audio energy + AI Cascade) into viral_blueprint.json.

    Args:
        campaign_id: Campaign slug under /opt/ai-media-studio/campaigns/ (e.g. 'elseve-kit2').
        product_title: Title of our product to tailor the viral blueprint.
        product_price: Price/offer string (e.g. 'R$ 69,99 (R$ 34,99 cada)').
        product_description: Key benefits or description of our product.
        viral_video_paths: Optional local path(s) (comma-separated) to upload to campaigns/{campaign_id}/virals/ before analyzing.
        target: 'vps' (default) or 'local'.
    """
    _, base_url, headers, auth = _resolve_target_conn(target)
    cid = (campaign_id or "").strip()
    if not cid:
        return json.dumps({"ok": False, "error": "Informe campaign_id"}, ensure_ascii=False)
    try:
        with httpx.Client(timeout=240.0, headers=headers, auth=auth) as client:
            if viral_video_paths and viral_video_paths.strip():
                raw_paths = [p.strip() for p in viral_video_paths.split(",") if p.strip()]
                local_files = [Path(os.path.expanduser(p)) for p in raw_paths if Path(os.path.expanduser(p)).is_file()]
                if local_files:
                    open_handles = []
                    try:
                        multi_files = []
                        for lf in local_files:
                            fh = lf.open("rb")
                            open_handles.append(fh)
                            multi_files.append(("files", (lf.name, fh, "video/mp4")))
                        up = client.post(f"{base_url}/api/campaigns/{cid}/upload-virals", files=multi_files)
                        up.raise_for_status()
                    finally:
                        for fh in open_handles:
                            try:
                                fh.close()
                            except Exception:
                                pass
            resp = client.post(
                f"{base_url}/api/campaigns/{cid}/analyze-virals",
                json={
                    "product_title": product_title,
                    "product_price": product_price,
                    "product_description": product_description,
                },
            )
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": data}, ensure_ascii=False, indent=2)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha ao analisar referências virais: {exc}"}, ensure_ascii=False)


@mcp.tool()
def studio_run_guided_pipeline(
    template_id: str,
    platform_preset: str = "shopee_20_9",
    params_json: str = "{}",
    wait_for_completion: bool = True,
    target: str = "vps",
) -> str:
    """Run one of the 5 Guided Pipelines with automatic 1-stack-at-a-time Docker RAM relay and checkpoints.

    Args:
        template_id: 'shopee_viral_clone', 'novela_shorts_polish', 'dark_channel_full', 'pov_cinema_ad', or 'express_jumpcut'.
        platform_preset: 'shopee_20_9' (default), 'tiktok_reels', 'yt_shorts', or 'yt_16_9'.
        params_json: JSON object string of mission parameters (e.g. '{"campaign_id":"elseve-kit2","title":"...","offer":"..."}').
        wait_for_completion: True to poll /api/pipelines/status/{run_id} until completed or error.
        target: 'vps' (default) or 'local'.
    """
    _, base_url, headers, auth = _resolve_target_conn(target)
    try:
        parsed_params = json.loads(params_json or "{}")
        if not isinstance(parsed_params, dict):
            parsed_params = {}
    except Exception:
        parsed_params = {}

    try:
        with httpx.Client(timeout=120.0, headers=headers, auth=auth) as client:
            resp = client.post(
                f"{base_url}/api/pipelines/start",
                json={
                    "template_id": template_id,
                    "platform_preset": platform_preset,
                    "params": parsed_params,
                    "wait": False,
                },
            )
            data = resp.json()
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": data}, ensure_ascii=False, indent=2)
            run_id = data.get("run_id") or ""
            if wait_for_completion and run_id:
                import time as _time
                deadline = _time.monotonic() + 300.0
                while _time.monotonic() < deadline:
                    st = client.get(f"{base_url}/api/pipelines/status/{run_id}")
                    if st.status_code < 400:
                        data = st.json()
                        if data.get("ready") or data.get("failed"):
                            break
                    _time.sleep(3.0)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"Falha na trilha guiada: {exc}"}, ensure_ascii=False)


@mcp.tool()
def webcam_look(
    prompt: str = "Descreva detalhadamente o objeto, produto ou cena que estou segurando na câmera."
) -> str:
    """Capture 1 frame on demand from /dev/video0, release the camera immediately, and describe it with Gemini 2.5 Flash.

    ONLY invoke this tool when the user explicitly asks to look at the webcam or identify something they are holding.
    """
    from webcam_vision import capture_and_describe

    res = capture_and_describe(prompt=prompt)
    return json.dumps(res, ensure_ascii=False, indent=2)


@mcp.tool()
def studio_run_novela(
    title: str = "Causos do Sertão — Episódio 01",
    visual_style: str = "3d_cartoon",
    platform_preset: str = "tiktok_reels",
    theme: str = "Enzo da capital confundindo gírias nordestinas na bodega de Seu Bastião",
    expressions_csv: str = "Oxente, Morreu Maria Preá, Botar Boneco, Deixe de cunversa",
    tone: str = "Comédia Leve",
    dialogue_json: str = "",
    dictionary_json: str = "",
    clean_cinema_mode: bool = True,
    subtitles_mode: str = "none",
    render_mode: str = "vps_preview",
    colab_engine: str = "echomimic_v3",
    accent_engine: str = "nordestino_raiz",
    quality: str = "standard",
    output_filename: str = "",
    wait: bool = True,
    poll_timeout_s: float = 600.0,
) -> str:
    """Produce a multi-character vertical mini-novela / regional comedy episode (Clean Cinema Shot-Reverse-Shot, Flow Pack, or Colab GPU Lip-Sync) with authentic Brazilian Northeast accent.

    Args:
        title: Episode title.
        visual_style: Visual aesthetic: '3d_cartoon' or 'cinematic_tv'.
        platform_preset: Safe-Zone target: 'tiktok_reels', 'yt_shorts', or 'shopee_20_9'.
        theme: Storyline/theme if dialogue_json is omitted.
        expressions_csv: Comma-separated regional expressions to feature.
        tone: Comedy/drama tone.
        dialogue_json: Optional JSON array of turns (supports optional 'guide_audio_path' per line).
        dictionary_json: Optional JSON array of dictionary items.
        clean_cinema_mode: True (default) for 100% clean full-bleed cinema without cluttering dialogue boxes/badges.
        subtitles_mode: 'none' (default clean screen) or 'minimal' (thin white outline line).
        render_mode: 'vps_preview' (default), 'flow_pack' (Veo 3.1 Prompts + Ingredients), or 'colab_gpu' (EchoMimicV3/LatentSync).
        colab_engine: When render_mode='colab_gpu': 'echomimic_v3' or 'latentsync'.
        accent_engine: Regional voice engine: 'nordestino_raiz' (Gemini Native TTS + SoXR 48kHz default), 'f5_tts_clone' (Colab F5-TTS-PT-BR), or 'seed_vc_guide' (Colab Seed-VC).
        quality: 'draft' or 'standard' (default).
        output_filename: Optional filename under /opt/ai-media-studio/exports/novela/.
        wait: Poll until completion (True default).
        poll_timeout_s: Max poll wait seconds (default 600s).
    """
    _, base_url, headers, auth = _resolve_target_conn("vps")
    expr_list = [x.strip() for x in (expressions_csv or "").split(",") if x.strip()]
    parsed_dialogue: list[dict[str, Any]] = json.loads(dialogue_json) if dialogue_json and dialogue_json.strip() else []
    parsed_dict: list[dict[str, str]] = json.loads(dictionary_json) if dictionary_json and dictionary_json.strip() else []

    body = {
        "title": title,
        "visual_style": visual_style if visual_style in ("3d_cartoon", "cinematic_tv") else "3d_cartoon",
        "platform_preset": platform_preset,
        "theme": theme,
        "expressions": expr_list,
        "tone": tone,
        "dialogue": parsed_dialogue,
        "dictionary": parsed_dict,
        "clean_cinema_mode": bool(clean_cinema_mode),
        "subtitles_mode": subtitles_mode if subtitles_mode in ("none", "minimal") else "none",
        "render_mode": render_mode if render_mode in ("vps_preview", "flow_pack", "colab_gpu") else "vps_preview",
        "colab_engine": colab_engine if colab_engine in ("echomimic_v3", "latentsync") else "echomimic_v3",
        "accent_engine": accent_engine if accent_engine in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide") else "nordestino_raiz",
        "quality": quality if quality in ("draft", "looks", "delivery", "standard", "high") else "standard",
        "output_filename": output_filename,
        "wait": False,
    }
    try:
        with httpx.Client(timeout=30.0, headers=headers, auth=auth) as client:
            resp = client.post(f"{base_url}/api/quick/novela", json=body)
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": resp.text[:400]}, ensure_ascii=False)
            data = resp.json()
            if wait and data.get("task_id"):
                data = _poll_quick_task(client, f"{base_url}/api/quick/novela/task/{data['task_id']}", timeout_s=poll_timeout_s)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def studio_generate_flow_pack(
    title: str = "Causos do Sertão — Episódio 01",
    visual_style: str = "3d_cartoon",
    theme: str = "Enzo da capital confundindo gírias nordestinas na bodega de Seu Bastião",
    expressions_csv: str = "Oxente, Morreu Maria Preá, Botar Boneco, Deixe de cunversa",
    tone: str = "Comédia Leve",
    dialogue_json: str = "",
) -> str:
    """Generate a Google Flow (Veo 3.1) Production Pack: clean 1080x1920 Shot-Reverse-Shot reference Ingredients + bilingual Veo 3.1 prompts + credit estimation (10 Lite / 20 Fast per take)."""
    _, base_url, headers, auth = _resolve_target_conn("vps")
    expr_list = [x.strip() for x in (expressions_csv or "").split(",") if x.strip()]
    parsed_dialogue: list[dict[str, Any]] = json.loads(dialogue_json) if dialogue_json and dialogue_json.strip() else []
    body = {
        "title": title,
        "visual_style": visual_style if visual_style in ("3d_cartoon", "cinematic_tv") else "3d_cartoon",
        "theme": theme,
        "expressions": expr_list,
        "tone": tone,
        "dialogue": parsed_dialogue,
    }
    try:
        with httpx.Client(timeout=180.0, headers=headers, auth=auth) as client:
            resp = client.post(f"{base_url}/api/quick/novela/flow-pack", json=body)
            return json.dumps(resp.json(), ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def studio_stitch_novela_takes(
    take_paths_csv: str = "",
    batch_id: str = "",
    output_ids_csv: str = "",
    title: str = "Novelinha Flow — Corte Seco",
    platform_preset: str = "tiktok_reels",
    subtitles_mode: str = "none",
    trim_silence: bool = True,
    redub_with_regional_tts: bool = False,
    accent_engine: str = "nordestino_raiz",
    dialogue_json: str = "",
) -> str:
    """Stitch ordered Google Flow / Colab MP4 takes into one seamless 1080x1920 @ 30fps episode, optionally redubbing takes with regional Northeast TTS (Gemini Native / F5-TTS-PT-BR / Seed-VC), trimming silent lead/tail via faster-whisper INT8 and normalizing audio to 48kHz Stereo SoXR."""
    _, base_url, headers, auth = _resolve_target_conn("vps")
    parsed_dlg: list[dict[str, Any]] = json.loads(dialogue_json) if dialogue_json and dialogue_json.strip() else []
    body = {
        "title": title,
        "batch_id": batch_id,
        "take_paths": [x.strip() for x in (take_paths_csv or "").split(",") if x.strip()],
        "output_ids": [x.strip() for x in (output_ids_csv or "").split(",") if x.strip()],
        "platform_preset": platform_preset,
        "subtitles_mode": subtitles_mode if subtitles_mode in ("none", "minimal") else "none",
        "trim_silence": bool(trim_silence),
        "redub_with_regional_tts": bool(redub_with_regional_tts),
        "accent_engine": accent_engine if accent_engine in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide") else "nordestino_raiz",
        "dialogue": parsed_dlg,
        "wait": False,
    }
    try:
        with httpx.Client(timeout=30.0, headers=headers, auth=auth) as client:
            resp = client.post(f"{base_url}/api/quick/novela/stitch-takes", json=body)
            if resp.status_code >= 400:
                return json.dumps({"ok": False, "status_code": resp.status_code, "detail": resp.text[:400]}, ensure_ascii=False)
            data = resp.json()
            if data.get("task_id"):
                data = _poll_quick_task(client, f"{base_url}/api/quick/novela/stitch-takes/task/{data['task_id']}", timeout_s=240.0)
            return json.dumps(data, ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def studio_preview_regional_voice(
    accent_engine: str = "nordestino_raiz",
    speaker: str = "",
    text: str = "",
    emotion: str = "warm",
    dialogue_json: str = "",
) -> str:
    """Synthesize a quick 48kHz Stereo SoXR preview MP3 of regional Northeast voices (Dona Socorro, Seu Bastião, Enzo) via Gemini Native TTS, F5-TTS-PT-BR, or Seed-VC."""
    _, base_url, headers, auth = _resolve_target_conn("vps")
    parsed_dlg: list[dict[str, Any]] = json.loads(dialogue_json) if dialogue_json and dialogue_json.strip() else []
    body = {
        "accent_engine": accent_engine if accent_engine in ("nordestino_raiz", "f5_tts_clone", "seed_vc_guide") else "nordestino_raiz",
        "speaker": speaker,
        "text": text,
        "emotion": emotion,
        "dialogue": parsed_dlg,
    }
    try:
        with httpx.Client(timeout=120.0, headers=headers, auth=auth) as client:
            resp = client.post(f"{base_url}/api/quick/novela/preview-voice", json=body)
            return json.dumps(resp.json(), ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


@mcp.tool()
def studio_colab_status(colab_url: str = "", colab_token: str = "") -> str:
    """Check status of remote Google Colab GPU Worker tunnel, or save and test a new trycloudflare.com URL if colab_url is provided."""
    _, base_url, headers, auth = _resolve_target_conn("vps")
    try:
        with httpx.Client(timeout=15.0, headers=headers, auth=auth) as client:
            if colab_url and colab_url.strip():
                r = client.post(f"{base_url}/api/colab/config", json={"colab_url": colab_url.strip(), "colab_token": colab_token.strip()})
            else:
                r = client.get(f"{base_url}/api/colab/status")
            return json.dumps(r.json(), ensure_ascii=False, indent=2)
    except Exception as exc:
        return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run(transport="stdio")
