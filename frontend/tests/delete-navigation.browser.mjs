/**
 * Optional rendered regression test. No provider or real backend is contacted.
 * npm ci; CHROMIUM_EXECUTABLE=/path/to/chrome-headless-shell \
 * PLAYWRIGHT_MODULE=/path/to/playwright-core/index.mjs node tests/delete-navigation.browser.mjs
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
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5198', '--strictPort'], {
  cwd: root, stdio: ['ignore', 'pipe', 'pipe'],
});
let serverLog = '';
server.stdout.on('data', data => { serverLog += data; });
server.stderr.on('data', data => { serverLog += data; });
const origin = 'http://127.0.0.1:5198';
let browser;
const failures = [];
const results = [];
let deadline;
const timeout = new Promise((_, reject) => {
  deadline = setTimeout(() => reject(new Error('Browser test deadline exceeded')), 120_000);
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
    for (const scenario of ['navigate', 'new-row-and-dialog', 'still-selected', 'failure']) {
      const name = `${viewport.width}px delayed delete A: ${scenario}`;
      const context = await browser.newContext({ viewport });
      const page = await context.newPage();
      page.setDefaultTimeout(10_000);
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      let resolveDelete;
      const deleted = new Promise(resolve => { resolveDelete = resolve; });
      const fixture = id => ({ id, title: `Consultation ${id}`, created_at: '2026-01-01T00:00:00Z', messages: [
        { role: 'user', content: `Existing ${id} question` },
        { role: 'assistant', stage1: null, stage2: null, stage3: null, metadata: { status: 'complete', consultation_id: `${id}-run` } },
      ] });
      await context.addInitScript(() => localStorage.setItem('llm_council_api_key', 'synthetic-test-key'));
      await page.route('**/*', async route => {
        const request = route.request();
        const url = new URL(request.url());
        if (url.origin === origin) return route.continue();
        if (url.origin !== 'http://localhost:8001') return route.abort();
        let body;
        if (url.pathname === '/api/conversations/A' && request.method() === 'DELETE') {
          resolveDelete(route);
          return;
        }
        if (url.pathname === '/api/conversations' && request.method() === 'POST') body = { ...fixture('C'), messages: [] };
        else if (url.pathname === '/api/conversations') body = ['A', 'B'].map(id => ({ ...fixture(id), message_count: 2 }));
        else if (url.pathname === '/api/config') body = { negative_mode_enabled: false, council_models: [], chairman_model: 'test', title_model: 'test' };
        else if (url.pathname === '/api/consultations/status') body = { active: [] };
        else if (url.pathname.endsWith('/consultation/status')) body = { status: 'none' };
        else if (url.pathname.endsWith('/queue')) body = { queue: [] };
        else if (/\/api\/conversations\/[ABC]$/.test(url.pathname)) body = fixture(url.pathname.at(-1));
        else throw new Error(`Unexpected synthetic API request: ${request.method()} ${url.pathname}`);
        await route.fulfill({ json: body });
      });
      const sidebarButton = name => page.getByRole('button', { name, exact: true }).filter({ visible: true });
      const openSidebar = async () => {
        if (viewport.width < 1024 && !await sidebarButton('Open consultation: Consultation B').count()) {
          await page.getByRole('button', { name: 'Open consultations', exact: true }).click();
        }
      };
      const selected = id => page.waitForFunction(async expected => {
        const { useAppStore } = await import('/src/store/useAppStore.js');
        const s = useAppStore.getState();
        return s.currentConversationId === expected && (expected === null || s.conversationsById[expected]);
      }, id);
      const snapshot = () => page.evaluate(async () => {
        const { useAppStore } = await import('/src/store/useAppStore.js');
        const s = useAppStore.getState();
        return { selected: s.currentConversationId, rows: s.conversations.map(c => c.id), cached: Object.keys(s.conversationsById), target: s.deleteTarget?.id ?? null, error: s.error };
      });
      try {
        await page.goto(`${origin}/?conversation=A`);
        await selected('A');
        await openSidebar();
        await sidebarButton('Delete consultation: Consultation A').click();
        await page.getByRole('alertdialog').getByRole('button', { name: 'Delete', exact: true }).click();
        const pending = await deleted;
        await page.getByRole('alertdialog').waitFor({ state: 'hidden' });
        if (scenario === 'navigate' || scenario === 'new-row-and-dialog') {
          await openSidebar();
          await sidebarButton('Open consultation: Consultation B').click();
          await selected('B');
        }
        if (scenario === 'new-row-and-dialog') {
          await openSidebar();
          await sidebarButton('New Consultation').click();
          await selected('C');
          await openSidebar();
          await sidebarButton('Open consultation: Consultation B').click();
          await selected('B');
          await openSidebar();
          await sidebarButton('Delete consultation: Consultation B').click();
          await page.getByRole('alertdialog').waitFor({ state: 'visible' });
        }
        if (scenario === 'failure') {
          await pending.fulfill({ status: 500, json: { detail: 'Synthetic delete failure' } });
          await page.waitForFunction(async () => {
            const { useAppStore } = await import('/src/store/useAppStore.js');
            return Boolean(useAppStore.getState().error);
          });
          const after = await snapshot();
          assert.equal(after.selected, 'A');
          assert.deepEqual(after.rows, ['A', 'B']);
          assert.ok(after.cached.includes('A'));
          assert.match(after.error.message, /delete/i);
        } else {
          await pending.fulfill({ json: { status: 'deleted' } });
          await page.waitForFunction(async () => {
            const { useAppStore } = await import('/src/store/useAppStore.js');
            const s = useAppStore.getState();
            return !s.conversations.some(c => c.id === 'A') && !s.conversationsById.A;
          });
          const after = await snapshot();
          assert.equal(after.selected, scenario === 'still-selected' ? null : 'B');
          assert.ok(after.rows.includes('B'));
          if (scenario === 'new-row-and-dialog') {
            assert.ok(after.rows.includes('C'), 'Newly created C must survive late A deletion');
            assert.ok(after.cached.includes('C'));
            assert.equal(after.target, 'B');
            assert.match(await page.getByRole('alertdialog').textContent(), /Consultation B/);
          }
          assert.equal(after.error, null);
        }
        assert.deepEqual(errors, []);
        if (process.env.SCREENSHOT_DIR && scenario === 'new-row-and-dialog') {
          await page.screenshot({ path: join(process.env.SCREENSHOT_DIR, `delete-${viewport.width}.png`), fullPage: true, animations: 'disabled' });
        }
        results.push(name);
        console.log(`PASS ${name}`);
      } catch (error) {
        failures.push({ name, error: error.message });
        console.error(`FAIL ${name}: ${error.message}; page errors: ${errors.join('; ')}`);
      } finally {
        // Closing context cancels a held synthetic response if an assertion failed.
        await context.close();
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
