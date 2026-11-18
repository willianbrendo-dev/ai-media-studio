#!/usr/bin/env node
import fs from 'node:fs';
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
      try { body = (await res.text()).slice(0, 160); } catch {}
      bucket.push(`[${label}] HTTP ${status} ${res.request().method()} ${res.url()}${body ? ' :: ' + body : ''}`);
    }
  });
  page.on('pageerror', (err) => bucket.push(`[${label}] PAGEERROR: ${err.message}`));
  page.on('console', (msg) => {
    if (msg.type() === 'error') bucket.push(`[${label}] CONSOLE.ERROR: ${msg.text()}`);
  });
}

async function checkNoOverflow(page, label) {
  const res = await page.evaluate(() => ({
    overflowX: document.documentElement.scrollWidth > window.innerWidth + 1,
    scrollWidth: document.documentElement.scrollWidth,
    innerWidth: window.innerWidth,
  }));
  assert(!res.overflowX, `${label}: overflow horizontal (${res.scrollWidth} > ${res.innerWidth})`);
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

async function verifyCampaignsAndGallery(page, label, isMobile = false) {
  // Verify /api/campaigns endpoint
  const campRes = await page.request.get(`${BASE_URL}/api/campaigns`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert(campRes.status() === 200, `${label}: /api/campaigns retornou ${campRes.status()}`);
  const campJson = await campRes.json();
  const elseve = (campJson.campaigns || []).find((c) => c.id === 'elseve-kit2');
  assert(elseve, `${label}: campanha elseve-kit2 ausente em /api/campaigns`);
  assert(elseve.curated_count === 6, `${label}: esperado 6 fotos curadas, obtido ${elseve.curated_count}`);
  assert(elseve.excluded_count === 3, `${label}: esperado 3 fotos filtradas, obtido ${elseve.excluded_count}`);

  // Select campaign in Estúdio Rápido -> Modo Hard -> HyperFrames and confirm thumbnails load cleanly
  await page.locator(isMobile ? '#mnav-quick' : '#tab-quick').click();
  if (await page.locator('#btn-quick-mode-hard').isVisible()) {
    await page.locator('#btn-quick-mode-hard').click();
  }
  await page.waitForSelector('#qhf-campaign', { state: 'visible' });
  await page.waitForFunction(
    () => Array.from(document.querySelectorAll('#qhf-campaign option')).some((o) => o.value === 'elseve-kit2'),
    null,
    { timeout: 15000 },
  );
  await page.selectOption('#qhf-campaign', 'elseve-kit2');
  await page.waitForFunction(
    () => document.querySelectorAll('#qhf-campaign-preview img').length === 6,
    null,
    { timeout: 15000 },
  );
  await checkNoOverflow(page, `${label} Quick HyperFrames Campaign`);

  // Verify Galeria & Downloads has the newly rendered Elseve MP4 at top
  await page.locator(isMobile ? '#mnav-outputs' : '#tab-outputs').click();
  await page.waitForSelector('#outputs-grid');
  await page.waitForFunction(
    () => (document.getElementById('outputs-grid')?.innerText || '').toLowerCase().includes('elseve'),
    null,
    { timeout: 15000 },
  );
  const outRes = await page.request.get(`${BASE_URL}/api/outputs?stack=hyperframes&limit=5`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  const outJson = await outRes.json();
  const topElseve = (outJson.items || []).find((x) => (x.filename || '').toLowerCase().includes('elseve'));
  assert(topElseve, `${label}: MP4 do Elseve não encontrado em /api/outputs`);
  const dlHead = await page.request.head(`${BASE_URL}${topElseve.download_url}`, {
    headers: { Authorization: `Bearer ${TOKEN}` },
  });
  assert(dlHead.status() === 200, `${label}: HEAD download_url retornou ${dlHead.status()}`);
  await checkNoOverflow(page, `${label} Galeria & Downloads`);
  console.log(`[PASS] ${label}: Campaign (6/3) + Thumbnails + Galeria MP4 (${topElseve.filename}, ${topElseve.size_human})`);
}

(async () => {
  const browser = await chromium.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    // Desktop
    const dCtx = await createAuthedContext(browser, { width: 1440, height: 900 });
    const dPage = await dCtx.newPage();
    const dErrs = [];
    attachStrictListeners(dPage, 'Desktop', dErrs);
    await dPage.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await verifyCampaignsAndGallery(dPage, 'Desktop', false);
    assert(dErrs.length === 0, `Desktop errs:\n${dErrs.join('\n')}`);
    await dCtx.close();

    // Mobile
    const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
    const mPage = await mCtx.newPage();
    const mErrs = [];
    attachStrictListeners(mPage, 'Mobile', mErrs);
    await mPage.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
    await verifyCampaignsAndGallery(mPage, 'Mobile', true);
    assert(mErrs.length === 0, `Mobile errs:\n${mErrs.join('\n')}`);
    await mCtx.close();

    console.log('\n>>> PLAYWRIGHT ELSEVE CAMPAIGN & GALERIA E2E: 100% APROVADO <<<');
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error('\n[E2E FAILED]:', err);
  process.exit(1);
});
