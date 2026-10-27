/**
 * Optional rendered regression test. No provider or real backend is contacted.
 * npm ci; CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
 * PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs node tests/interactive-navigation.browser.mjs
 * Supply your installed Playwright module and sandbox-capable Chromium explicitly.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { once } from 'node:events';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE } = process.env;
assert.ok(CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE, 'Set CHROMIUM_EXECUTABLE and PLAYWRIGHT_MODULE');
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root = process.env.BROWSER_FRONTEND_ROOT || fileURLToPath(new URL('..', import.meta.url));
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5199', '--strictPort'], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
});
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
const origin = 'http://127.0.0.1:5199';
let browser;
const failures = [];
const results = [];
let deadline;
const timeout = new Promise((_, reject) => {
  deadline = setTimeout(() => reject(new Error('Browser test deadline exceeded')), 240_000);
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
  if (process.env.SCREENSHOT_DIR) await mkdir(process.env.SCREENSHOT_DIR, { recursive: true });
  for (const viewport of [{ width: 1280, height: 900 }, { width: 390, height: 844 }]) {
    for (const action of ['submit', 'skip']) {
      for (const scenario of ['navigate', 'next-prompt', 'next-round', 'current', 'navigate-error', 'next-prompt-error', 'next-round-error', 'current-error']) {
        const name = `${viewport.width}px ${action}: ${scenario}`;
        const context = await browser.newContext({ viewport });
        const page = await context.newPage();
        page.setDefaultTimeout(6000);
        const errors = [];
        const posts = [];
        page.on('pageerror', error => errors.push(error.message));
        let release;
        const requestHeld = new Promise(resolve => { release = resolve; });
        const prompts = Object.fromEntries(['A', 'B'].map(id => [id, {
          session_id: `${id}-session`, prompt_id: `${id}-prompt`,
          prompt_type: 'clarification', title: `Clarify ${id}`, description: `Synthetic ${id} clarification`,
          skip_allowed: true, fields: [{ id: 'answer', type: 'text', label: 'Your answer', default: '' }],
        }]));
        const fixture = id => ({ id, title: `Consultation ${id}`, created_at: '2026-01-01T00:00:00Z', messages: [
          { role: 'user', content: `Existing ${id} question` },
          { role: 'assistant', stage1: null, stage2: null, stage3: null,
            metadata: { status: 'interactive', consultation_id: `${id}-run`, interactive_prompt: prompts[id] } },
        ] });
        await context.addInitScript(() => localStorage.setItem('llm_council_api_key', 'synthetic-test-key'));
        await page.route('**/*', async route => {
          const request = route.request();
          const url = new URL(request.url());
          if (url.origin === origin) return route.continue();
          if (url.origin !== 'http://localhost:8001') return route.abort();
          let body;
          if (url.pathname === '/api/interactive-response' && request.method() === 'POST') {
            posts.push(request.postDataJSON());
            release(route);
            return;
          }
          if (url.pathname === '/api/conversations') body = ['A', 'B'].map(id => ({ ...fixture(id), message_count: 2 }));
          else if (url.pathname === '/api/config') body = { negative_mode_enabled: false, council_models: [], chairman_model: 'test', title_model: 'test' };
          else if (url.pathname === '/api/consultations/status') body = { active: [] };
          // Keep ordinary polling from changing the test's deliberate in-flight state.
          else if (url.pathname.endsWith('/consultation/status')) body = { status: 'none' };
          else if (url.pathname.endsWith('/queue')) body = { queue: [] };
          else if (/\/api\/conversations\/[AB]$/.test(url.pathname)) body = fixture(url.pathname.at(-1));
          else throw new Error(`Unexpected synthetic API request: ${request.method()} ${url.pathname}`);
          await route.fulfill({ json: body });
        });
        const selected = id => page.waitForFunction(async expected => {
          const { useAppStore } = await import('/src/store/useAppStore.js');
          const s = useAppStore.getState();
          return s.currentConversationId === expected && s.currentInteractivePrompt?.session_id === `${expected}-session`;
        }, id);
        const snapshot = () => page.evaluate(async () => {
          const { useAppStore } = await import('/src/store/useAppStore.js');
          const s = useAppStore.getState();
          return { selected: s.currentConversationId, session: s.interactiveSessionId,
            prompt: s.currentInteractivePrompt?.prompt_id ?? null, error: s.error };
        });
        try {
          await page.goto(`${origin}/?conversation=A`);
          await selected('A');
          await page.getByLabel('Your answer', { exact: true }).fill('Original A answer');
          await page.getByRole('button', { name: action === 'submit' ? 'Continue' : 'Skip (Use Defaults)', exact: true }).click();
          const pending = await requestHeld;
          assert.deepEqual(posts, [{ session_id: 'A-session', prompt_id: 'A-prompt',
            responses: action === 'submit' ? { answer: 'Original A answer' } : {}, skipped: action === 'skip' }]);
          if (scenario.startsWith('navigate')) {
            if (viewport.width < 1024) await page.getByRole('button', { name: 'Open consultations', exact: true }).click();
            await page.getByRole('button', { name: 'Open consultation: Consultation B', exact: true }).filter({ visible: true }).click();
            await selected('B');
            await page.getByLabel('Your answer', { exact: true }).fill('Unsubmitted B answer');
          } else if (scenario.startsWith('next-')) {
            // Simulate a later server prompt reaching the shared store before POST completion.
            // Submission and rendering still use the production React components.
            await page.evaluate(async samePromptId => {
              const { useAppStore } = await import('/src/store/useAppStore.js');
              const s = useAppStore.getState();
              s.setCurrentInteractivePrompt({ ...s.currentInteractivePrompt, prompt_id: samePromptId ? s.currentInteractivePrompt.prompt_id : 'A-next', title: 'Next clarification A' });
            }, scenario.startsWith('next-round'));
            await page.getByLabel('Your answer', { exact: true }).fill('Unsubmitted next answer');
          }
          const responseDone = page.waitForResponse(response => response.url().endsWith('/api/interactive-response'));
          await pending.fulfill(scenario.endsWith('error')
            ? { status: 500, json: { detail: 'Synthetic delayed response failure' } }
            : { json: { status: 'accepted' } });
          await (await responseDone).finished();
          // Let response JSON parsing and the production async callback settle before assertions.
          await page.waitForTimeout(200);
          const after = await snapshot();
          assert.equal(after.selected, scenario.startsWith('navigate') ? 'B' : 'A');
          if (scenario === 'current') {
            assert.equal(after.prompt, null);
            assert.equal(after.session, null);
            assert.equal(after.error, null);
            assert.equal(await page.getByLabel('Your answer', { exact: true }).count(), 0);
          } else {
            const expected = scenario.startsWith('navigate') ? 'B-prompt' : scenario.startsWith('next-prompt') ? 'A-next' : 'A-prompt';
            assert.equal(after.prompt, expected, 'A late reply must not clear the current prompt');
            assert.equal(after.session, scenario.startsWith('navigate') ? 'B-session' : 'A-session');
            assert.equal(await page.getByLabel('Your answer', { exact: true }).inputValue(), scenario.startsWith('navigate')
              ? 'Unsubmitted B answer' : scenario.startsWith('next-') ? 'Unsubmitted next answer' : 'Original A answer');
            if (scenario === 'current-error') assert.match(after.error?.message ?? '', /Failed to (submit response|skip prompt)/);
            else assert.equal(after.error, null, 'A stale failure must not appear on a newer prompt');
          }
          assert.deepEqual(errors, []);
          if (process.env.SCREENSHOT_DIR && action === 'submit' && scenario === 'navigate') {
            await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, `interactive-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
          }
          results.push(name);
          console.log(`PASS ${name}`);
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
