// Real local API + browser + PNG export smoke test. Input is synthetic; no collection or model calls.
import { chromium } from 'playwright';
import { readFile, writeFile, mkdir, mkdtemp, rm, access } from 'node:fs/promises';
import { constants } from 'node:fs';
import { spawn } from 'node:child_process';
import { createServer } from 'node:net';
import { createHash } from 'node:crypto';
import { tmpdir } from 'node:os';
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const web = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const root = path.resolve(web, '..');
const output = path.join(web, 'test-results');
await mkdir(output, { recursive: true });
const dataDir = await mkdtemp(path.join(tmpdir(), 'bfsi-live-'));
const localPython = path.join(root, '.venv/bin/python');
const python = process.env.BFSI_PYTHON || (await access(localPython, constants.X_OK).then(() => true, () => false) ? localPython : 'python3');
const env = { ...process.env, PYTHONDONTWRITEBYTECODE: '1', BFSI_PROJECT_ROOT: root, BFSI_DATA_DIR: dataDir, BFSI_TIMEZONE: 'UTC' };
delete env.PYTHONPATH; // Exercise the installed package, including its real startup path.
const expected = JSON.parse(await readFile(path.join(root, 'examples/demo/payload.json'), 'utf8'));
assert.equal(expected.analysis.synthetic, true);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const pngSignature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
const run = (command, args) => new Promise((resolve, reject) => {
  const process = spawn(command, args, { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  let log = '';
  process.stdout.on('data', value => { log += value; });
  process.stderr.on('data', value => { log += value; });
  process.on('error', reject);
  process.on('exit', code => code === 0 ? resolve() : reject(new Error(`Demo installation failed (${code}): ${log}`)));
});
const stop = async process => {
  if (!process || process.exitCode !== null) return;
  const exited = new Promise(resolve => process.once('exit', resolve));
  process.kill('SIGINT');
  const finished = await Promise.race([exited.then(() => true), new Promise(resolve => setTimeout(() => resolve(false), 3000))]);
  if (!finished) { process.kill('SIGKILL'); await exited; }
};
let server;
let browser;
let serverLog = '';
const checks = [];
try {
  await run(python, ['-B', 'scripts/demo.py', '--install']);
  checks.push('synthetic run installed in a temporary data directory');
  const port = await new Promise((resolve, reject) => {
    const socket = createServer();
    socket.on('error', reject);
    socket.listen(0, '127.0.0.1', () => { const selected = socket.address().port; socket.close(() => resolve(selected)); });
  });
  const baseURL = `http://127.0.0.1:${port}`;
  server = spawn(python, ['-B', '-m', 'bfsi.server', '--no-open', '--port', String(port)], { cwd: root, env, stdio: ['ignore', 'pipe', 'pipe'] });
  server.stdout.on('data', value => { serverLog += value; });
  server.stderr.on('data', value => { serverLog += value; });
  server.on('error', error => { serverLog += error.message; });
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(`Local API exited: ${serverLog}`);
    try { if ((await fetch(`${baseURL}/api/health`)).ok) { ready = true; break; } } catch {}
    await new Promise(resolve => setTimeout(resolve, 100));
  }
  assert.ok(ready, `Local API did not start: ${serverLog}`);
  const latest = await (await fetch(`${baseURL}/api/latest`)).json();
  const runId = expected.analysis.run_id;
  assert.equal(latest.run_id, runId);
  const resultResponse = await fetch(`${baseURL}/api/runs/${runId}/result`);
  assert.equal(resultResponse.status, 200);
  const result = await resultResponse.json();
  assert.deepEqual(result, expected);
  checks.push('real latest and result API return the synthetic run');
  const macChrome = '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';
  const executablePath = process.env.BFSI_CHROME_PATH || (process.platform === 'darwin' && await access(macChrome).then(() => true, () => false) ? macChrome : undefined);
  browser = await chromium.launch({ headless: true, executablePath });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, acceptDownloads: true, reducedMotion: 'reduce' });
  const page = await context.newPage();
  const errors = [];
  const endpoints = new Set();
  page.on('pageerror', error => errors.push(error.message));
  page.on('response', response => { const url = new URL(response.url()); if (url.pathname.startsWith('/api/')) endpoints.add(url.pathname); });
  await page.goto(baseURL, { waitUntil: 'networkidle' });
  await page.getByTestId('bfsi').waitFor();
  assert.equal(await page.getByTestId('bfsi').innerText(), expected.headline.bfsi.toFixed(1));
  assert.ok((await page.locator('body').innerText()).includes('合成演示数据'));
  assert.ok(endpoints.has('/api/latest'));
  assert.ok(endpoints.has(`/api/runs/${runId}/result`));
  checks.push('browser loads real API responses and renders the correct run');
  await page.getByRole('button', { name: '导出结果图', exact: true }).click();
  await page.getByRole('link', { name: '下载 PNG' }).waitFor();
  const previewURL = await page.getByRole('link', { name: '预览图片' }).getAttribute('href');
  const downloadURL = await page.getByRole('link', { name: '下载 PNG' }).getAttribute('href');
  assert.equal(previewURL, `/api/runs/${runId}/poster`);
  const image = page.locator('.export-ready img');
  await image.waitFor();
  await page.waitForFunction(() => { const img = document.querySelector('.export-ready img'); return img?.complete && img.naturalWidth === 1080; });
  const preview = await page.request.get(`${baseURL}${previewURL}`);
  assert.equal(preview.status(), 200);
  assert.match(preview.headers()['content-type'], /^image\/png/);
  assert.match(preview.headers()['content-disposition'], /^inline;/);
  const previewBytes = await preview.body();
  assert.deepEqual(previewBytes.subarray(0, 8), pngSignature);
  const downloadResponse = await page.request.get(`${baseURL}${downloadURL}`);
  assert.equal(downloadResponse.status(), 200);
  assert.match(downloadResponse.headers()['content-type'], /^image\/png/);
  assert.match(downloadResponse.headers()['content-disposition'], /^attachment;/);
  assert.equal(digest(await downloadResponse.body()), digest(previewBytes));
  const downloadEvent = page.waitForEvent('download');
  await page.getByRole('link', { name: '下载 PNG' }).click();
  const download = await downloadEvent;
  const downloadedPath = await download.path();
  assert.equal(digest(await readFile(downloadedPath)), digest(previewBytes));
  const pngPath = path.join(dataDir, 'runs', runId, 'exports/result_poster.png');
  const manifest = JSON.parse(await readFile(path.join(dataDir, 'runs', runId, 'exports/manifest.json'), 'utf8'));
  assert.equal(manifest.sha256, digest(previewBytes));
  assert.equal(digest(await readFile(pngPath)), digest(previewBytes));
  assert.equal(manifest.run_id, runId);
  assert.equal(previewBytes.readUInt32BE(16), 1080);
  checks.push('export button creates a real 1080px PNG and HTTP preview/download match the persisted fingerprint');
  await page.evaluate(() => document.fonts.ready);
  await page.evaluate(() => scrollTo(0, 0));
  await page.screenshot({ path: path.join(output, 'live.png') });
  assert.deepEqual(errors, []);
  const report = { passed: true, checks, uncaught_errors: errors, png_width: 1080, png_height: previewBytes.readUInt32BE(20), png_sha256: digest(previewBytes), data: 'synthetic only; real loopback API and browser; no request interception or model calls' };
  await writeFile(path.join(output, 'live-report.json'), JSON.stringify(report, null, 2) + '\n');
  console.log(JSON.stringify(report));
} catch (error) {
  await writeFile(path.join(output, 'live-report.json'), JSON.stringify({ passed: false, checks, error: error.message }, null, 2) + '\n');
  throw error;
} finally {
  await browser?.close();
  await stop(server);
  await rm(dataDir, { recursive: true, force: true });
}
