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
| Fase 5 — Spikes de benchmark no Colab | NÃO INICIADA | Túnel Colab `online: false` via `/api/colab/status` em 2026-11-19 06:36 -03 | Preparar script de benchmark L4 (Wan2.2-S2V-14B fp8, LongCat-Video-Avatar 1.5 INT8, InfiniteTalk, LTX-2.5, Seed-VC) e executar assim que o Colab L4 estiver online |
| Fase 6 — Implementação | NÃO INICIADA | — | Implementar atrás de feature flag (`cinema_engine='v4'`, fallback `v3.1`): Audio Quality Gate, Bíblia + Shot Compiler, Storyboard `flux.1-kontext-dev`, `flow_pack_v4` + `stitch` J/L-cut e worker Colab v4 |
| Fase 7 — Prova | NÃO INICIADA | — | Renderizar mesmo roteiro em v3.1 vs v4, rodar `tests/cinema_qa.py` e entregar tabela antes/depois + teste cego |
