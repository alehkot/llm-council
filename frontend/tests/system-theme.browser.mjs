/**
 * Real rendered system-theme synchronization regression with synthetic APIs only.
 * Supply PLAYWRIGHT_MODULE and sandbox-capable CHROMIUM_EXECUTABLE. Optional
 * BROWSER_FRONTEND_ROOT targets an unchanged baseline; SCREENSHOT_DIR saves evidence.
 */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { once } from 'node:events';
import { mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

const { CHROMIUM_EXECUTABLE, PLAYWRIGHT_MODULE, SCREENSHOT_DIR } = process.env;
assert.ok(CHROMIUM_EXECUTABLE && PLAYWRIGHT_MODULE, 'Set browser executable and Playwright module');
const { chromium } = await import(pathToFileURL(PLAYWRIGHT_MODULE).href);
const root = process.env.BROWSER_FRONTEND_ROOT || fileURLToPath(new URL('..', import.meta.url));
const origin = 'http://127.0.0.1:5230';
const server = spawn(process.execPath, ['node_modules/vite/bin/vite.js', '--host', '127.0.0.1', '--port', '5230', '--strictPort'], {
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
const opposite = value => value === 'light' ? 'dark' : 'light';

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
    for (const initial of ['light', 'dark']) {
      for (const scenario of ['system', 'stored']) {
        const name = `${viewport.width}px ${scenario} ${initial}`;
        const context = await browser.newContext({ viewport, colorScheme: initial });
        const page = await context.newPage();
        page.setDefaultTimeout(5000);
        const errors = [];
        const unexpected = [];
        page.on('pageerror', error => errors.push(error.message));
        await context.addInitScript(({ initial, scenario }) => {
          localStorage.setItem('llm_council_api_key', 'synthetic-test-key');
          // Set the preference once; reload must observe what the toggle saved.
          if (!sessionStorage.getItem('theme-test-initialized')) {
            if (scenario === 'stored') localStorage.setItem('llm-council-theme', initial);
            else localStorage.removeItem('llm-council-theme');
            sessionStorage.setItem('theme-test-initialized', 'yes');
          }
        }, { initial, scenario });
        await page.route('**/*', async route => {
          const url = new URL(route.request().url());
          if (url.origin === origin) return route.continue();
          // External fonts are intentionally blocked for an offline deterministic check.
          if (url.hostname === 'fonts.googleapis.com' || url.hostname === 'fonts.gstatic.com') return route.abort();
          if (url.origin !== 'http://localhost:8001') {
            unexpected.push(route.request().url());
            return route.abort();
          }
          let body;
          if (url.pathname === '/api/conversations') body = [];
          else if (url.pathname === '/api/config') body = { council_models: [], negative_mode_enabled: true };
          else if (url.pathname === '/api/consultations/status') body = { active: [] };
          else {
            unexpected.push(`${route.request().method()} ${url.pathname}`);
            return route.abort();
          }
          await route.fulfill({ json: body });
        });
        const assertTheme = async expected => {
          await page.waitForFunction(theme => (
            document.documentElement.classList.contains(theme)
            && document.body.classList.contains(theme)
          ), expected);
          assert.equal(await page.locator('html').evaluate((el, value) => el.classList.contains(value), opposite(expected)), false);
          assert.equal(await page.locator('body').evaluate((el, value) => el.classList.contains(value), opposite(expected)), false);
          const toggle = page.locator('button[title^="Switch to"]');
          await toggle.waitFor();
          // Allow React's external-store subscription to commit before asserting.
          await page.waitForFunction(label => document.querySelector('button[title^="Switch to"]')?.getAttribute('aria-label') === label,
            `Switch to ${opposite(expected)} mode`, { timeout: 1500 });
          assert.equal(await toggle.getAttribute('title'), `Switch to ${opposite(expected)} mode`);
        };
        try {
          await page.goto(origin);
          await assertTheme(initial);
          if (scenario === 'system') {
            // Both directions and repeated changes must reach the same React state
            // that supplies the accessible label and click handler.
            for (const scheme of [opposite(initial), initial, opposite(initial)]) {
              await page.emulateMedia({ colorScheme: scheme });
              await assertTheme(scheme);
            }
            await page.getByRole('button', { name: `Switch to ${initial} mode`, exact: true }).click();
            await assertTheme(initial);
            assert.equal(await page.evaluate(() => localStorage.getItem('llm-council-theme')), initial);
          } else {
            await page.emulateMedia({ colorScheme: opposite(initial) });
            await delay(100);
            await assertTheme(initial);
          }
          // Once explicitly chosen, even repeated OS changes must not override it.
          for (const scheme of [initial, opposite(initial)]) {
            await page.emulateMedia({ colorScheme: scheme });
            await delay(100);
            await assertTheme(initial);
          }
          await page.reload();
          await assertTheme(initial);
          assert.equal(await page.evaluate(() => localStorage.getItem('llm-council-theme')), initial);
          if (SCREENSHOT_DIR) await page.screenshot({ path: join(SCREENSHOT_DIR, `theme-${viewport.width}-${scenario}-${initial}.png`), fullPage: true });
          assert.deepEqual(errors, []);
          assert.deepEqual(unexpected, []);
          results.push(name);
          console.log(`PASS ${name}`);
        } catch (error) {
          failures.push({ name, error: error.message });
          console.error(`FAIL ${name}: ${error.message}`);
        } finally {
          await context.close();
        }
      }
    }
  }
}
try {
  await Promise.race([run(), timeout]);
  console.log(JSON.stringify({ passed: results.length, failed: failures.length, failures, sandbox: true, liveProviders: false }));
  if (failures.length) process.exitCode = 1;
} finally {
  clearTimeout(deadline);
  if (browser) await browser.close();
  if (server.exitCode === null) {
    server.kill('SIGTERM');
    await Promise.race([once(server, 'exit'), delay(2000)]);
    if (server.exitCode === null) server.kill('SIGKILL');
  }
}
