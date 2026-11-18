#!/usr/bin/env node
import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';

const require = createRequire('/home/wbrendo/Projetos/fresh-start/package.json');
const { chromium } = require('playwright');

const BASE_URL = 'https://187.77.202.176.sslip.io';
const MOCK_LONG_MP4 = '/home/wbrendo/.gemini/antigravity/brain/a7f7d602-7234-4486-835e-7b2c2a8e0e98/scratch/mock_video_125s.mp4';
const MOCK_SHORT_MP4 = '/home/wbrendo/.gemini/antigravity/brain/a7f7d602-7234-4486-835e-7b2c2a8e0e98/scratch/mock_short_4s.mp4';
const BAD_STATUSES = new Set([400, 401, 403, 404, 422, 500, 502, 503, 504]);

function loadToken() {
  if (process.env.VPS_PANEL_TOKEN && process.env.VPS_PANEL_TOKEN.trim()) {
    return process.env.VPS_PANEL_TOKEN.trim();
  }
  const envFile = '/home/wbrendo/.openjarvis/.env';
  if (fs.existsSync(envFile)) {
    const line = fs
      .readFileSync(envFile, 'utf8')
      .split(/\r?\n/)
      .find((l) => l.startsWith('VPS_PANEL_TOKEN='));
    if (line) return line.slice('VPS_PANEL_TOKEN='.length).trim();
  }
  throw new Error('VPS_PANEL_TOKEN não encontrado no ambiente nem em ~/.openjarvis/.env');
}

const TOKEN = loadToken();

function assert(cond, msg) {
  if (!cond) throw new Error(`ASSERTION FAILED: ${msg}`);
}

async function apiRequest(method, apiPath, body = undefined) {
  const opts = {
    method,
    headers: {
      Authorization: `Bearer ${TOKEN}`,
      ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
    },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  };
  const res = await fetch(`${BASE_URL}${apiPath}`, opts);
  const text = await res.text();
  let json = null;
  try {
    json = JSON.parse(text);
  } catch {
    json = { raw: text };
  }
  if (!res.ok) {
    throw new Error(`API ${method} ${apiPath} -> HTTP ${res.status}: ${text.slice(0, 300)}`);
  }
  return json;
}

async function stopAllAndAssertZero() {
  await apiRequest('POST', '/api/projects/stop-all');
  const deadline = Date.now() + 25000;
  while (Date.now() < deadline) {
    const sys = await apiRequest('GET', '/api/system');
    if (sys.active_containers === 0) {
      return sys;
    }
    await new Promise((r) => setTimeout(r, 1000));
  }
  const sysFinal = await apiRequest('GET', '/api/system');
  assert(sysFinal.active_containers === 0, `Esperado 0 contêineres ativos após stop-all, obtido ${sysFinal.active_containers}`);
  return sysFinal;
}

async function startOnlyStack(stackId) {
  await stopAllAndAssertZero();
  const actionRes = await apiRequest('POST', `/api/projects/${stackId}/action`, { action: 'start' });
  assert(actionRes.ok, `Falha ao iniciar ${stackId}`);

  const deadline = Date.now() + 100000;
  while (Date.now() < deadline) {
    const data = await apiRequest('GET', '/api/projects');
    const list = data.projects || [];
    const target = list.find((p) => p.id === stackId);
    const othersRunning = list.filter((p) => p.id !== stackId && p.running_containers > 0);
    assert(othersRunning.length === 0, `Violação de isolamento RAM: outras stacks ativas além de ${stackId}: ${othersRunning.map((p) => p.id).join(', ')}`);
    if (target && target.http_ready) {
      console.log(`[STACK] ${stackId} ONLINE isolada (${target.running_containers}/${target.total_containers} contêineres)`);
      return target;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error(`Timeout aguardando http_ready para stack ${stackId}`);
}

function attachStrictListeners(page, label, bucket) {
  page.on('response', (res) => {
    const status = res.status();
    if (BAD_STATUSES.has(status)) {
      const entry = `[${label}] HTTP ${status} ${res.request().method()} ${res.url()}`;
      bucket.push(entry);
    }
  });
  page.on('pageerror', (err) => {
    bucket.push(`[${label}] PAGEERROR: ${err.message}`);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      const txt = msg.text();
      bucket.push(`[${label}] CONSOLE.ERROR: ${txt}`);
    }
  });
}

async function checkLayoutAndScroll(page, label, { requireVerticalScroll = false } = {}) {
  const res = await page.evaluate(() => {
    const overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
    const winCanScroll = document.documentElement.scrollHeight > window.innerHeight + 1;
    let scrolledY = 0;

    if (winCanScroll) {
      window.scrollTo({ top: 350, behavior: 'instant' });
      scrolledY = Math.max(window.scrollY, document.documentElement.scrollTop, document.body.scrollTop);
      window.scrollTo({ top: 0, behavior: 'instant' });
    }

    const candidates = Array.from(document.querySelectorAll('[data-testid="stMain"], section.main, [data-testid="stAppViewContainer"], [data-testid="stApp"], main, section, div, body'));
    let innerScrolled = 0;
    let innerCanScroll = false;
    for (const el of candidates) {
      const ov = getComputedStyle(el).overflowY;
      if ((ov === 'auto' || ov === 'scroll') && el.scrollHeight > el.clientHeight + 2) {
        innerCanScroll = true;
        const before = el.scrollTop;
        el.scrollTo({ top: 250, behavior: 'instant' });
        if (el.scrollTop > before) innerScrolled = Math.max(innerScrolled, el.scrollTop);
        el.scrollTo({ top: before, behavior: 'instant' });
        if (innerScrolled > 0) break;
      }
    }

    return {
      overflowX,
      scrollWidth: document.documentElement.scrollWidth,
      innerWidth: window.innerWidth,
      winCanScroll,
      scrolledY,
      innerCanScroll,
      innerScrolled,
    };
  });
  assert(!res.overflowX, `${label}: overflow horizontal detectado (${res.scrollWidth} > ${res.innerWidth})`);
  if (requireVerticalScroll) {
    assert(res.winCanScroll || res.innerCanScroll, `${label}: sem capacidade de scroll vertical detectada`);
    assert(res.scrolledY > 0 || res.innerScrolled > 0, `${label}: scroll vertical não alterou scrollTop/scrollY`);
  }
  return res;
}

function assertNoBadEvents(label, bucket) {
  assert(bucket.length === 0, `${label} falhou com erros de rede/JS/console:\n - ${bucket.join('\n - ')}`);
}

async function createAuthedContext(browser, { width = 1440, height = 900, acceptDownloads = true, isMobile = false, hasTouch = false } = {}) {
  const ctx = await browser.newContext({
    viewport: { width, height },
    extraHTTPHeaders: { Authorization: `Bearer ${TOKEN}` },
    acceptDownloads,
    isMobile,
    hasTouch,
  });
  // Strip Authorization from third-party CDNs (Google Fonts, etc.) so CORS preflight never fails while keeping same-origin + wss:// authed
  await ctx.route((url) => !url.href.startsWith(BASE_URL), async (route) => {
    const reqHeaders = { ...route.request().headers() };
    delete reqHeaders.authorization;
    delete reqHeaders.Authorization;
    await route.continue({ headers: reqHeaders });
  });
  return ctx;
}

// =============================================================================
// BLOCO 1 — SupoClip (/app/supoclip/)
// =============================================================================
async function testSupoClip(browser) {
  console.log('\n=== BLOCO 1: SupoClip (Auth + Workflows + Upload Real MP4 + Lista) ===');
  await startOnlyStack('supoclip');

  const ts = Date.now();
  const qaEmail = `qa-e2e-${ts}@test.local`;
  const qaPass = 'QaE2E!2026#Strong';

  // 1A: Sign-Up em contexto limpo
  {
    const ctx = await createAuthedContext(browser);
    const page = await ctx.newPage();
    const bad = [];
    attachStrictListeners(page, 'SupoClip SignUp', bad);

    await page.goto(`${BASE_URL}/app/supoclip/sign-up`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.locator('button[type="submit"]:not([disabled])').waitFor({ state: 'visible', timeout: 45000 });
    await page.locator('input[type="text"]').first().fill('QA Studio User');
    await page.locator('input[type="email"]').fill(qaEmail);
    await page.locator('input[type="password"]').fill(qaPass);

    const [signUpRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/auth/sign-up/email') && r.request().method() === 'POST', { timeout: 30000 }),
      page.locator('button[type="submit"]').click(),
    ]);
    assert(signUpRes.status() === 200, `SupoClip sign-up retornou HTTP ${signUpRes.status()}`);
    await page.waitForURL((u) => u.pathname.startsWith('/app/supoclip/list'), { timeout: 60000 });
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
    await page.waitForTimeout(1000);
    await checkLayoutAndScroll(page, 'SupoClip List Pós-SignUp');
    assertNoBadEvents('SupoClip SignUp', bad);
    console.log(`[PASS] Sign-Up BetterAuth (${qaEmail}) -> redirecionado para ${page.url()}`);
    await ctx.close();
  }

  // 1B: Sign-In em contexto novo (/app/supoclip/sign-in?) + Workflow + Upload MP4 + Task
  {
    const ctx = await createAuthedContext(browser);
    const page = await ctx.newPage();
    const bad = [];
    attachStrictListeners(page, 'SupoClip Flow', bad);

    await page.goto(`${BASE_URL}/app/supoclip/sign-in?`, { waitUntil: 'networkidle', timeout: 60000 });
    await page.locator('button[type="submit"]:not([disabled])').waitFor({ state: 'visible', timeout: 45000 });
    await page.locator('input[type="email"]').fill(qaEmail);
    await page.locator('input[type="password"]').fill(qaPass);

    const [signInRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/auth/sign-in/email') && r.request().method() === 'POST', { timeout: 30000 }),
      page.locator('button[type="submit"]').click(),
    ]);
    assert(signInRes.status() === 200, `SupoClip sign-in retornou HTTP ${signInRes.status()}`);
    await page.waitForURL((u) => u.pathname.startsWith('/app/supoclip/list'), { timeout: 60000 });
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});

    const cookies = await ctx.cookies(BASE_URL);
    const sessionCookie = cookies.find((c) => c.name.includes('better-auth.session_token'));
    assert(Boolean(sessionCookie && sessionCookie.value), 'Cookie __Secure-better-auth.session_token não gravado no navegador');
    await checkLayoutAndScroll(page, 'SupoClip /list');

    // Clicar em botão/link de criação ou navegar para /app/supoclip/
    const newTaskLink = page.locator('a[href="/app/supoclip/"], a[href="/app/supoclip"], a[href="/"]').first();
    if (await newTaskLink.isVisible().catch(() => false)) {
      await newTaskLink.click();
      await page.waitForURL((u) => u.pathname === '/app/supoclip/' || u.pathname === '/app/supoclip', { timeout: 45000 });
    } else {
      await page.goto(`${BASE_URL}/app/supoclip/`, { waitUntil: 'networkidle', timeout: 60000 });
    }

    await page.locator('[aria-labelledby="what-to-make"] button').first().waitFor({ state: 'visible', timeout: 45000 });
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});
    await checkLayoutAndScroll(page, 'SupoClip Create Desktop');

    // Clicar em "Destaques" e depois "Série"
    const highlightsBtn = page.locator('[aria-labelledby="what-to-make"] button', { hasText: /Destaques|Highlights/i }).first();
    await highlightsBtn.click();
    const seriesBtn = page.locator('[aria-labelledby="what-to-make"] button', { hasText: /Série|Series/i }).first();
    await seriesBtn.click();

    // Selecionar arquivo MP4 real (o upload dispara junto no submit do formulário)
    await page.locator('#video-upload').setInputFiles(MOCK_LONG_MP4);
    await page.waitForTimeout(500);

    // Submeter formulário -> dispara POST /upload e em seguida POST /api/tasks/create
    const submitBtn = page.locator('form button[type="submit"]').first();
    await submitBtn.scrollIntoViewIfNeeded();
    const uploadPromise = page.waitForResponse((r) => r.url().includes('/app/supoclip-api/upload') && r.request().method() === 'POST', { timeout: 45000 });
    const taskPromise = page.waitForResponse((r) => r.url().includes('/api/tasks/create') && r.request().method() === 'POST', { timeout: 45000 });
    await submitBtn.click();
    const upRes = await uploadPromise;
    assert(upRes.status() === 200, `Upload SupoClip retornou HTTP ${upRes.status()}`);
    const upData = await upRes.json();
    assert(String(upData.video_path || '').startsWith('upload://'), `video_path inválido: ${JSON.stringify(upData)}`);
    const taskRes = await taskPromise;
    assert(taskRes.status() === 200 || taskRes.status() === 201, `tasks/create retornou HTTP ${taskRes.status()}`);
    await page.waitForURL((u) => u.pathname.includes('/app/supoclip/tasks/'), { timeout: 55000 });
    await page.waitForLoadState('networkidle', { timeout: 30000 }).catch(() => {});

    // Conferir na listagem /app/supoclip/list
    await page.goto(`${BASE_URL}/app/supoclip/list`, { waitUntil: 'networkidle', timeout: 45000 });
    const listText = await page.locator('body').innerText();
    assert(listText.trim().length > 20, 'Página /app/supoclip/list vazia');

    // Mobile 390x844
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`${BASE_URL}/app/supoclip/`, { waitUntil: 'domcontentloaded', timeout: 30000 });
    await page.locator('[aria-labelledby="what-to-make"] button').first().waitFor({ state: 'visible', timeout: 20000 });
    await checkLayoutAndScroll(page, 'SupoClip Mobile 390x844');

    assertNoBadEvents('SupoClip Flow', bad);
    console.log('[PASS] SupoClip Sign-In (?), Cookie BetterAuth, Destaques/Série, Upload MP4, Task Create & Mobile!');
    await ctx.close();
  }

  await stopAllAndAssertZero();
}

// =============================================================================
// BLOCO 2 — MoneyPrinterTurbo (/app/moneyprinter/)
// =============================================================================
async function testMoneyPrinterTurbo(browser) {
  console.log('\n=== BLOCO 2: MoneyPrinterTurbo (WebSocket + Form + Upload + Render MP4) ===');
  await startOnlyStack('moneyprinter');

  const ctx = await createAuthedContext(browser, { width: 1440, height: 900, acceptDownloads: true });
  const page = await ctx.newPage();
  const bad = [];
  let wsConnected = false;
  attachStrictListeners(page, 'MoneyPrinterTurbo', bad);

  page.on('websocket', (ws) => {
    if (ws.url().includes('_stcore/stream')) wsConnected = true;
  });

  await page.goto(`${BASE_URL}/app/moneyprinter/`, { waitUntil: 'domcontentloaded', timeout: 60000 });
  await page.locator('textarea[aria-label="Tema do vídeo"]').waitFor({ state: 'visible', timeout: 40000 });
  assert(wsConnected, 'WebSocket _stcore/stream do Streamlit não conectou');

  // Dispensar tour Driver.js
  await page.locator('.driver-overlay, button.driver-popover-close-btn').first().waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
  for (let i = 0; i < 4; i++) {
    const closeBtn = page.locator('button.driver-popover-close-btn').first();
    if (await closeBtn.isVisible().catch(() => false)) {
      await closeBtn.click().catch(() => {});
    } else if ((await page.locator('.driver-overlay').count()) > 0) {
      await page.keyboard.press('Escape');
    }
    await page.waitForTimeout(300);
  }
  assert((await page.locator('.driver-overlay').count()) === 0, 'Overlay driver-js não foi fechado');

  await checkLayoutAndScroll(page, 'MoneyPrinter Desktop', { requireVerticalScroll: true });

  // Preencher campos obrigatórios e interagir com checkbox
  await page.locator('textarea[aria-label="Tema do vídeo"]').fill('Teste E2E Estudio IA');
  await page.locator('textarea[aria-label="Roteiro do vídeo (opcional)"]').fill('Tecnologia em tempo real.');

  const subCheckbox = page.locator('input[aria-label="Ativar Legendas"]').first();
  if ((await subCheckbox.count()) > 0 && !(await subCheckbox.isChecked())) {
    await subCheckbox.click({ force: true });
  }

  // Upload de arquivo MP4 local
  await page.locator('[data-testid="stFileUploaderDropzoneInput"]').first().setInputFiles(MOCK_SHORT_MP4);
  await page.waitForTimeout(2500);

  if (await page.locator('button.driver-popover-close-btn').isVisible().catch(() => false)) {
    await page.locator('button.driver-popover-close-btn').click().catch(() => {});
  }

  const genBtn = page.getByRole('button', { name: /^Gerar Vídeo$|^Generate Video$/i }).first();
  await genBtn.scrollIntoViewIfNeeded();
  await genBtn.click();
  console.log('[INFO] MoneyPrinterTurbo: aguardando renderização de <video> MP4 (até 150s)...');

  let videoSrc = '';
  const deadline = Date.now() + 150000;
  while (Date.now() < deadline) {
    if ((await page.locator('video').count()) > 0) {
      videoSrc = (await page.locator('video').first().getAttribute('src')) || '';
      if (videoSrc) break;
    }
    await page.waitForTimeout(3000);
  }
  assert(Boolean(videoSrc), 'Nenhum <video> renderizado pelo MoneyPrinterTurbo em 150s');

  const fullVideoUrl = new URL(videoSrc, page.url()).href;
  const mediaRes = await page.evaluate(async (u) => {
    const r = await fetch(u);
    const b = await r.arrayBuffer();
    return { status: r.status, bytes: b.byteLength };
  }, fullVideoUrl);
  assert(mediaRes.status === 200 && mediaRes.bytes > 1000, `Vídeo MP4 inválido: ${JSON.stringify(mediaRes)}`);

  const dlBtn = page.getByRole('button', { name: /Download Video|Baixar Vídeo/i }).first();
  assert(await dlBtn.isVisible(), 'Botão Download Video não ficou visível após renderização');
  const [dl] = await Promise.all([
    page.waitForEvent('download', { timeout: 25000 }),
    dlBtn.click(),
  ]);
  assert(dl.suggestedFilename().toLowerCase().endsWith('.mp4'), `Arquivo baixado não é MP4: ${dl.suggestedFilename()}`);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(500);
  await checkLayoutAndScroll(page, 'MoneyPrinter Mobile 390x844');

  assertNoBadEvents('MoneyPrinterTurbo', bad);
  console.log(`[PASS] MoneyPrinterTurbo: WS OK, renderizado ${mediaRes.bytes} bytes (${dl.suggestedFilename()}) e 0 erros!`);
  await ctx.close();
  await stopAllAndAssertZero();
}

// =============================================================================
// BLOCO 3 — VoiceStudio (/app/voicestudio/)
// =============================================================================
async function testVoiceStudio(browser) {
  console.log('\n=== BLOCO 3: VoiceStudio (Select Voz + Síntese + Audio + Download MP3) ===');
  await startOnlyStack('voicestudio');

  const ctx = await createAuthedContext(browser, { width: 1440, height: 900, acceptDownloads: true });
  const page = await ctx.newPage();
  const bad = [];
  attachStrictListeners(page, 'VoiceStudio', bad);

  await page.goto(`${BASE_URL}/app/voicestudio/`, { waitUntil: 'networkidle', timeout: 45000 });
  await checkLayoutAndScroll(page, 'VoiceStudio Desktop', { requireVerticalScroll: true });

  await page.selectOption('#voice-select', 'pt-BR-FranciscaNeural');
  await page.selectOption('#rate-select', '+15%');
  await page.selectOption('#pitch-select', '+5Hz');
  await page.fill('#tts-text', 'Teste interativo completo no VoiceStudio na VPS Hostinger.');

  const [ttsRes] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/app/voicestudio/api/tts') && r.request().method() === 'POST', { timeout: 30000 }),
    page.click('#btn-gen'),
  ]);
  assert(ttsRes.status() === 200, `VoiceStudio /api/tts retornou HTTP ${ttsRes.status()}`);
  await page.locator('#player-box').waitFor({ state: 'visible', timeout: 15000 });

  const audioSrc = await page.locator('#audio-player').getAttribute('src');
  assert(Boolean(audioSrc), '<audio id="audio-player"> sem src');
  const fullAudioUrl = new URL(audioSrc, page.url()).href;
  const audioCheck = await page.evaluate(async (u) => {
    const r = await fetch(u);
    const buf = await r.arrayBuffer();
    return { status: r.status, contentType: r.headers.get('content-type') || '', bytes: buf.byteLength };
  }, fullAudioUrl);
  assert(audioCheck.status === 200 && audioCheck.bytes > 1000 && audioCheck.contentType.includes('audio'), `Áudio MP3 inválido: ${JSON.stringify(audioCheck)}`);

  const [dl] = await Promise.all([
    page.waitForEvent('download', { timeout: 15000 }),
    page.click('#download-link'),
  ]);
  assert(dl.suggestedFilename().toLowerCase().endsWith('.mp3'), `Download VoiceStudio inesperado: ${dl.suggestedFilename()}`);

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(300);
  await checkLayoutAndScroll(page, 'VoiceStudio Mobile 390x844');

  assertNoBadEvents('VoiceStudio', bad);
  console.log(`[PASS] VoiceStudio: MP3 ${audioCheck.bytes} bytes (${dl.suggestedFilename()}) + Desktop/Mobile OK!`);
  await ctx.close();
  await stopAllAndAssertZero();
}

// =============================================================================
// BLOCO 4 — Painel Central Desktop (1440x900) + Mobile (390x844)
// =============================================================================
async function testControlPanel(browser) {
  console.log('\n=== BLOCO 4: Painel Central (Desktop + Abas + Modo Dev + Estúdio Rápido + Galeria + Mobile) ===');
  await stopAllAndAssertZero();

  // 4A: Desktop 1440x900
  {
    const ctx = await createAuthedContext(browser, { width: 1440, height: 900, acceptDownloads: true });
    const page = await ctx.newPage();
    const bad = [];
    attachStrictListeners(page, 'ControlPanel Desktop', bad);

    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 45000 });
    await page.locator('#projects-grid').waitFor({ state: 'visible', timeout: 25000 });

    const tabs = [
      { nav: '#tab-stacks', section: '#section-stacks', scroll: true },
      { nav: '#tab-quick', section: '#section-quick', scroll: true },
      { nav: '#tab-outputs', section: '#section-outputs', scroll: false },
      { nav: '#tab-jarvis', section: '#openjarvis-card', scroll: true },
      { nav: '#tab-skills', section: '#section-skills', scroll: true },
      { nav: '#tab-vault', section: '#section-vault', scroll: true },
      { nav: '#tab-audit', section: '#section-audit', scroll: false },
    ];

    for (const t of tabs) {
      await page.click(t.nav);
      await page.locator(t.section).waitFor({ state: 'visible', timeout: 10000 });
      await page.waitForTimeout(250);
      await checkLayoutAndScroll(page, `Painel Desktop Aba ${t.nav}`, { requireVerticalScroll: t.scroll });
    }

    // Diagnóstico Global (com 0 stacks ativas)
    const [diagRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/diagnostics/all') && r.request().method() === 'POST', { timeout: 60000 }),
      page.click('#btn-global-diag'),
    ]);
    assert(diagRes.status() === 200, `/api/diagnostics/all retornou HTTP ${diagRes.status()}`);
    await page.waitForTimeout(500);

    // Filtros Cofre de Chaves
    await page.click('#tab-vault');
    await page.click('#vtab-cascade');
    await page.click('#vtab-media');
    await page.click('#vtab-all');

    // Modo Dev Toggle ON -> OFF (ou restaura para Repouso no final)
    await page.click('#tab-skills');
    await page.waitForTimeout(300);
    const pillBefore = (await page.locator('#sidebar-modo-dev-pill').innerText()).trim();
    if (pillBefore !== 'Repouso') {
      await page.click('#dev-mode-toggle');
      await page.waitForFunction(() => document.getElementById('sidebar-modo-dev-pill')?.textContent?.trim() === 'Repouso', { timeout: 20000 });
    }
    await page.click('#dev-mode-toggle');
    await page.waitForFunction(() => document.getElementById('sidebar-modo-dev-pill')?.textContent?.trim() === 'ATIVO', { timeout: 20000 });
    const badgeActive = (await page.locator('#nav-skills-badge').innerText()).trim();
    assert(badgeActive.includes('+27'), `Badge Modo Dev ativo esperado com '+27', obtido '${badgeActive}'`);

    await page.click('#dev-mode-toggle');
    await page.waitForFunction(() => document.getElementById('sidebar-modo-dev-pill')?.textContent?.trim() === 'Repouso', { timeout: 20000 });
    const badgeRest = (await page.locator('#nav-skills-badge').innerText()).trim();
    assert(!badgeRest.includes('+'), `Badge Modo Dev em repouso inválido: '${badgeRest}'`);
    console.log(`[PASS] Todas 7 abas Desktop + Diagnóstico + Cofre + Toggle Modo Dev (${badgeActive} -> ${badgeRest}) OK!`);

    // Estúdio Rápido — 1 stack por vez!
    await page.click('#tab-quick');
    await page.locator('#section-quick').waitFor({ state: 'visible', timeout: 10000 });

    // 4A.1 SupoClip Card (Upload MP4 + Disparar)
    await page.locator('#section-quick button:has-text("Upload MP4")').click();
    await page.locator('#btn-qs-upload').waitFor({ state: 'visible', timeout: 5000 });
    await page.locator('#qs-upload-file').setInputFiles(MOCK_LONG_MP4);
    const [qsUpRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/quick/supoclip/upload') && r.request().method() === 'POST', { timeout: 95000 }),
      page.click('#btn-qs-upload'),
    ]);
    assert(qsUpRes.status() === 200, `Quick SupoClip Upload retornou HTTP ${qsUpRes.status()}`);
    await page.waitForFunction(() => (document.getElementById('qs-url')?.value || '').startsWith('upload://'), { timeout: 15000 });

    await page.selectOption('#qs-cut-mode', 'highlights');
    const [qsSubRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/quick/supoclip') && r.request().method() === 'POST' && !r.url().includes('/upload'), { timeout: 60000 }),
      page.click('#btn-qs-submit'),
    ]);
    assert(qsSubRes.status() === 200, `Quick SupoClip Submit retornou HTTP ${qsSubRes.status()}`);
    await page.locator('#qs-status-box').waitFor({ state: 'visible', timeout: 10000 });
    console.log('[PASS] Estúdio Rápido -> SupoClip Upload + Task Submit OK!');
    await page.click('#btn-stop-all');
    await stopAllAndAssertZero();

    // 4A.2 MoneyPrinter Card (Submissão local B-Roll)
    await page.fill('#qm-subject', 'Produto QA E2E Painel');
    await page.fill('#qm-script', 'Video curto automatizado via Painel Mobile.');
    await page.selectOption('#qm-source', 'local');
    const [qmSubRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/quick/moneyprinter') && r.request().method() === 'POST', { timeout: 80000 }),
      page.click('#btn-qm-submit'),
    ]);
    assert(qmSubRes.status() === 200, `Quick MoneyPrinter Submit retornou HTTP ${qmSubRes.status()}`);
    await page.locator('#qm-status-box').waitFor({ state: 'visible', timeout: 10000 });
    console.log('[PASS] Estúdio Rápido -> MoneyPrinter Submit OK!');
    await page.click('#btn-stop-all');
    await stopAllAndAssertZero();

    // 4A.3 VoiceStudio Card (Síntese MP3 + Player + Download)
    await page.fill('#qv-text', 'Narração neural disparada direto do Estúdio Rápido.');
    await page.selectOption('#qv-voice', 'pt-BR-AntonioNeural');
    const [qvSubRes] = await Promise.all([
      page.waitForResponse((r) => r.url().includes('/api/quick/voicestudio') && r.request().method() === 'POST', { timeout: 60000 }),
      page.click('#btn-qv-submit'),
    ]);
    assert(qvSubRes.status() === 200, `Quick VoiceStudio retornou HTTP ${qvSubRes.status()}`);
    await page.locator('#qv-result-box').waitFor({ state: 'visible', timeout: 15000 });
    const qvSrc = await page.locator('#qv-audio-player').getAttribute('src');
    const fullQvUrl = new URL(qvSrc, page.url()).href;
    const qvCheck = await page.evaluate(async (u) => {
      const r = await fetch(u);
      const b = await r.arrayBuffer();
      return { status: r.status, bytes: b.byteLength };
    }, fullQvUrl);
    assert(qvCheck.status === 200 && qvCheck.bytes > 1000, `Quick Voice audio falhou: ${JSON.stringify(qvCheck)}`);
    const [qvDl] = await Promise.all([
      page.waitForEvent('download', { timeout: 15000 }),
      page.click('#qv-download-btn'),
    ]);
    assert(qvDl.suggestedFilename().toLowerCase().endsWith('.mp3'), `Download Quick Voice inesperado: ${qvDl.suggestedFilename()}`);
    console.log(`[PASS] Estúdio Rápido -> VoiceStudio (${qvCheck.bytes} bytes, ${qvDl.suggestedFilename()}) OK!`);
    await stopAllAndAssertZero();

    // 4A.4 Galeria & Downloads (Filtros + Preview + Download)
    await page.click('#tab-outputs');
    await page.locator('#outputs-grid').waitFor({ state: 'visible', timeout: 10000 });
    await page.waitForFunction(() => document.querySelectorAll('#outputs-grid > div').length > 0, { timeout: 15000 });

    await page.click('#ofilter-voicestudio');
    await page.waitForTimeout(300);
    const outAudioSrc = await page.locator('#outputs-grid audio').first().getAttribute('src');
    assert(Boolean(outAudioSrc), 'Nenhum <audio> visível na Galeria para filtro VoiceStudio');
    const outAudioCheck = await page.evaluate(async (u) => {
      const r = await fetch(new URL(u, window.location.href).href);
      const b = await r.arrayBuffer();
      return { status: r.status, bytes: b.byteLength };
    }, outAudioSrc);
    assert(outAudioCheck.status === 200 && outAudioCheck.bytes > 1000, `Preview áudio na Galeria falhou: ${JSON.stringify(outAudioCheck)}`);

    const [galDl] = await Promise.all([
      page.waitForEvent('download', { timeout: 20000 }),
      page.locator('#outputs-grid a[download]').first().click(),
    ]);
    assert(Boolean(galDl.suggestedFilename()), 'Download direto da Galeria falhou');

    await page.click('#ofilter-moneyprinter');
    await page.waitForTimeout(300);
    if ((await page.locator('#outputs-grid video').count()) > 0) {
      const outVidSrc = await page.locator('#outputs-grid video').first().getAttribute('src');
      const outVidCheck = await page.evaluate(async (u) => {
        const r = await fetch(new URL(u, window.location.href).href);
        const b = await r.arrayBuffer();
        return { status: r.status, bytes: b.byteLength };
      }, outVidSrc);
      assert(outVidCheck.status === 200 && outVidCheck.bytes > 1000, `Preview vídeo MoneyPrinter na Galeria falhou: ${JSON.stringify(outVidCheck)}`);
    }
    await page.click('#ofilter-all');

    assertNoBadEvents('ControlPanel Desktop', bad);
    console.log(`[PASS] Galeria & Downloads Desktop (${galDl.suggestedFilename()}) + 0 erros!`);
    await ctx.close();
  }

  // 4B: Mobile 390x844
  {
    const ctx = await createAuthedContext(browser, { width: 390, height: 844, acceptDownloads: true, isMobile: true, hasTouch: true });
    const page = await ctx.newPage();
    const bad = [];
    attachStrictListeners(page, 'ControlPanel Mobile', bad);

    await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 45000 });
    await page.locator('#mobile-bottom-nav').waitFor({ state: 'visible', timeout: 20000 });

    for (const m of [
      { btn: '#mnav-stacks', sec: '#section-stacks' },
      { btn: '#mnav-quick', sec: '#section-quick' },
      { btn: '#mnav-outputs', sec: '#section-outputs' },
    ]) {
      await page.click(m.btn);
      await page.locator(m.sec).waitFor({ state: 'visible', timeout: 10000 });
      if (m.btn === '#mnav-outputs') {
        await page.waitForFunction(() => document.querySelectorAll('#outputs-grid > div').length > 0, { timeout: 15000 });
      }
      await page.waitForTimeout(250);
      await checkLayoutAndScroll(page, `Painel Mobile ${m.btn}`, { requireVerticalScroll: true });
    }

    // Drawer Lateral Mobile
    await page.click('#mnav-menu');
    await page.waitForFunction(() => !document.getElementById('sidebar')?.classList.contains('-translate-x-full'), { timeout: 5000 });
    await page.click('#tab-skills');
    await page.waitForFunction(() => document.getElementById('sidebar')?.classList.contains('-translate-x-full'), { timeout: 5000 });
    await page.locator('#section-skills').waitFor({ state: 'visible', timeout: 10000 });
    await checkLayoutAndScroll(page, 'Painel Mobile Drawer -> Skills', { requireVerticalScroll: true });

    assertNoBadEvents('ControlPanel Mobile', bad);
    console.log('[PASS] Painel Mobile 390x844 (Bottom Nav + Drawer Lateral + Scroll + Zero Overflow)!');
    await ctx.close();
  }
}

async function main() {
  let browser;
  try {
    browser = await chromium.launch({
      executablePath: '/usr/bin/google-chrome',
      headless: true,
      args: ['--no-sandbox', '--disable-dev-shm-usage'],
    });

    const only = (process.env.E2E_ONLY || 'all').toLowerCase();
    if (only === 'all' || only === 'supoclip') await testSupoClip(browser);
    if (only === 'all' || only === 'moneyprinter') await testMoneyPrinterTurbo(browser);
    if (only === 'all' || only === 'voicestudio') await testVoiceStudio(browser);
    if (only === 'all' || only === 'panel') await testControlPanel(browser);

    console.log('\n==============================================================');
    console.log('ETAPA 1 E2E CONCLUÍDA: 100% APROVADO · 0 FALHAS · 0 ERROS HTTP');
    console.log('==============================================================');
  } finally {
    if (browser) await browser.close().catch(() => {});
    try {
      await stopAllAndAssertZero();
      console.log('[CLEANUP] VPS garantida em repouso: 0/7 contêineres Docker ativos.');
    } catch (e) {
      console.error('[CLEANUP ERROR]', e.message);
      process.exitCode = 1;
    }
  }
}

main().catch((err) => {
  console.error('\n[FATAL E2E FAILURE]', err.stack || err.message);
  process.exit(1);
});
