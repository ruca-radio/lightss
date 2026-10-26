// Run with node tests/music_show_browser.cjs (requires Playwright + Chromium).
const {spawn} = require('node:child_process');
const {once} = require('node:events');
const readline = require('node:readline');
const assert = require('node:assert/strict');
const {chromium} = require('playwright');

(async () => {
  const server = spawn(process.env.PYTHON || 'python', ['tests/browser_show_server.py'], {stdio: ['ignore', 'pipe', 'inherit']});
  let browser;
  try {
    const lines = readline.createInterface({input: server.stdout});
    const [line] = await once(lines, 'line');
    const {port, wav} = JSON.parse(line);
    browser = await chromium.launch({headless: true, executablePath: process.env.CHROMIUM_PATH,
      args: ['--no-sandbox', '--use-fake-device-for-media-stream',
      '--use-fake-ui-for-media-stream', `--use-file-for-fake-audio-capture=${wav}`]});
    const page = await browser.newPage();
    await page.route('**/*', route => new URL(route.request().url()).hostname === '127.0.0.1'
      ? route.continue() : route.abort());
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    const measurements = [];
    page.on('request', request => {
      if (request.url().endsWith('/api/show/features')) measurements.push(JSON.parse(request.postData()));
    });
    await page.goto(`http://127.0.0.1:${port}`);
    await page.locator('#aiShowBtn').click();
    await page.waitForFunction(() => document.getElementById('autoStatus').textContent.includes('AI directing'), {timeout: 15000});
    await page.waitForFunction(() => document.getElementById('musicTitle').textContent.includes('Synthetic 120 BPM'), {timeout: 15000});
    assert(measurements.length >= 8, 'Audio frames must reach the server');
    assert(measurements.some(frame => frame.beat), 'Synthetic kicks must produce beat onsets');
    assert(measurements.some(frame => frame.bass > frame.treble), '90 Hz kicks must map to bass');
    assert(measurements.some(frame => frame.bpm >= 110 && frame.bpm <= 130), 'Tempo should follow 120 BPM kicks');
    const state = await page.evaluate(async () => (await (await fetch('/api/show/control', {
      method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({command: 'status'})})).json()));
    assert.equal(state.theme.seg[0].fx, 67);
    assert(state.sent_frames > 8);
    await page.locator('#musicModeStopBtn').click();
    await page.waitForFunction(() => document.getElementById('autoStatus').textContent === 'Music show stopped.');
    await page.waitForTimeout(300);
    const count = measurements.length;
    await page.waitForTimeout(300);
    assert.equal(measurements.length, count, 'Stopping must stop audio delivery');
    await page.locator('#aiShowBtn').click();
    await page.waitForFunction(() => document.getElementById('autoStatus').textContent.includes('AI directing'));
    await page.evaluate(() => send('off'));
    await page.waitForFunction(() => document.getElementById('autoStatus').textContent === 'Music show stopped.');
    await page.selectOption('#musicAudioSource', 'tab');
    await page.evaluate(() => {
      navigator.mediaDevices.getDisplayMedia = async () => ({getAudioTracks: () => [], getTracks: () => [{stop() {}}]});
    });
    await page.locator('#aiShowBtn').click();
    await page.waitForFunction(() => document.getElementById('autoStatus').textContent.includes('No shared audio'));
    assert.equal(await page.locator('#musicModeBtn').isEnabled(), true);
    assert.deepEqual(errors, []);
    console.log(`Browser smoke passed: ${count} audio frames, AI theme active, song recognized, clean stop.`);
  } finally {
    if (browser) await browser.close();
    server.kill();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
