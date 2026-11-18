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

async function checkLayoutAndScroll(page, label, { requireVerticalScroll = true } = {}) {
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

function assertConsultativeContent(replyText, label) {
  const low = (replyText || '').toLowerCase();
  assert(replyText.length > 180, `${label}: resposta muito curta (${replyText.length} chars)`);
  assert(low.includes('hyperframes'), `${label}: deve citar HyperFrames`);
  assert(low.includes('moneyprinter'), `${label}: deve citar MoneyPrinterTurbo`);
  assert(low.includes('supoclip'), `${label}: deve citar SupoClip`);
  assert(low.includes('novela') || low.includes('series'), `${label}: deve citar modo novela/series do SupoClip`);
  assert(low.includes('video use') || low.includes('videouse'), `${label}: deve citar combo/opção Video Use`);
  assert(low.includes('?'), `${label}: deve fazer perguntas pontuais de descoberta`);
  assert(!low.includes('capcut') && !low.includes('premiere'), `${label}: alucinou ferramenta externa fora do estúdio`);
}

async function runDesktop(browser) {
  console.log('\n=== DESKTOP 1440x900: OPENJARVIS MASTER OPERATOR ===');
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Desktop', errs);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('#openjarvis-card #input-jarvis-ask', { timeout: 15000 });

  // Focus via OpenJarvis AI sidebar tab
  await page.locator('#tab-jarvis').click();
  await page.waitForTimeout(400);

  // Verify quick objective chips populate the prompt cleanly
  const chips = [
    { label: 'Planejar anúncio Shopee', mustContain: 'Shopee' },
    { label: 'Cortar episódio em modo Novela', mustContain: 'novela' },
    { label: 'Limpar silêncios e tratar cor de vídeo', mustContain: 'Video Use' },
  ];
  for (const ch of chips) {
    const btn = page.locator('#openjarvis-card button', { hasText: ch.label });
    assert((await btn.count()) >= 1, `Chip "${ch.label}" ausente no Desktop`);
    await btn.first().click();
    const val = await page.locator('#input-jarvis-ask').inputValue();
    assert(val.toLowerCase().includes(ch.mustContain.toLowerCase()), `Chip "${ch.label}" não preencheu '${ch.mustContain}'`);
  }

  // Click Combo chip (exact dual test prompt!)
  const comboPrompt = 'Jarvis, quero criar um vídeo de vendas para um produto da Shopee e também cortar um vídeo longo em partes estilo novela. Qual o melhor caminho no nosso estúdio?';
  await page.locator('#input-jarvis-ask').fill(comboPrompt);

  const askPromise = page.waitForResponse(
    (r) => r.url().includes('/api/openjarvis/ask') && r.request().method() === 'POST',
    { timeout: 45000 },
  );
  await page.locator('#btn-jarvis-ask').click();
  const askRes = await askPromise;
  assert(askRes.status() === 200, `POST /api/openjarvis/ask retornou ${askRes.status()}`);
  const payload = await askRes.json();
  assert(payload.ok === true, 'payload.ok != true');
  assert(Array.isArray(payload.tools_used) && payload.tools_used.includes('studio_plan_production'), 'tools_used deve incluir studio_plan_production');
  assert(payload.plan?.vps_environment?.active_containers === 0, 'VPS deve reportar 0 active_containers');

  await page.waitForSelector('#jarvis-reply-box #jarvis-reply-text', { timeout: 15000 });
  const uiReply = await page.locator('#jarvis-reply-text').innerText();
  console.log('--- Preview Desktop Reply ---\n' + uiReply.slice(0, 750) + (uiReply.length > 750 ? '...\n' : '\n'));
  assertConsultativeContent(uiReply, 'Desktop UI');
  await checkLayoutAndScroll(page, 'Desktop OpenJarvis Reply');
  console.log(`[PASS] Desktop OpenJarvis (${payload.model}) · tools=${payload.tools_used.join(',')} · ${uiReply.length} chars`);

  assert(errs.length === 0, `Erros capturados no Desktop:\n${errs.join('\n')}`);
  await ctx.close();
}

async function runMobile(browser) {
  console.log('\n=== MOBILE 390x844: OPENJARVIS AI RESPONSIVE + SCROLL ===');
  const ctx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Mobile', errs);

  await page.goto(`${BASE_URL}/`, { waitUntil: 'networkidle', timeout: 30000 });
  await page.waitForSelector('#openjarvis-card #input-jarvis-ask', { timeout: 15000 });
  await checkLayoutAndScroll(page, 'Mobile OpenJarvis Inicial');

  // Test mobile chip + submit ("Limpar silêncios e tratar cor de vídeo")
  const vuChip = page.locator('#openjarvis-card button', { hasText: 'Limpar silêncios e tratar cor de vídeo' });
  await vuChip.first().click();
  const askPromise = page.waitForResponse(
    (r) => r.url().includes('/api/openjarvis/ask') && r.request().method() === 'POST',
    { timeout: 45000 },
  );
  await page.locator('#btn-jarvis-ask').click();
  const askRes = await askPromise;
  assert(askRes.status() === 200, `Mobile POST /api/openjarvis/ask retornou ${askRes.status()}`);
  const payload = await askRes.json();
  assert(payload.ok === true, 'Mobile payload.ok != true');

  await page.waitForSelector('#jarvis-reply-box #jarvis-reply-text', { timeout: 15000 });
  const uiReply = await page.locator('#jarvis-reply-text').innerText();
  const low = uiReply.toLowerCase();
  assert(low.includes('video use') || low.includes('videouse'), 'Mobile reply deve citar Video Use');
  assert(low.includes('?'), 'Mobile reply deve fazer perguntas de descoberta');
  await checkLayoutAndScroll(page, 'Mobile OpenJarvis Reply');

  // Assert 0/7 active Docker containers on /api/system
  const sysRes = await page.request.get(`${BASE_URL}/api/system`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  const sysJson = await sysRes.json();
  assert(sysJson.active_containers === 0, `Esperado 0 active_containers, obtido ${sysJson.active_containers}`);
  console.log(`[PASS] Mobile OpenJarvis OK · 0 overflow · active_containers=${sysJson.active_containers} · RAM disponível=${sysJson.ram_available_gb} GB`);

  assert(errs.length === 0, `Erros capturados no Mobile:\n${errs.join('\n')}`);
  await ctx.close();
}

(async () => {
  const browser = await chromium.launch({
    executablePath: '/usr/bin/google-chrome',
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });
  try {
    await runDesktop(browser);
    await runMobile(browser);
    console.log('\n>>> PLAYWRIGHT STAGE 4 (OPENJARVIS MASTER OPERATOR DESKTOP & MOBILE): 100% APROVADO <<<');
  } finally {
    await browser.close();
  }
})().catch((err) => {
  console.error('\n[E2E FAILED]:', err);
  process.exit(1);
});
