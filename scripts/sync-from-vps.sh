#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VPS_HOST="${VPS_HOST:-root@187.77.202.176}"
OPENJARVIS_DIR="${OPENJARVIS_DIR:-$HOME/open-jarvis}"
OPENJARVIS_CFG="${OPENJARVIS_CFG:-$HOME/.openjarvis}"

echo "[1/5] Sincronizando stacks e serviços de $VPS_HOST:/opt/ai-media-studio/ ..."
VPS_DIRS=(
  media-control-panel
  tools
  campaigns
  dev-skills-vault
  MoneyPrinterTurbo
  VoiceStudio
  openfilm
  open-higgsfield
  open-seo
  supoclip
  youtube-automation-agent
)

RSYNC_EXCLUDES=(
  --exclude='.git/'
  --exclude='.git'
  --exclude='.venv/'
  --exclude='venv/'
  --exclude='node_modules/'
  --exclude='__pycache__/'
  --exclude='*.pyc'
  --exclude='*.pyo'
  --exclude='.next/'
  --exclude='.turbo/'
  --exclude='.output/'
  --exclude='.pnpm-store/'
  --exclude='.tanstack/'
  --exclude='.wrangler/'
  --exclude='.cache/'
  --exclude='.playwright-mcp/'
  --exclude='playwright-report/'
  --exclude='test-results/'
  --exclude='dist/'
  --exclude='build/'
  --exclude='coverage/'
  --exclude='*.tsbuildinfo'
  --include='.env.example'
  --include='.env.*.example'
  --exclude='.env'
  --exclude='.env.*'
  --exclude='.dev.vars'
  --exclude='*vault.json'
  --exclude='*.htpasswd*'
  --exclude='*.pem'
  --exclude='*.key'
  --exclude='*.crt'
  --exclude='*.p12'
  --exclude='storage/'
  --exclude='outputs/'
  --exclude='output/'
  --exclude='uploads/'
  --exclude='clips/'
  --exclude='exports/'
  --exclude='Videos/'
  --exclude='.openfilm/'
  --exclude='logs/'
  --exclude='temp/'
  --exclude='tmp/'
  --exclude='*.log'
  --exclude='*.mp4'
  --exclude='*.mkv'
  --exclude='*.webm'
  --exclude='*.avi'
  --exclude='*.mov'
  --exclude='*.mp3'
  --exclude='*.wav'
  --exclude='*.m4a'
  --exclude='*.ogg'
  --exclude='*.flac'
  --exclude='*.aac'
  --exclude='*.ttc'
  --exclude='*.ttf'
  --exclude='*.woff'
  --exclude='*.woff2'
  --exclude='*.pt'
  --exclude='*.pth'
  --exclude='*.bin'
  --exclude='*.gguf'
  --exclude='*.safetensors'
  --exclude='*.onnx'
  --exclude='*.i8'
  --exclude='*.wasm'
  --exclude='*.node'
  --exclude='*.so'
  --exclude='*.dylib'
  --exclude='*.dll'
  --exclude='*.db'
  --exclude='*.db-*'
  --exclude='*.sqlite'
  --exclude='*.sqlite3'
  --exclude='config.toml.bak'
)

for d in "${VPS_DIRS[@]}"; do
  mkdir -p "$REPO_ROOT/$d"
  if [ "$d" = "MoneyPrinterTurbo" ]; then
    rsync -az --delete --links \
      --exclude='/config.toml' \
      --exclude='/config.example.studio.toml' \
      "${RSYNC_EXCLUDES[@]}" \
      "$VPS_HOST:/opt/ai-media-studio/$d/" "$REPO_ROOT/$d/"
  elif [ "$d" = "youtube-automation-agent" ]; then
    rsync -az --delete --links \
      --exclude='/data/' \
      --exclude='/config/credentials.json' \
      --exclude='/config/tokens.json' \
      "${RSYNC_EXCLUDES[@]}" \
      "$VPS_HOST:/opt/ai-media-studio/$d/" "$REPO_ROOT/$d/"
  else
    rsync -az --delete --links "${RSYNC_EXCLUDES[@]}" \
      "$VPS_HOST:/opt/ai-media-studio/$d/" "$REPO_ROOT/$d/"
  fi
done

echo "[2/5] Gerando configs sanitizados e infraestrutura (infra/) ..."
mkdir -p "$REPO_ROOT/infra/nginx" "$REPO_ROOT/infra/systemd" "$REPO_ROOT/infra/bin" "$REPO_ROOT/infra/env-examples"

ssh -o BatchMode=yes "$VPS_HOST" 'cat /etc/systemd/system/media-panel.service' \
  > "$REPO_ROOT/infra/systemd/media-panel.service"

ssh -o BatchMode=yes "$VPS_HOST" 'cat /usr/local/bin/modo-dev' \
  > "$REPO_ROOT/infra/bin/modo-dev.vps"
chmod +x "$REPO_ROOT/infra/bin/modo-dev.vps"

if [ -f "$HOME/.local/bin/modo-dev" ]; then
  cp "$HOME/.local/bin/modo-dev" "$REPO_ROOT/infra/bin/modo-dev.local"
  chmod +x "$REPO_ROOT/infra/bin/modo-dev.local"
fi

ssh -o BatchMode=yes "$VPS_HOST" 'python3 - <<'\''PY'\''
import re
with open("/etc/nginx/sites-available/media-studio", "r", encoding="utf-8") as fh:
    txt = fh.read()
txt = re.sub(r"\bst_[A-Za-z0-9_-]{20,}", "st_<REPLACE_WITH_VPS_PANEL_TOKEN>", txt)
print(txt, end="")
PY' > "$REPO_ROOT/infra/nginx/media-studio.conf"

ssh -o BatchMode=yes "$VPS_HOST" 'python3 - <<'\''PY'\''
with open("/opt/ai-media-studio/media-control-panel/.env", "r", encoding="utf-8") as fh:
    for line in fh:
        s = line.strip()
        if not s or s.startswith("#"):
            print(line.rstrip())
        elif "=" in line:
            k, _ = line.split("=", 1)
            print(f"{k.strip()}=")
PY' > "$REPO_ROOT/infra/env-examples/media-control-panel.env.example"

ssh -o BatchMode=yes "$VPS_HOST" 'python3 - <<'\''PY'\''
with open("/opt/ai-media-studio/MoneyPrinterTurbo/config.toml", "r", encoding="utf-8") as fh:
    for line in fh:
        s = line.strip()
        if not s or s.startswith("#") or s.startswith("["):
            print(line.rstrip())
            continue
        if "=" in line:
            k, _, v = line.partition("=")
            kl = k.lower()
            if any(w in kl for w in ("key", "secret", "token", "password")):
                if v.strip().startswith("["):
                    print(f"{k.rstrip()} = []")
                else:
                    print(f"{k.rstrip()} = \"\"")
                continue
        print(line.rstrip())
PY' > "$REPO_ROOT/MoneyPrinterTurbo/config.example.studio.toml"

echo "[3/5] Sincronizando OpenJarvis custom (openjarvis-client/) ..."
mkdir -p "$REPO_ROOT/openjarvis-client/dot-openjarvis/skills/studio-controller"
mkdir -p "$REPO_ROOT/openjarvis-client/src/openjarvis/agents"
mkdir -p "$REPO_ROOT/openjarvis-client/src/openjarvis/speech"
mkdir -p "$REPO_ROOT/openjarvis-client/src/openjarvis/tools"

for rel in \
  clap_listener.py \
  studio_controller_mcp.py \
  iniciar-jarvis.sh \
  webcam_vision.py \
  src/openjarvis/agents/orchestrator.py \
  src/openjarvis/speech/__init__.py \
  src/openjarvis/speech/_tts_discovery.py \
  src/openjarvis/speech/faster_whisper.py \
  src/openjarvis/speech/edge_tts_backend.py \
  src/openjarvis/tools/text_to_speech.py
do
  if [ -f "$OPENJARVIS_DIR/$rel" ]; then
    cp "$OPENJARVIS_DIR/$rel" "$REPO_ROOT/openjarvis-client/$rel"
  fi
done

if [ -d "$OPENJARVIS_DIR/.git" ]; then
  git -C "$OPENJARVIS_DIR" diff > "$REPO_ROOT/openjarvis-client/openjarvis-upstream.diff"
fi

[ -f "$OPENJARVIS_CFG/config.toml" ] && cp "$OPENJARVIS_CFG/config.toml" "$REPO_ROOT/openjarvis-client/dot-openjarvis/config.toml"
[ -f "$OPENJARVIS_CFG/mcp_servers.json" ] && cp "$OPENJARVIS_CFG/mcp_servers.json" "$REPO_ROOT/openjarvis-client/dot-openjarvis/mcp_servers.json"
[ -f "$OPENJARVIS_CFG/skills/studio-controller/SKILL.md" ] && cp "$OPENJARVIS_CFG/skills/studio-controller/SKILL.md" "$REPO_ROOT/openjarvis-client/dot-openjarvis/skills/studio-controller/SKILL.md"

if [ -f "$OPENJARVIS_CFG/.env" ]; then
  python3 - "$OPENJARVIS_CFG/.env" > "$REPO_ROOT/openjarvis-client/dot-openjarvis/.env.example" <<'PY'
import sys
with open(sys.argv[1], "r", encoding="utf-8") as fh:
    for line in fh:
        s = line.strip()
        if not s or s.startswith("#"):
            print(line.rstrip())
        elif "=" in line:
            k, _ = line.split("=", 1)
            print(f"{k.strip()}=")
PY
fi

echo "[4/5] Sincronizando testes E2E e scripts de verificação (tests/) ..."
mkdir -p "$REPO_ROOT/tests"
for f in "$HOME"/e2e_*.mjs "$HOME"/verify_*.py; do
  [ -f "$f" ] && cp "$f" "$REPO_ROOT/tests/"
done

echo "[5/5] Executando auditoria automática de segredos ..."
python3 "$REPO_ROOT/scripts/secret-scan.py"
echo "Sync concluído com sucesso!"
