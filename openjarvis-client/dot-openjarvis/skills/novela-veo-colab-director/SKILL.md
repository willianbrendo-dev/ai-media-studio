---
name: novela-veo-colab-director
description: Diretor Cinematográfico Clean Cinema (Plano e Contraplano sem poluição visual) para micro-séries multi-personagem com geração de Production Pack para Google Flow (Veo 3.1 Lite/Fast), Costurador Automático de Takes (.mp4 via faster-whisper INT8 + 48kHz Estéreo SoXR) e Ponte Remota Google Colab GPU (EchoMimicV3 / LatentSync 1.5).
---

# Novela Studio v2.5 · Cinema Clean, Google Flow (Veo 3.1) & Colab GPU Director

Use esta skill sempre que o usuário pedir para criar novelinhas, micro-séries de humor nordestino/regional ou cenas com múltiplos personagens sem "caixas de texto/placas" cobrindo a tela — priorizando ritmo rápido de corte seco (**Plano e Contraplano / Over-the-Shoulder**) e lip-sync real.

## Princípio Cinematográfico Inegociável ("Clean Cinema Mode")
- **Tela 100% limpa (`clean_cinema_mode=True`, `subtitles_mode="none"` por padrão):** proibido cobrir o rosto ou o corpo dos personagens com crachás, banners, etiquetas de cena ou cartões escuros gigantes de diálogo.
- Quando legendas forem solicitadas, use `subtitles_mode="minimal"` (apenas 1 linha enxuta branca com contorno preto fino de `3px`, fundo transparente, dentro da Safe-Zone).
- **Direção Shot-Reverse-Shot (Plano / Contraplano):**
  1. **Take 1 (`over_shoulder`):** Plano médio por cima do ombro de quem ouve (`35mm`), foco nítido no personagem falando com postura ativa e figurantes discretos ao fundo.
  2. **Take 2 (`reverse_over_shoulder`):** Contraplano invertido (`35mm`) pelo outro lado, cortando seco para a resposta do segundo personagem.
  3. **Take 3+ (`reaction_closeup`):** Close-up dramático (`50mm`) de reação imediata ("Oxente!").

## Os 3 Motores de Produção

### Modo 1 · Diretor Google Flow (Veo 3.1 Pack + Costurador de Takes)
1. **Gerar Production Pack (`POST /api/quick/novela/flow-pack` ou `studio_generate_flow_pack`):**
   - Produz **Ingredients limpos (`1080x1920` JPEG)** via Flux Dev (`black-forest-labs/flux.1-dev`) sem qualquer texto/marca.
   - Redige **Prompts Bilíngues Veo 3.1 (`veo_prompt`)**: direção técnica de câmera/lente/ação/iluminação em inglês + fala exata em português brasileiro com sotaque nordestino entre aspas + trava obrigatória `"No subtitles, no text overlay, no watermarks, realistic lip-sync speech in Brazilian Portuguese."`
   - Calcula automaticamente os créditos do Google Flow Pro (`10 créditos/take` no Veo 3.1 Lite · `20 créditos/take` no Veo 3.1 Fast).
2. **Costurar Takes (`POST /api/quick/novela/upload-takes` -> `POST /api/quick/novela/stitch-takes` ou `studio_stitch_novela_takes`):**
   - Depois de gerar os clipes `.mp4` de 4s–8s no Google Flow, suba na ordem certa para aparar apenas o silêncio inicial/final via `faster-whisper` INT8 + `silencedetect` (sem cortar sílabas), padronizar em `1080x1920 @ 30fps` e equalizar em **48kHz Estéreo SoXR (`loudnorm I=-16`)**.

### Modo 2 · Colab GPU Lip-Sync Automático (`EchoMimicV3` / `LatentSync 1.5`)
- Fornece o notebook 1-clique (`GET /api/colab/notebook` -> `/opt/ai-media-studio/tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb`) que sobe servidor FastAPI (`/health` + `/animate-take`) + túnel Cloudflare (`trycloudflare.com`) em GPU T4/L4.
- Salve a URL do túnel com `POST /api/colab/config` e execute `POST /api/quick/novela` com `render_mode="colab_gpu"` e `colab_engine="echomimic_v3" | "latentsync"`.

### Modo 3 · Preview Rápido VPS (`render_mode="vps_preview"`)
- Renderiza diretamente na CPU da VPS (`0 MB RAM Docker`) um storyboard animado Shot-Reverse-Shot limpo para aprovar ritmo, vozes e cortes antes de gastar créditos do Google Flow ou unidades do Colab.
