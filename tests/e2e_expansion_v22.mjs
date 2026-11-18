#!/usr/bin/env node
import fs from 'node:fs';
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

async function checkLayoutAndScroll(page, label, { requireVerticalScroll = false } = {}) {
  const res = await page.evaluate(() => {
    const overflowX = document.documentElement.scrollWidth > window.innerWidth + 1;
    const winCanScroll = document.documentElement.scrollHeight > window.innerHeight + 1;
    let scrolledY = 0;
    if (winCanScroll) {
      window.scrollTo({ top: 320, behavior: 'instant' });
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
        el.scrollTo({ top: 240, behavior: 'instant' });
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
  assert(!res.overflowX, `${label}: overflow horizontal (${res.scrollWidth} > ${res.innerWidth})`);
  if (requireVerticalScroll) {
    assert(res.winCanScroll || res.innerCanScroll, `${label}: sem scroll vertical`);
    assert(res.scrolledY > 0 || res.innerScrolled > 0, `${label}: scroll vertical não moveu`);
  }
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
  console.log('\n=== DESKTOP 1440x900: SKILLS (12) + VIDEO USE + HYPERFRAMES + GALERIA ===');
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Desktop', errs);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForTimeout(800);

  // 1. Central de Skills: 12 Global Media Skills
  await page.locator('#tab-skills').click();
  await page.waitForSelector('#skills-global-badge');
  await page.waitForFunction(
    () => (document.getElementById('skills-global-badge')?.textContent || '').includes('12'),
    null,
    { timeout: 15000 },
  );
  const globalBadgeText = await page.locator('#skills-global-badge').innerText();
  assert(globalBadgeText.includes('12 Skills de Mídia Ativas'), `Badge esperado '12 Skills de Mídia Ativas', obtido '${globalBadgeText}'`);
  const skillsGridText = await page.locator('#skills-grid').innerText();
  assert(skillsGridText.toLowerCase().includes('video-use'), 'Skill video-use deve aparecer na Central de Skills');
  assert(skillsGridText.toLowerCase().includes('manim-video'), 'Skill manim-video deve aparecer na Central de Skills');
  assert(skillsGridText.toLowerCase().includes('hyperframes'), 'Skill hyperframes deve aparecer na Central de Skills');
  await checkLayoutAndScroll(page, 'Desktop Skills', { requireVerticalScroll: true });
  console.log('[PASS] Central de Skills Desktop: 12 Skills Globais verificadas (video-use + manim-video + hyperframes)');

  // 2. Estúdio Rápido -> Modo Hard -> Card 5 (Video Use) & Card 6 (HyperFrames)
  await page.locator('#tab-quick').click();
  if (await page.locator('#btn-quick-mode-hard').isVisible()) {
    await page.locator('#btn-quick-mode-hard').click();
  }
  await page.waitForSelector('#qvu-source-select', { state: 'visible' });
  await page.waitForSelector('#qhf-title', { state: 'visible' });
  await checkLayoutAndScroll(page, 'Desktop Estúdio Rápido', { requireVerticalScroll: true });

  // Disparo real Card 5: Video Use
  await page.selectOption('#qvu-grade', 'Cinema Warm');
  const vuPostPromise = page.waitForResponse((r) => r.url().includes('/api/quick/videouse') && r.request().method() === 'POST', { timeout: 30000 });
  await page.locator('#btn-qvu-submit').click();
  const vuPostRes = await vuPostPromise;
  assert(vuPostRes.status() === 200, `POST /api/quick/videouse retornou ${vuPostRes.status()}`);
  await page.waitForFunction(
    () => (document.getElementById('qvu-status-title')?.textContent || '').includes('Concluído'),
    null,
    { timeout: 120000 },
  );
  const vuTitle = await page.locator('#qvu-status-title').innerText();
  const vuDlHref = await page.locator('#qvu-downloads a[download]').getAttribute('href');
  assert(vuDlHref && vuDlHref.includes('stack=videouse'), `Download Video Use inválido: ${vuDlHref}`);
  const vuHead = await page.request.head(`${BASE_URL}${vuDlHref}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert(vuHead.status() === 200, `HEAD ${vuDlHref} retornou ${vuHead.status()}`);
  console.log(`[PASS] Video Use UI E2E: "${vuTitle}" -> ${vuDlHref}`);

  // Disparo real Card 6: HyperFrames
  await page.fill('#qhf-title', 'AI Media Studio Pro E2E');
  await page.fill('#qhf-offer', 'De R$ 199 por R$ 49,90');
  await page.fill('#qhf-cta', 'Assinar Agora');
  await page.selectOption('#qhf-style', 'B2B Emerald');
  await page.selectOption('#qhf-aspect', '9:16');
  await page.selectOption('#qhf-duration', '5');
  const hfPostPromise = page.waitForResponse((r) => r.url().includes('/api/quick/hyperframes') && r.request().method() === 'POST', { timeout: 30000 });
  await page.locator('#btn-qhf-submit').click();
  const hfPostRes = await hfPostPromise;
  assert(hfPostRes.status() === 200, `POST /api/quick/hyperframes retornou ${hfPostRes.status()}`);
  await page.waitForFunction(
    () => (document.getElementById('qhf-status-title')?.textContent || '').includes('Motion Ad pronto'),
    null,
    { timeout: 150000 },
  );
  const hfTitle = await page.locator('#qhf-status-title').innerText();
  const hfDlHref = await page.locator('#qhf-downloads a[download]').getAttribute('href');
  assert(hfDlHref && hfDlHref.includes('stack=hyperframes'), `Download HyperFrames inválido: ${hfDlHref}`);
  const hfHead = await page.request.head(`${BASE_URL}${hfDlHref}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert(hfHead.status() === 200, `HEAD ${hfDlHref} retornou ${hfHead.status()}`);
  console.log(`[PASS] HyperFrames UI E2E: "${hfTitle}" -> ${hfDlHref}`);

  // 3. Galeria & Downloads -> filtros Video Use e HyperFrames
  await page.locator('#tab-outputs').click();
  await page.waitForSelector('#ofilter-videouse');
  await page.locator('#ofilter-videouse').click();
  await page.waitForTimeout(300);
  const vuGrid = await page.locator('#outputs-grid').innerText();
  assert(vuGrid.includes('videouse_') && vuGrid.includes('.mp4'), 'Galeria Video Use não exibiu MP4 gerado');

  await page.locator('#ofilter-hyperframes').click();
  await page.waitForTimeout(300);
  const hfGrid = await page.locator('#outputs-grid').innerText();
  assert(hfGrid.includes('hyperframes_') && hfGrid.includes('.mp4'), 'Galeria HyperFrames não exibiu MP4 gerado');
  await checkLayoutAndScroll(page, 'Desktop Galeria');
  console.log('[PASS] Galeria & Downloads Desktop: filtros Video Use e HyperFrames validados com HTTP 200');

  assert(errs.length === 0, `Erros Desktop:\n - ${errs.join('\n - ')}`);
  await ctx.close();
}

async function runMobile(browser) {
  console.log('\n=== MOBILE 390x844: ESTÚDIO RÁPIDO + SKILLS (12) + GALERIA ===');
  const mCtx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const mPage = await mCtx.newPage();
  const mErrs = [];
  attachStrictListeners(mPage, 'Mobile', mErrs);

  await mPage.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await mPage.waitForTimeout(800);

  // Abrir Estúdio Rápido -> Modo Hard via navegação
  await mPage.evaluate(() => {
    switchTab('quick');
    if (typeof setQuickStudioMode === 'function') setQuickStudioMode('hard');
  });
  await mPage.waitForSelector('#btn-qvu-submit', { state: 'visible' });
  await mPage.waitForSelector('#btn-qhf-submit', { state: 'visible' });
  const vuBox = await mPage.locator('#btn-qvu-submit').boundingBox();
  const hfBox = await mPage.locator('#btn-qhf-submit').boundingBox();
  assert(vuBox && vuBox.height >= 44, `Botão Video Use mobile altura < 44px: ${vuBox?.height}`);
  assert(hfBox && hfBox.height >= 44, `Botão HyperFrames mobile altura < 44px: ${hfBox?.height}`);
  await checkLayoutAndScroll(mPage, 'Mobile Estúdio Rápido', { requireVerticalScroll: true });

  await mPage.evaluate(() => switchTab('skills'));
  await mPage.waitForTimeout(300);
  const mBadge = await mPage.locator('#skills-global-badge').innerText();
  assert(mBadge.includes('12 Skills de Mídia Ativas'), `Mobile badge inesperado: ${mBadge}`);
  await checkLayoutAndScroll(mPage, 'Mobile Skills', { requireVerticalScroll: true });

  await mPage.evaluate(() => switchTab('outputs'));
  await mPage.waitForTimeout(300);
  await checkLayoutAndScroll(mPage, 'Mobile Galeria', { requireVerticalScroll: true });

  assert(mErrs.length === 0, `Erros Mobile:\n - ${mErrs.join('\n - ')}`);
  await mCtx.close();
  console.log('[PASS] Mobile 390x844 validado (0 overflow · touch targets >= 44px · 12 Skills)');
}

function runMcpVerify() {
  console.log('\n=== OPENJARVIS MCP VERIFICATION ===');
  const out = execFileSync(
    '/home/wbrendo/open-jarvis/.venv/bin/python',
    [
      '-c',
      [
        'import sys, json',
        'sys.path.insert(0, "/home/wbrendo/open-jarvis")',
        'import studio_controller_mcp as m',
        'st = json.loads(m.studio_list_stacks("vps"))',
        'assert st["ok"]',
        'assert all(p["running_containers"] == 0 for p in st["stacks"]), f"Esperado 0 containers: {st}"',
        'vu = json.loads(m.studio_list_outputs("videouse", "vps"))',
        'hf = json.loads(m.studio_list_outputs("hyperframes", "vps"))',
        'assert vu["ok"] and vu["count"] >= 1, f"Sem output videouse: {vu}"',
        'assert hf["ok"] and hf["count"] >= 1, f"Sem output hyperframes: {hf}"',
        'print(json.dumps({"videouse_outputs": vu["count"], "hyperframes_outputs": hf["count"], "idle_containers": 0}))',
      ].join('\n'),
    ],
    { encoding: 'utf8', timeout: 45000 },
  );
  console.log('[PASS] OpenJarvis MCP:', out.trim());
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
    runMcpVerify();
    console.log('\nE2E EXPANSION V2.2 CONCLUÍDA: 100% APROVADO · 0 FALHAS');
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('\n[E2E FAILED]:', err);
  process.exit(1);
});
