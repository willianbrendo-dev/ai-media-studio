#!/usr/bin/env node
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire('/home/wbrendo/Projetos/fresh-start/package.json');
const { chromium } = require('playwright');

const BASE_URL = 'https://187.77.202.176.sslip.io';
const BAD_STATUSES = new Set([400, 401, 403, 404, 422, 500, 502, 503, 504]);
const SAMPLE_VIRAL_MP4 = '/home/wbrendo/.gemini/antigravity/brain/a7f7d602-7234-4486-835e-7b2c2a8e0e98/scratch/virals_test/ref_viral_1.mp4';

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
    if (!msg.includes('ERR_ABORTED')) {
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
    window.scrollTo(0, 260);
    const sy = window.scrollY;
    window.scrollTo(0, 0);
    return { sw, iw, overflowX: sw > iw + 1, sy };
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

const TEMPLATES = [
  'shopee_viral_clone',
  'novela_shorts_polish',
  'dark_channel_full',
  'pov_cinema_ad',
  'express_jumpcut',
];

async function runDesktop(browser) {
  console.log('\n=== DESKTOP 1440x900: DUAL-MODE + 5 TRILHAS + CLONE VIRAL + ABRIR NO MODO HARD ===');
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Desktop', errs);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.locator('#tab-quick').click();
  await page.waitForSelector('#quick-mode-switcher');
  await page.waitForSelector('#gp-templates-grid');

  // 1. Test Dual-Mode Switcher <->
  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#quick-hard-grid', { state: 'visible' });
  await page.waitForSelector('#qhf-viral-files', { state: 'visible' });
  await page.waitForSelector('#btn-qhf-analyze-virals', { state: 'visible' });
  assert(await page.locator('#quick-guided-panel').isHidden(), 'Guided panel deve ficar oculto no Modo Hard');

  await page.locator('#btn-quick-mode-guided').click();
  await page.waitForSelector('#quick-guided-panel', { state: 'visible' });
  assert(await page.locator('#quick-hard-grid').isHidden(), 'Hard grid deve ficar oculto no Modo Guiado');
  console.log('[PASS] Dual-Mode Switcher: 🎯 Modo Guiado <-> 🛠️ Modo Hard validado');

  // 2. Test all 5 mission cards morphing lean form + visual stage map cleanly
  for (const tid of TEMPLATES) {
    await page.locator(`#gp-tpl-${tid}`).click();
    await page.waitForSelector(`#gp-fields-${tid}`, { state: 'visible' });
    for (const other of TEMPLATES) {
      if (other !== tid) {
        assert(await page.locator(`#gp-fields-${other}`).isHidden(), `${other} devia estar oculto quando ${tid} está ativo`);
      }
    }
    const stepsHtml = await page.locator('#gp-steps-map').innerText();
    assert(stepsHtml.includes('Etapa 1'), `${tid}: mapa visual sem Etapa 1`);
    await checkLayout(page, `Desktop Template ${tid}`);
  }
  console.log('[PASS] Todas as 5 Trilhas Guiadas renderizaram formulários enxutos + mapa visual sem overflow');

  // 3. Shopee Viral Clone end-to-end via UI (Upload viral MP4 -> Analyze DNA -> Execute Pipeline -> Poll -> Preview -> Open in Hard Mode)
  await page.locator('[data-platform="shopee_20_9"]').click();
  await page.locator('#gp-tpl-shopee_viral_clone').click();
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll('#gp-campaign option')).some((o) => o.value === 'elseve-kit2'),
    null,
    { timeout: 15000 },
  );
  await page.selectOption('#gp-campaign', 'elseve-kit2');
  await page.waitForSelector('#gp-campaign-preview img', { state: 'visible', timeout: 15000 });

  assert(fs.existsSync(SAMPLE_VIRAL_MP4), `Sample viral MP4 ausente: ${SAMPLE_VIRAL_MP4}`);
  const upProm = page.waitForResponse(
    (r) => r.url().includes('/api/campaigns/elseve-kit2/upload-virals') && r.request().method() === 'POST',
    { timeout: 30000 },
  );
  await page.locator('#gp-viral-files').setInputFiles(SAMPLE_VIRAL_MP4);
  const upRes = await upProm;
  assert(upRes.status() === 200, `Upload viral HTTP ${upRes.status()}`);

  const anProm = page.waitForResponse(
    (r) => r.url().includes('/api/campaigns/elseve-kit2/analyze-virals') && r.request().method() === 'POST',
    { timeout: 120000 },
  );
  await page.locator('#btn-gp-analyze-virals').click();
  const anRes = await anProm;
  assert(anRes.status() === 200, `Analyze viral HTTP ${anRes.status()}`);

  await page.waitForSelector('#gp-viral-blueprint-card', { state: 'visible', timeout: 15000 });
  const filledScript = (await page.locator('#gp-script').inputValue()).trim();
  assert(filledScript.length > 20, `Roteiro não foi preenchido pelo DNA Viral: "${filledScript}"`);
  console.log(`[PASS] UI Viral Clone Upload + Análise DNA OK (roteiro ${filledScript.length} chars)`);

  // Execute Guided Pipeline
  const startProm = page.waitForResponse(
    (r) => r.url().includes('/api/pipelines/start') && r.request().method() === 'POST',
    { timeout: 30000 },
  );
  await page.locator('#btn-gp-run').click();
  const startRes = await startProm;
  assert(startRes.status() === 200, `Pipeline start HTTP ${startRes.status()}`);

  // Wait for completion & <video> preview in Stepper monitor
  await page.waitForSelector('#gp-result-box', { state: 'visible', timeout: 300000 });
  const stepperText = await page.locator('#gp-stepper-list').innerText();
  assert(stepperText.includes('Concluída ✓'), `Stepper não exibiu etapas concluídas: ${stepperText}`);
  const previewSrc = await page.locator('#gp-preview-video').getAttribute('src');
  assert(previewSrc && previewSrc.includes('/api/outputs/download'), `Video preview src inválido: ${previewSrc}`);
  const headVid = await page.request.head(`${BASE_URL}${previewSrc}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert(headVid.status() === 200, `HEAD preview vídeo retornou ${headVid.status()}`);
  const ramBadge = await page.locator('#gp-ram-badge').innerText();
  assert(ramBadge.includes('0/7 Dockers'), `Esperado 0/7 Dockers no final, obtido '${ramBadge}'`);
  console.log(`[PASS] Trilha Guiada concluída na UI com preview MP4 HTTP 200 (${ramBadge})`);

  // Click "🛠️ Abrir no Modo Hard"
  await page.locator('#btn-gp-open-hard').click();
  await page.waitForSelector('#quick-hard-grid', { state: 'visible' });
  assert(await page.locator('#quick-guided-panel').isHidden(), 'Modo Guiado deve ocultar ao abrir no Modo Hard');
  const hardTitle = (await page.locator('#qhf-title').inputValue()).trim();
  const hardScript = (await page.locator('#qhf-voice-script').inputValue()).trim();
  assert(hardTitle.length > 5 && hardScript.length > 15, `Transferência para Modo Hard incompleta: title="${hardTitle}" script="${hardScript}"`);
  await checkLayout(page, 'Desktop Modo Hard após transferência');
  console.log('[PASS] "🛠️ Abrir no Modo Hard" transferiu título, campanha e roteiro com sucesso');

  assert(errs.length === 0, `Erros Desktop:\n - ${errs.join('\n - ')}`);
  await ctx.close();
}

async function runMobile(browser) {
  console.log('\n=== MOBILE 390x844: DUAL-MODE + 4 PLATAFORMAS + 5 TRILHAS + 0 OVERFLOW ===');
  const ctx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Mobile', errs);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.locator('#mnav-quick').click();
  await page.waitForSelector('#quick-mode-switcher');

  // Verify touch target heights & zero overflow across Dual-Mode and all 5 templates
  const gBox = await page.locator('#btn-quick-mode-guided').boundingBox();
  const hBox = await page.locator('#btn-quick-mode-hard').boundingBox();
  assert(gBox && gBox.height >= 44 && hBox && hBox.height >= 44, 'Touch height < 44px no switcher Dual-Mode');

  for (const plat of ['shopee_20_9', 'tiktok_reels', 'yt_shorts', 'yt_16_9']) {
    await page.locator(`[data-platform="${plat}"]`).click();
    await checkLayout(page, `Mobile Platform ${plat}`);
  }
  await page.locator('[data-platform="shopee_20_9"]').click();

  for (const tid of TEMPLATES) {
    await page.locator(`#gp-tpl-${tid}`).click();
    await page.waitForSelector(`#gp-fields-${tid}`, { state: 'visible' });
    await checkLayout(page, `Mobile Template ${tid}`);
  }

  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#quick-hard-grid', { state: 'visible' });
  await checkLayout(page, 'Mobile Modo Hard');

  await page.locator('#btn-quick-mode-guided').click();
  await page.waitForSelector('#quick-guided-panel', { state: 'visible' });

  assert(errs.length === 0, `Erros Mobile:\n - ${errs.join('\n - ')}`);
  await ctx.close();
  console.log('[PASS] Mobile 390x844 Dual-Mode & 5 Trilhas: 0 overflow · touch targets >= 44px · 0 erros');
}

async function main() {
  const browser = await chromium.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    await runDesktop(browser);
    await runMobile(browser);
    console.log('\n>>> E2E GUIDED PIPELINES V2.3: 100% APROVADO (DESKTOP + MOBILE) <<<');
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('\n[E2E FAILED]:', err);
  process.exit(1);
});
