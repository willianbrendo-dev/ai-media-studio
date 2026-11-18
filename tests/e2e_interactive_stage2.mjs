#!/usr/bin/env node
import fs from 'node:fs';
import zlib from 'node:zlib';
import { execFileSync } from 'node:child_process';
import { createRequire } from 'node:module';

const require = createRequire('/home/wbrendo/Projetos/fresh-start/package.json');
const { chromium } = require('playwright');

const BASE_URL = 'https://187.77.202.176.sslip.io';
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

function createTinyPng(pathOut) {
  const w = 32;
  const h = 32;
  const raw = Buffer.alloc((w * 4 + 1) * h);
  for (let y = 0; y < h; y++) {
    raw[y * (w * 4 + 1)] = 0;
    for (let x = 0; x < w; x++) {
      const idx = y * (w * 4 + 1) + 1 + x * 4;
      raw[idx] = 32;
      raw[idx + 1] = 210;
      raw[idx + 2] = 180;
      raw[idx + 3] = 255;
    }
  }
  const crc = (buf) => {
    let c = 0xffffffff;
    for (const b of buf) {
      c ^= b;
      for (let k = 0; k < 8; k++) c = (c >>> 1) ^ (0xedb88320 & -(c & 1));
    }
    return (c ^ 0xffffffff) >>> 0;
  };
  const chunk = (type, data) => {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length, 0);
    const t = Buffer.from(type);
    const cBuf = Buffer.alloc(4);
    cBuf.writeUInt32BE(crc(Buffer.concat([t, data])), 0);
    return Buffer.concat([len, t, data, cBuf]);
  };
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(w, 0);
  ihdr.writeUInt32BE(h, 4);
  ihdr[8] = 8;
  ihdr[9] = 6;
  fs.writeFileSync(
    pathOut,
    Buffer.concat([
      Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
      chunk('IHDR', ihdr),
      chunk('IDAT', zlib.deflateSync(raw)),
      chunk('IEND', Buffer.alloc(0)),
    ]),
  );
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
  const deadline = Date.now() + 30000;
  while (Date.now() < deadline) {
    const sys = await apiRequest('GET', '/api/system');
    if (sys.active_containers === 0) return sys;
    await new Promise((r) => setTimeout(r, 1000));
  }
  const sysFinal = await apiRequest('GET', '/api/system');
  assert(sysFinal.active_containers === 0, `Esperado 0 contêineres após stop-all, obtido ${sysFinal.active_containers}`);
  return sysFinal;
}

async function startOnlyStack(stackId) {
  await stopAllAndAssertZero();
  const actionRes = await apiRequest('POST', `/api/projects/${stackId}/action`, { action: 'start' });
  assert(actionRes.ok, `Falha ao iniciar ${stackId}`);

  const deadline = Date.now() + 160000;
  while (Date.now() < deadline) {
    const data = await apiRequest('GET', '/api/projects');
    const list = data.projects || [];
    const target = list.find((p) => p.id === stackId);
    const othersRunning = list.filter((p) => p.id !== stackId && p.running_containers > 0);
    assert(othersRunning.length === 0, `Violação RAM: outras stacks ativas além de ${stackId}: ${othersRunning.map((p) => p.id).join(', ')}`);
    if (target && target.http_ready) {
      console.log(`[STACK] ${stackId} ONLINE isolada (${target.running_containers}/${target.total_containers} contêineres)`);
      return target;
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  throw new Error(`Timeout aguardando http_ready para stack ${stackId}`);
}

function attachStrictListeners(page, label, bucket) {
  page.on('response', async (res) => {
    const status = res.status();
    if (BAD_STATUSES.has(status) || status >= 400) {
      let body = '';
      try { body = (await res.text()).slice(0, 160); } catch {}
      bucket.push(`[${label}] HTTP ${status} ${res.request().method()} ${res.url()}${body ? ' :: ' + body : ''}`);
    }
  });
  page.on('pageerror', (err) => {
    bucket.push(`[${label}] PAGEERROR: ${err.message}`);
  });
  page.on('console', (msg) => {
    if (msg.type() === 'error') {
      bucket.push(`[${label}] CONSOLE.ERROR: ${msg.text()}`);
    }
  });
}

async function checkLayoutAndScroll(page, label, { requireVerticalScroll = false } = {}) {
  const res = await page.evaluate(() => {
    const overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
    const winCanScroll = document.documentElement.scrollHeight > window.innerHeight + 1;
    let scrolledY = 0;

    if (winCanScroll) {
      window.scrollTo({ top: 300, behavior: 'instant' });
      scrolledY = Math.max(window.scrollY, document.documentElement.scrollTop, document.body.scrollTop);
      window.scrollTo({ top: 0, behavior: 'instant' });
    }

    let innerScrolled = 0;
    let innerCanScroll = false;
    for (const el of document.querySelectorAll('main, section, div, body')) {
      const ov = getComputedStyle(el).overflowY;
      if ((ov === 'auto' || ov === 'scroll') && el.scrollHeight > el.clientHeight + 2) {
        innerCanScroll = true;
        const before = el.scrollTop;
        el.scrollTo({ top: 220, behavior: 'instant' });
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
    assert(res.winCanScroll || res.innerCanScroll, `${label}: sem scroll vertical detectado`);
    assert(res.scrolledY > 0 || res.innerScrolled > 0, `${label}: scroll vertical não moveu scrollTop/scrollY`);
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
  await ctx.route((url) => !url.href.startsWith(BASE_URL), async (route) => {
    const reqHeaders = { ...route.request().headers() };
    delete reqHeaders.authorization;
    delete reqHeaders.Authorization;
    await route.continue({ headers: reqHeaders });
  });
  return ctx;
}

// =============================================================================
// BLOCO 1: OPENFILM (/app/openfilm/)
// =============================================================================
async function testBloco1OpenFilm(browser) {
  console.log('\n=== BLOCO 1 (ETAPA 2): OPENFILM ===');
  const proj = await startOnlyStack('openfilm');
  const openUrl = `${BASE_URL}${proj.ui_url.startsWith('/') ? proj.ui_url : '/app/openfilm/'}`;

  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  let filmHits = 0;
  page.on('response', (r) => {
    if (r.url().includes('/app/openfilm-film/') && r.status() === 200) filmHits++;
  });
  attachStrictListeners(page, 'OpenFilm-Desktop', errs);

  await page.goto(openUrl, { waitUntil: 'networkidle', timeout: 45000 });
  await page.waitForTimeout(1000);

  // If on Projects home, test creating a project then switching to 'Meu Filme Incrivel'
  const newProjBtn = page.getByRole('button', { name: /New project/i }).first();
  if ((await newProjBtn.count()) && (await newProjBtn.isVisible())) {
    await newProjBtn.click();
    await page.waitForTimeout(1000);
  }

  // Open project switcher -> All projects… (covers /api/projects/*/poster SVG fix) -> click 'Meu Filme Incrivel'
  const switcherBtn = page.locator('button[aria-label*="Switch project" i], button').filter({ hasText: /All projects|Untitled|Project|Meu Filme/i }).first();
  if (await switcherBtn.count()) {
    await switcherBtn.click();
    await page.waitForTimeout(400);
    const allProjBtn = page.getByRole('button', { name: /All projects/i }).last();
    if ((await allProjBtn.count()) && (await allProjBtn.isVisible())) {
      await allProjBtn.click();
      await page.waitForTimeout(900);
    }
  }

  // Close any lingering backdrop overlay before interacting, or pick 'Meu Filme Incrivel' inside the projects grid
  const modalCard = page.locator('[role="dialog"] button, [role="dialog"] a, .fixed button').filter({ hasText: 'Meu Filme Incrivel' }).last();
  if ((await modalCard.count()) && (await modalCard.isVisible())) {
    await modalCard.click();
    await page.waitForTimeout(1500);
  } else {
    const anyOverlay = page.locator('.fixed.inset-0.bg-black\\/60');
    if (await anyOverlay.count()) {
      await page.keyboard.press('Escape').catch(() => {});
      await page.waitForTimeout(400);
    }
    const mainCard = page.getByText('Meu Filme Incrivel').last();
    if ((await mainCard.count()) && (await mainCard.isVisible())) {
      await mainCard.click();
      await page.waitForTimeout(1500);
    }
  }

  // Ensure Layout -> Library toggles cleanly (remember OpenFilm workspace tabs)
  const layoutBtn = page.getByRole('button', { name: /^Layout$/i }).first();
  if ((await layoutBtn.count()) && (await layoutBtn.isVisible())) {
    await layoutBtn.click();
    await page.waitForTimeout(300);
    const libMenuItem = page.getByRole('button', { name: /Library/i }).last();
    if ((await libMenuItem.count()) && (await libMenuItem.isVisible())) {
      await libMenuItem.click().catch(() => {});
      await page.waitForTimeout(400);
    } else {
      await page.keyboard.press('Escape').catch(() => {});
    }
  }

  for (const btnRe of [/Properties/i, /Info/i]) {
    const b = page.getByRole('button', { name: btnRe }).first();
    if ((await b.count()) && (await b.isVisible())) {
      await b.click().catch(() => {});
      await page.waitForTimeout(300);
    }
  }

  // Trigger Export dialog or request
  const exportBtn = page.getByRole('button', { name: /^Export$/i }).first();
  if ((await exportBtn.count()) && (await exportBtn.isVisible())) {
    await exportBtn.click();
    await page.waitForTimeout(500);
    const confirmExp = page.locator('dialog, [role="dialog"]').getByRole('button', { name: /^Export/i }).first();
    if ((await confirmExp.count()) && (await confirmExp.isVisible())) {
      await confirmExp.click();
      await page.waitForTimeout(1000);
    } else {
      await page.keyboard.press('Escape').catch(() => {});
    }
  }

  await checkLayoutAndScroll(page, 'OpenFilm Desktop');
  assertNoBadEvents('OpenFilm Desktop', errs);
  await ctx.close();

  // Mobile 390x844
  const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const mPage = await mCtx.newPage();
  const mErrs = [];
  attachStrictListeners(mPage, 'OpenFilm-Mobile', mErrs);
  await mPage.goto(openUrl, { waitUntil: 'networkidle', timeout: 45000 });
  await mPage.waitForTimeout(900);
  await checkLayoutAndScroll(mPage, 'OpenFilm Mobile');
  assertNoBadEvents('OpenFilm Mobile', mErrs);
  await mCtx.close();

  console.log(`[PASS] OpenFilm Desktop + Mobile validados (0 erros · filmAssets=${filmHits})`);
}

// =============================================================================
// BLOCO 2: AGENTTUBE (/app/agenttube/)
// =============================================================================
async function testBloco2AgentTube(browser) {
  console.log('\n=== BLOCO 2 (ETAPA 2): AGENTTUBE ===');
  await startOnlyStack('agenttube');

  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'AgentTube-Desktop', errs);

  await page.goto(`${BASE_URL}/app/agenttube/`, { waitUntil: 'networkidle', timeout: 35000 });
  await page.waitForTimeout(800);

  const setupHidden = await page.locator('#setup-banner').evaluate((el) => el.classList.contains('hidden'));
  assert(setupHidden, 'AgentTube #setup-banner deve estar oculto (Cascade + local mode configurados)');

  const genDisabled = await page.locator('#generate-button').isDisabled();
  assert(!genDisabled, 'AgentTube #generate-button não deve estar desabilitado');

  // Test all 8 sidebar nav items
  const navViews = ['operator', 'pipeline', 'calendar', 'analytics', 'engagement', 'readiness', 'settings', 'overview'];
  for (const view of navViews) {
    await page.locator(`button.nav-item[data-view="${view}"]`).click();
    await page.waitForTimeout(250);
    const activeVisible = await page.locator(`#${view}-view.active`).count();
    assert(activeVisible === 1, `AgentTube tab '${view}' não ativou seção correspondente`);
  }

  // Open Create video modal and dispatch real background job via AI Cascade
  await page.locator('#generate-button').click();
  await page.locator('#generate-form').waitFor({ state: 'visible', timeout: 5000 });
  await page.locator('#generate-form input[name="topic"]').fill('Automacao de Workflows com IA em 2026');
  await page.locator('#generate-form select[name="style"]').selectOption('explainer');
  await page.locator('#generate-form select[name="length"]').selectOption('short');
  const [postRes] = await Promise.all([
    page.waitForResponse((r) => r.url().includes('/generate') && r.request().method() === 'POST', { timeout: 25000 }),
    page.locator('#generate-form button[type="submit"]').click(),
  ]);
  assert(postRes.status() < 400, `AgentTube POST geração falhou com status ${postRes.status()}`);
  await page.waitForTimeout(1000);

  await checkLayoutAndScroll(page, 'AgentTube Desktop', { requireVerticalScroll: false });
  assertNoBadEvents('AgentTube Desktop', errs);
  await ctx.close();

  // Mobile 390x844
  const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const mPage = await mCtx.newPage();
  const mErrs = [];
  attachStrictListeners(mPage, 'AgentTube-Mobile', mErrs);
  await mPage.goto(`${BASE_URL}/app/agenttube/`, { waitUntil: 'networkidle', timeout: 35000 });
  await mPage.waitForTimeout(700);
  await checkLayoutAndScroll(mPage, 'AgentTube Mobile');
  assertNoBadEvents('AgentTube Mobile', mErrs);
  await mCtx.close();

  console.log('[PASS] AgentTube Desktop + Mobile validados (0 erros · job IA iniciado)');
}

// =============================================================================
// BLOCO 3: OPEN HIGGSFIELD (/app/higgsfield/)
// =============================================================================
async function testBloco3OpenHiggsfield(browser) {
  console.log('\n=== BLOCO 3 (ETAPA 2): OPEN HIGGSFIELD ===');
  await startOnlyStack('openhiggsfield');

  const tmpPng = '/tmp/e2e_stage2_ohf_ref.png';
  createTinyPng(tmpPng);

  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'OpenHiggsfield-Desktop', errs);

  try {
    await page.goto(`${BASE_URL}/app/higgsfield/`, { waitUntil: 'networkidle', timeout: 35000 });
    await page.waitForTimeout(600);

    const hydrated = await page.evaluate(() => ({
      next: !!window.next,
      reactEls: [...document.querySelectorAll('*')].filter((el) => Object.keys(el).some((k) => k.startsWith('__react'))).length,
    }));
    assert(hydrated.next && hydrated.reactEls > 0, `OpenHiggsfield não hidratou React: ${JSON.stringify(hydrated)}`);

    // Key modal interactive save
    await page.locator('button.ohf-key').click();
    await page.locator('dialog[open]').waitFor({ timeout: 5000 });
    await page.getByRole('button', { name: 'Usar Motor VPS' }).click();
    await page.locator('dialog[open]').waitFor({ state: 'detached', timeout: 5000 });

    // Upload mock PNG reference & generate Video MP4
    await page.getByRole('tab', { name: 'Video', exact: true }).click();
    await page.waitForTimeout(300);
    await page.getByRole('button', { name: /Add an input|Change the inputs/i }).click();
    await page.locator('.ohf-popover').waitFor({ timeout: 5000 });
    const [chooser] = await Promise.all([
      page.waitForEvent('filechooser'),
      page.locator('.ohf-popover').getByRole('button', { name: 'Upload file' }).click(),
    ]);
    await chooser.setFiles(tmpPng);
    await page.locator('.ohf-popover img[src*="/api/media/uploads/"]').first().waitFor({ timeout: 12000 });
    const doneBtn = page.locator('.ohf-popover button').filter({ hasText: /^Done$/i }).first();
    if (await doneBtn.count()) await doneBtn.click();
    else await page.keyboard.press('Escape');

    await page.locator('textarea[aria-label="Prompt"]').fill('Orbital shot around cybernetic prism monolith in mist');
    await page.locator('button.ohf-generate').click();
    await page.locator('.ohf-gallery video[src*="/api/media/outputs/"]').first().waitFor({ timeout: 55000 });

    // Switch to Image, use Model Picker (Flux), generate Image PNG
    await page.getByRole('tab', { name: 'Image', exact: true }).click();
    await page.waitForTimeout(300);
    const modelBtn = page.locator('.ohf-composer button').filter({ hasText: /Soul|Flux|Kling|Wan|Recraft|Qwen|Ideogram|Grok|Z-Image/i }).first();
    await modelBtn.click();
    await page.locator('.ohf-popover--picker').waitFor({ timeout: 5000 });
    await page.locator('.ohf-picker-input').fill('Flux');
    await page.waitForTimeout(200);
    await page.locator('.ohf-popover--picker button').filter({ hasText: /FLUX/i }).first().click();

    await page.locator('textarea[aria-label="Prompt"]').fill('Neon reflections over rainy Tokyo cyber bridge');
    await page.locator('button.ohf-generate').click();
    await page.locator('.ohf-gallery img[src*="/api/media/outputs/"]').first().waitFor({ timeout: 45000 });

    // Open Viewer -> Download + Favorite + Close
    await page.locator('.ohf-gallery .ohf-tile-open').first().click();
    await page.locator('dialog[open]').waitFor({ timeout: 5000 });
    const [dl] = await Promise.all([
      page.waitForEvent('download'),
      page.locator('dialog[open] a.ohf-viewer-download').click(),
    ]);
    assert(dl.suggestedFilename().endsWith('.png'), `Download OpenHiggsfield inesperado: ${dl.suggestedFilename()}`);
    await page.locator('dialog[open] button[aria-label="Close"]').click();
    await page.locator('dialog[open]').waitFor({ state: 'detached', timeout: 5000 });

    await page.getByRole('tab', { name: 'Assets', exact: true }).click();
    await page.waitForTimeout(300);
    await checkLayoutAndScroll(page, 'OpenHiggsfield Desktop');
    assertNoBadEvents('OpenHiggsfield Desktop', errs);
  } finally {
    if (fs.existsSync(tmpPng)) fs.unlinkSync(tmpPng);
    await ctx.close();
  }

  // Mobile 390x844
  const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const mPage = await mCtx.newPage();
  const mErrs = [];
  attachStrictListeners(mPage, 'OpenHiggsfield-Mobile', mErrs);
  await mPage.goto(`${BASE_URL}/app/higgsfield/`, { waitUntil: 'networkidle', timeout: 35000 });
  await mPage.waitForTimeout(600);
  await checkLayoutAndScroll(mPage, 'OpenHiggsfield Mobile');
  assertNoBadEvents('OpenHiggsfield Mobile', mErrs);
  await mCtx.close();

  console.log('[PASS] OpenHiggsfield Desktop + Mobile validados (0 erros · upload + PNG + MP4 + download)');
}

// =============================================================================
// BLOCO 4: OPENSEO (/app/openseo/)
// =============================================================================
async function testBloco4OpenSEO(browser) {
  console.log('\n=== BLOCO 4 (ETAPA 2): OPENSEO ===');
  await startOnlyStack('openseo');

  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'OpenSEO-Desktop', errs);

  async function dismissModal(p) {
    for (let i = 0; i < 3; i++) {
      const ov = p.locator('[data-slot="dialog-overlay"]');
      if ((await ov.count()) === 0) break;
      const btn = p.locator('[data-slot="dialog-content"] button').filter({ hasText: /^Dismiss$|^Close$/i }).first();
      if (await btn.count()) await btn.click().catch(() => {});
      else await p.keyboard.press('Escape');
      await p.waitForTimeout(250);
    }
  }

  await page.goto(`${BASE_URL}/app/openseo/`, { waitUntil: 'networkidle', timeout: 40000 });
  await page.waitForTimeout(600);
  await dismissModal(page);

  // Navigate to Context & save brand details
  await page.locator('nav a, a').filter({ hasText: /^Context$/ }).first().click();
  await page.waitForTimeout(600);
  await dismissModal(page);
  await page.locator('main textarea').first().fill('AI Media Studio cloud suite for creators and SEO teams.');
  await page.locator('main button:not([disabled])').filter({ hasText: /^Save changes$/ }).first().click();
  await page.waitForTimeout(500);

  // Navigate to Site Audit, run audit on https://example.com and export CSV
  await page.locator('nav a, a').filter({ hasText: /^Site Audit$/ }).first().click();
  await page.waitForTimeout(600);
  await dismissModal(page);
  if (!page.url().includes('/audit')) {
    await page.locator('nav a, a').filter({ hasText: /^Site Audit$/ }).first().click();
    await page.waitForTimeout(600);
  }
  const startNew = page.locator('main button').filter({ hasText: /Start New Audit|Start Audit/i }).first();
  await startNew.click();
  await page.waitForTimeout(400);
  const inp = page.locator('main input').first();
  if ((await inp.count()) && (await inp.isVisible())) {
    await inp.fill('https://example.com');
    await page.locator('main button').filter({ hasText: /^Start Audit$/i }).first().click();
  }
  await page.locator('main button').filter({ hasText: /^Export$/i }).first().waitFor({ timeout: 25000 });
  await page.locator('main button').filter({ hasText: /^Pages/i }).first().click();
  await page.waitForTimeout(250);
  await page.locator('main button').filter({ hasText: /^Issues/i }).first().click();
  await page.waitForTimeout(250);

  // Export CSV dropdown menu -> wait for download
  await page.locator('main button').filter({ hasText: /^Export$/i }).first().click();
  const csvItem = page.locator('[role="menuitem"], [role="option"], button').filter({ hasText: /Export CSV/i }).first();
  await csvItem.waitFor({ timeout: 5000 });
  const [dl] = await Promise.all([
    page.waitForEvent('download'),
    csvItem.click(),
  ]);
  assert(dl.suggestedFilename().toLowerCase().includes('.csv'), `Export CSV inesperado: ${dl.suggestedFilename()}`);

  // Check additional sidebar pages
  for (const navName of ['Saved Keywords', 'Reports', 'Agent setup', 'Settings']) {
    const link = page.locator('nav a, a').filter({ hasText: new RegExp(`^${navName}$`) }).first();
    if (await link.count()) {
      await link.click();
      await page.waitForTimeout(350);
      await dismissModal(page);
    }
  }

  await page.locator('nav a, a').filter({ hasText: /^Context$/ }).first().click();
  await page.waitForTimeout(450);
  await dismissModal(page);
  await checkLayoutAndScroll(page, 'OpenSEO Desktop');
  assertNoBadEvents('OpenSEO Desktop', errs);
  await ctx.close();

  // Mobile 390x844
  const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const mPage = await mCtx.newPage();
  const mErrs = [];
  attachStrictListeners(mPage, 'OpenSEO-Mobile', mErrs);
  await mPage.goto(`${BASE_URL}/app/openseo/`, { waitUntil: 'networkidle', timeout: 40000 });
  await mPage.waitForTimeout(500);
  await dismissModal(mPage);
  await checkLayoutAndScroll(mPage, 'OpenSEO Mobile');
  assertNoBadEvents('OpenSEO Mobile', mErrs);
  await mCtx.close();

  console.log('[PASS] OpenSEO Desktop + Mobile validados (0 erros · get-session 200 · Context + Site Audit + CSV Export)');
}

// =============================================================================
// BLOCO 5: PAINEL CENTRAL DIAGNÓSTICO PROFUNDO + OPENJARVIS LOCAL E2E
// =============================================================================
async function testBloco5DiagnosticsAndJarvis(browser) {
  console.log('\n=== BLOCO 5 (ETAPA 2): DIAGNÓSTICO PROFUNDO + OPENJARVIS ===');
  const sysBefore = await stopAllAndAssertZero();
  assert(sysBefore.active_containers === 0, 'Deve iniciar Bloco 5 com 0/7 stacks ativas');

  // 5A. Playwright Desktop + Mobile on Painel Central Diagnóstico Profundo
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'PanelDiag-Desktop', errs);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(600);
  await page.locator('#btn-global-diag').click();
  await page.locator('#global-audit-container').getByText(/INFRA BLINDADA/i).waitFor({ timeout: 45000 });
  const auditText = await page.locator('#global-audit-container').innerText();
  assert(auditText.includes('9/9 rotas HTTPS'), 'Matriz 9/9 rotas HTTPS deve estar visível');
  assert(auditText.includes('Google Gemini'), 'Cascata IA deve exibir tiers ativos');

  await checkLayoutAndScroll(page, 'PanelDiag Desktop', { requireVerticalScroll: true });
  assertNoBadEvents('PanelDiag Desktop', errs);
  await ctx.close();

  const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const mPage = await mCtx.newPage();
  const mErrs = [];
  attachStrictListeners(mPage, 'PanelDiag-Mobile', mErrs);
  await mPage.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await mPage.waitForTimeout(600);
  await mPage.locator('#btn-global-diag').click();
  await mPage.locator('#global-audit-container').getByText(/INFRA BLINDADA/i).waitFor({ timeout: 45000 });
  await checkLayoutAndScroll(mPage, 'PanelDiag Mobile', { requireVerticalScroll: true });
  assertNoBadEvents('PanelDiag Mobile', mErrs);
  await mCtx.close();

  // 5B. OpenJarvis Local E2E over HTTPS
  const pyOut = execFileSync(
    '/home/wbrendo/open-jarvis/.venv/bin/python',
    [
      '-c',
      [
        'import sys, json',
        'sys.path.insert(0, "/home/wbrendo/open-jarvis")',
        'import studio_controller_mcp as m',
        'st = json.loads(m.studio_list_stacks("vps"))',
        'assert st["ok"] and st["stack_count"] == 7',
        'dg = json.loads(m.studio_cascade_status(live_check=True, target="vps"))',
        'assert dg["ok"] and dg["infra_diagnostic"]["overall_status"] == "ok"',
        'sc = json.loads(m.studio_scan_media("", "both"))',
        'assert sc["ok"] and sc["vps_gallery_count"] > 0',
        'print(json.dumps({"stacks": st["stack_count"], "infra": dg["infra_diagnostic"]["summary"], "gallery": sc["vps_gallery_count"]}))',
      ].join('\n'),
    ],
    { encoding: 'utf8', timeout: 60000 },
  );
  console.log('[OPENJARVIS MCP]', pyOut.trim());

  const cliOut = execFileSync(
    '/home/wbrendo/open-jarvis/iniciar-jarvis.sh',
    ['--ask', 'Responda exatamente: JARVIS E2E STAGE2 OK'],
    { encoding: 'utf8', timeout: 45000 },
  );
  assert(cliOut.includes('JARVIS E2E STAGE2 OK'), `CLI OpenJarvis resposta inesperada: ${cliOut.slice(-200)}`);
  console.log('[OPENJARVIS CLI] Respondeu via HTTPS Gateway: JARVIS E2E STAGE2 OK');
}

async function main() {
  const browser = await chromium.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    await testBloco1OpenFilm(browser);
    await testBloco2AgentTube(browser);
    await testBloco3OpenHiggsfield(browser);
    await testBloco4OpenSEO(browser);
    await testBloco5DiagnosticsAndJarvis(browser);
    const sysEnd = await stopAllAndAssertZero();
    console.log(`\nETAPA 2 E2E CONCLUÍDA: 100% APROVADO · 0 FALHAS · ACTIVE_CONTAINERS=${sysEnd.active_containers}/7`);
  } finally {
    await stopAllAndAssertZero().catch(() => {});
    await browser.close();
  }
}

main().catch((err) => {
  console.error('\n[E2E STAGE 2 FAILED]:', err);
  process.exit(1);
});
