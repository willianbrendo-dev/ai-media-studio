# Progresso v4.0 — AI Media Studio (`feat/v4-cinema`)

**Branch atual:** `feat/v4-cinema`
**Última atualização:** 2026-11-19 07:20 -03

| Fase | Status | Evidência (arquivo ou commit) | Próximo passo exato |
|---|---|---|---|
| Fase 0 — Reconhecimento (somente leitura) | FEITA | `docs/ARQUITETURA_V4.md` (Seção "FASE 0"), mapeado com `arquivo:linha` em `vps_novela_runner.py`, `novela_lipsync_animator.py`, `regional_voice_engine.py`, `guided_pipelines.py`, `main.py`, `verify_lipsync_motion.py`, `ai_studio_colab_novela_gpu.ipynb` | — |
| Fase 1 — Reprodução e evidência | FEITA | `tests/cinema_qa.py` (corrigido + calibrado + compilado + executado na VPS em `hoje.mp4` e `espectativa.mp4`); `docs/measurements/cinema_qa_hoje.json` (`all_criteria_passed=false`, 3/13); `docs/measurements/cinema_qa_espectativa.json` (`all_criteria_passed=true`, 13/13); inspeção visual dos grids e timestamps Whisper | — |
| Fase 2 — Matriz de cruzamento | FEITA | `docs/ARQUITETURA_V4.md` (Seção "FASE 2"), cruzando Claude + Gemini + ChatGPT + H1–H9 contra código e medições (C4 refutada p/ APIs pagas/Gemini Image 429; alternativa NIM `flux.1-kontext-dev` verificada) | — |
| Fase 3 — Árvore de causa raiz | FEITA | `docs/ARQUITETURA_V4.md` (Seção "FASE 3"), separando ~80–85% arquitetural de ~15–20% prompt/áudio/edição | — |
| Fase 4 — Decisão de arquitetura (gate) | FEITA | `docs/ARQUITETURA_V4.md` (Seção "FASE 4"), pesquisa com fontes/datas/licenças, 3 candidatas, escolhida **Cand. A (Híbrido Cinema v4)** | Gate humano para Fase 5: subir sessão Google Colab L4 e configurar `COLAB_GPU_URL` (atualmente `online: false`), ou autorizar início do spike NIM `flux.1-kontext-dev` + Audio Gate na VPS |
| Fase 5 — Spikes de benchmark no Colab | AGUARDANDO NOVO .IPYNB (EM REFINO) | Pivotado p/ Zero-Drive `/content/ai_studio_v4` + dual venv Python 3.10 via `uv` (`envs/seedvc` OK na L4 + `envs/wangp` 109 deps + `insightface` + Triton L4 OK); último erro isolado em `MPLBACKEND=module://matplotlib_inline.backend_inline` do Jupyter herdado pelo `venv`; gerando novo `.ipynb` blindado via Claude Code | Subir novo `.ipynb` Zero-Drive v4.0 sanitizado na L4, validar Célula 3 smoke real e conectar túnel em `COLAB_GPU_URL` |
| Fase 6 — Implementação | FEITA (CORE + UI + QA) | `tools/hyperframes/regional_voice_engine.py`, `tools/hyperframes/cinema_v4_director.py`, `tools/hyperframes/vps_novela_runner.py`, `media-control-panel/{guided_pipelines.py,main.py,static/index.html}`, `tools/colab-gpu-worker/{colab_v4_cinema_server.py,ai_studio_colab_novela_gpu.ipynb}`, `notebooks/ai_studio_colab_novela_gpu.ipynb`; `cinema_qa.py` **9/13** em `stitch-takes v4`, E2E Playwright **100%** (Desktop + Mobile), `secret-scan` **0 violações** | Gerar 1 episódio no Google Flow usando `flow_v4_1795092497_3e552b` **e/ou** conectar Colab L4 v4.0 |
| Fase 7 — Prova | PARCIAL (9/13 SEM DIFUSÃO DE VÍDEO) | `stitch-takes v4`: **9/13** (`100%` não-frontal, `LRA 9.9 LU`, `-15.8 LUFS`, `-2.0 dBTP`, `0` cortes mudos); `render v4` Nordestino: **8/13** (transcrição 100% fiel nas falas, `100%` não-frontal, `4` planos OTS/conjunto); faltam os 4 critérios de vídeo-difusão (`motion >= 3.0`, `lipsync`, `identity >= 0.80`) | Validar vídeo final com takes do Google Flow Veo 3.1 Lite e/ou Colab L4 v4.0 no `tests/cinema_qa.py` (meta >= 12/13) |

---

## Checkpoint Fase 6 (2026-11-19 — Branch `feat/v4-cinema`)

- **Descobertas empíricas & correções verificadas em execução:**
  - `flux.1-kontext-dev` na NVIDIA NIM rejeita `9:16`, `guidance_scale`, `seed` e base64 `image` (`HTTP 422`), e `nvcf/assets` retorna `410 Gone`; corrigido storyboard para `flux.1-dev` sanitizado com **composição OTS/Conjunto no início do prompt**, direção positiva de olhar fora da lente e inferência de gênero PT-BR (`CONVIDADA 1` renderizada corretamente).
  - Audio Quality Gate v4 em [regional_voice_engine.py](tools/hyperframes/regional_voice_engine.py): `RMS >= -32 dBFS` + `ZCR >= 0.025` operando inclusive em Python stdlib puro (`array` + `math`) quando o ambiente web `.venv` não tem `numpy` (corrigiu timeout/500 em `/api/quick/novela/preview-voice`), mantendo `faster-whisper` INT8 + polyphase/boxcar anti-aliasing no worker completo.
  - Alinhamento preciso de L-cut em [cinema_v4_director.py](tools/hyperframes/cinema_v4_director.py): duração dos planos calibrada pela duração medida via `ffprobe` do WAV real sintetizado + pausa automática pós-L-cut, eliminando sobreposição de fala sobre o Shot 3 (`"O quê?!"`).
- **Métricas & Regressões pós-última edição:**
  - `tests/cinema_qa.py` no `novela_stitched_segredo-no-altar-r2_1795093240_c2c490.mp4` (`cinema_engine='v4'`): **9/13 critérios aprovados** (`shots=[2.58, 4.04, 1.12, 3.42]`, `non_frontal=100%`, `multi_person_or_ots=3`, `no_corrupted_mute_dialogue_shot=true`, `lufs=-15.8`, `lra=9.9 LU`, `tp=-2.0 dBTP`).
  - `tests/cinema_qa.py` no `v4_e2e_nordeste_render_r2.mp4` (storyboard animado sem difusão de vídeo): **8/13 critérios** (`whisper_full_transcript="Seu bastião? Quem mexeu no meu relógio de ouro? Calma com madre. Foi o Enzo tentando consertar o ponteiro. O quê?"`, `non_frontal=100%`, `ots_cnt=4`, `lufs=-15.9`, `lra=10.0 LU`, `tp=-3.1 dBTP`).
  - Feature flag `_resolve_cinema_engine`: `'default': 'v3.1'`, `'v31': 'v3.1'`, `'v4': 'v4'` (legado v3.1 preservado intacto).
  - Playwright E2E `tests/e2e_novela_voices_v26.mjs`: **100% aprovado** (`Desktop 1440x900` + `Mobile 390x844`, 0 erros de console/HTTP/overflow).
  - `tests/verify_studio_deep.py`: **33/34** aprovados (`CONTAINERS=0`, `free_ram_gb=6.31`; falha pré-existente apenas no Test 34 que compara `#nav-skills-badge` contra `'10'` em vez dos `'13'` atuais).
  - `scripts/secret-scan.py`: **0 violações** (`11390` arquivos checados).
