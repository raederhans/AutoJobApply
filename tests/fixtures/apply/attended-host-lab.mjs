/** Test-only isolated Chromium facade. All URLs are fulfilled locally or denied. */
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import { spawnSync } from 'node:child_process';
import { createInAppBrowserHost } from '../../../scripts/visual-bridge-host.mjs';

let browser, page, host, writes = 0, stopOnFill = false;
async function dispatch(request) {
  if (request.method === 'init') {
    const found = spawnSync(process.env.APPLYPILOT_TEST_PYTHON || 'python', ['-X', 'utf8', '-c',
      'import pathlib,playwright;print(pathlib.Path(playwright.__file__).parent / "driver" / "package")'],
    { encoding: 'utf8', windowsHide: true });
    if (found.status !== 0) throw Error('Playwright runtime unavailable');
    const { chromium } = createRequire(import.meta.url)(found.stdout.trim());
    browser = await chromium.launch({ headless: true });
    const context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: false });
    const html = `<!doctype html><html lang="en"><title>Controlled application</title><body>
      <form onsubmit="event.preventDefault();throw Error('Submission unavailable')">
      <label for="email">Email</label><input id="email" type="email" value="test@example.test">
      <fieldset id="employment-record"><legend>Employment 1</legend>
      <label for="company">Company</label><input id="company" required>
      <label for="title">Job title</label><input id="title" required>
      </fieldset></form><script>window.events=[];
      document.addEventListener('input',e=>events.push(e.target.id));</script></body></html>`;
    await context.route('**/*', route => route.request().url() === request.url
      ? route.fulfill({ status: 200, contentType: 'text/html', body: html }) : route.abort());
    page = await context.newPage();
    await page.goto(request.url);
    const cdp = await context.newCDPSession(page);
    const tab = { id: 'controlled-tab', url: () => page.url(), title: () => page.title(),
      capabilities: { get: async name => { if (name !== 'cdp') throw Error('Unsupported capability'); return cdp; } },
      playwright: { domSnapshot: () => page.locator('body').ariaSnapshot(), evaluate: fn => page.evaluate(fn),
        locator: selector => {
          const locator = page.locator(selector);
          const facade = { count: () => locator.count() };
          for (const method of ['fill', 'press', 'selectOption', 'setChecked', 'click']) {
            facade[method] = async (value, options) => {
              writes++;
              const result = method === 'click' ? await locator.click({ timeout: value?.timeoutMs || 3000 })
                : await locator[method](value, { timeout: options?.timeoutMs || 3000 });
              if (stopOnFill && method === 'fill') { await browser.close(); throw Error('Controlled Host disconnect after input'); }
              return result;
            };
          }
          return facade;
        } } };
    host = await createInAppBrowserHost({ directory: request.directory, tab, observationMode: 'playwright', continuity: true });
    return host.binding;
  }
  if (request.method === 'inspect') return host.inspect();
  if (request.method === 'review') return host.review(request.request_id);
  if (request.method === 'execute') return host.execute(request.request_id);
  if (request.method === 'stop') { await host.close(); return { stopped: true }; }
  if (request.method === 'stop-on-fill') { stopOnFill = true; return {}; }
  if (request.method === 'parser') {
    await page.locator('#company').evaluate(el => el.addEventListener('input', () => {
      if (!window.parsed) { window.parsed = true; document.querySelector('#email').value = 'parser@example.test'; }
    }));
    return {};
  }
  if (request.method === 'coverage') {
    await page.evaluate(() => document.body.append(document.createElement('iframe')));
    return {};
  }
  if (request.method === 'change') { await page.locator('#email').fill('changed@example.test'); return {}; }
  if (request.method === 'oracle') return { writes, ...await page.evaluate(() => ({
    values: Object.fromEntries([...document.querySelectorAll('input')].map(el => [el.id, el.value])), events: window.events })) };
  if (request.method === 'close') { await browser?.close(); return {}; }
  throw Error('Unknown fixture method');
}

for await (const line of createInterface({ input: process.stdin })) {
  try { process.stdout.write(JSON.stringify({ result: await dispatch(JSON.parse(line)) }) + '\n'); }
  catch (error) { process.stdout.write(JSON.stringify({ error: String(error.stack) }) + '\n'); }
}
await browser?.close();
