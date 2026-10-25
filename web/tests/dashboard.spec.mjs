// Browser contract tests use synthetic API responses and never call collection or a model.
import { chromium } from 'playwright';
import { readFile, writeFile, mkdir, access } from 'node:fs/promises';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const payload = JSON.parse(await readFile(path.join(web, '../examples/demo/payload.json'), 'utf8'));
assert.equal(payload.analysis.synthetic, true);
const output = path.join(web, 'test-results');
await mkdir(output, { recursive: true });
const port = await new Promise((resolve, reject) => {
  const server = createServer();
  server.on('error', reject);
  server.listen(0, '127.0.0.1', () => { const value = server.address().port; server.close(() => resolve(value)); });
});
let preview;
const baseURL = process.env.BFSI_TEST_URL || `http://127.0.0.1:${port}/`;
if (!process.env.BFSI_TEST_URL) {
  preview = spawn(process.execPath, ['node_modules/vite/bin/vite.js', 'preview', '--host', '127.0.0.1', '--port', String(port), '--strictPort', '--configLoader', 'runner'], { cwd: web, stdio: 'ignore' });
  let ready = false;
  for (let i = 0; i < 60; i++) {
    try { if ((await fetch(baseURL)).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  if (!ready) { preview.kill(); throw new Error('Preview did not start. Run the frontend build first.'); }
}
const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
const executablePath = process.env.BFSI_CHROME_PATH || (process.platform === 'darwin' && await access(macChrome).then(() => true, () => false) ? macChrome : undefined);
let browser;
const checks = [];
const check = async (name, run) => { await run(); checks.push(name); };
try {
  browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1000 }, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  let fixture = structuredClone(payload);
  let sampleFailure = false;
  let current = null;
  let finishOnResume = false;
  const requests = [];
  const runId = '20260101T000000Z_00000001';
  const job = state => ({ run_id: runId, bvid: 'BV0000000000', state, stage: state === 'running' ? 'collection' : state, stage_label: state === 'paused' ? '已暂停' : state === 'completed' ? '已完成' : '采集中', message: '合成测试任务', collected: 8, analyzed: state === 'completed' ? 8 : 0, total: 8, mode: 'full', limit: null });
  const json = (route, body, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  await page.route('**/api/**', async route => {
    const request = route.request();
    const pathname = new URL(request.url()).pathname;
    if (pathname === '/api/latest') return json(route, { run_id: null });
    if (pathname === '/api/sample') return sampleFailure ? json(route, { error: '合成测试读取失败' }, 500) : json(route, fixture);
    if (pathname === '/api/runs') return json(route, current ? [current] : []);
    if (pathname === '/api/analyze') { requests.push(request.postDataJSON()); current = job('running'); return json(route, current, 202); }
    if (pathname.endsWith('/pause')) { current = job('paused'); return json(route, job('pausing'), 202); }
    if (pathname.endsWith('/resume')) { const resumed = job('running'); current = job(finishOnResume ? 'completed' : 'running'); return json(route, resumed, 202); }
    if (pathname.endsWith('/export')) return json(route, { preview_url: `/api/runs/${runId}/poster`, download_url: `/api/runs/${runId}/poster?download=1`, path: 'runtime/demo/exports/result_poster.png' });
    if (pathname.endsWith('/poster')) return route.fulfill({ contentType: 'image/png', body: Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScLbtAAAAABJRU5ErkJggg==', 'base64') });
    if (pathname.endsWith('/result')) { const result = structuredClone(payload); result.analysis.run_id = runId; result.video.video_bvid = current.bvid; return json(route, result); }
    if (pathname === `/api/runs/${runId}`) return json(route, current);
    return json(route, { error: `Unexpected test API ${pathname}` }, 404);
  });
  const settle = async () => { await page.getByTestId('bfsi').waitFor(); await page.evaluate(() => document.fonts.ready); };
  const reset = async () => { await page.goto(baseURL); await page.evaluate(() => localStorage.clear()); await page.reload(); await settle(); };
  await reset();
  await check('synthetic example and live metrics render through API', async () => {
    assert.equal(await page.getByTestId('bfsi').innerText(), payload.headline.no_directional_signal ? '—' : payload.headline.bfsi.toFixed(1));
    assert.ok((await page.locator('body').innerText()).includes('合成演示数据'));
    assert.ok(!(await page.locator('body').innerText()).includes('377条'));
    assert.equal(await page.getByTestId('commentary').innerText(), payload.commentary);
  });
  await check('representative tabs, context disclosure and keyboard controls work', async () => {
    for (const [category, rows] of Object.entries(payload.representative_comments)) {
      const tab = page.locator(`#tab-${category}`);
      if (!(await tab.count())) continue;
      await tab.click();
      assert.equal(await page.locator('.comment-card').count(), rows.length);
      if (rows.length) {
        const card = page.locator('.comment-card').first();
        assert.equal(await card.locator('blockquote').textContent(), rows[0].raw_text);
        await card.getByRole('button', { name: '查看上下文' }).click();
        assert.equal(await card.getByRole('button', { name: '查看上下文' }).getAttribute('aria-expanded'), 'true');
      }
    }
    await page.locator('#tab-bullish').click();
    await page.locator('#tab-bullish').press('ArrowRight');
    assert.equal(await page.locator('#tab-bearish').getAttribute('aria-selected'), 'true');
    await page.locator('.methodology summary').click();
    assert.equal(await page.locator('.formula').isVisible(), true);
  });
  for (const [name, width, height] of [['desktop', 1440, 1000], ['tablet', 820, 1180], ['mobile', 390, 844], ['mobile-small', 360, 800]]) {
    await check(`${name} fits viewport`, async () => {
      await page.setViewportSize({ width, height });
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.evaluate(() => scrollTo(0, 0));
      await page.screenshot({ path: path.join(output, `${name}.png`), fullPage: name === 'mobile' });
    });
  }
  await page.setViewportSize({ width: 1440, height: 1000 });
  await check('full collection, pause, resume, result and poster actions remain connected', async () => {
    await page.getByLabel('BV 号或 B站视频链接').fill('BV0000000000');
    await page.getByRole('button', { name: '开始分析' }).click();
    await page.getByRole('button', { name: '暂停分析' }).waitFor();
    assert.deepEqual(requests.at(-1), { input: 'BV0000000000', mode: 'full', limit: null });
    await page.getByRole('button', { name: '暂停分析' }).click();
    await page.getByRole('button', { name: '继续分析' }).waitFor();
    finishOnResume = true;
    await page.getByRole('button', { name: '继续分析' }).click();
    await page.getByRole('button', { name: '导出结果图' }).waitFor();
    await page.getByRole('button', { name: '导出结果图' }).click();
    await page.getByRole('link', { name: '下载 PNG' }).waitFor();
    assert.equal(await page.getByRole('link', { name: '下载 PNG' }).getAttribute('href'), `/api/runs/${runId}/poster?download=1`);
    assert.equal(await page.locator('.export-ready img').count(), 1);
  });
  await check('quick mode sends explicit sample size and clears prior result', async () => {
    await page.getByLabel('分析模式', { exact: true }).selectOption('quick');
    await page.getByLabel('最多分析', { exact: true }).selectOption('20');
    await page.getByRole('button', { name: '开始分析' }).click();
    await page.getByRole('button', { name: '暂停分析' }).waitFor();
    assert.equal(requests.at(-1).mode, 'quick'); assert.equal(requests.at(-1).limit, 20);
    assert.equal(await page.getByTestId('bfsi').count(), 0);
  });
  current = null;
  await reset();
  await check('zero signal and empty sections have explicit states', async () => {
    fixture.headline.no_directional_signal = true;
    fixture.headline.no_bullish_bearish_signal = true;
    fixture.headline.bfsi = 50;
    fixture.top_financial_topics = []; fixture.asset_market_target_indices = []; fixture.representative_comments = {};
    await page.reload(); await settle();
    assert.equal(await page.getByTestId('bfsi').innerText(), '—');
    assert.ok((await page.locator('body').innerText()).includes('暂无财经话题'));
    assert.ok((await page.locator('body').innerText()).includes('暂无方向信号'));
  });
  await check('sample read failures can be recovered through the sample control', async () => {
    sampleFailure = true;
    await page.reload(); await page.getByRole('alert').waitFor();
    assert.ok((await page.getByRole('alert').innerText()).includes('合成测试读取失败'));
    sampleFailure = false; fixture = structuredClone(payload);
    await page.getByRole('button', { name: '查看合成演示', exact: true }).click(); await settle();
  });
  assert.deepEqual(errors, []);
  await writeFile(path.join(output, 'browser-report.json'), JSON.stringify({ passed: true, checks, uncaught_errors: errors, data: 'synthetic fixtures; API intercepted; no network model calls' }, null, 2) + '\n');
  console.log(JSON.stringify({ passed: true, checks: checks.length, data: 'synthetic API fixtures' }));
} catch (error) {
  await writeFile(path.join(output, 'browser-report.json'), JSON.stringify({ passed: false, checks, error: error.message }, null, 2) + '\n');
  throw error;
} finally { await browser?.close(); preview?.kill(); }
