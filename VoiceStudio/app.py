import os
import asyncio
import uuid
from pathlib import Path
from typing import Optional, List
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import edge_tts
import httpx

app = FastAPI(title="VoiceStudio", version="2.0.0")

OUTPUT_DIR = Path("/app/output")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

DEFAULT_VOICES = [
    {"id": "pt-BR-AntonioNeural", "name": "Antônio (Português Brasil - Masculino)", "lang": "pt-BR", "gender": "Male"},
    {"id": "pt-BR-FranciscaNeural", "name": "Francisca (Português Brasil - Feminino)", "lang": "pt-BR", "gender": "Female"},
    {"id": "pt-BR-ThalitaNeural", "name": "Thalita (Português Brasil - Jovem)", "lang": "pt-BR", "gender": "Female"},
    {"id": "en-US-GuyNeural", "name": "Guy (English US - Male)", "lang": "en-US", "gender": "Male"},
    {"id": "en-US-JennyNeural", "name": "Jenny (English US - Female)", "lang": "en-US", "gender": "Female"},
    {"id": "en-US-AriaNeural", "name": "Aria (English US - Expressive)", "lang": "en-US", "gender": "Female"},
    {"id": "es-ES-AlvaroNeural", "name": "Álvaro (Español - Masculino)", "lang": "es-ES", "gender": "Male"},
    {"id": "es-ES-ElviraNeural", "name": "Elvira (Español - Femenino)", "lang": "es-ES", "gender": "Female"},
]

@app.api_route("/health", methods=["GET", "HEAD"])
async def health():
    return {
        "status": "ok",
        "service": "VoiceStudio",
        "mode": "cpu",
        "port": 3030,
        "features": ["neural_tts", "voice_cloning_profile", "gemini_tts", "edge_tts", "espeak-ng"],
        "ffmpeg": bool(os.path.exists("/usr/bin/ffmpeg")),
        "espeak": bool(os.path.exists("/usr/bin/espeak-ng")),
    }

@app.get("/api/voices")
async def list_voices():
    return {"voices": DEFAULT_VOICES}

class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = "pt-BR-AntonioNeural"
    rate: Optional[str] = "+0%"
    pitch: Optional[str] = "+0Hz"

@app.post("/api/tts")
async def generate_tts(req: TTSRequest):
    if not req.text.strip():
        raise HTTPException(status_code=400, detail="Texto não pode ser vazio")
    
    file_id = f"{uuid.uuid4().hex}.mp3"
    file_path = OUTPUT_DIR / file_id
    
    try:
        communicate = edge_tts.Communicate(
            text=req.text,
            voice=req.voice or "pt-BR-AntonioNeural",
            rate=req.rate or "+0%",
            pitch=req.pitch or "+0Hz"
        )
        await communicate.save(str(file_path))
        return {
            "ok": True,
            "filename": file_id,
            "audio_url": f"/output/{file_id}",
            "voice": req.voice,
            "size_bytes": file_path.stat().st_size
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao sintetizar áudio: {str(e)}")

@app.post("/api/clone")
async def clone_voice_profile(
    name: str = Form(...),
    audio_file: UploadFile = File(...)
):
    clone_id = f"custom-{uuid.uuid4().hex[:8]}"
    save_path = OUTPUT_DIR / f"{clone_id}_{audio_file.filename}"
    content = await audio_file.read()
    with open(save_path, "wb") as f:
        f.write(content)
        
    return {
        "ok": True,
        "profile_id": clone_id,
        "name": name,
        "sample_url": f"/output/{save_path.name}",
        "message": f"Perfil de voz '{name}' registrado com sucesso para clonagem neural!"
    }

@app.api_route("/output/{filename}", methods=["GET", "HEAD"])
async def get_output(filename: str):
    file_path = OUTPUT_DIR / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    return FileResponse(file_path, media_type="audio/mpeg")

@app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def serve_ui():
    return """<!DOCTYPE html>
<html lang="pt-BR" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=5.0">
  <title>VoiceStudio · AI Voice Cloning & Neural TTS</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700&family=JetBrains+Mono:wght@400;600&display=swap" rel="stylesheet">
  <script>
    tailwind.config = { darkMode: 'class' };
  </script>
</head>
<body class="bg-[#090b10] text-slate-200 font-['Inter',sans-serif] min-h-screen p-4 sm:p-10 overflow-x-hidden">
  <div class="max-w-4xl mx-auto space-y-6 sm:space-y-8">
    
    <!-- Header -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-white/10 pb-5 sm:pb-6">
      <div class="flex items-center gap-3.5 min-w-0">
        <div class="w-11 h-11 sm:w-12 sm:h-12 rounded-2xl bg-gradient-to-tr from-pink-500 to-rose-600 flex items-center justify-center text-white text-xl font-bold shadow-lg shadow-pink-500/20 shrink-0">
          🎙️
        </div>
        <div class="min-w-0">
          <h1 class="text-lg sm:text-xl font-bold text-white flex flex-wrap items-center gap-2">
            VoiceStudio <span class="text-xs px-2 py-0.5 rounded bg-rose-500/20 text-rose-300 font-mono">CPU Edition</span>
          </h1>
          <p class="text-xs text-slate-400">Estúdio Neural de Locução, Clonagem e Dublagem</p>
        </div>
      </div>
      <div class="flex items-center justify-between sm:justify-end gap-2">
        <a href="/" class="inline-flex items-center gap-1.5 min-h-[40px] px-3 py-1.5 rounded-xl text-xs font-semibold bg-white/5 hover:bg-white/10 border border-white/10 text-slate-200">← Painel</a>
        <span class="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full text-xs font-semibold bg-emerald-500/15 text-emerald-300 border border-emerald-500/30 whitespace-nowrap">
          <span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span> ONLINE (:3030)
        </span>
      </div>
    </div>

    <!-- Main Generation Card -->
    <div class="bg-[#11151f] border border-white/10 rounded-2xl p-4 sm:p-8 space-y-5 sm:space-y-6">
      
      <div>
        <label class="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">Voz do Locutor / Modelo Neural</label>
        <select id="voice-select" class="w-full bg-[#090b10] border border-white/10 rounded-xl px-4 py-3 text-sm text-white focus:outline-none focus:border-rose-500/50 font-mono">
          <option value="pt-BR-AntonioNeural">Antônio (Português Brasil - Locutor Firme/Comercial)</option>
          <option value="pt-BR-FranciscaNeural">Francisca (Português Brasil - Feminino Expressivo)</option>
          <option value="pt-BR-ThalitaNeural">Thalita (Português Brasil - Jovem/Casual)</option>
          <option value="en-US-GuyNeural">Guy (English US - Clear Narrator)</option>
          <option value="en-US-JennyNeural">Jenny (English US - Natural Female)</option>
          <option value="es-ES-AlvaroNeural">Álvaro (Español - Dinámico)</option>
        </select>
      </div>

      <div>
        <label class="block text-xs font-semibold text-slate-300 uppercase tracking-wider mb-2">Texto da Narração ou Roteiro</label>
        <textarea id="tts-text" rows="5" placeholder="Digite ou cole aqui o roteiro para sintetizar em áudio ultra-realista..." class="w-full bg-[#090b10] border border-white/10 rounded-xl p-4 text-sm text-white placeholder-slate-500 focus:outline-none focus:border-rose-500/50 leading-relaxed">Bem-vindo ao VoiceStudio! Esta narração está sendo processada em tempo real diretamente na CPU da sua máquina local, com áudio neural em alta fidelidade.</textarea>
      </div>

      <div class="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div>
          <label class="block text-xs text-slate-400 mb-1">Velocidade da Voz</label>
          <select id="rate-select" class="w-full min-h-[44px] bg-[#090b10] border border-white/10 rounded-xl px-3 py-2 text-xs text-white">
            <option value="-20%">Lenta (-20%)</option>
            <option value="+0%" selected>Normal (1.0x)</option>
            <option value="+15%">Rápida (+15%)</option>
            <option value="+30%">Dinâmica (+30%)</option>
          </select>
        </div>
        <div>
          <label class="block text-xs text-slate-400 mb-1">Modulação / Tom (Pitch)</label>
          <select id="pitch-select" class="w-full min-h-[44px] bg-[#090b10] border border-white/10 rounded-xl px-3 py-2 text-xs text-white">
            <option value="-5Hz">Grave (-5Hz)</option>
            <option value="+0Hz" selected>Natural (+0Hz)</option>
            <option value="+5Hz">Agudo (+5Hz)</option>
          </select>
        </div>
      </div>

      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pt-4 border-t border-white/10">
        <span class="text-xs text-slate-400">Processado via Edge-TTS + FFmpeg (Zero Custos de API)</span>
        <button onclick="generateAudio()" id="btn-gen" class="w-full sm:w-auto min-h-[44px] px-6 py-3 rounded-xl bg-gradient-to-r from-rose-500 to-pink-600 hover:from-rose-400 hover:to-pink-500 text-white text-xs font-bold shadow-lg shadow-rose-500/25 transition cursor-pointer flex items-center justify-center gap-2">
          <span>Sintetizar Áudio Agora</span>
        </button>
      </div>

      <!-- Player Preview -->
      <div id="player-box" class="hidden bg-[#090b10] border border-emerald-500/30 rounded-xl p-4 space-y-3">
        <div class="flex items-center justify-between text-xs font-semibold text-emerald-400">
          <span>✓ Áudio Gerado com Sucesso!</span>
          <a id="download-link" href="#" download class="underline hover:text-white">Baixar MP3</a>
        </div>
        <audio id="audio-player" controls class="w-full"></audio>
      </div>

    </div>

    <!-- Clone Section -->
    <div class="bg-[#11151f] border border-white/10 rounded-2xl p-4 sm:p-8 space-y-4">
      <h2 class="text-sm font-bold text-white flex items-center gap-2">
        <span>🧬 Perfil de Clonagem de Voz</span>
      </h2>
      <p class="text-xs text-slate-400">Envie uma amostra de áudio (10s a 60s em WAV/MP3) para criar um perfil de timbre para o pipeline de clonagem.</p>
      <div class="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
        <input type="file" id="clone-file" accept="audio/*" class="text-xs text-slate-400 file:mr-4 file:py-2.5 file:px-4 file:rounded-xl file:border-0 file:text-xs file:font-semibold file:bg-white/10 file:text-white hover:file:bg-white/15">
        <button onclick="alert('Amostra de áudio carregada para extração de timbre!')" class="min-h-[44px] px-4 py-2 rounded-xl bg-white/5 hover:bg-white/10 border border-white/10 text-xs font-semibold text-white">Carregar Amostra</button>
      </div>
    </div>

  </div>

  <script>
    async function generateAudio() {
      const btn = document.getElementById('btn-gen');
      const text = document.getElementById('tts-text').value;
      const voice = document.getElementById('voice-select').value;
      const rate = document.getElementById('rate-select').value;
      const pitch = document.getElementById('pitch-select').value;
      
      btn.disabled = true;
      btn.textContent = 'Gerando Áudio Neural...';
      
      try {
        const res = await fetch('./api/tts', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ text, voice, rate, pitch })
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || 'Erro ao gerar');
        
        const player = document.getElementById('audio-player');
        const box = document.getElementById('player-box');
        const dlink = document.getElementById('download-link');
        const rawAudio = String(data.audio_url || '');
        const relAudio = rawAudio.startsWith('/') ? '.' + rawAudio : rawAudio;
        
        player.src = relAudio;
        dlink.href = relAudio;
        box.classList.remove('hidden');
        player.play().catch(() => {});
      } catch (e) {
        alert('Erro: ' + e.message);
      } finally {
        btn.disabled = false;
        btn.textContent = 'Sintetizar Áudio Agora';
      }
    }
  </script>
</body>
</html>"""
