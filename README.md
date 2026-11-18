# AI Media Studio

Ecossistema completo de produção audiovisual com IA e automação na VPS (`root@187.77.202.176` / `https://187.77.202.176.sslip.io`) + operador local de voz/visão **OpenJarvis** (`openjarvis-client/`).

> **Segurança (Zero Segredos no Git):** este repositório **nunca** armazena `.env`, `vault.json`, `.htpasswd`, chaves de API (`GEMINI_API_KEY`, `NVIDIA_API_KEY`, `OPENROUTER_API_KEY`, etc.), tokens Bearer (`VPS_PANEL_TOKEN`), `node_modules`, `.venv`, bancos SQLite nem binários pesados (`.mp4`, `.mp3`, `.wav`, pesos de modelo). Consulte `.gitignore` e execute `python3 scripts/secret-scan.py` antes de qualquer commit.

---

## Arquitetura do Repositório

| Caminho | Origem real | Função |
| --- | --- | --- |
| `media-control-panel/` | VPS `/opt/ai-media-studio/media-control-panel/` | Painel FastAPI (`:9000`), orquestração 1-stack-por-vez (8 GB RAM guard), cofre UI, Central de Skills, `/api/campaigns`, OpenJarvis Cloud |
| `tools/hyperframes/` | VPS `/opt/ai-media-studio/tools/hyperframes/` | Motor zero-RAM `vps_ad_runner.py` (HTML/GSAP + Chromium + Edge-TTS 48 kHz + FFmpeg 1080×1920) |
| `tools/video-use/` | VPS `/opt/ai-media-studio/tools/video-use/` | Motor zero-RAM `vps_zero_cost_runner.py` (Faster-Whisper + VAD + color grade + ASS burn-in Shopee-safe) |
| `campaigns/elseve-kit2/` | VPS `/opt/ai-media-studio/campaigns/elseve-kit2/` | Ativos e manifesto `campaign.json` da campanha Kit 2 Óleos Elseve L'Oréal Paris |
| `dev-skills-vault/` | VPS `/opt/ai-media-studio/dev-skills-vault/` | Cofre de +27 skills de desenvolvimento ativadas via `modo-dev` |
| `MoneyPrinterTurbo/`, `VoiceStudio/`, `openfilm/`, `open-higgsfield/`, `open-seo/`, `supoclip/`, `youtube-automation-agent/` | VPS `/opt/ai-media-studio/*` | Os 7 stacks Docker sob demanda do estúdio (com correções de Nginx/subpath, worker ARQ e safe-zone) |
| `openjarvis-client/` | PC Local `~/open-jarvis/` + `~/.openjarvis/` | Operador Master (`studio_controller_mcp.py`, `clap_listener.py`, `webcam_vision.py`, `edge_tts_backend.py`, `SKILL.md`, patch upstream) |
| `infra/` | VPS `/etc/nginx/`, `/etc/systemd/`, `/usr/local/bin/` | Unit `media-panel.service`, configuração sanitizada do Nginx (`media-studio.conf`), `modo-dev` e `.env.example` |
| `tests/` | PC Local `~/e2e_*.mjs`, `~/verify_*.py` | Suítes Playwright E2E interativas (Desktop `1440×900` + Mobile `390×844`) e auditoria visual de Safe-Zone |
| `scripts/` | — | Utilitários `sync-from-vps.sh` e `secret-scan.py` |

---

## Padrão Shopee Video Safe-Zone (`1080×1920`)

Todos os motores verticais (`hyperframes`, `video-use`, `MoneyPrinterTurbo` preset `shopee_safe`) seguem a malha calibrada em aparelho real Android:
- **Topo (`Y: 0–215px`):** 100% livre (status bar + barra de busca `< [Q Pesquisar]`).
- **Topo Safe:** pill/hook em `Y: 220px`; palco visual hero entre `Y: 285px` e `960px` (`X: 60–910px`).
- **Legendas (`#sub-box`):** `Y: 985–1095px`, `X: 60–880px` (máximo 5 palavras/bloco).
- **CTA (`#cart-cta-zone`):** `Y: 1110–1165px`.
- **Base (`Y: 1175–1920px`) & Lateral Direita (`X: 910–1080px`):** 100% livres para o Card Branco de Produto, descrição, ícones de interação e botões sociais.

---

## Como Atualizar Este Repositório e Fazer Rollback

### 1. Salvar novas alterações após atualizar a VPS ou OpenJarvis
```bash
bash scripts/sync-from-vps.sh
python3 scripts/secret-scan.py
git status
git add -A
git commit -m "chore(studio): descreva a atualização"
git push origin main
```

### 2. Voltar atrás (Rollback) se uma atualização quebrar algo
```bash
# Inspecionar commits anteriores
git log --oneline -n 15

# Exemplo: restaurar um módulo específico (ex.: media-control-panel ou tools/hyperframes)
git checkout <HASH_ESTAVEL> -- media-control-panel/
rsync -avz --delete \
  --exclude='.env' --exclude='vault.json' --exclude='.venv' --exclude='__pycache__' \
  ./media-control-panel/ root@187.77.202.176:/opt/ai-media-studio/media-control-panel/
ssh root@187.77.202.176 "systemctl restart media-panel"
```
