#!/usr/bin/env node
import fs from 'node:fs';
import { createRequire } from 'node:module';

const require = createRequire('/home/wbrendo/Projetos/fresh-start/package.json');
const { chromium } = require('playwright');

const BASE_URL = 'https://187.77.202.176.sslip.io';
const BAD_STATUSES = new Set([400, 401, 403, 404, 422, 500, 502, 503, 504]);
const TEST_TAKES = [
  '/tmp/e2e_v25_takes/flow_take_1.mp4',
  '/tmp/e2e_v25_takes/flow_take_2.mp4',
  '/tmp/e2e_v25_takes/flow_take_3.mp4',
];

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
    window.scrollTo(0, 220);
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
  console.log('\n=== DESKTOP 1440x900: V2.5 CLEAN CINEMA + FLOW PACK A/B + STITCHER + COLAB + PREVIEW ===');
  const ctx = await createAuthedContext(browser, { width: 1440, height: 900 });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Desktop', errs);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#tab-quick', { state: 'visible', timeout: 25000 });
  await page.locator('#tab-quick').click();
  await page.waitForSelector('#gp-tpl-novela_regional_studio', { state: 'visible', timeout: 25000 });
  await page.locator('#gp-tpl-novela_regional_studio').click();
  await page.waitForSelector('#gp-fields-novela_regional_studio', { state: 'visible' });
  await checkLayout(page, 'Desktop Trilha 6 Inicial');

  // 1) Production Pack Google Flow Veo 3.1 em AMBOS estilos (cinematic_tv + 3d_cartoon)
  for (const style of ['cinematic_tv', '3d_cartoon']) {
    await page.locator(`#gp-nov-style-${style}`).click();
    const fpWait = page.waitForResponse(
      (r) => r.url().includes('/api/quick/novela/flow-pack') && r.status() === 200,
      { timeout: 180000 }
    );
    await page.locator('#btn-nov-flow-pack').click();
    const fpRes = await fpWait;
    const fpJson = await fpRes.json();
    assert(fpJson.ok && fpJson.visual_style === style && fpJson.takes.length >= 2, `Flow pack ${style} inválido`);
    assert(fpJson.total_credits_lite === fpJson.takes.length * 10, 'Crédito Lite incorreto');
    assert(fpJson.total_credits_fast === fpJson.takes.length * 20, 'Crédito Fast incorreto');
    for (const tk of fpJson.takes) {
      assert(tk.veo_prompt.includes('No subtitles, no text overlay, no watermarks'), 'Veo prompt sem trava anti-texto');
      assert(tk.veo_prompt.includes(tk.tts_text), 'Veo prompt sem fala PT-BR');
      const ingR = await ctx.request.get(`${BASE_URL}${tk.ingredient_download_url}`, {
        headers: { Authorization: `Bearer ${TOKEN}` },
      });
      assert(ingR.status() === 200, `Ingredient ${tk.ingredient_filename} retornou HTTP ${ingR.status()}`);
    }
    await page.waitForSelector('#nov-flow-pack-result', { state: 'visible' });
    await page.locator('#nov-flow-pack-cards button:has-text("Copiar Prompt Veo 3.1")').first().click();
    await checkLayout(page, `Desktop Flow Pack ${style}`);
    console.log(`[PASS] Flow Pack ${style}: ${fpJson.takes.length} takes, ${fpJson.total_credits_lite} Lite / ${fpJson.total_credits_fast} Fast créd, Ingredients HTTP 200`);
  }

  // 2) Costurador de Takes (.mp4 upload + stitch-takes via UI)
  for (const tp of TEST_TAKES) assert(fs.existsSync(tp), `Falta take de teste ${tp}`);
  const upWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/upload-takes') && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#nov-stitch-files').setInputFiles(TEST_TAKES);
  const upRes = await upWait;
  const upJson = await upRes.json();
  assert(upJson.ok && upJson.count === 3, `Esperado 3 takes enviados, veio ${upJson.count}`);
  await page.waitForSelector('#nov-stitch-list', { state: 'visible' });

  const stWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela/stitch-takes') && r.request().method() === 'POST' && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#btn-nov-stitch').click();
  await stWait;
  await page.waitForSelector('#nov-stitch-result video', { state: 'visible', timeout: 120000 });
  const stitchHref = await page.locator('#nov-stitch-result a[download]').getAttribute('href');
  assert(stitchHref && stitchHref.includes('/api/outputs/download'), `Link costurado inválido: ${stitchHref}`);
  const stHead = await ctx.request.get(`${BASE_URL}${stitchHref}`, {
    headers: { Authorization: `Bearer ${TOKEN}`, Range: 'bytes=0-1023' },
  });
  assert(stHead.status() === 200 || stHead.status() === 206, `Episódio costurado HTTP ${stHead.status()}`);
  console.log(`[PASS] Costurador UI: 3 takes unidos e validados com HTTP ${stHead.status()}`);

  // 3) Colab GPU Worker Status + Download do Notebook .ipynb
  await page.locator('#gp-nov-mode-colab_gpu').click();
  await page.waitForSelector('#gp-nov-panel-colab_gpu', { state: 'visible' });
  const nbHref = await page.locator('#btn-nov-colab-notebook').getAttribute('href');
  const nbRes = await ctx.request.get(`${BASE_URL}${nbHref}`, { headers: { Authorization: `Bearer ${TOKEN}` } });
  assert(nbRes.status() === 200, `Download Notebook HTTP ${nbRes.status()}`);
  const nbJson = JSON.parse(await nbRes.text());
  assert(nbJson.nbformat === 4, 'Notebook inválido (nbformat != 4)');
  console.log('[PASS] Modo 2 Colab GPU: painel + download notebook .ipynb HTTP 200 válidos');

  // 4) Modo 3 Clean Cinema Preview defaults & Modo Hard Clean Cinema Render real
  await page.locator('#gp-nov-mode-vps_preview').click();
  assert(await page.locator('#gp-nov-clean-cinema').isChecked(), 'Clean Cinema deve vir ativado por padrão');
  assert((await page.locator('#gp-nov-subtitles').inputValue()) === 'none', 'Legenda padrão deve ser none');

  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#card-hard-novela', { state: 'visible' });
  assert(await page.locator('#qnv-clean-cinema').isChecked(), 'Modo Hard Clean Cinema deve iniciar marcado');
  await page.selectOption('#qnv-render-mode', 'vps_preview');
  await page.selectOption('#qnv-subtitles-mode', 'none');
  // Reduzir para 2 falas curtas no teste rápido E2E de render Clean Cinema UI
  await page.locator('#qnv-dialogue-list button:has-text("Remover")').last().click();
  await page.locator('#qnv-dialogue-list button:has-text("Remover")').last().click();
  const hardNovWait = page.waitForResponse(
    (r) => r.url().includes('/api/quick/novela') && !r.url().includes('task') && !r.url().includes('flow-pack') && r.status() === 200,
    { timeout: 60000 }
  );
  await page.locator('#btn-qnv-submit').click();
  const hardNovRes = await hardNovWait;
  const hardNovJson = await hardNovRes.json();
  assert(hardNovJson.task_id && hardNovJson.clean_cinema_mode === true, 'Render Hard Clean Cinema não iniciou corretamente');
  await page.waitForSelector('#qnv-downloads video', { state: 'visible', timeout: 300000 });
  const ccHref = await page.locator('#qnv-downloads a[download]').getAttribute('href');
  const ccDl = await ctx.request.get(`${BASE_URL}${ccHref}`, { headers: { Authorization: `Bearer ${TOKEN}`, Range: 'bytes=0-1023' } });
  assert(ccDl.status() === 200 || ccDl.status() === 206, `Clean Cinema MP4 HTTP ${ccDl.status()}`);
  await checkLayout(page, 'Desktop Modo Hard Clean Cinema Concluído');
  console.log(`[PASS] Modo Hard Clean Cinema Preview renderizado pela UI com HTTP ${ccDl.status()}`);

  // 5) Galeria & Downloads (#ofilter-novela)
  await page.locator('#tab-outputs').click();
  await page.waitForSelector('#ofilter-novela', { state: 'visible' });
  await page.locator('#ofilter-novela').click();
  await page.waitForTimeout(400);
  await checkLayout(page, 'Desktop Galeria Novela');

  assert(errs.length === 0, `Erros Desktop:\n - ${errs.join('\n - ')}`);
  await ctx.close();
}

async function runMobile(browser) {
  console.log('\n=== MOBILE 390x844: MODO 1/2/3 CLEAN CINEMA SEM OVERFLOW ===');
  const ctx = await createAuthedContext(browser, { width: 390, height: 844, isMobile: true, hasTouch: true });
  const page = await ctx.newPage();
  const errs = [];
  attachStrictListeners(page, 'Mobile', errs);

  await page.goto(BASE_URL, { waitUntil: 'domcontentloaded', timeout: 45000 });
  await page.waitForSelector('#mnav-quick', { state: 'visible', timeout: 25000 });
  await page.locator('#mnav-quick').click();
  await page.waitForSelector('#gp-tpl-novela_regional_studio', { state: 'visible', timeout: 25000 });
  await page.locator('#gp-tpl-novela_regional_studio').click();
  await page.waitForSelector('#gp-fields-novela_regional_studio', { state: 'visible' });

  for (const mode of ['flow_pack', 'colab_gpu', 'vps_preview']) {
    await page.locator(`#gp-nov-mode-${mode}`).click();
    await page.waitForSelector(`#gp-nov-panel-${mode}`, { state: 'visible' });
    const box = await page.locator(`#gp-nov-mode-${mode}`).boundingBox();
    assert(box && box.height >= 44, `Botão mobile ${mode} < 44px`);
    await checkLayout(page, `Mobile Motor ${mode}`);
  }
  await page.locator('#gp-nov-mode-flow_pack').click();
  await checkLayout(page, 'Mobile Flow Pack Volta');

  await page.locator('#btn-quick-mode-hard').click();
  await page.waitForSelector('#card-hard-novela', { state: 'visible' });
  await checkLayout(page, 'Mobile Modo Hard Novela');

  assert(errs.length === 0, `Erros Mobile:\n - ${errs.join('\n - ')}`);
  await ctx.close();
  console.log('[PASS] Mobile 390x844: 3 Motores + Modo Hard 0 overflow · touch >= 44px · 0 erros!');
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
    console.log('\n>>> E2E NOVELA STUDIO V2.5 (CLEAN CINEMA + VEO 3.1 + STITCHER + COLAB): 100% APROVADO <<<');
  } finally {
    await browser.close();
  }
}

main().catch((err) => {
  console.error('\n[E2E V2.5 FAILED]:', err);
  process.exit(1);
});
