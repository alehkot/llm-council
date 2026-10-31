/**
 * Rendered draft lifecycle regression. Synthetic APIs only; no provider calls.
 * Supply installed PLAYWRIGHT_MODULE and sandbox-capable CHROMIUM_EXECUTABLE.
 * Optional BROWSER_FRONTEND_ROOT checks an unchanged baseline; SCREENSHOT_DIR
 * retains desktop/mobile evidence. The script manages its own Vite server.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE, SCREENSHOT_DIR } = process.env;
assert.ok(CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE, 'Set CHROMIUM_EXECUTABLE and PLAYWRIGHT_MODULE');
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root = process.env.BROWSER_FRONTEND_ROOT || fileURLToPath(new URL('..', import.meta.url));
const origin = 'http://127.0.0.1:5208';
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5208', '--strictPort'], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
});
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
let browser;
let deadline;
const results = [];
const failures = [];
const timeout = new Promise((_, reject) => {
  deadline = setTimeout(() => reject(new Error('Browser test deadline exceeded')), 120_000);
});
const original = { input: '  Preserve this carefully written question  ', negativePrompt: '  Check assumptions and risks  ' };
const newer = { input: 'A newer draft', negativePrompt: 'Newer critique instructions' };
const other = { input: 'B draft must stay independent', negativePrompt: 'B critique instructions' };

async function run() {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(serverLog);
    try { ready = (await fetch(origin)).ok; } catch { /* startup */ }
    if (ready) break;
    await delay(100);
  }
  assert.ok(ready, `Vite startup failed: ${serverLog}`);
  browser = await chromium.launch({ executablePath: CHROMIUM_EXECUTABLE, headless: true, chromiumSandbox: true });
  if (SCREENSHOT_DIR) await mkdir(SCREENSHOT_DIR, { recursive: true });
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    for (const scenario of ['retry', 'queued', 'edit-success', 'edit-failure', 'navigate-success', 'navigate-failure', 'revisit-pending']) {
      const name = `${viewport.width}px draft ${scenario}`;
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      page.setDefaultTimeout(5000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      const sends = [];
      const held = [];
      const fixture = id => ({ id, title: `Consultation ${id}`, created_at: '2026-01-01T00:00:00Z', messages: id === 'A' ? [] : [
        { role: 'user', content: 'Saved B question' },
        { role: 'assistant', stage1: null, stage2: null, stage3: null, metadata: { status: 'complete' } },
      ] });
      await context.addInitScript(() => localStorage.setItem('llm_council_api_key', 'synthetic-test-key'));
      await page.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin === origin) return route.continue();
        if (url.origin !== 'http://localhost:8001') return route.abort();
        if (url.pathname.endsWith('/message')) {
          sends.push({ path: url.pathname, body: request.postDataJSON() });
          held.push(route);
          return;
        }
        let body;
        if (url.pathname === '/api/conversations') body = ['A', 'B'].map(id => ({ ...fixture(id), message_count: id === 'A' ? 0 : 2 }));
        else if (url.pathname === '/api/config') body = { negative_mode_enabled: true, council_models: [], chairman_model: 'test', title_model: 'test' };
        else if (url.pathname === '/api/consultations/status') body = { active: [] };
        else if (url.pathname.endsWith('/consultation/status')) body = { status: 'none' };
        else if (url.pathname.endsWith('/queue')) body = { queue: [] };
        else if (/\/api\/conversations\/[AB]$/.test(url.pathname)) body = fixture(url.pathname.at(-1));
        else throw new Error(`Unexpected synthetic request: ${request.method()} ${url.pathname}`);
        await route.fulfill({ json: body });
      });
      const fields = async () => ({ input: await page.locator('#prompt-input').inputValue(), negativePrompt: await page.locator('#negative-prompt-input').inputValue() });
      const fill = async draft => {
        await page.locator('#prompt-input').fill(draft.input);
        await page.locator('#negative-prompt-input').fill(draft.negativePrompt);
      };
      const select = async id => {
        if (viewport.width < 1024) await page.getByRole('button', { name: 'Open consultations', exact: true }).click();
        await page.getByRole('button', { name: `Open consultation: Consultation ${id}`, exact: true }).filter({ visible: true }).click();
        await page.waitForFunction(async id => {
          const { useAppStore } = await import('/src/store/useAppStore.js');
          const s = useAppStore.getState();
          return s.currentConversationId === id && Boolean(s.conversationsById[id]);
        }, id);
        await page.locator('#prompt-input').waitFor();
      };
      const nextSend = async index => {
        for (let i = 0; i < 100 && !held[index]; i++) await delay(20);
        assert.ok(held[index], 'Explicit submission reached the synthetic API');
        assert.equal(sends[index].path, '/api/conversations/A/message');
        assert.deepEqual(sends[index].body, { content: original.input, negative_prompt: original.negativePrompt });
        return held[index];
      };
      const settle = async (route, outcome) => {
        if (outcome === 'failure') await route.fulfill({ status: 503, json: { detail: 'Synthetic send failure' } });
        else await route.fulfill({ json: { status: outcome, consultation_id: 'A-run' } });
        await page.waitForFunction(async outcome => {
          const { useAppStore } = await import('/src/store/useAppStore.js');
          const messages = useAppStore.getState().conversationsById.A?.messages || [];
          return outcome === 'started' ? messages.at(-1)?.metadata?.consultation_id === 'A-run' : messages.length === 0;
        }, outcome);
        // Flush React's response and pending-control state before reading fields.
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
      };
      try {
        // Cache B before opening empty A, so navigation tests exercise one mounted
        // composer rather than incidental loading-state unmounts.
        await page.goto(`${origin}/?conversation=B`);
        await page.locator('#prompt-input').waitFor();
        await select('A');
        await fill(original);
        await page.locator('#prompt-input').press('Enter');
        const pending = await nextSend(0);
        if (scenario.startsWith('edit-')) await fill(newer);
        if (scenario.startsWith('navigate-') || scenario === 'revisit-pending') {
          await select('B');
          await fill(other);
        }
        if (scenario === 'revisit-pending') {
          await select('A');
          assert.deepEqual(await fields(), original, 'Returning to pending A preserves its draft');
          assert.equal(await page.locator('.chat-send-btn').isDisabled(), true);
          // The keyboard path must be guarded as well as the disabled button.
          await page.locator('#prompt-input').press('Enter');
          await delay(100);
          assert.equal(sends.length, 1, 'Revisiting A must not duplicate the pending request');
        }
        const outcome = scenario === 'queued' ? 'queued' : ['retry', 'edit-failure', 'navigate-failure', 'revisit-pending'].includes(scenario) ? 'failure' : 'started';
        await settle(pending, outcome);
        if (scenario.startsWith('navigate-')) {
          assert.deepEqual(await fields(), other, 'A result must not change B draft');
          await select('A');
        }
        const expected = scenario.startsWith('edit-') ? newer : outcome === 'failure' ? original : { input: '', negativePrompt: '' };
        assert.deepEqual(await fields(), expected, 'Only an accepted unchanged submission clears its own draft');
        await delay(100);
        assert.equal(sends.length, 1, 'Failures never automatically retry');
        if (scenario === 'retry') {
          await page.locator('#prompt-input').press('Enter');
          const retry = await nextSend(1);
          await settle(retry, 'started');
          assert.deepEqual(await fields(), { input: '', negativePrompt: '' });
          assert.equal(sends.length, 2, 'Only the explicit retry submits again');
        }
        if (scenario === 'revisit-pending') {
          await select('B');
          assert.deepEqual(await fields(), other, 'B draft survives round-trip');
        }
        assert.deepEqual(errors, []);
        if (SCREENSHOT_DIR) await page.screenshot({ path: join(SCREENSHOT_DIR, `draft-${viewport.width}-${scenario}.png`), fullPage: true });
        results.push(name);
        console.log(`PASS ${name}`);
      } catch (error) {
        failures.push({ name, error: error.message });
        console.error(`FAIL ${name}: ${error.message}`);
        if (SCREENSHOT_DIR) await page.screenshot({ path: join(SCREENSHOT_DIR, `draft-${viewport.width}-${scenario}-failure.png`), fullPage: true });
      } finally {
        await context.close();
      }
    }
  }
}
try {
  await Promise.race([run(), timeout]);
} finally {
  clearTimeout(deadline);
  await browser?.close();
  if (server.exitCode === null) {
    const exited = once(server, 'exit');
    server.kill('SIGTERM');
    const forceStop = setTimeout(() => server.kill('SIGKILL'), 3000);
    try { await exited; } finally { clearTimeout(forceStop); }
  }
}
console.log(JSON.stringify({ passed: results.length, failed: failures.length, failures }, null, 2));
if (failures.length) process.exitCode = 1;
