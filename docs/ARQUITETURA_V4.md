# Arquitetura Cinema Engine v4.0 — AI Media Studio

**Data:** 2026-11-19
**Branch:** `feat/v4-cinema` (baseado em `main` @ `81a24e1`)
**Objetivo:** Diagnosticar com evidência o abismo entre `hoje.mp4` (v3.1) e `espectativa.mp4`, cruzar os 3 relatórios de LLMs com medições objetivas e definir a arquitetura v4.0 dentro da restrição de **custo adicional zero**.

---

## FASE 0 — Reconhecimento do Código Atual (Fonte da Verdade)

### 0.1 O que o código realmente faz hoje

1. **Orquestração e Roteiro (`media-control-panel/guided_pipelines.py`)**
   - `generate_novela_script` (`guided_pipelines.py:992-1164`): gera 4 cortes (`viral_4_cuts`) via cascata IA (NVIDIA NIM Nemotron em Tier 1). Embora o prompt da IA mencione `over_shoulder`, `reverse_over_shoulder`, `reaction_closeup` (`guided_pipelines.py:1040`) e fixe o Corte 4 como `emotion = "shame"` e `camera_action = "dramatic_push"` (`guided_pipelines.py:1106,1112,1136`), o executor visual downstream ignora a direção de câmera/olhar. `[LIDO NO CÓDIGO guided_pipelines.py:992-1164]`
   - `build_fallback_novela_script` (`guided_pipelines.py:187-307`): gera roteiro determinístico de 4 cortes com Corte 2 `lead_in_pause=0.12` e Corte 4 silencioso `2.0s` (`shame`). `[LIDO NO CÓDIGO guided_pipelines.py:187-307]`

2. **Decupagem e Geração Visual (`tools/hyperframes/vps_novela_runner.py`)**
   - `FRONTAL_SINGLE_SUBJECT_DIRECTIVE` (`vps_novela_runner.py:304-308`): impõe `"single character only (strictly one person in frame, no second person, no foreground shoulder), facing the camera directly with direct frontal eye-contact into the lens"`. `[LIDO NO CÓDIGO vps_novela_runner.py:304-308]`
   - `SHOT_FRAMINGS` (`vps_novela_runner.py:310-353`): **todos** os 7 enquadramentos (`establishing`, `medium`, `over_shoulder`, `reverse_over_shoulder`, `close_up`, `reaction_closeup`, `reaction`) injetam `{FRONTAL_SINGLE_SUBJECT_DIRECTIVE}` tanto no `flux_cue` quanto no `veo_cue`. Ou seja, mesmo quando o roteiro pede `over_shoulder`, o prompt enviado ao Flux proíbe segunda pessoa/ombro e exige olhar na lente. `[LIDO NO CÓDIGO vps_novela_runner.py:310-353]`
   - `compile_dramatic_cuts` (`vps_novela_runner.py:432-534`): força Corte 2 `lead_in_pause` entre `0.08` e `0.18s` (default `0.12s`, `L486`), e se Corte 4 não existir cria `emotion: "happiness"`, ou preserva `cut4.get("emotion") or "happiness"` (`L520,531`). `[LIDO NO CÓDIGO vps_novela_runner.py:432-534]`
   - `_resolve_turn_visual` (`vps_novela_runner.py:859-954`):
     - Gera retratos via NVIDIA NIM `black-forest-labs/flux.1-dev` em **768x1344** (22 steps) ampliados via Lanczos para 1080x1920 (`vps_novela_runner.py:705-746`). `[LIDO NO CÓDIGO vps_novela_runner.py:705-746]`
     - Impõe `closed_mouth_mandate` + braços baixados (`L911-914`). `[LIDO NO CÓDIGO vps_novela_runner.py:911-914]`
     - `master_cache_key = ("v31_frontal_moda_lock", speaker_key, style_norm)` (`L879`): na 2ª aparição do personagem (`close_up`, `reaction_closeup`, `reaction` ou `is_silent_reaction`), **não gera novo plano** — apenas aplica crop óptico +14% (`scale=1232:2190:flags=lanczos,crop=1080:1920:76:95`) ou `shutil.copy2` sobre a imagem da 1ª aparição (`L882-899`). `[LIDO NO CÓDIGO vps_novela_runner.py:879-899]`
   - `render_novela_episode` (`vps_novela_runner.py:2205-2675`):
     - Quando `is_silent=True` (Corte 4), escolhe `"shame"` se `_tone_is_dramatic` ou `"happiness"` caso contrário (`L2278`), e chama `_synthesize_reaction_soundtrack_wav` (`L2056-2138`: cama `-28 LUFS` + síntese senoidal `aevalsrc` + mini fala via `rve.synthesize_regional_turn`). `[LIDO NO CÓDIGO vps_novela_runner.py:2056-2138,2277-2278]`
     - No modo default (`enable_lipsync=True`), chama `nla.animate_character_talking_take` por turno (`L2471-2491`), concatena os MP4s, aplica cama de ruído rosa contínua `-33 LUFS` (`_create_master_ambience_bed_wav`, `L2043-2053,2542`) + `loudnorm=I=-16:TP=-1.5:LRA=11` (`L2545`), e queima legenda `.ass` `DejaVu Sans` tamanho `42` na parte inferior da safe-zone (`L2524-2538`). `[LIDO NO CÓDIGO vps_novela_runner.py:2444-2565]`
   - `generate_flow_veo_pack` (`vps_novela_runner.py:1026-1173`) e `stitch_novela_takes` (`vps_novela_runner.py:1685-1978`): já existem e implementam preparo de Ingredients + prompts bilíngues Veo 3.1 + corte de silêncios via `faster-whisper` INT8 + re-dublagem opcional via Colab `/lipsync_video`. `[LIDO NO CÓDIGO vps_novela_runner.py:1026-1173,1685-1978]`

3. **Motor de Áudio Regional (`tools/hyperframes/regional_voice_engine.py`)**
   - Hierarquia em `synthesize_regional_turn` (`regional_voice_engine.py:1100-1348`): (0) Cache SHA-256 em `voices/cache/`; (1) `seed_vc_guide` (`/voice-convert` no Colab); (2) `f5_tts_clone` (`/tts-clone` no Colab); (3A) Gemini Live Audio WebSocket (`gemini-3.1-flash-live-preview` / `gemini-2.5-flash-native-audio-latest` / `gemini-3.8-live`); (3B) Pool Round-Robin REST (`GEMINI_TTS_MODELS`); (4) `edge-tts` fonético. `[LIDO NO CÓDIGO regional_voice_engine.py:51-62,1100-1348]`
   - `_pcm_to_48k_stereo_wav` (`L381-417`): aplica `atempo=1.16` quando `comedy_fast=True` e `loudnorm=I=-16:TP=-1.5:LRA=11`. `[LIDO NO CÓDIGO regional_voice_engine.py:381-417]`
   - **Lacuna crítica:** `synthesize_regional_turn` **não possui Quality Gate acústico/ASR** após receber o áudio do WebSocket/REST — apenas verifica `raw_dur < 0.35` e `raw_dur > max_sane_sec` (`L855-861`) ou `len(raw) < 1000` (`L644`). Se o modelo devolver ruído/zumbido de duração válida, ele é salvo no cache SHA-256 e enviado para a GPU. `[LIDO NO CÓDIGO regional_voice_engine.py:644,855-861,1256]`

4. **Animador GPU e Colab (`tools/hyperframes/novela_lipsync_animator.py` & `ai_studio_colab_novela_gpu.ipynb`)**
   - `_map_colab_moda_emotion` (`novela_lipsync_animator.py:775-792`): quando `is_silent_reaction=True` e `emotion in ("", "neutral", "shame", "warm", "comedy", "laughing", "happiness", "happy")`, **sobrescreve `"shame"` para `"happiness"` com `cfg_scale=1.35`** (`L783-785`). `[LIDO NO CÓDIGO novela_lipsync_animator.py:775-792]`
   - `_try_tier1_colab_animate` (`L844-1097`): se `/health` retorna `moda_ready=True`, envia todos os cortes para `/animate_turn` (`engine="moda_livevasa_l4"`). Senão cai para 2 estágios (`LivePortrait fp16` -> `LatentSync 1.5` em chunks `<= 2.85s`). Se Colab offline/ausente, cai para `_try_zerogpu_cloud_animate` (`L1375-1564`: `MoDA-fast-talking-head`, `LongCat-Video-Avatar-1.6`, `EchoMimic`, `wan2-2-fp8da-aoti-faster`, `ltx-video-distilled`). Distorção 2D local é banida (`L486-505`). `[LIDO NO CÓDIGO novela_lipsync_animator.py:486-505,844-1564]`
   - **Divergência Dossiê vs Notebook (`ai_studio_colab_novela_gpu.ipynb`):** O dossiê afirma que o notebook executa `/content/moda_l4_server.py` com `LiveVASAPipeline`. Porém, no notebook versionado (`notebooks/ai_studio_colab_novela_gpu.ipynb:172-528`, idêntico a `tools/colab-gpu-worker/ai_studio_colab_novela_gpu.ipynb`), a Célula 3 apenas escreve um stub de 4 linhas comentadas em `/content/moda_l4_server.py` (`L173-178`) e o servidor FastAPI executado na porta `8787` roda `_stage1_liveportrait` + `_stage2_lipsync_video` (`LatentSync 1.5` / `MuseTalk 1.5`), embora `/health` reporte `'moda_ready': os.path.exists('/content/MoDA/pretrain_weights')` (`L359`). Em runtime atual na VPS, `/api/colab/status` retornou `online: false` (`COLAB_GPU_URL` offline). `[LIDO NO CÓDIGO notebooks/ai_studio_colab_novela_gpu.ipynb:172-528] [MEDIDO]`

5. **Contratos de API do Painel (`media-control-panel/main.py` & `static/index.html`)**
   - Rotas ativas consumidas pela UI: `/api/quick/novela/generate-script`, `/api/quick/novela`, `/api/quick/novela/task/{task_id}`, `/api/quick/novela/flow-pack`, `/api/quick/novela/upload-takes`, `/api/quick/novela/stitch-takes`, `/api/quick/novela/stitch-takes/task/{task_id}`, `/api/quick/novela/preview-voice`, `/api/quick/novela/preview-audio/{filename}`, `/api/quick/novela/upload-line-audio`, `/api/colab/notebook`, `/api/colab/status`, `/api/colab/config`. `[LIDO NO CÓDIGO main.py:3172-3701, index.html:5457-6102]`

6. **Auditor Atual (`tools/hyperframes/verify_lipsync_motion.py`)**
   - Mede apenas movimento relativo da ROI da boca e piscar compensando translação 2D por template matching da ponte nasal (`cv2.matchTemplate`, `L72-96,155-230`). Não mede enquadramento, yaw/linha de olhar, OTS/múltiplos rostos, movimento corporal/fundo, dinâmica LRA nem reuso estático de retratos. `[LIDO NO CÓDIGO verify_lipsync_motion.py:72-304]`

---

## FASE 1 — Reprodução e Evidência Objetiva (`tests/cinema_qa.py`)

Medições executadas na VPS via `/opt/ai-media-studio/tools/video-use/.venv/bin/python` (`cv2`, `mediapipe 1.2.3 FaceLandmarker`, `faster-whisper base INT8`, `ffmpeg ebur128`). Nota: `md5sum(docs/refs/hoje.mp4) == md5sum(/opt/ai-media-studio/exports/novela/novela_moda_l4_v31.mp4) == 93741d4137345d740d4e668859426df6`. `[MEDIDO]`

### 1.1 Tabela Comparativa Medida (`docs/measurements/cinema_qa_*.json`)

| Métrica | `hoje.mp4` (`novela_moda_l4_v31.mp4`) | `espectativa.mp4` (Gabarito) | Critério Calibrado v4 |
|---|---|---|---|
| Resolução / FPS / Duração | `1080x1920` / `30.0` / `15.18s` | `720x1280` / `24.0` / `10.12s` | Vertical 9:16 |
| Movimento médio / mediana (`180x320 \|Δ\|`) | `0.58` / `0.26` | `4.84` / `3.53` | `>= 3.0` / `>= 2.5` |
| Movimento cabeça / tronco+mãos / fundo externo | `1.02` / `0.27` / `0.06` | `6.15` / `5.08` / `2.59` | Corpo+câmera ativos |
| Cortes visuais detectados (`MAD > 18.5`) | `2` (`4.13s`, `8.10s`) | `3` (`3.12s`, `7.12s`, `7.83s`) | `>= 3` cortes (`>= 4` planos) |
| Duração de cada plano (`s`) | `[4.13, 3.97, 7.00]` (`std=1.39s`) | `[3.12, 4.00, 0.71, 2.12]` (`std=1.22s`) | `min <= 1.5s`, `max <= 5.0s` |
| Reuso estático / crop `+14%` | **Sim** (Plano 1 -> 3, `NCC=0.9886`) | **Não** (`0`) | `0` reusos estáticos |
| Frames com fala fora da lente (`\|Yaw\| > 8°`) | `15.0%` (`85.0%` olhando na lente) | `96.6%` (`+67.4°`, `-34.98°`, `+54.35°`, `+53.07°`) | `>= 60.0%` fora da lente |
| Planos c/ 2+ rostos ou OTS foreground | `0` (`max_faces=1`) | `3` de 4 (`max_faces=3`, OTS em P1/P2/P3) | `>= 1` plano por cena |
| Loudness / LRA / True Peak (`ebur128`) | `-15.2 LUFS` / `2.1 LU` / `-1.3 dBFS` | `-15.6 LUFS` / `6.5 LU` / `-2.5 dBFS` | `-16±1.5 LUFS`, `LRA >= 5.0`, `TP <= -1.5` |
| Janelas `0.25s` `< -40 dB` / `< -45 dB` | `0` / `0` (`min=-35.33 dBFS`) | `2` / `0` (`min=-41.70 dBFS`) | `0` janelas `< -45 dBFS` |
| Maior lacuna sem fala no diálogo (pré-reação) | **`5.53s`** (`7.74s -> 13.26s`, Corte 3 corrompido) | `0.76s` (`3.34s -> 4.10s`) | `<= 2.0s` |
| Legendas queimadas | Sim (`83.9%`, `Y=1328..1430px`, cobre torso) | Não (`0%`) | Opcional, nunca no torso/boca |
| `verify_lipsync_motion.py` (legado) | `ok: false` (`diff=-0.587`) | `ok: false` (`diff=-0.81`) | **Quebra em cinema (`yaw!=0`)** |
| Modulação labial por landmarks (`mouth_open_std`) | `[0.0456, 0.0178, 0.0399]` | `[0.0654, 0.0255, 0.0356, 0.0070]` | `>= 0.012` em planos falados |
| Critérios `cinema_qa.py` aprovados | **3 / 13 (`FAIL`)** | **13 / 13 (`PASS`)** | `all_criteria_passed = true` |

### 1.2 Evidências Forenses Adicionais (Inspeção Visual & Áudio)
1. **Bug grave de áudio no Corte 3 de `hoje.mp4` (`[MEDIDO]`):** Na janela `[8.10s -> 12.87s]` (Corte 3), o RMS é `-14.02 dBFS`, mas `ZCR = 0.0107` (vs `0.1776` no Corte 1 e `0.0931` no Corte 2) e o Whisper (mesmo com `vad_filter=False`) detectou **0 palavras**. A legenda exibe *"Hahaha! Se esse motor pegar de primeira hoje / o senhor ganha até café com pão de queijo!"*, enquanto o áudio contém apenas zumbido grave sem fala. No Corte 4 (`13.26s -> 14.90s`), o Whisper transcreve *"Ha-ha-ha! Ih, tá, seu Bartião!"* (erro de pronúncia sobre `"Eita, Seu Bastião"`).
2. **Inexistência de corte visual entre Corte 3 e Corte 4 em `hoje.mp4` (`[MEDIDO]`):** Confirmado via `hoje_cut34.png`: aos `12.90s` (`frame 387`), `MAD = 2.91` — Enzo permanece no mesmo retrato `+14%` zoomed por `7.0s` contínuos (`8.10s..15.10s`), apenas resetando para um sorriso aberto em `13.0s`.
3. **L-cuts e atuação de figurantes em `espectativa.mp4` (`[MEDIDO]`):**
   - Corte visual 1 ocorre em `3.12s`, mas *"vergonha!"* termina em `3.34s` (**L-cut de `0.22s`** sobre a mulher mais velha piscando antes de falar em `4.10s`).
   - Corte visual 2 ocorre em `7.12s`, mas *"homem."* termina em `7.26s` (**L-cut de `0.14s`** sobre o noivo).
   - No Plano 4 (`7.83s..9.96s`, reação muda), entre `8.83s..9.08s` o MAD sobe para `10.89..12.63` com **figurantes no fundo virando para fofocar** (`espectativa_grid.png`), elevando `outer_background_zone_mean` para `2.59` (43x maior que `0.06` de `hoje.mp4`).
4. **Calibração dos limiares (`[MEDIDO]`):**
   - `espectativa.mp4` tem `2` janelas de `0.25s` em `-41.7 dBFS` (pausas dramáticas naturais). O critério de silêncio morto foi calibrado para `< -45 dBFS` (`windows_below_minus_45db == 0`), preservando pausas dramáticas sem permitir silêncio digital (`-73 dB`).
   - `verify_lipsync_motion.py` falha em `espectativa.mp4` (`-0.81`) porque seu `matchTemplate` nasal exige cabeça estática frontal. A métrica primária de sincronismo em cena cinematográfica passa a usar abertura labial por landmarks (`mouth_open_std >= 0.012`) + correlação com envelope RMS no plano.

---

## FASE 2 — Matriz de Cruzamento (Relatórios das LLMs + Hipóteses H1–H9)

| Afirmação | Origem | Evidência no Código | Evidência Medida (`hoje` vs `espectativa`) | Status | Impacto Estimado na Qualidade |
|---|---|---|---|---|---|
| **H1:** MoDA/LivePortrait/LatentSync animam só cabeça sobre foto estática; não geram corpo, mãos, câmera, profundidade nem 2 pessoas | Prompt / Claude / Gemini / ChatGPT | `novela_lipsync_animator.py:844-1097` recebe `image_path` 1-pessoa e gera face warping/talking-head | `lower_torso_hands_zone_mean`: `0.27` vs `5.08` (18.8x); `max_faces`: `1` vs `3` | **CONFIRMADA** | **Crítico (raiz ~55% do gap)** |
| **H2:** v3.1 impôs `FRONTAL_SINGLE_SUBJECT_DIRECTIVE` (olhar na lente, zero 2ª cabeça/ombro, boca fechada), matando campo/contracampo e OTS | Prompt / Claude / Gemini / ChatGPT | `vps_novela_runner.py:304-353,911-914` injeta a diretiva em todos os 7 `SHOT_FRAMINGS` | Frames falados fora da lente: `15.0%` vs `96.6%`; planos OTS/2+: `0` vs `3` | **CONFIRMADA** | **Crítico (~20% do gap)** |
| **H3:** Plano de reação reutiliza o mesmo retrato com crop `+14%`, sem nova performance | Prompt / Claude / Gemini / ChatGPT | `vps_novela_runner.py:879-896` (`master_cache_key` + `scale=1232:2190,crop=1080:1920`) | Plano 1 -> Plano 3 com `NCC=0.9886` (`optical_crop_14pct`); Corte 3->4 sem corte (`MAD=2.91`) | **CONFIRMADA** | **Alto (~8% do gap)** |
| **H4:** Emoção é rótulo único (`enum + cfg_scale`), Corte 4 forçado a `happiness` | Prompt / Claude / Gemini / ChatGPT | `vps_novela_runner.py:520,531` e `novela_lipsync_animator.py:783-785` convertem `shame`/`warm` para `happiness @ 1.35` | `hoje_grid.png` em `13.0s`: Enzo abre sorriso largo fora de contexto após 5s mudo | **CONFIRMADA** | **Médio-Alto (~5% do gap)** |
| **H5:** Flux dev gera retrato "banco de imagem" sem iluminação prática nem cenário compartilhado/profundidade | Prompt / Claude / Gemini | `vps_novela_runner.py:705-708,904-919` gera `768x1344` text-only por turno, sem imagem de referência do set/personagem | `hoje_grid.png`: Shot 1/3 (varanda clara dia) vs Shot 2 (parede escura bokeh noturno) não dividem espaço | **CONFIRMADA** | **Alto (~10% do gap, parte ligada a H1/H2)** |
| **H6:** TTS plano + `loudnorm -16` + cama `-33 dB` constante achatam dinâmica (LRA 2.1 vs 6.5) sem acústica da cena | Prompt / Claude / Gemini | `vps_novela_runner.py:2043-2053,2545` + `regional_voice_engine.py:408` | `LRA`: `2.1 LU` vs `6.5 LU`; `TP`: `-1.3` vs `-2.5 dBFS`; Corte 3 com `ZCR=0.0107` e `0` palavras | **CONFIRMADA** (e agravada por bug sem Quality Gate no Corte 3) | **Médio-Alto (~7% do gap)** |
| **H7:** Edição com cortes uniformes `~4s`, sem J/L-cut; legenda minúscula sobre o corpo | Prompt / Claude / Gemini | `vps_novela_runner.py:2508-2538` (corte seco por duração TTS; `.ass` `DejaVu Sans` `42` em `margin_v`) | Cortes `4.13s / 3.97s / 7.00s` vs `3.12s / 4.00s / 0.71s / 2.12s`; L-cuts de `0.22s` e `0.14s` em `espectativa`; legenda em `Y=1328..1430px` (`overlaps_torso_zone=true`) | **CONFIRMADA** | **Médio (~5% do gap)** |
| **H8:** v2.6–v3.1 foram remendos na mesma família de cabeça; `verify_lipsync_motion.py` só mede boca/piscar | Prompt / Claude / ChatGPT | `verify_lipsync_motion.py:72-304` + histórico `git log` `65a4f95..7e066b4` | `verify_lipsync_motion.py` reprova `espectativa.mp4` (`ok: false, -0.81`) por exigir cabeça estática | **CONFIRMADA** | **Sistêmico (processo de QA cego)** |
| **H9:** 7 das 9 capacidades do estúdio não estão no caminho crítico da novela | Prompt / Claude | `main.py:112-256` e `guided_pipelines.py:57-123` (SupoClip, MoneyPrinter, OpenFilm, VoiceStudio, Higgsfield, AgentTube, OpenSEO) | 100% da trilha `novela_regional_studio` roda Zero-RAM fora dessas 7 stacks | **CONFIRMADA** | **Foco de engenharia (evita retrabalho)** |
| **C1:** Corte 3 de `hoje.mp4` está corrompido (sem fala humana, só ruído) | Gemini LLM | `regional_voice_engine.py:855-861,1256` salva no cache sem checagem ASR/ZCR | `hoje [8.10-12.87s]`: `rms=-14.02 dBFS`, `ZCR=0.0107`, Whisper `0` palavras (`max_dialogue_speech_gap=5.53s`) | **CONFIRMADA** | **Crítico (quebra episódio)** |
| **C2:** `espectativa.mp4` (720x1280 @ 24fps) é compatível com saída da família Veo | Claude / Prompt | `vps_novela_runner.py:1026-1173` já previa Flow Veo 3.1 | `720x1280`, `24 fps`, `9.96s` compatível com specs 720p24fps de modelos generativos (Veo/Wan/LTX), porém sem watermark/metadata explícito de origem no MP4 | **NÃO VERIFICADA** (compatível, origem exata não comprovável só pelo container) | Informativo |
| **C3:** Flow Pro dá 1.000 créd/mês + 50/dia; Veo 3.1 Lite = 10 créd/clipe; Fast/Quality aposentam em 05/dez/2026 | Claude / Prompt | `vps_novela_runner.py:1141-1142` já estima 10 créd Lite / 20 créd Fast | Verificado na pesquisa web (fontes Google Support / guias Flow nov/2026): AI Pro = 1.000/mês + 50 grátis/dia; Fast/Quality/Omni 1.1 aposentam em 05/dez/2026; Lite (10 créd) aceita Ingredients em 8s; Omni 1.2 = 3 créd/s @ 720p | **CONFIRMADA** (`[PESQUISADO]`) | **Alto (define orçamento mensal Flow)** |
| **C4:** Usar Gemini Image / Nano Banana Pro ou Flux Kontext pago via fal.ai/Replicate (~$1/ep) | Gemini LLM | `main.py` usa chaves existentes sem billing novo | Testado na VPS: `gemini-2.5-flash-image` e `gemini-3.1-flash-image-preview` retornam `HTTP 429 (quota=0)`. APIs pagas violam custo zero. **Alternativa descoberta:** `black-forest-labs/flux.1-kontext-dev` está ATIVO na NVIDIA NIM (`ai.api.nvidia.com/v1/genai/`) com a chave atual! | **REFUTADA (Gemini Image/APIs pagas); CONFIRMADA alternativa gratuita via NIM Kontext** | **Crítico (viabiliza Storyboard Lock a custo zero)** |
| **C5:** Licença de `FLUX.1-dev` / `F5-TTS-pt-br` / `MoDA` tem restrição não-comercial | ChatGPT | `vps_novela_runner.py:923` usa `flux.1-dev`; `ai_studio_colab_novela_gpu.ipynb:58,132` usa `lixinyizju/moda` e `firstpixel/F5-TTS-pt-br` | Pesquisado no HF: `firstpixel/F5-TTS-pt-br` = `cc-by-nc-4.0`; `lixinyizju/moda` = pesquisa acadêmica; `Wan2.2-S2V-14B` e `InfiniteTalk/MultiTalk` e `LongCat-Video-Avatar 1.5` = Apache 2.0 / MIT; `LTX-2.5` = Community (<$10M/ano gratuito) | **CONFIRMADA** (`[PESQUISADO]`) | **Alto para uso comercial** |
| **C6:** Seed-VC preserva ritmo/tempo e mantém lip-sync em conversão fala-para-fala | Claude / Prompt | `regional_voice_engine.py:1158-1186` e `ai_studio_colab_novela_gpu.ipynb:461-479` já implementam `/voice-convert` | Documentação `Plachtaa/seed-vc` confirma preservação de ritmo/prosódia/F0, mas **sem benchmark medido em PT-BR sobre vídeo** | **NÃO VERIFICADA** (aguarda spike Fase 5) | **Médio-Alto (habilita voz regional em clipes nativos)** |
| **C7:** LongCat-Video-Avatar 1.5 / Wan2.2-S2V-14B / InfiniteTalk / LTX-2.5 cabem e performam em 1x Colab L4 (24 GB) | Prompt / Gemini / ChatGPT | Colab atualmente offline (`online: false`); sem benchmarks gravados | Pesquisado: todos suportam FP8/INT8 em 24 GB com offload/distilled, mas VRAM/tempo/identidade em 1 L4 só se provam com execução | **NÃO VERIFICADA** (aguarda spike Fase 5) | **Crítico (define caminho autônomo L4)** |

---

## FASE 3 — Árvore de Causa Raiz (Gap até `espectativa.mp4`)

```
GAP TOTAL (hoje.mp4 3/13 PASS vs espectativa.mp4 13/13 PASS)
│
├── [1] CAUSA-RAIZ ARQUITETURAL (~80–85% do gap — NÃO resolvível com prompt/parâmetro no MoDA)
│   ├── Motor de "cabeça falante" sobre imagem estática (H1)
│   │   ├── Sintoma: Tronco/mãos congelados (0.27 vs 5.08), fundo morto (0.06 vs 2.59)
│   │   └── Sintoma: Impossível gerar 2 personagens interagindo ou figurantes vivos (max_faces=1 vs 3)
│   ├── Inversão técnica -> regra de direção: FRONTAL_SINGLE_SUBJECT_DIRECTIVE (H2)
│   │   └── Sintoma: 85% olhar fixo na lente (vs 3.4%), 0 planos OTS (vs 3), eixo 180° quebrado
│   └── Continuidade por texto + crop óptico +14% ao invés de Reference Conditioning (H3, H5)
│       └── Sintoma: Cenários incompatíveis entre personagens (Shot 1 dia vs Shot 2 noite), NCC=0.9886 entre planos
│
└── [2] CAUSAS DE DIREÇÃO, ÁUDIO, EDIÇÃO & QA (~15–20% do gap — resolvíveis em código/pipeline)
    ├── Ausência de Audio Quality Gate pós-TTS (`regional_voice_engine.py`) (C1)
    │   └── Sintoma: Corte 3 com 4.77s de zumbido grave sem fala (ZCR=0.0107, 0 palavras ASR)
    ├── Override emocional hardcoded (`shame` -> `happiness @ 1.35`) (H4)
    │   └── Sintoma: Sorriso robótico no Corte 4 independente do drama
    ├── Mixagem chapada (-33 dB contínuo + loudnorm -16) sem J/L-cut nem acústica de cena (H6)
    │   └── Sintoma: LRA=2.1 LU (vs 6.5 LU) e True Peak -1.3 dBFS (> -1.5)
    ├── Decupagem uniforme (~4s) e legenda no peito (H7)
    │   └── Sintoma: Sem plano curto de impacto (<1.5s); legenda Y=1328..1430 cobrindo tronco
    └── Auditor cego a cinema (`verify_lipsync_motion.py`) (H8)
        └── Sintoma: Otimização local de boca/piscar mascarou regressão visual global
```

**Conclusão quantitativa da Fase 3:** Apenas ajustar prompts/parâmetros mantendo `MoDA` + `flux.1-dev` por texto fecha no máximo **~15–20%** do gap (corrigiria Corte 3 mudo, emoção do Corte 4, legenda e LRA do áudio), mas deixaria **~80–85%** do abismo intacto (`lower_torso_hands_zone_mean=0.27`, `outer_background_zone_mean=0.06`, `0` planos OTS/2-pessoas, estética de videochamada). **Trocar a classe do motor visual é obrigatório.**

---

## FASE 4 — Decisão de Arquitetura (Gate)

### 4.1 Pesquisa Verificada em Fontes Primárias (2026-11-19)

| Tecnologia / Modelo | Fonte Primária | Licença | Specs / VRAM / Custo | Observação Crítica |
|---|---|---|---|---|
| **Google Flow (Veo 3.1 Lite / Omni Flash 1.2)** | Central de Ajuda Google Flow & guias (`[PESQUISADO 2026-11-19]`) | Proprietário (uso via créditos Flow) | AI Pro ($19.99 já existente) = **1.000 créd/mês + 50/dia**. Veo 3.1 Lite = **10 créd/clipe** (720p, 4/6/8s, áudio nativo, **Ingredients em 8s**). Omni 1.2 = 3 créd/s @ 720p. Sem bloqueio de horário de pico p/ assinantes | **Veo 3.1 Fast, Quality e Omni 1.1 aposentam em 05/dez/2026.** Uso no Flow deve ser passo humano de 1 clique (sem bot) |
| **NVIDIA NIM `black-forest-labs/flux.1-kontext-dev`** | Testado diretamente em `ai.api.nvidia.com/v1/genai/` (`[MEDIDO 2026-11-19]`) | FLUX.1 Non-Commercial / NVIDIA Developer Terms | Custo adicional **ZERO** na chave NIM atual. Aceita `image` + `prompt` + `aspect_ratio` (`"9:16"`, `"match_input_image"`) | Resolve Reference Conditioning (mesmo cenário/roupa em OTS/contracampo) sem gastar API paga |
| **Gemini API `gemini-2.5-flash-image` / `3.1-flash-image`** | Testado diretamente na API Gemini com chave atual (`[MEDIDO 2026-11-19]`) | Proprietário | Retornou `HTTP 429 (quota=0)` na chave atual | **DESCARTADA** (exige billing novo) |
| **LongCat-Video-Avatar 1.5** | `meituan-longcat/LongCat-Video` (GitHub) / HF (`[PESQUISADO 2026-11-19]`) | **MIT** (comercial OK) | Destilado DMD **8 passos**, Whisper-Large-v3, `--use-int8`, multi-personagem AI2V/AT2V | Exemplo padrão usa 2 GPUs; cabe em 1x 24GB com INT8 — **precisa spike em 1 L4** |
| **Wan2.2-S2V-14B** (`fp8` / GGUF) | `Wan-AI/Wan2.2-S2V-14B` (HF) / `WanVideoWrapper` (`[PESQUISADO 2026-11-19]`) | **Apache 2.0** (comercial OK) | Audio-driven I2V MoE 14B, blocos de 77 frames, corpo + câmera + lip-sync; roda em 24GB via `fp8`/offload | Candidato forte para atuar corpo+mãos em áudio PT-BR — **precisa spike na L4** |
| **InfiniteTalk / MultiTalk** | `MeiGen-AI/InfiniteTalk` & `MultiTalk` (GitHub/HF, `[PESQUISADO 2026-11-19]`) | **Apache 2.0** (comercial OK) | Base Wan2.1-I2V-14B, suporta 2+ pessoas no mesmo quadro com áudios separados; roda em 24GB c/ GGUF/quant/480p | Candidato multi-personagem aberto — **precisa spike na L4** |
| **WanGP (`deepbeepmeep/WanGP`)** | `github.com/deepbeepmeep/WanGP` (`[PESQUISADO 2026-11-19]`) | Open-source (pesos seguem licença base) | Engine low-VRAM c/ quantização automática, offloading e Sage/FlashAttention p/ Wan2.1/2.2/InfiniteTalk/MultiTalk | Excelente runtime empacotado para L4 24GB |
| **LTX-2.5 (`Lightricks/LTX-2`)** | `github.com/Lightricks/LTX-2` / HF (`[PESQUISADO 2026-11-19]`) | LTX Community (`< $10M`/ano livre) | 22B DiT **vídeo + áudio 48kHz nativo + multi-shot**; `DistilledPipeline` (8 passos) + `fp8` cabe em 24GB c/ offload | Precisa validar adesão PT-BR e velocidade real em L4 24GB |
| **Seed-VC (`Plachtaa/seed-vc`)** | `github.com/Plachtaa/seed-vc` (`[PESQUISADO 2026-11-19]`) | **GPL-3.0** | Speech-to-speech zero-shot mantendo tempo/F0/prosódia; já instalado no Colab | Ideal para trocar voz nativa de Veo/LTX pelo timbre regional preservando lip-sync — **validar em PT-BR na Fase 5** |

### 4.2 Tabela de Trade-Offs das Arquiteturas Candidatas

| Critério | **Cand. A: Híbrido Cinema Engine v4 (ESCOLHIDA)** | **Cand. B: Flow-Only (Veo 3.1 Lite / Omni 1.2) + Seed-VC** | **Cand. C: Colab L4-Only (Wan2.2-S2V / LongCat / LTX-2.5)** |
|---|---|---|---|
| **Visão Geral** | Bíblia Visual + Shot Compiler + Storyboard NIM (`flux.1-kontext-dev`) -> Dual-Path: `flow_pack_v4` (1 clique humano p/ herói) + `colab_cinema_v4` (modelo aberto L4 p/ volume); Seed-VC opcional; Audio+Cinema QA Gate; v3.1 atrás de flag | Mesmo Shot Compiler + Storyboard NIM -> 100% geração de vídeo manual no Flow (`Veo 3.1 Lite`) -> ingestão automática + Seed-VC + `stitch_novela_takes` | Mesmo Shot Compiler + Storyboard NIM -> 100% automático no Colab L4 (modelo aberto vencedor da Fase 5) + `rve` com Quality Gate |
| **Qualidade Visual / Atuação** | **Máxima** em heróis (`espectativa.mp4`), **Alta** em volume (conforme spike Fase 5) | **Máxima** (referência Veo) | **Média-Alta** (limitada a 480p/720p do modelo aberto em 24GB) |
| **Custo Adicional** | **R\$ 0** (AI Pro + Colab Pro + NIM + Gemini existentes) | **R\$ 0** (até ~100 clipes Lite/mês = ~25 eps de 4 cortes) | **R\$ 0** (ilimitado dentro da cota de GPU do Colab Pro) |
| **Tempo por Episódio** | ~2-4 min (L4 auto) ou ~5 min (Flow 1-clique + stitch) | ~4-6 min c/ interação humana | ~3-8 min (depende do benchmark L4) |
| **RAM VPS (8 GB)** | **Zero-Idle-RAM** (<300 MB em execução, 0 containers) | **Zero-Idle-RAM** (<300 MB) | **Zero-Idle-RAM** (<300 MB) |
| **VRAM / Tempo L4 (24 GB)** | Testado na Fase 5 (`fp8`/`INT8`) | Apenas Seed-VC (~4-6 GB VRAM, <5s/take) | Crítico: exige caber em 24 GB sem OOM |
| **Esforço / Risco** | Médio / **Baixo** (reaproveita `flow_pack` e `stitch`, fallback v3.1 preservado) | Baixo / Médio (risco de cota mensal de 1.000 créd e dependência manual) | Médio-Alto / Médio (risco de latência ou artefatos em 24 GB) |
| **Passos Humanos** | **0 passos** no modo `colab_cinema_v4`; **1 clique** no Flow no modo `flow_pack_v4` | Obrigatório 1 clique humano por episódio | **0 passos** após subir o notebook Colab |

### 4.3 Decisão de Gate
**Arquitetura escolhida: Candidata A — Cinema Engine v4 Híbrido.**
1. **Camada 1 (Direção & Continuidade):** Substituir `FRONTAL_SINGLE_SUBJECT_DIRECTIVE` por um **Shot Compiler + Bíblia Visual** (personagens + cenário + regra de eixo 180° + ritmo variável `~3s / ~4s / <1.5s / ~2s` + emoção semântica derivada do conflito) atrás de feature flag (`cinema_engine='v4'` default ou opt-in gradual com `v3.1` intacto como fallback).
2. **Camada 2 (Storyboard Reference-Conditioned):** Usar `black-forest-labs/flux.1-dev` para gerar os **Master Plates** (folha de personagem + cenário base) e `black-forest-labs/flux.1-kontext-dev` (NIM gratuito existente) para derivar cada enquadramento (`two_shot`, `over_shoulder`, `reverse_over_shoulder`, `reaction_closeup`) preservando iluminação, figurino e cenário compartilhado.
3. **Camada 3 (Execução de Vídeo Full-Scene):**
   - *Rota Autônoma (`colab_cinema_v4`)*: rodar o vencedor dos spikes da **Fase 5** no Colab L4 (`Wan2.2-S2V-14B fp8`, `LongCat-Video-Avatar 1.5 INT8`, `InfiniteTalk/MultiTalk` ou `LTX-2.5 distilled fp8`), mantendo `MoDA` apenas como fallback barato.
   - *Rota Herói (`flow_pack_v4` -> `stitch_novela_takes`)*: gerar pack v4 com keyframes OTS + prompts de atuação/fala PT-BR para Veo 3.1 Lite (10 créd, 1 clique manual no Flow) + ingestão e costura com J/L-cut, opção **Seed-VC** para manter lip-sync com timbre regional, e `LRA >= 5.0 LU`.
4. **Camada 4 (Quality Gates):** Audio Quality Gate pós-TTS (`RMS + ZCR + faster-whisper overlap >= 70%`) + `tests/cinema_qa.py` no pós-render.

### 4.4 Bloqueio / Ação Necessária para Fase 5 (Spikes na L4)
- **Túnel Colab L4 (`COLAB_GPU_URL`) está offline** (`online: false` no endpoint `/api/colab/status` em 2026-11-19 06:36 -03). Sem uma sessão ativa do Colab Pro (L4 24 GB), os benchmarks reais de tempo/VRAM/lip-sync da Fase 5 (`LongCat-Video-Avatar 1.5`, `Wan2.2-S2V-14B fp8`, `InfiniteTalk`, `LTX-2.5`, `Seed-VC`) não podem ser executados ainda.
- **Próximo passo enquanto o Colab L4 não está conectado:** Podemos avançar na validação local/VPS dos componentes independentes do Colab (ex.: Spike gratuito de `flux.1-kontext-dev` na NVIDIA NIM para provar continuidade OTS/Two-Shot entre planos e implementação do Audio Quality Gate + Shot Compiler v4 atrás de feature flag), **ou** aguardar a conexão do notebook Colab L4 para executar os spikes da Fase 5 antes da implementação completa.

---

## Atualização Empírica (Fase 6 Concluída na VPS + APIs Externas Verificadas)

1. **Verificação Empírica NVIDIA NIM & Fontes Gratuitas:**
   - Testado `black-forest-labs/flux.1-kontext-dev` ao vivo na NVIDIA NIM: rejeita `"aspect_ratio": "9:16"`, `"guidance_scale"` e `"seed"` (`HTTP 422`), exige `"example_id"` em vez de `image` base64 (`422 {"detail":"Expected: example_id, got: base64"}`), e `https://api.nvcf.nvidia.com/v2/nvcf/assets` retorna `410 Gone`.
   - `flux.1-dev` e `flux.1-schnell` aceitam prompts customizados em `768x1344` (`HTTP 200`), exigindo sanitização de filtros excessivamente restritivos (`_sanitize_nim_prompt`) e estrutura de prompt com **composição OTS/Wide nos primeiros tokens e direção positiva de olhar fora da lente** (nunca negações com `"camera lens"`).
   - `Qwen/Qwen-Image-Edit` no HF ZeroGPU excede cota gratuita (`-120s left`) e OpenRouter `:free` não expõe modelos gratuitos de geração de imagem.
   - Portanto, na Fase 6, a Bíblia Visual (`set_master.jpg`, `char_*.jpg`) + placas únicas OTS/Contraplano/Reação/Ensemble usam `flux.1-dev` sanitizado com fallback `flux.1-schnell`, enquanto consistência `>= 0.80` com ingredientes é entregue ao **Google Flow (`Veo 3.1 Lite`)** e ao **Colab L4 v4.0 (`Wan 2.2 S2V / LTX-2.5`)**.
2. **Audio Quality Gate v4 (`regional_voice_engine.py`):**
   - Validação de `RMS >= -32 dBFS` e `ZCR >= 0.025` em Python stdlib puro (`array` + `math`, funcionando tanto no `.venv` enxuto do painel web para `/api/quick/novela/preview-voice` quanto no worker pesado com `numpy` + `faster-whisper` CPU INT8 + anti-aliasing).
   - Rejeição e limpeza automática de cache corrompido + bloqueio de respostas falsas `"fallback"`/`"copy"` do Colab.
3. **Cinema Director Engine v4 (`cinema_v4_director.py`, `vps_novela_runner.py`, `guided_pipelines.py`, `main.py`, `index.html`):**
   - Feature flag `cinema_engine` (`"v4"` vs `"v3.1"` preservado intacto como fallback).
   - Montagem `stitch_cinema_v4_episode` com alinhamento estrito pela duração real dos WAVs sintetizados, pausas pós-L-cut (`<= 0.18s`), ambientação `-38 dBFS` calibrada em NumPy e ganho linear único (`LRA = 9.9 LU`, `-15.8 LUFS`, `-2.0 dBTP`).
   - Resultado objetivo em `tests/cinema_qa.py` (`novela_stitched_segredo-no-altar-r2_*.mp4`): **9/13 critérios aprovados** (`shots_ge_4_and_varied=true`, `non_frontal_gaze_ge_60pct=true` com `100%` não-frontal / `0%` camera stare, `at_least_1_multi_person_or_ots=true`, `audio_lra_ge_5_0=true`, `audio_lufs_in_target=true`, `audio_true_peak_le_minus_1_5=true`, `audio_no_dead_window_below_45db=true`, `zero_static_crop_reuse=true`, `no_corrupted_mute_dialogue_shot=true`). Os 4 critérios pendentes (`motion >= 3.0`, `lipsync`, `identity_similarity >= 0.80`) dependem dos clipes de difusão de vídeo da Fase 5/7 (`Flow Veo 3.1` ou `Colab L4 v4`).
4. **Colab Pro L4 Clean-Slate Zero-Drive v4.0 (`tools/colab-gpu-worker/`):**
   - Arquitetura pivoteada para execução **100% Zero Google Drive** em NVMe local `/content/ai_studio_v4` (zero latência/mount do Drive), com detecção automática da versão CUDA (`cu128`/`cu126`/`cu124`) e **dois ambientes Python 3.10 isolados via `uv`**: `envs/seedvc` (`numpy==1.26.4` + `transformers==4.46.3` + `descript-audio-codec==1.0.0`, validado na L4 com `SEEDVC_ENV_OK 2.10.0+cu128 NVIDIA L4`) e `envs/wangp` (`FastAPI` + `WanGP` v20.00 `shared/api.py` + todos os 109 pacotes de `requirements.txt` + `insightface==0.7.3 --no-build-isolation`).
   - Descoberta empírica no último boot da L4 (Célula 2): compilação Triton no L4 passou, mas `wgp.py:3008 -> models/wan/scail/scail_pose_draw.py:17 -> import matplotlib` falhou com `ValueError: Key backend: 'module://matplotlib_inline.backend_inline' is not a valid value for backend` porque o kernel Jupyter do Colab exporta `MPLBACKEND` para dentro do venv isolado. Correção arquitetural obrigatória no novo `.ipynb`: helper hermético de sanitização de ambiente (`MPLBACKEND=Agg`, remoção de `JPY_*`, `PYTHONHOME`, `PYTHONPATH` etc.) em todas as células e subprocessos do servidor.
