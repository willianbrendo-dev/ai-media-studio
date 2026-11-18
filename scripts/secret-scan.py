#!/usr/bin/env python3
"""Scan staged or workspace files in ai-media-studio for leaked secrets."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

FORBIDDEN_FILENAMES = {
    ".env",
    "vault.json",
    ".htpasswd-studio",
}

FORBIDDEN_PATHS = {
    "MoneyPrinterTurbo/config.toml",
    "MoneyPrinterTurbo/config.toml.bak",
    "youtube-automation-agent/config/credentials.json",
    "youtube-automation-agent/config/tokens.json",
}

TEXT_SECRET_PATTERNS = [
    ("studio_bearer_token", re.compile(r"\bst_[A-Za-z0-9_-]{20,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{25,}")),
    ("openai_or_openrouter_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}")),
    ("nvidia_nim_key", re.compile(r"\bnvapi-[A-Za-z0-9_-]{20,}")),
    ("github_pat", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}")),
    ("groq_key", re.compile(r"\bgsk_[A-Za-z0-9_-]{20,}")),
    ("huggingface_token", re.compile(r"\bhf_[A-Za-z0-9]{20,}")),
    ("private_key_pem", re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----")),
    ("basic_auth_header", re.compile(r"Authorization[\"':\s]+Basic\s+[A-Za-z0-9+/=]{12,}")),
]

# Known synthetic placeholders / upstream unit-test fixtures (verified harmless)
SYNTHETIC_TEST_RX = re.compile(
    r"^(?:sk-(?:ant-api03-should-not-be-used|ant-oat01-subscription\w*|LEAK\w+|supersecret\w+|abcdef\w+|proj-ABCDEF\w+|proj-abcdef\w+|test[_-]\w+|or-v1-test\w+)|gsk_test_\w+|st_<REPLACE_WITH_VPS_PANEL_TOKEN>|hf_(?:abcdefghijklmnopqrstuvwxyz\w+|QWERTYUIOP\w+|(?:appapp|envenv|clicli)0+\w+|subsub\w+|syntheticmirrorprobe\w+))$"
)


def iter_candidate_files() -> list[Path]:
    res = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    items = [p for p in res.stdout.decode("utf-8", errors="ignore").split("\0") if p]
    return [ROOT / rel for rel in items]


def main() -> int:
    violations: list[str] = []
    files = iter_candidate_files()
    for fp in files:
        if not fp.is_file() or fp.is_symlink():
            continue
        rel = fp.relative_to(ROOT).as_posix()
        if rel == "scripts/secret-scan.py":
            continue
        if fp.name in FORBIDDEN_FILENAMES or rel in FORBIDDEN_PATHS:
            violations.append(f"FORBIDDEN_FILE: {rel}")
            continue
        if (fp.name.startswith(".env.") and not fp.name.endswith(".example")) or ".htpasswd" in fp.name:
            violations.append(f"FORBIDDEN_ENV_FILE: {rel}")
            continue
        if fp.stat().st_size > 2_000_000:
            continue
        txt = fp.read_text(encoding="utf-8", errors="ignore")
        for label, rx in TEXT_SECRET_PATTERNS:
            for m in rx.finditer(txt):
                token = m.group(0)
                if SYNTHETIC_TEST_RX.match(token):
                    continue
                if rel == "VoiceStudio/tests/test_macos_notarization_api.py" and label == "private_key_pem":
                    continue
                violations.append(f"SECRET_MATCH ({label}): {rel} -> prefix={token[:8]}... (len={len(token)})")
    if violations:
        print("SECRET SCAN FAILED:")
        for v in violations:
            print("  -", v)
        return 1
    print(f"SECRET SCAN PASSED ({len(files)} candidate files checked, 0 violations).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
