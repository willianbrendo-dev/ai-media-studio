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

async function runDesktop(browser) {
  console.log('\n=== DESKTOP 1440x900: NOVELA STUDIO V2.4 + GERADOR IA + MODO HARD + GALERIA A/B ===');
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Desktop', errs);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#tab-quick', { state: 'visible', timeout: 25000 });
  await page.locator('#tab-quick').click();
  await page.waitForSelector('#gp-tpl-novela_regional_studio', { state: 'visible', timeout: 25000 });
  await checkLayout(page, 'Desktop Inicial');

  // 1) Seleciona Trilha 6 no Modo Guiado
  await page.locator('#gp-tpl-novela_regional_studio').click();
  await page.waitForSelector('#gp-fields-novela_regional_studio', { state: 'visible' });
  await checkLayout(page, 'Desktop Trilha 6 Guiado');

  // Alterna 3d_cartoon <-> cinematic_tv
  await page.locator('#gp-nov-style-cinematic_tv').click();
  let curStyle = await page.locator('#gp-nov-style').inputValue();
  assert(curStyle === 'cinematic_tv', `Esperado cinematic_tv, recebido ${curStyle}`);

  await page.locator('#gp-nov-style-3d_cartoon').click();
  curStyle = await page.locator('#gp-nov-style').inputValue();
  assert(curStyle === '3d_cartoon', `Esperado 3d_cartoon, recebido ${curStyle}`);

  // Dispara gerador IA de roteiro via clique real
  const scriptWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/generate-script') && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#btn-gp-nov-gen').click();
  await scriptWait;
  await page.waitForSelector('#gp-nov-preview-box', { state: 'visible', timeout: 15000 });
  const prevLinesCount = await page.locator('#gp-nov-preview-lines > div').count();
  assert(prevLinesCount >= 2, `Esperado >= 2 falas geradas pela IA, veio ${prevLinesCount}`);
  console.log(`[PASS] Desktop Trilha 6 gerou diálogo com IA (${prevLinesCount} falas) + toggle A/B OK`);

  // 2) Alterna para Modo Hard -> Novela Studio editor
  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#card-hard-novela', { state: 'visible' });
  const initialRows = await page.locator('#qnv-dialogue-list > div').count();
  assert(initialRows >= 3, `Esperado >= 3 falas iniciais no Modo Hard, veio ${initialRows}`);
  await page.locator('#btn-qnv-add-line').click();
  const afterRows = await page.locator('#qnv-dialogue-list > div').count();
  assert(afterRows === initialRows + 1, `Esperado ${initialRows + 1} falas após adicionar linha, veio ${afterRows}`);
  await checkLayout(page, 'Desktop Modo Hard Novela');
  console.log(`[PASS] Desktop Modo Hard Novela Studio: ${afterRows} falas editáveis`);

  // Volta para Guiado para manter estado limpo
  await page.locator('#btn-quick-mode-guided').click();

  // 3) Abre Galeria & Downloads -> filtro Novela Studio + ambos pilotos A/B com HTTP 200
  await page.locator('#tab-outputs').click();
  await page.waitForSelector('#ofilter-novela', { state: 'visible', timeout: 15000 });
  await page.locator('#ofilter-novela').click();
  await page.waitForFunction(
    () => {
      const txt = document.getElementById('outputs-grid')?.innerText || '';
      return txt.includes('novela_piloto_3d_cartoon.mp4') && txt.includes('novela_piloto_cinematografico_tv.mp4');
    },
    null,
    { timeout: 20000 }
  );

  const dlLinks = await page.locator('#outputs-grid a[download]').evaluateAll((els) =>
    els.map((a) => ({ name: a.getAttribute('download'), href: a.getAttribute('href') }))
  );
  for (const targetName of ['novela_piloto_3d_cartoon.mp4', 'novela_piloto_cinematografico_tv.mp4']) {
    const match = dlLinks.find((d) => d.name === targetName);
    assert(match && match.href, `Link de download para ${targetName} não encontrado!`);
    const r = await ctx.request.get(`${BASE_URL}${match.href}`, {
      headers: { Authorization: `Bearer ${TOKEN}`, Range: 'bytes=0-1023' },
    });
    assert(r.status() === 200 || r.status() === 206, `Download ${targetName} retornou HTTP ${r.status()}`);
  }
  await checkLayout(page, 'Desktop Galeria Novela');
  assert(errs.length === 0, `Erros Desktop:\n - ${errs.join('\n - ')}`);
  await ctx.close();
  console.log('[PASS] Desktop Galeria: ambos pilotos A/B visíveis, preview/download HTTP 200/206, 0 erros!');
}

async function runMobile(browser) {
  console.log('\n=== MOBILE 390x844: TRILHA 6 + MODO HARD + GALERIA SEM OVERFLOW ===');
  const ctx = await createAuthedContext(browser, {
    width: 390,
    height: 844,
    isMobile: true,
    hasTouch: true,
  });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Mobile', errs);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#mnav-quick', { state: 'visible', timeout: 25000 });
  await page.locator('#mnav-quick').click();
  await page.waitForSelector('#gp-tpl-novela_regional_studio', { state: 'visible', timeout: 25000 });
  await page.locator('#gp-tpl-novela_regional_studio').click();
  await page.waitForSelector('#gp-fields-novela_regional_studio', { state: 'visible' });
  await checkLayout(page, 'Mobile Trilha 6 Guiado');

  // Touch target size check
  for (const sel of ['#gp-tpl-novela_regional_studio', '#gp-nov-style-3d_cartoon', '#gp-nov-style-cinematic_tv', '#btn-gp-nov-gen']) {
    const box = await page.locator(sel).boundingBox();
    assert(box && box.height >= 44, `${sel} mobile height < 44px: ${box?.height}`);
  }

  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#card-hard-novela', { state: 'visible' });
  await checkLayout(page, 'Mobile Modo Hard Novela');

  await page.locator('#mnav-outputs').click();
  await page.waitForSelector('#ofilter-novela', { state: 'visible', timeout: 15000 });
  await page.locator('#ofilter-novela').click();
  await page.waitForFunction(
    () => {
      const txt = document.getElementById('outputs-grid')?.innerText || '';
      return txt.includes('novela_piloto_3d_cartoon.mp4') && txt.includes('novela_piloto_cinematografico_tv.mp4');
    },
    null,
    { timeout: 20000 }
  );
  await checkLayout(page, 'Mobile Galeria Novela');

  assert(errs.length === 0, `Erros Mobile:\n - ${errs.join('\n - ')}`);
  await ctx.close();
  console.log('[PASS] Mobile 390x844: Trilha 6 + Modo Hard + Galeria 0 overflow · touch >= 44px · 0 erros!');
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
    console.log('\n>>> E2E NOVELA STUDIO V2.4: 100% APROVADO (DESKTOP + MOBILE) <<<');
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('\n[E2E FAILED]:', err);
  process.exit(1);
});
