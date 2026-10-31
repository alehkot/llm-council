/**
 * Optional rendered regression test. No provider or real backend is contacted.
 * npm ci; CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
 * PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs node tests/side-chat-navigation.browser.mjs
 * Supply your installed Playwright module and sandbox-capable Chromium explicitly.
 */
import assert from 'node:assert/strict';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE } = process.env;
assert.ok(CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE, 'Set CHROMIUM_EXECUTABLE and PLAYWRIGHT_MODULE');
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root = process.env.BROWSER_FRONTEND_ROOT || fileURLToPath(new URL('..', import.meta.url));
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5200', '--strictPort'], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
});
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
const origin = 'http://127.0.0.1:5200';
let browser;
const failures = [];
const results = [];
let deadline;
const timeout = new Promise((_, reject) => {
  deadline = setTimeout(() => reject(new Error('Browser test deadline exceeded')), 180_000);
});
const run = async () => {
  let ready = false;
  for (let i = 0; i < 100; i++) {
    if (server.exitCode !== null) throw new Error(serverLog);
    try { ready = (await fetch(origin)).ok; } catch { /* startup */ }
    if (ready) break;
    await delay(100);
  }
  assert.ok(ready, `Vite startup failed: ${serverLog}`);
  browser = await chromium.launch({ executablePath: CHROMIUM_EXECUTABLE, headless: true, chromiumSandbox: true });

  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    for (const action of ['send', 'open', 'model-lock']) {
      for (const outcome of action === 'model-lock' ? ['success'] : ['success', 'failure']) {
        const name = `${viewport.width}px ${action} ${outcome}`;
        const context = await browser.newContext({ viewport });
        const page = await context.newPage();
        page.setDefaultTimeout(8000);
        const errors = [];
        page.on('pageerror', error => errors.push(error.message));
        let hold;
        const held = new Promise(resolve => { hold = resolve; });
        let heldOnce = false;
        const sentThreads = [];
        const thread = (model, suffix = 'saved') => ({
          id: `${model}-${suffix}`, target_model: `test/${model}`, source_model: `test/${model}`,
          source_type: 'stage1', created_at: '2026-01-01T00:00:00Z', updated_at: '2026-01-01T00:00:00Z',
          context: { effective_prompt: 'Synthetic question', source_content: `${model} source` },
          messages: [],
        });
        const alpha = thread('alpha');
        const beta = thread('beta');
        const fixture = {
          id: 'A', title: 'Side chat test', created_at: '2026-01-01T00:00:00Z', messages: [
            { role: 'user', content: 'Synthetic question' },
            { role: 'assistant', stage1: [
              { model: 'test/alpha', response: 'alpha source' },
              { model: 'test/beta', response: 'beta source' },
            ], stage2: null, stage3: null, metadata: { status: 'complete' },
            side_chats: action === 'send' ? [alpha, beta] : [beta] },
          ],
        };
        await context.addInitScript(() => localStorage.setItem('llm_council_api_key', 'synthetic-test-key'));
        await page.route('**/*', async route => {
          const request = route.request();
          const url = new URL(request.url());
          if (url.origin === origin) return route.continue();
          if (url.origin !== 'http://localhost:8001') return route.abort();
          let body;
          if (url.pathname.endsWith('/side-chats/open')) {
            const payload = request.postDataJSON();
            if (action !== 'send' && payload.start_new && !heldOnce) {
              heldOnce = true; hold(route); return;
            }
            const model = payload.source_model === 'test/alpha' ? 'alpha' : 'beta';
            body = { created: Boolean(payload.start_new), thread: thread(model) };
          } else if (url.pathname.includes('/side-chats/') && url.pathname.endsWith('/message')) {
            const id = url.pathname.split('/').at(-2);
            sentThreads.push({ id, content: request.postDataJSON().content });
            if (action === 'send' && !heldOnce) {
              heldOnce = true; hold(route); return;
            }
            body = { thread: { ...beta, messages: [{ role: 'assistant', content: 'beta answer' }] } };
          } else if (url.pathname === '/api/conversations') body = [{ ...fixture, message_count: 2 }];
          else if (url.pathname === '/api/config') body = { negative_mode_enabled: false, council_models: ['test/alpha', 'test/beta'], chairman_model: 'test/alpha', title_model: 'test/alpha' };
          else if (url.pathname === '/api/consultations/status') body = { active: [] };
          else if (url.pathname.endsWith('/consultation/status')) body = { status: 'none' };
          else if (url.pathname.endsWith('/queue')) body = { queue: [] };
          else if (url.pathname === '/api/conversations/A') body = fixture;
          else throw new Error(`Unexpected synthetic API request: ${request.method()} ${url.pathname}`);
          await route.fulfill({ json: body });
        });
        try {
          await page.goto(`${origin}/?conversation=A`);
          await page.getByRole('button', { name: 'Side Chat', exact: true }).click();
          const composer = page.getByPlaceholder('Ask a focused follow-up about this result…');
          await composer.waitFor();
          await page.waitForFunction(() => !document.querySelector('textarea[placeholder^="Ask a focused"]')?.disabled);
          // Observe completion of real API promises without replacing their behavior.
          await page.evaluate(async () => {
            const { api } = await import('/src/api.js');
            window.sideChatSettled = 0;
            for (const name of ['openSideChat', 'sendSideChatMessage']) {
              const original = api[name];
              api[name] = async (...args) => {
                try { return await original(...args); }
                finally { window.sideChatSettled += 1; }
              };
            }
          });
          if (action === 'send') {
            await composer.fill('alpha follow-up');
            await page.getByRole('button', { name: 'Send Follow-up', exact: true }).click();
          } else await page.getByRole('button', { name: 'Start New', exact: true }).click();
          const pending = await held;
          if (action === 'model-lock') {
            assert.equal(await page.locator('#side-chat-model').isDisabled(), true, 'Model selection must lock while opening a thread');
          } else {
            await page.keyboard.press('Escape');
            await page.getByRole('dialog').waitFor({ state: 'hidden' });
            await page.getByRole('tab', { name: 'beta', exact: true }).click();
            await page.getByRole('button', { name: 'Side Chat', exact: true }).click();
            await composer.waitFor();
            await page.waitForFunction(() => !document.querySelector('textarea[placeholder^="Ask a focused"]')?.disabled);
            await composer.fill('beta draft survives');
          }
          const before = await page.evaluate(() => window.sideChatSettled);
          if (outcome === 'failure') await pending.fulfill({ status: 500, json: { detail: 'Synthetic alpha failure' } });
          else await pending.fulfill({ json: { created: action !== 'send', thread: {
            ...alpha, id: action === 'send' ? alpha.id : 'alpha-new',
            messages: [{ role: 'assistant', content: 'late alpha answer' }],
          } } });
          await page.waitForFunction(count => window.sideChatSettled > count, before);
          await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
          if (action === 'model-lock') {
            await page.waitForFunction(() => !document.querySelector('#side-chat-model').disabled);
          } else {
            assert.equal(await composer.inputValue(), 'beta draft survives', 'Late alpha result must not erase beta draft');
            assert.equal(await page.getByText('late alpha answer', { exact: true }).count(), 0);
            assert.equal(await page.getByText('Synthetic alpha failure', { exact: true }).count(), 0);
            assert.match(await page.locator('#side-chat-model').textContent(), /beta/);
            if (outcome === 'success') {
              const cached = await page.evaluate(async () => {
                const { useAppStore } = await import('/src/store/useAppStore.js');
                return useAppStore.getState().conversationsById.A.messages[1].side_chats;
              });
              assert.ok(cached.some(t => t.messages.some(m => m.content === 'late alpha answer')), 'Successful old result must remain cached in its originating message');
            }
            if (process.env.SCREENSHOT_DIR && action === 'send' && outcome === 'success') {
              await mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
              await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, `side-chat-${viewport.width}.png`), animations: 'disabled' });
            }
            await page.getByRole('button', { name: 'Send Follow-up', exact: true }).click();
            await page.getByText('beta answer', { exact: true }).waitFor();
            assert.deepEqual(sentThreads.at(-1), { id: 'beta-saved', content: 'beta draft survives' }, 'Next message must use beta thread');
          }
          assert.deepEqual(errors, []);
          results.push(name); console.log(`PASS ${name}`);
        } catch (error) {
          failures.push({ name, error: error.message });
          console.error(`FAIL ${name}: ${error.message}; page errors: ${errors.join('; ')}`);
        } finally { await context.close(); }
      }
    }
  }

};
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
