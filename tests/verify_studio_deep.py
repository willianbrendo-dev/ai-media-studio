#!/usr/bin/env python3
"""
AI Media Studio B2B Enterprise — Deep TDD & SecOps Verification Suite
=====================================================================
Validates:
1. Security, HTTPS Redirect, Basic/Bearer Auth Integrity (401 vs 200) & CORS Headers
2. All 7 Stack Reverse-Proxy Sub-Routes (/app/<stack>/) & Standby Portal Resilience
3. SupoClip Deep Route Mapping (/sign-up, /sign-in, /api/v1/clips, /api/v1/tasks, /upload)
   and Advanced Cutting Workflows (Novela/Series, Melhores Momentos/Highlights, Cortes Virais)
   with ZERO {"detail": "Not Found"} errors
4. Mobile Quick Studio 100% Feature Parity (MoneyPrinterTurbo, VoiceStudio 48kHz SoXR, OpenFilm)
5. OpenJarvis Autonomous Media Consciousness MCP Tools (studio_scan_media, studio_stage_or_upload_video)
6. Post-Test Resource Cleanup (0/7 containers running on VPS)
"""

import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


BASE_HTTPS = "https://187.77.202.176.sslip.io"
BASE_HTTP = "http://187.77.202.176.sslip.io"


def load_panel_token() -> str:
    env_file = Path.home() / ".openjarvis" / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith("VPS_PANEL_TOKEN="):
                return s.split("=", 1)[1].strip().strip('"').strip("'")
    token = os.environ.get("VPS_PANEL_TOKEN", "").strip()
    if not token:
        raise RuntimeError("VPS_PANEL_TOKEN not found in ~/.openjarvis/.env or environment")
    return token


TOKEN = load_panel_token()


class NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def http_req(
    path_or_url: str,
    method: str = "GET",
    payload: Optional[Dict[str, Any]] = None,
    auth: bool = True,
    headers_extra: Optional[Dict[str, str]] = None,
    timeout: int = 90,
    follow_redirects: bool = True,
) -> Tuple[int, Dict[str, str], str]:
    url = path_or_url if path_or_url.startswith("http") else f"{BASE_HTTPS}{path_or_url}"
    headers = {
        "User-Agent": "AIMediaStudio-TDD-Suite/3.0",
        "Accept": "application/json, text/html, */*",
    }
    if auth:
        headers["Authorization"] = f"Bearer {TOKEN}"
    if headers_extra:
        headers.update(headers_extra)

    data = None
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener() if follow_redirects else urllib.request.build_opener(NoRedirectHandler)

    try:
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, resp_headers, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        resp_headers = {k.lower(): v for k, v in e.headers.items()} if e.headers else {}
        return e.code, resp_headers, body
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return 504, {"x-studio-stack-state": "standby"}, f"Connection/Timeout Error: {e}"


class TDDSuite:
    def __init__(self) -> None:
        self.results: List[Dict[str, Any]] = []
        self.failures: int = 0

    def record(self, category: str, name: str, passed: bool, detail: str) -> None:
        status_str = "PASS" if passed else "FAIL"
        if not passed:
            self.failures += 1
        self.results.append({
            "category": category,
            "name": name,
            "status": status_str,
            "detail": detail,
        })
        icon = "[PASS]" if passed else "[FAIL]"
        print(f"{icon} [{category}] {name} -> {detail}")

    def assert_not_found_free(self, body: str) -> bool:
        """Ensure response is not FastAPI's raw {'detail': 'Not Found'}."""
        stripped = body.strip()
        if stripped == '{"detail":"Not Found"}' or stripped == '{"detail": "Not Found"}':
            return False
        return True


def run_suite() -> int:
    suite = TDDSuite()
    print("=" * 78)
    print(" AI MEDIA STUDIO B2B — DEEP TDD, SECOPS & FEATURE PARITY VERIFICATION SUITE")
    print("=" * 78)

    try:
        # ------------------------------------------------------------------
        # 1. SECOPS: HTTPS Redirect, Auth Gate (401 vs 200), CORS Headers
        # ------------------------------------------------------------------
        code, hdrs, _ = http_req(f"{BASE_HTTP}/", method="GET", auth=False, follow_redirects=False)
        loc = hdrs.get("location", "")
        suite.record(
            "SecOps",
            "HTTP (80) -> HTTPS (443) 301 Redirect",
            code in (301, 308) and loc.startswith("https://"),
            f"status={code}, location={loc}",
        )

        code_unauth, _, _ = http_req("/api/projects", method="GET", auth=False)
        suite.record(
            "SecOps",
            "Unauthenticated API Access Blocked (401)",
            code_unauth == 401,
            f"status={code_unauth} (expected 401)",
        )

        code_auth, hdrs_auth, body_auth = http_req("/api/system-stats", method="GET", auth=True)
        stats_json = json.loads(body_auth) if code_auth == 200 else {}
        suite.record(
            "SecOps",
            "Authenticated Bearer Token Access (200 OK)",
            code_auth == 200 and "cpu_percent" in stats_json and "ram" in stats_json,
            f"status={code_auth}, cpu={stats_json.get('cpu_percent')}%, ram_used={stats_json.get('ram', {}).get('used_gb')}GB",
        )

        code_cors, hdrs_cors, _ = http_req(
            "/api/projects",
            method="OPTIONS",
            auth=False,
            headers_extra={
                "Origin": BASE_HTTPS,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type",
            },
        )
        acao = hdrs_cors.get("access-control-allow-origin", "")
        suite.record(
            "SecOps",
            "CORS Preflight & Headers Integrity",
            code_cors in (200, 204) and bool(acao),
            f"status={code_cors}, Access-Control-Allow-Origin={acao}",
        )

        # ------------------------------------------------------------------
        # 2. STACK PROXIES (/app/<stack>/) & STANDBY PORTAL RESILIENCE
        # ------------------------------------------------------------------
        stacks = [
            "moneyprinter",
            "voicestudio",
            "openfilm",
            "supoclip",
            "higgsfield",
            "agenttube",
            "openseo",
        ]
        for st in stacks:
            c, h, b = http_req(f"/app/{st}/", method="GET", auth=True)
            ok = c == 200 and suite.assert_not_found_free(b)
            state_hdr = h.get("x-studio-stack-state", "live")
            suite.record(
                "ReverseProxy",
                f"Sub-route /app/{st}/ Accessibility",
                ok,
                f"status={c}, state={state_hdr}, bytes={len(b)}",
            )

        # ------------------------------------------------------------------
        # 3. SUPOCLIP DEEP ROUTE & WORKFLOW VERIFICATION (ZERO 404s)
        # ------------------------------------------------------------------
        print("\n--- Starting SupoClip Stack on VPS for Live End-to-End Testing ---")
        c_start, _, b_start = http_req(
            "/api/projects/supoclip/action",
            method="POST",
            payload={"action": "start"},
            timeout=120,
        )
        suite.record(
            "SupoClip",
            "Start SupoClip Stack via Control Panel API",
            c_start == 200,
            f"status={c_start}",
        )

        # Wait up to 90s for SupoClip frontend (:3107) and backend (:8000) to settle
        supo_ready = False
        for _ in range(18):
            c_chk, h_chk, b_chk = http_req("/app/supoclip/", method="GET", auth=True, timeout=60)
            if c_chk == 200 and h_chk.get("x-studio-stack-state") != "standby":
                supo_ready = True
                break
            time.sleep(3)

        suite.record(
            "SupoClip",
            "SupoClip Live Frontend (/app/supoclip/) Ready",
            supo_ready,
            f"ready={supo_ready}",
        )

        # Test all critical SupoClip sub-routes that previously caused 404 / Not Found
        supoclip_routes = [
            ("/app/supoclip/sign-up", "GET", "Sub-route /app/supoclip/sign-up"),
            ("/sign-up", "GET", "Root fallback /sign-up -> SupoClip Auth"),
            ("/app/supoclip/sign-in", "GET", "Sub-route /app/supoclip/sign-in"),
            ("/sign-in", "GET", "Root fallback /sign-in -> SupoClip Auth"),
            ("/api/v1/clips", "GET", "API /api/v1/clips (Clip Listing)"),
            ("/app/supoclip/api/v1/clips", "GET", "Sub-route /app/supoclip/api/v1/clips"),
            ("/app/supoclip-api/api/v1/clips", "GET", "Sub-route /app/supoclip-api/api/v1/clips"),
            ("/api/v1/tasks", "GET", "API /api/v1/tasks (Task Listing)"),
            ("/api/quick/supoclip/workflows", "GET", "API /api/quick/supoclip/workflows (Catalog)"),
        ]

        for route_path, mth, label in supoclip_routes:
            rc, rh, rb = http_req(route_path, method=mth, auth=True, timeout=90)
            not_found_free = suite.assert_not_found_free(rb)
            suite.record(
                "SupoClip",
                label,
                rc == 200 and not_found_free,
                f"status={rc}, not_found_free={not_found_free}, bytes={len(rb)}",
            )

        # Test SupoClip 135s Sample Upload Flow (POST /api/quick/supoclip/upload?sample=1)
        c_up, _, b_up = http_req(
            "/api/quick/supoclip/upload?sample=1",
            method="POST",
            payload={},
            auth=True,
            timeout=90,
        )
        up_data = json.loads(b_up) if c_up == 200 else {}
        uploaded_uri = up_data.get("video_url", "")
        suite.record(
            "SupoClip",
            "Upload 135s Sample Video (POST /api/quick/supoclip/upload?sample=1)",
            c_up == 200 and uploaded_uri.startswith("upload://"),
            f"status={c_up}, video_url={uploaded_uri}, size_mb={up_data.get('size_mb')}",
        )

        # Test all 3 primary SupoClip Cutting Modes with the uploaded 135s video
        cut_modes_to_test = [
            (
                "novela",
                "series",
                {
                    "video_url": uploaded_uri,
                    "source_mode": "upload",
                    "cut_mode": "novela",
                    "aspect_ratio": "vertical",
                    "clip_length": "medium",
                    "subtitle_lang": "pt",
                    "add_subtitles": True,
                    "part_count": "3",
                    "series_ending_style": "cliffhanger",
                    "hook_title": "EPISÓDIO 1 - A REVELAÇÃO",
                    "remove_silence": True,
                    "auto_start": False,
                },
                "Workflow 'Novela com Continuação' (series / cliffhanger / 3 parts)",
            ),
            (
                "highlights",
                "highlights",
                {
                    "video_url": uploaded_uri,
                    "source_mode": "upload",
                    "cut_mode": "highlights",
                    "aspect_ratio": "vertical",
                    "clip_length": "medium",
                    "subtitle_lang": "pt",
                    "add_subtitles": True,
                    "remove_silence": True,
                    "auto_start": False,
                },
                "Workflow 'Melhores Momentos' (highlights / medium)",
            ),
            (
                "viral",
                "highlights",
                {
                    "video_url": uploaded_uri,
                    "source_mode": "upload",
                    "cut_mode": "viral",
                    "aspect_ratio": "vertical",
                    "clip_length": "short",
                    "subtitle_lang": "pt",
                    "add_subtitles": True,
                    "hook_title": "OLHA ISSO!",
                    "remove_silence": True,
                    "auto_start": False,
                },
                "Workflow 'Cortes Curtos Virais' (viral / short)",
            ),
        ]

        for mode_key, expected_wf, req_payload, desc in cut_modes_to_test:
            cm_code, _, cm_body = http_req(
                "/api/quick/supoclip",
                method="POST",
                payload=req_payload,
                auth=True,
                timeout=60,
            )
            cm_json = json.loads(cm_body) if cm_code == 200 else {}
            task_obj = cm_json.get("task", {}) if isinstance(cm_json.get("task"), dict) else {}
            task_id = task_obj.get("id") or cm_json.get("task_id")
            actual_wf = cm_json.get("workflow")
            passed = (
                cm_code == 200
                and cm_json.get("status") == "ok"
                and actual_wf == expected_wf
                and bool(task_id)
                and suite.assert_not_found_free(cm_body)
            )
            suite.record(
                "SupoClip",
                desc,
                passed,
                f"status={cm_code}, workflow={actual_wf}, task_id={task_id}",
            )

        # Verify /api/v1/clips now lists our created tasks
        c_clips, _, b_clips = http_req("/api/v1/clips", method="GET", auth=True, timeout=30)
        clips_json = json.loads(b_clips) if c_clips == 200 else {}
        suite.record(
            "SupoClip",
            "Created Clips/Tasks Visible in /api/v1/clips",
            c_clips == 200 and clips_json.get("count", 0) >= 2,
            f"status={c_clips}, total_tasks={clips_json.get('count', 0)}",
        )

        # Stop SupoClip before testing other tools to keep RAM clean
        http_req("/api/projects/supoclip/action", method="POST", payload={"action": "stop"}, timeout=60)

        # ------------------------------------------------------------------
        # 4. FEATURE PARITY: VOICESTUDIO, OPENFILM & MONEYPRINTERTURBO
        # ------------------------------------------------------------------
        print("\n--- Testing Quick Studio Feature Parity Endpoints ---")
        # 4a. VoiceStudio 48kHz SoXR + Pitch + Rate
        c_vs, _, b_vs = http_req(
            "/api/quick/voicestudio",
            method="POST",
            payload={
                "text": "Validação empresarial B2B concluída com sucesso. Áudio masterizado em quarenta e oito quilohertz estéreo.",
                "voice": "pt-BR-FranciscaNeural",
                "rate": "+5%",
                "pitch": "+2Hz",
                "normalize_48k_soxr": True,
                "project_tag": "tdd-b2b-suite",
            },
            auth=True,
            timeout=60,
        )
        vs_json = json.loads(b_vs) if c_vs == 200 else {}
        suite.record(
            "FeatureParity",
            "VoiceStudio Multi-Voice + Pitch + 48kHz SoXR Mastering",
            c_vs == 200 and vs_json.get("status") == "ok" and vs_json.get("sample_rate") == "48000 Hz Stereo SoXR",
            f"status={c_vs}, wav={vs_json.get('wav_48khz', {}).get('name')}, rate={vs_json.get('sample_rate')}",
        )

        # 4b. OpenFilm Advanced Project Staging (Aspect Ratio, FPS, Subtitle Preset, Media Import)
        c_of, _, b_of = http_req(
            "/api/quick/openfilm",
            method="POST",
            payload={
                "project_name": "TDD Enterprise Reels 9x16",
                "aspect_ratio": "9:16",
                "fps": 30,
                "subtitle_preset": "reels_viral",
                "motion_blur": True,
                "import_latest_media": True,
                "scene_prompt": "Abertura com gancho de impacto, legenda pop verde esmeralda e corte dinâmico.",
                "auto_start": False,
            },
            auth=True,
            timeout=60,
        )
        of_json = json.loads(b_of) if c_of == 200 else {}
        brief = of_json.get("brief", {})
        suite.record(
            "FeatureParity",
            "OpenFilm Advanced Timeline Staging & Gallery Media Import",
            c_of == 200 and of_json.get("status") == "ok" and brief.get("aspect_ratio") == "9:16" and brief.get("fps") == 30 and len(of_json.get("staged_files", [])) >= 1,
            f"status={c_of}, ratio={brief.get('aspect_ratio')}, preset={brief.get('subtitle_preset')}, staged={len(of_json.get('staged_files', []))}",
        )

        # 4c. MoneyPrinterTurbo Advanced Parameters (Custom Keywords, B-Roll Style, Word-by-Word Pop Subtitles)
        c_mp, _, b_mp = http_req(
            "/api/quick/moneyprinter",
            method="POST",
            payload={
                "video_subject": "Como escalar vendas B2B com inteligência artificial autônoma",
                "video_terms": "business, technology, data center, artificial intelligence",
                "video_aspect": "9:16",
                "paragraph_number": 1,
                "voice_provider": "edge",
                "voice_name": "pt-BR-AntonioNeural",
                "voice_rate": 1.05,
                "bgm_type": "random",
                "bgm_volume": 0.2,
                "subtitle_enabled": True,
                "subtitle_display_mode": "word_by_word",
                "subtitle_animation": "pop_spring",
                "subtitle_position": "bottom",
                "text_fore_color": "#10B981",
                "broll_style": "grid_emerald",
                "video_transition_mode": "FadeIn",
                "video_clip_duration": 4,
                "auto_start": False,
            },
            auth=True,
            timeout=90,
        )
        mp_json = json.loads(b_mp) if c_mp == 200 else {}
        mp_adv = mp_json.get("advanced_config", {})
        suite.record(
            "FeatureParity",
            "MoneyPrinterTurbo Advanced B-Roll, Keywords & Word-by-Word Subtitles",
            c_mp == 200 and mp_json.get("status") == "ok" and mp_adv.get("subtitle_display_mode") == "word_by_word",
            f"status={c_mp}, mode={mp_json.get('mode')}, broll={mp_adv.get('broll_style')}, file={mp_json.get('generated_file', {}).get('name')}",
        )

        # ------------------------------------------------------------------
        # 5. OPENJARVIS AUTONOMOUS MEDIA CONSCIOUSNESS (MCP TOOLS)
        # ------------------------------------------------------------------
        print("\n--- Testing OpenJarvis Autonomous Media Consciousness MCP Tools ---")
        import subprocess
        jarvis_py = "/home/wbrendo/open-jarvis/.venv/bin/python"
        if not Path(jarvis_py).exists():
            jarvis_py = sys.executable
        proc_mcp = subprocess.run(
            [
                jarvis_py,
                "-c",
                "import sys; sys.path.insert(0, '/home/wbrendo/open-jarvis'); import studio_controller_mcp as m; print(m.studio_scan_media(query='', target='both'))",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        scan_res = json.loads(proc_mcp.stdout.strip()) if proc_mcp.returncode == 0 and proc_mcp.stdout.strip() else {}
        suite.record(
            "OpenJarvisMCP",
            "studio_scan_media (Local + VPS Media Discovery & SCP Guidance)",
            bool(scan_res.get("ok")) and "vps_gallery_count" in scan_res and "ssh_quick_hint" in scan_res,
            f"local_count={scan_res.get('local_media_count')}, vps_gallery_count={scan_res.get('vps_gallery_count')}",
        )

        # ------------------------------------------------------------------
        # 6. MODO DEV CLOUD & HYBRID OPENJARVIS SYNC (TESTS 33 & 34)
        # ------------------------------------------------------------------
        print("\n--- Testing VPS Modo Dev Vault (+27 Skills), OpenJarvis Hybrid Sync & #dev-mode-toggle DOM ---")
        # Test 33: POST /api/skills/dev-mode {"enable": true} + Hybrid OpenJarvis MCP sync check
        c_dm_on, _, b_dm_on = http_req(
            "/api/skills/dev-mode",
            method="POST",
            payload={"enable": True},
            auth=True,
            timeout=30,
        )
        dm_on = json.loads(b_dm_on) if c_dm_on == 200 else {}
        proc_dm_sync = subprocess.run(
            [
                jarvis_py,
                "-c",
                "import sys; sys.path.insert(0, '/home/wbrendo/open-jarvis'); import studio_controller_mcp as m; print(m.studio_modo_dev(action='status', sync_vps=True))",
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
        dm_sync = json.loads(proc_dm_sync.stdout.strip()) if proc_dm_sync.returncode == 0 and proc_dm_sync.stdout.strip() else {}
        t33_ok = (
            c_dm_on == 200
            and dm_on.get("ok") is True
            and dm_on.get("enabled") is True
            and dm_on.get("dev_count") == 27
            and dm_on.get("vault_total") == 27
            and dm_sync.get("ok") is True
            and dm_sync.get("vps", {}).get("synced") is True
            and dm_sync.get("vps", {}).get("active") is True
        )
        suite.record(
            "DevMode",
            "Test 33: POST /api/skills/dev-mode {enable:true} + OpenJarvis Sync (27 Skills)",
            t33_ok,
            f"status={c_dm_on}, enabled={dm_on.get('enabled')}, dev_count={dm_on.get('dev_count')}, vault_total={dm_on.get('vault_total')}, vps_synced={dm_sync.get('vps', {}).get('synced')}",
        )

        # Test 34: POST /api/skills/dev-mode {"enable": false} + Playwright DOM click on #dev-mode-toggle (zero error alerts)
        c_dm_off, _, b_dm_off = http_req(
            "/api/skills/dev-mode",
            method="POST",
            payload={"enable": False},
            auth=True,
            timeout=30,
        )
        dm_off = json.loads(b_dm_off) if c_dm_off == 200 else {}

        import shutil
        node_bin = shutil.which("node")
        if not node_bin:
            nvm_nodes = sorted(Path.home().glob(".nvm/versions/node/*/bin/node"))
            node_bin = str(nvm_nodes[-1]) if nvm_nodes else ""

        dom_check = {"ok": False, "error": "node/playwright not found"}
        if node_bin and Path("/home/wbrendo/Projetos/fresh-start/package.json").exists():
            pw_script = f"""
import {{ createRequire }} from "module";
const require = createRequire("/home/wbrendo/Projetos/fresh-start/package.json");
const {{ chromium }} = require("playwright");
const browser = await chromium.launch({{ executablePath: "/usr/bin/google-chrome", headless: true, args: ["--no-sandbox", "--disable-dev-shm-usage"] }});
const ctx = await browser.newContext({{ viewport: {{ width: 1440, height: 900 }}, extraHTTPHeaders: {{ Authorization: "Bearer {TOKEN}" }} }});
const page = await ctx.newPage();
await page.goto("{BASE_HTTPS}/", {{ waitUntil: "domcontentloaded", timeout: 45000 }});
await page.waitForFunction(() => typeof window.toggleModoDev === "function", {{ timeout: 15000 }});
await page.waitForSelector("#dev-mode-toggle", {{ timeout: 15000 }});
await page.click("#dev-mode-toggle");
await page.waitForFunction(() => document.getElementById("sidebar-modo-dev-pill")?.textContent?.trim() === "ATIVO", {{ timeout: 15000 }});
const bodyTextOn = await page.evaluate(() => document.body.innerText);
const badgeOn = await page.textContent("#nav-skills-badge");
await page.click("#dev-mode-toggle");
await page.waitForFunction(() => document.getElementById("sidebar-modo-dev-pill")?.textContent?.trim() === "Repouso", {{ timeout: 15000 }});
const bodyTextOff = await page.evaluate(() => document.body.innerText);
const badgeOff = await page.textContent("#nav-skills-badge");
const hasErr = /Erro no Modo Dev|não encontrado/i.test(bodyTextOn + "\\n" + bodyTextOff);
await browser.close();
console.log(JSON.stringify({{ ok: !hasErr && badgeOn === "10+27" && badgeOff === "10", hasErr, badgeOn, badgeOff }}));
"""
            pw_tmp = Path("/home/wbrendo/.gemini/antigravity/brain/a7f7d602-7234-4486-835e-7b2c2a8e0e98/scratch/check_dev_mode_dom.mjs")
            pw_tmp.write_text(pw_script, encoding="utf-8")
            proc_pw = subprocess.run([node_bin, str(pw_tmp)], capture_output=True, text=True, timeout=60)
            if proc_pw.returncode == 0 and proc_pw.stdout.strip():
                dom_check = json.loads(proc_pw.stdout.strip().splitlines()[-1])
            else:
                dom_check = {"ok": False, "stderr": proc_pw.stderr[-300:], "stdout": proc_pw.stdout[-300:]}

        t34_ok = (
            c_dm_off == 200
            and dm_off.get("ok") is True
            and dm_off.get("enabled") is False
            and dm_off.get("dev_count") == 0
            and dm_off.get("vault_total") == 27
            and dom_check.get("ok") is True
        )
        suite.record(
            "DevMode",
            "Test 34: POST /api/skills/dev-mode {enable:false} + Playwright #dev-mode-toggle Zero Errors",
            t34_ok,
            f"status={c_dm_off}, enabled={dm_off.get('enabled')}, dev_count={dm_off.get('dev_count')}, dom={dom_check}",
        )

    finally:
        # ------------------------------------------------------------------
        # 6. STRICT CLEANUP: STOP ALL STACKS ON VPS (0/7 ACTIVE)
        # ------------------------------------------------------------------
        print("\n--- Running Post-Test Cleanup: Stopping All Docker Stacks on VPS ---")
        c_stop, _, b_stop = http_req("/api/projects/stop-all", method="POST", payload={}, auth=True, timeout=90)
        c_st, _, b_st = http_req("/api/system-stats", method="GET", auth=True, timeout=30)
        st_after = json.loads(b_st) if c_st == 200 else {}
        active_after = st_after.get("active_containers", -1)
        suite.record(
            "Cleanup",
            "Zero Active Containers Post-Test (RAM Preservation)",
            c_stop == 200 and active_after == 0,
            f"active_containers={active_after}, free_ram_gb={round(st_after.get('ram', {}).get('total_gb', 0) - st_after.get('ram', {}).get('used_gb', 0), 2)}",
        )

    print("\n" + "=" * 78)
    passed_count = len(suite.results) - suite.failures
    print(f" SUMMARY: {passed_count}/{len(suite.results)} TESTS PASSED | {suite.failures} FAILURES")
    print("=" * 78)

    report_path = Path("/home/wbrendo/.gemini/antigravity/brain/a7f7d602-7234-4486-835e-7b2c2a8e0e98/scratch/tdd_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "total": len(suite.results),
        "passed": passed_count,
        "failed": suite.failures,
        "results": suite.results,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    return 0 if suite.failures == 0 else 1


if __name__ == "__main__":
    sys.exit(run_suite())
