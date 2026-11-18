#!/usr/bin/env bash
set -euo pipefail

JARVIS_HOME="$HOME/open-jarvis"
VENV_PY="$JARVIS_HOME/.venv/bin/python"
JARVIS_BIN="$JARVIS_HOME/.venv/bin/jarvis"
STATE_DIR="$HOME/.openjarvis"
RUN_DIR="$STATE_DIR/run"
LOG_DIR="$STATE_DIR/logs"
VAULT_JSON="$HOME/media-control-panel/vault.json"
ENV_FILE="$STATE_DIR/.env"
PORTAUDIO_DIR="$JARVIS_HOME/.venv/opt/portaudio/usr/lib/x86_64-linux-gnu"

mkdir -p "$RUN_DIR" "$LOG_DIR"

load_env() {
  export PATH="$HOME/.local/bin:$PATH"
  if [ -d "$PORTAUDIO_DIR" ]; then
    export LD_LIBRARY_PATH="$PORTAUDIO_DIR${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
  fi
  if command -v wpctl >/dev/null 2>&1; then
    wpctl set-volume @DEFAULT_AUDIO_SOURCE@ 0.70 >/dev/null 2>&1 || true
  fi

  if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
  fi

  export VPS_PANEL_URL="${VPS_PANEL_URL:-https://187.77.202.176.sslip.io}"
  export STUDIO_DEFAULT_TARGET="vps"
  export OPENAI_BASE_URL="${VPS_PANEL_URL%/}/v1"
  export VLLM_HOST="${VPS_PANEL_URL%/}"
  export JARVIS_SKIP_MODEL_PICK=1
  export OMP_NUM_THREADS=2
  export MKL_NUM_THREADS=2
  export OPENBLAS_NUM_THREADS=2
  export VECLIB_MAXIMUM_THREADS=2
  export NUMEXPR_NUM_THREADS=2

  if [ -n "${VPS_PANEL_TOKEN:-}" ]; then
    export OPENAI_API_KEY="$VPS_PANEL_TOKEN"
    export VLLM_API_KEY="$VPS_PANEL_TOKEN"
  elif [ -f "$VAULT_JSON" ]; then
    local vault_key=""
    vault_key="$("$VENV_PY" -c '
import json, sys
try:
    v = json.load(open(sys.argv[1]))
    k = (v.get("OPENAI_API_KEY") or v.get("GEMINI_API_KEY") or "").strip()
    print(k)
except Exception:
    print("")
' "$VAULT_JSON" 2>/dev/null || true)"
    export OPENAI_API_KEY="${vault_key:-${OPENAI_API_KEY:-local-gateway-vault}}"
    export VLLM_API_KEY="${VLLM_API_KEY:-$OPENAI_API_KEY}"
  fi
}

is_pid_running() {
  local pid_file="$1"
  [ -f "$pid_file" ] || return 1
  local pid
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null
}

stop_pid_file() {
  local pid_file="$1"
  local label="$2"
  if is_pid_running "$pid_file"; then
    local pid
    pid="$(cat "$pid_file")"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.25
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    echo "$label parado (PID $pid)."
  fi
  rm -f "$pid_file"
}

cmd_stop() {
  stop_pid_file "$RUN_DIR/clap_listener.pid" "Clap Listener"
  stop_pid_file "$RUN_DIR/jarvis_serve.pid" "Jarvis Server"
  pkill -f "$JARVIS_HOME/clap_listener.py" 2>/dev/null || true
  pkill -f "jarvis serve --host 127.0.0.1 --port 8090" 2>/dev/null || true
  echo "OpenJarvis encerrado (RAM liberada)."
}

cmd_status() {
  local server_running=false server_pid=""
  if is_pid_running "$RUN_DIR/jarvis_serve.pid"; then
    server_running=true
    server_pid="$(cat "$RUN_DIR/jarvis_serve.pid")"
  fi
  echo "OpenJarvis Status:"
  echo "  Modo Escuta: Push-to-Talk Sob Demanda (Gatilho de Palmas Desativado)"
  echo "  API Server (8090): $server_running ${server_pid:+(PID $server_pid)}"
  echo "  Gateway Cascade (VPS): ${VPS_PANEL_URL:-https://187.77.202.176.sslip.io}/v1"
}

start_daemon_serve() {
  stop_pid_file "$RUN_DIR/clap_listener.pid" "Clap Listener"
  pkill -f "$JARVIS_HOME/clap_listener.py" 2>/dev/null || true
  if is_pid_running "$RUN_DIR/jarvis_serve.pid"; then
    echo "Jarvis Server já está rodando (PID $(cat "$RUN_DIR/jarvis_serve.pid"))."
    return 0
  fi
  load_env
  nohup nice -n 5 "$JARVIS_BIN" serve --host 127.0.0.1 --port 8090 \
    > "$LOG_DIR/jarvis_serve.log" 2>&1 < /dev/null &
  local pid=$!
  echo "$pid" > "$RUN_DIR/jarvis_serve.pid"
  echo "OpenJarvis API Server iniciado em background na porta 8090 (PID $pid, escuta sob demanda). Log: $LOG_DIR/jarvis_serve.log"
}

usage() {
  cat <<EOF
Uso: $(basename "$0") [OPÇÃO]

Modos sob demanda (Push-to-Talk / Comando Ativo):
  --ptt, --voice, voz      Executa 1 captura de voz sob demanda (Push-to-Talk) e responde
  --interactive            Inicia terminal interativo Push-to-Talk (ENTER para falar ou digite)
  --ask-text "pergunta"    Envia comando de texto ativo com resposta falada em 48kHz
  --chat, chat             Inicia chat interativo no terminal (texto)
  --serve, --web, web      Inicia servidor API/Web OpenJarvis na porta 8090

Controle em background (usado pelo Painel :9000):
  --daemon [serve|both]         Inicia API Server em background (sem daemon de palmas)
  --stop                        Para os processos em background e libera RAM
  --status                      Exibe status atual dos processos
EOF
}

MODE="${1:---interactive}"
case "$MODE" in
  --ptt|--voice|voz)
    shift || true
    load_env
    exec "$VENV_PY" "$JARVIS_HOME/clap_listener.py" --ptt "$@"
    ;;
  --interactive|interativo)
    shift || true
    load_env
    exec "$VENV_PY" "$JARVIS_HOME/clap_listener.py" --interactive "$@"
    ;;
  --ask-text)
    shift || true
    load_env
    exec "$VENV_PY" "$JARVIS_HOME/clap_listener.py" --ask-text "$@"
    ;;
  --chat|chat)
    shift || true
    load_env
    exec "$JARVIS_BIN" chat "$@"
    ;;
  --voice-chat)
    shift || true
    load_env
    exec "$VENV_PY" "$JARVIS_HOME/clap_listener.py" --interactive "$@"
    ;;
  --serve|--web|web)
    shift || true
    load_env
    exec "$JARVIS_BIN" serve --host 127.0.0.1 --port 8090 "$@"
    ;;
  --ask|ask)
    shift || true
    load_env
    exec "$JARVIS_BIN" ask "$@"
    ;;
  --daemon)
    start_daemon_serve
    ;;
  --stop|stop)
    cmd_stop
    ;;
  --status|status)
    cmd_status
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    load_env
    exec "$JARVIS_BIN" "$@"
    ;;
esac
