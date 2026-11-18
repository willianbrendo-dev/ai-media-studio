#!/usr/bin/env node
import fs from 'node:fs';
import { execSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire('/home/wbrendo/Projetos/fresh-start/package.json');
const { chromium } = require('playwright');

const BASE_URL = 'https://187.77.202.176.sslip.io';
const BAD_STATUSES = new Set([400, 401, 403, 404, 422, 500, 502, 503, 504]);
const TMP_TAKES_DIR = '/tmp/e2e_v26_takes';
const TEST_TAKES = [`${TMP_TAKES_DIR}/t1.mp4`, `${TMP_TAKES_DIR}/t2.mp4`];
const TEST_GUIDE_WAV = `${TMP_TAKES_DIR}/guide.wav`;

function loadToken() {
  if (process.env.VPS_PANEL_TOKEN && process.env.VPS_PANEL_TOKEN.trim()) {
    return process.env.VPS_PANEL_TOKEN.trim();
  }
  const envFile = '/home/wbrendo/.openjarvis/.env';
  if (fs.existsSync(envFile)) {
    const line = fs.readFileSync(envFile, 'utf8').split(/\r?\n/).find((l) => l.startsWith('VPS_PANEL_TOKEN='));
    if (line) return line.slice('VPS_PANEL_TOKEN='.length).trim();
  }
  throw new Error('VPS_PANEL_TOKEN não encontrado');
}

const TOKEN = loadToken();

function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERTION FAILED: ${msg}`);
}

function prepareFixtures() {
  fs.mkdirSync(TMP_TAKES_DIR, { recursive: true });
  for (let i = 0; i < TEST_TAKES.length; i++) {
    const col = i === 0 ? '#2b4a5e' : '#5e2b3e';
    if (!fs.existsSync(TEST_TAKES[i])) {
      execSync(
        `ffmpeg -y -hide_banner -loglevel error -f lavfi -i "color=c=${col}:s=1080x1920:r=30:d=2.0" ` +
        `-f lavfi -i "anullsrc=r=48000:cl=stereo" -t 2.0 -c:v libx264 -preset ultrafast -pix_fmt yuv420p ` +
        `-c:a aac -ar 48000 -ac 2 "${TEST_TAKES[i]}"`,
        { stdio: 'pipe' }
      );
    }
  }
  if (!fs.existsSync(TEST_GUIDE_WAV)) {
    execSync(
      `ffmpeg -y -hide_banner -loglevel error -f lavfi -i "sine=frequency=330:duration=1.4" -ar 16000 -ac 1 "${TEST_GUIDE_WAV}"`,
      { stdio: 'pipe' }
    );
  }
}

function cleanupFixtures() {
  fs.rmSync(TMP_TAKES_DIR, { recursive: true, force: true });
}

function attachStrictListeners(page, label, bucket) {
  page.on('response', async (res) => {
    const status = res.status();
    if (BAD_STATUSES.has(status) || status >= 400) {
      let body = '';
      try { body = (await res.text()).slice(0, 180); } catch {}
      bucket.push(`[${label}] HTTP ${status} ${res.request().method()} ${res.url()}${body ? ' :: ' + body : ''}`);
    }
  });
  page.on('requestfailed', (req) => {
    const msg = req.failure()?.errorText || '';
    if (!msg.includes('ERR_ABORTED') && !msg.includes('ERR_BLOCKED_BY_RESPONSE')) {
      bucket.push(`[${label}] REQ_FAILED ${req.method()} ${req.url()} :: ${msg}`);
    }
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      bucket.push(`[${label}] CONSOLE_ERROR: ${msg.text()}`);
    }
  });
  page.on('pageerror', (err) => {
    bucket.push(`[${label}] PAGE_ERROR: ${err.message}`);
  });
}

async function checkLayout(page, label) {
  const res = await page.evaluate(() => {
    const sw = document.documentElement.scrollWidth;
    const iw = window.innerWidth;
    return { sw, iw, overflowX: sw > iw + 1 };
  });
  assert(!res.overflowX, `${label}: overflow horizontal (${res.sw} > ${res.iw})`);
  return res;
}

async function createAuthedContext(browser, opts = {}) {
  const ctx = await browser.newContext({
    viewport: { width: opts.width || 1440, height: opts.height || 900 },
    extraHTTPHeaders: { Authorization: `Bearer ${TOKEN}` },
    isMobile: Boolean(opts.isMobile),
    hasTouch: Boolean(opts.hasTouch),
  });
  await ctx.route((url) => !url.href.startsWith(BASE_URL), async (route) => {
    const reqHeaders = { ...route.request().headers() };
    delete reqHeaders.authorization;
    delete reqHeaders.Authorization;
    await route.continue({ headers: reqHeaders });
  });
  return ctx;
}

async function runDesktop(browser) {
  console.log('\n=== DESKTOP 1440x900: V2.6 VOZES NORDESTINAS + PREVIEW + GUIA + STITCH REDUB ===');
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Desktop', errs);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#tab-quick', { state: 'visible', timeout: 25000 });
  await page.locator('#tab-quick').click();
  await page.waitForSelector('#gp-tpl-novela_regional_studio', { state: 'visible', timeout: 25000 });
  await page.locator('#gp-tpl-novela_regional_studio').click();
  await page.waitForSelector('#nov-accent-engine', { state: 'visible', timeout: 15000 });

  // v2.8 Dual-World + Viral 4 Cuts selectors
  await page.waitForSelector('#gp-nov-style-cinematic_live_action', { state: 'visible' });
  await page.waitForSelector('#gp-nov-style-stylized_3d_pixar', { state: 'visible' });
  await page.waitForSelector('#nov-dramatic-structure', { state: 'visible' });
  assert((await page.locator('#nov-dramatic-structure').inputValue()) === 'viral_4_cuts', 'Default #nov-dramatic-structure inesperado');
  await page.locator('#gp-nov-style-cinematic_live_action').click();
  assert((await page.locator('#gp-nov-style').inputValue()) === 'cinematic_live_action', 'Deveria selecionar cinematic_live_action');
  await page.locator('#gp-nov-style-stylized_3d_pixar').click();
  assert((await page.locator('#gp-nov-style').inputValue()) === 'stylized_3d_pixar', 'Deveria voltar para stylized_3d_pixar');

  const genScriptWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/generate-script') && r.request().method() === 'POST' && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#btn-gp-nov-gen').click();
  const gsJson = await (await genScriptWait).json();
  assert(gsJson.ok && Array.isArray(gsJson.dialogue) && gsJson.dialogue.length === 4, 'Esperado roteiro viral de 4 cortes');
  await page.waitForSelector('#gp-nov-preview-box:not(.hidden)', { state: 'visible' });
  await page.waitForSelector('#gp-nov-preview-lines :text("Reação Muda de Vergonha")', { state: 'visible', timeout: 10000 });
  console.log(`[PASS v2.8] Dual-World + Viral 4 Cuts script preview (${gsJson.dialogue.length} cortes)`);

  await page.locator('#gp-nov-mode-colab_gpu').click();
  await page.waitForSelector('#gp-nov-panel-colab_gpu:not(.hidden)', { state: 'visible' });
  const colabPanelTxt = await page.locator('#gp-nov-panel-colab_gpu').innerText();
  assert(colabPanelTxt.includes('LivePortrait + LatentSync 1.5'), 'Banner híbrido v2.8 Colab T4 ausente');
  assert((await page.locator('#nov-colab-engine').inputValue()) === 'liveportrait_latentsync', 'Default #nov-colab-engine inesperado');
  await page.locator('#gp-nov-mode-flow_pack').click();

  const defaultAccent = await page.locator('#nov-accent-engine').inputValue();
  assert(defaultAccent === 'nordestino_raiz', `Default #nov-accent-engine inesperado: ${defaultAccent}`);

  await page.waitForSelector('#nov-badge-voice', { state: 'visible' });
  await page.waitForSelector('#nov-badge-lipsync', { state: 'visible' });
  const badgeVoiceTxt = await page.locator('#nov-badge-voice').innerText();
  const badgeLipTxt = await page.locator('#nov-badge-lipsync').innerText();
  assert(badgeVoiceTxt.includes('100% Gemini Native TTS'), `Selo voz inesperado: ${badgeVoiceTxt}`);
  assert(badgeLipTxt.includes('Lip-Sync & Movimento Ativo'), `Selo lip-sync inesperado: ${badgeLipTxt}`);

  const optVals = await page.locator('#nov-accent-engine option').evaluateAll((opts) => opts.map((o) => o.value));
  for (const needed of ['nordestino_raiz', 'f5_tts_clone', 'seed_vc_guide']) {
    assert(optVals.includes(needed), `Opção ausente em #nov-accent-engine: ${needed}`);
  }

  // 1) Botão "Testar Vozes Nordestinas" -> preview-voice 48kHz MP3 (100% Gemini Native TTS, 0 edge-tts!)
  const pvWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/preview-voice') && r.request().method() === 'POST' && r.status() === 200,
    { timeout: 120000 }
  );
  await page.locator('#btn-nov-preview-voice').click();
  const pvRes = await pvWait;
  const pvJson = await pvRes.json();
  assert(pvJson.ok && pvJson.audio_url, `Resposta inválida preview-voice: ${JSON.stringify(pvJson)}`);
  assert(
    Array.isArray(pvJson.engines_used) &&
      pvJson.engines_used.length > 0 &&
      pvJson.engines_used.every((e) => {
        const s = String(e);
        return s.startsWith('gemini_live:') || s.startsWith('gemini_tts:');
      }),
    `Esperado 100% gemini_live/gemini_tts no preview-voice, veio: ${JSON.stringify(pvJson.engines_used)}`
  );
  await page.waitForSelector('#nov-voice-preview-player:not(.hidden)', { state: 'visible', timeout: 15000 });
  const playerSrc = await page.locator('#nov-voice-preview-player').getAttribute('src');
  assert(playerSrc && playerSrc.includes('/api/quick/novela/preview-audio/'), `Player src inválido: ${playerSrc}`);
  const audioFetch = await ctx.request.get(`${BASE_URL}${playerSrc}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert(audioFetch.status() === 200 && (await audioFetch.body()).length > 3000, 'Preview MP3 vazio ou HTTP != 200');
  console.log(`[PASS] Botão "🔊 Testar Vozes Nordestinas": engines=${(pvJson.engines_used || []).join(',')} (${pvJson.duration}s MP3 HTTP 200)`);

  // 2) Stitcher com toggle "Redublar Takes com Sotaque Nordestino" (#nov-stitch-redub)
  await page.waitForSelector('#nov-stitch-redub', { state: 'visible' });
  await page.locator('#nov-stitch-redub').check();
  assert(await page.locator('#nov-stitch-redub').isChecked(), '#nov-stitch-redub deveria estar marcado');

  const upTakesWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/upload-takes') && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#nov-stitch-files').setInputFiles(TEST_TAKES);
  const upTakesRes = await upTakesWait;
  const upTakesJson = await upTakesRes.json();
  assert(upTakesJson.ok && upTakesJson.count === 2, 'Upload de 2 takes falhou');

  const stPostWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/stitch-takes') && r.request().method() === 'POST' && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#btn-nov-stitch').click();
  await stPostWait;
  await page.waitForSelector('#nov-stitch-result video', { state: 'visible', timeout: 150000 });
  const stitchUrl = await page.locator('#nov-stitch-result a[download]').getAttribute('href');
  const stitchDl = await ctx.request.get(`${BASE_URL}${stitchUrl}`, { headers: { Authorization: `Bearer ${TOKEN}`, Range: 'bytes=0-1023' } });
  assert(stitchDl.status() === 200 || stitchDl.status() === 206, `Download costurado redub HTTP ${stitchDl.status()}`);
  console.log(`[PASS] Costurador UI + Redublagem Regional (#nov-stitch-redub): vídeo final HTTP ${stitchDl.status()}`);
  await checkLayout(page, 'Desktop Guided Trilha 6 + Stitch Redub');

  // 3) Modo Hard: seletor sincronizado, selos v2.7, "Ouvir Fala" individual e upload "Áudio Guia"
  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#card-hard-novela', { state: 'visible' });
  await page.waitForSelector('#qnv-style', { state: 'visible' });
  await page.waitForSelector('#qnv-dramatic-structure', { state: 'visible' });
  assert((await page.locator('#qnv-style').inputValue()) === 'stylized_3d_pixar', 'Default #qnv-style v2.8 inesperado');
  assert((await page.locator('#qnv-dramatic-structure').inputValue()) === 'viral_4_cuts', 'Default #qnv-dramatic-structure inesperado');
  await page.waitForSelector('#qnv-accent-engine', { state: 'visible' });
  await page.waitForSelector('#qnv-badge-voice', { state: 'visible' });
  await page.waitForSelector('#qnv-badge-lipsync', { state: 'visible' });
  await page.waitForSelector('#qnv-dialogue-list :text("Lip-Sync & Movimento Ativo")', { state: 'visible' });

  const linePlayWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/preview-voice') && r.request().method() === 'POST' && r.status() === 200,
    { timeout: 90000 }
  );
  await page.locator('#btn-qnv-line-play-0').click();
  const lpRes = await linePlayWait;
  const lpJson = await lpRes.json();
  assert(lpJson.ok && lpJson.audio_url, 'Ouvir Fala individual falhou');
  await page.waitForSelector('#qnv-voice-preview-player:not(.hidden)', { state: 'visible', timeout: 10000 });
  console.log(`[PASS] Modo Hard "▶️ Ouvir Fala #1": OK (${lpJson.duration}s)`);

  const guideWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/upload-line-audio') && r.request().method() === 'POST' && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#qnv-guide-file-0').setInputFiles(TEST_GUIDE_WAV);
  const guideRes = await guideWait;
  const guideJson = await guideRes.json();
  assert(guideJson.ok && guideJson.sample_rate === 48000 && guideJson.channels === 2, 'Upload áudio-guia inválido');
  await page.waitForSelector('#qnv-dialogue-list :text("✅ Guia")', { state: 'visible', timeout: 10000 });
  assert((await page.locator('#qnv-accent-engine').inputValue()) === 'seed_vc_guide', 'Deveria trocar automaticamente para seed_vc_guide');
  console.log(`[PASS] Modo Hard Áudio-Guia 48kHz (${guideJson.duration}s) + auto-switch Seed-VC`);

  await checkLayout(page, 'Desktop Modo Hard v2.6');

  // 4) v2.9 Telemetria de Cotas em Tempo Real (Card CASCATA IA + Modal + HF_TOKEN no Cofre)
  await page.locator('#tab-stacks').click();
  await page.waitForSelector('#kpi-cascade-tier', { state: 'visible', timeout: 15000 });
  await page.waitForSelector('#kpi-cascade-pills', { state: 'visible', timeout: 15000 });
  const kpiTierTxt = await page.locator('#kpi-cascade-tier').innerText();
  const kpiPillsTxt = await page.locator('#kpi-cascade-pills').innerText();
  assert(kpiTierTxt.includes('Texto:') && kpiTierTxt.includes('Voz:'), `KPI Cascata v2.9 inesperado: ${kpiTierTxt}`);
  assert(kpiPillsTxt.includes('NIM') && kpiPillsTxt.includes('Live'), `Pills Cascata v2.9 inesperadas: ${kpiPillsTxt}`);

  await page.locator('#btn-open-quota-modal').click();
  await page.waitForSelector('#quota-modal:not(.hidden)', { state: 'visible', timeout: 10000 });
  await page.waitForSelector('#quota-modal-body table tbody tr', { state: 'visible', timeout: 10000 });
  const modalRowCount = await page.locator('#quota-modal-body table tbody tr').count();
  assert(modalRowCount >= 15, `Esperado >= 15 modelos no modal de cotas, veio ${modalRowCount}`);
  await page.locator('#quota-modal button[title="Fechar"]').click();
  await page.waitForSelector('#quota-modal.hidden', { state: 'attached', timeout: 5000 });

  await page.locator('#tab-vault').click();
  await page.waitForSelector('#input-HF_TOKEN', { state: 'visible', timeout: 10000 });
  const hfBadge = await page.locator('#badge-HF_TOKEN').innerText();
  assert(hfBadge.includes('Ativa'), `Esperado badge HF_TOKEN ativo no Cofre, veio: ${hfBadge}`);
  console.log(`[PASS] v2.9 Cascata Cotas (${modalRowCount} modelos) + HF_TOKEN Cofre (${hfBadge})`);

  assert(errs.length === 0, `Erros Desktop:\n - ${errs.join('\n - ')}`);
  await ctx.close();
}

async function runMobile(browser) {
  console.log('\n=== MOBILE 390x844: SELETOR NORDESTINO + STITCHER + MODO HARD SEM OVERFLOW ===');
  const ctx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Mobile', errs);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#mnav-quick', { state: 'visible', timeout: 25000 });
  await page.locator('#mnav-quick').click();
  await page.waitForSelector('#gp-tpl-novela_regional_studio', { state: 'visible', timeout: 25000 });
  await page.locator('#gp-tpl-novela_regional_studio').click();
  await page.waitForSelector('#nov-accent-engine', { state: 'visible' });
  await page.waitForSelector('#btn-nov-preview-voice', { state: 'visible' });
  await page.waitForSelector('#nov-stitch-redub', { state: 'visible' });
  await checkLayout(page, 'Mobile Trilha 6 Vozes Nordestinas');

  for (const mode of ['flow_pack', 'colab_gpu', 'vps_preview']) {
    await page.locator(`#gp-nov-mode-${mode}`).click();
    await page.waitForSelector(`#gp-nov-panel-${mode}`, { state: 'visible' });
    await checkLayout(page, `Mobile Motor ${mode}`);
  }

  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#card-hard-novela', { state: 'visible' });
  await page.waitForSelector('#btn-qnv-line-play-0', { state: 'visible' });
  await page.waitForSelector('#btn-qnv-line-guide-0', { state: 'visible' });
  await checkLayout(page, 'Mobile Modo Hard Novela v2.6');

  // Validar Card Cascata IA + Modal de Cotas responsivo em Mobile 390x844
  await page.locator('#mnav-stacks').click();
  await page.waitForSelector('#kpi-cascade-pills', { state: 'visible', timeout: 10000 });
  await checkLayout(page, 'Mobile Card Cascata Cotas v2.9');
  await page.locator('#btn-open-quota-modal').click();
  await page.waitForSelector('#quota-modal:not(.hidden)', { state: 'visible', timeout: 10000 });
  await checkLayout(page, 'Mobile Modal Cotas v2.9');
  await page.locator('#quota-modal button[title="Fechar"]').click();

  assert(errs.length === 0, `Erros Mobile:\n - ${errs.join('\n - ')}`);
  await ctx.close();
  console.log('[PASS] Mobile 390x844: UI v2.6 100% responsiva, 0 overflow, 0 erros!');
}

async function main() {
  prepareFixtures();
  const browser = await chromium.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    await runDesktop(browser);
    await runMobile(browser);
    console.log('\n>>> E2E NOVELA VOICES V2.6 (PLAYWRIGHT 1440x900 + 390x844): 100% APROVADO <<<');
  } finally {
    await browser.close();
    cleanupFixtures();
  }
}

main().catch((err) => {
  cleanupFixtures();
  console.error('\n[E2E V2.6 FAILED]:', err);
  process.exit(1);
});
