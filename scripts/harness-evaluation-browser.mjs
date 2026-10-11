/** Controlled, network-denied preparation laboratory. Never attaches to a user browser.
 * All experimental arms use this identical production browserAdapter and allowlist.
 * The terminal oracle is evaluator-only, never an exposed model tool.
 */
import { createInterface } from 'node:readline';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import { randomUUID, createHash } from 'node:crypto';
import fs from 'node:fs/promises';
import { browserAdapter } from './visual-bridge-host.mjs';
import { ControlNotReady } from './browser-form-state.mjs';

const allowed = new Set(['fill_control', 'select_control', 'open_control', 'search_control', 'fill_batch', 'prepare_subgoal']);
const scenarios = new Set(['native', 'async', 'duplicate', 'record', 'overwrite', 'missing', 'no-commit']);
let browser, context, page, adapter, tab, task, observationId, stopped = false, initial;
const id = name => task.family === 'aria' ? `holdout-${name}` : name;
const field = name => page.locator(`[id="${id(name)}"]`);
const names = ['school', 'eduDate', 'schoolSearch', 'eduDate2', 'company', 'title', 'start', 'end',
  'company2', 'title2', 'start2', 'city', 'resume', 'cover', 'generic', 'education', 'edu1', 'edu2', 'work', 'job1', 'job2', 'schools'];
async function launch(spec) {
  if (task) throw Error('One isolated trial per laboratory process');
  if (!spec || !scenarios.has(spec.scenario) || !['fieldset', 'aria'].includes(spec.family) ||
      !['lab-a', 'lab-b'].includes(spec.employer)) throw Error('Unknown controlled fixture');
  task = spec;
  const python = process.env.APPLYPILOT_TEST_PYTHON || 'python';
  const location = spawnSync(python, ['-c', 'import pathlib,playwright;print(pathlib.Path(playwright.__file__).parent / "driver" / "package")'],
    { encoding: 'utf8', timeout: 15000, windowsHide: true });
  if (location.status !== 0) throw Error('Playwright installation unavailable');
  const { chromium } = createRequire(import.meta.url)(location.stdout.trim());
  browser = await chromium.launch({ headless: true, ...(process.env.APPLYPILOT_TEST_CHROMIUM ?
    { executablePath: process.env.APPLYPILOT_TEST_CHROMIUM } : {}) });
  context = await browser.newContext({ serviceWorkers: 'block', acceptDownloads: false });
  await context.route('**/*', route => route.abort());
  page = await context.newPage();
  let html = await fs.readFile(new URL('../tests/fixtures/apply/semantic-preparation-lab.html', import.meta.url), 'utf8');
  const scenario = ['async', 'duplicate', 'no-commit'].includes(task.scenario) ? task.scenario : '';
  html = html.replace("window.scenario = new URL(location.href).searchParams.get('scenario');", `window.scenario = ${JSON.stringify(scenario)};`);
  if (task.employer === 'lab-b') html = html.replaceAll('Example University', 'Cedar Institute');
  if (task.family === 'aria') {
    html = html.replaceAll('<fieldset', '<div role="group"').replaceAll('</fieldset>', '</div>')
      .replaceAll('<legend>', '<h3>').replaceAll('</legend>', '</h3>');
    for (const name of [...names].sort((a, b) => b.length - a.length)) {
      html = html.replaceAll(`id="${name}"`, `id="holdout-${name}"`).replaceAll(`for="${name}"`, `for="holdout-${name}"`)
        .replaceAll(`aria-controls="${name}"`, `aria-controls="holdout-${name}"`)
        .replaceAll(`'#${name}'`, `'#holdout-${name}'`);
    }
  }
  await page.setContent(html);
  if (task.scenario === 'overwrite') await field('title').evaluate(el => {
    el.addEventListener('input', () => {
      if (window.overwriteDone) return;
      window.overwriteDone = true;
      document.querySelector('input[id$="company"]').value = 'Parser replacement';
      window.events.push({ event: 'parser_overwrite' });
    });
  });
  const cdp = await context.newCDPSession(page);
  tab = { id: randomUUID(), reads: 0, writes: 0, url: () => page.url(), title: () => page.title(),
    capabilities: { get: async name => { if (name !== 'cdp') throw Error('Unsupported capability'); return cdp; } },
    playwright: { domSnapshot: () => page.locator('body').ariaSnapshot(),
      evaluate: async callback => { tab.reads++; return page.evaluate(callback); },
      locator: selector => {
        const locator = page.locator(selector); const wrapped = { count: () => locator.count() };
        for (const method of ['fill', 'press', 'selectOption', 'setChecked', 'click']) wrapped[method] = async (value, options) => {
          tab.writes++;
          return method === 'click' ? locator.click({ timeout: value?.timeoutMs || 3000 })
            : locator[method](value, { timeout: options?.timeoutMs || 3000 });
        };
        return wrapped;
      },
    } };
  adapter = browserAdapter(tab, { observationMode: 'playwright', allowFieldBatch: true,
    allowPreparationSubgoals: true, reuseFormObservations: true });
  initial = await state();
  return { scope: 'controlled_chromium_preparation', task: spec, network: 'denied', submission: 'unavailable',
    browser_version: browser.version(), initial_state_digest: createHash('sha256').update(JSON.stringify({ initial, spec })).digest('hex') };
}
async function state() {
  return page.evaluate(() => Object.fromEntries([...document.querySelectorAll('input,select,textarea')]
    .filter(e => e.type !== 'file').map(e => [e.id, { value: e.value, invalid: e.validity?.valid === false }])));
}
async function observe(actionResult) {
  const content = await adapter.observe({ mode: 'dom', actionResult });
  observationId = randomUUID();
  return { ok: true, observation_id: observationId, content, reads: tab.reads, writes: tab.writes };
}
async function oracle() {
  const current = await state();
  const schoolTask = ['native', 'async', 'duplicate', 'no-commit'].includes(task.scenario);
  const target = task.employer === 'lab-a' ? 'Example University' : 'Cedar Institute';
  const desired = schoolTask ? {} : { company: task.employer === 'lab-a' ? 'Example Corp' : 'Maple Studio',
    title: 'Engineer', start: '2021-01-01', end: '2022-01-01' };
  let errors = 0, substantive = 0;
  if (schoolTask) {
    const committed = task.scenario === 'native' ? current[id('school')].value === 'demo'
      : (await page.locator('.select__single-value').innerText()) === target;
    if (!committed) { errors++;
      const display = task.scenario === 'native' ? current[id('school')].value : await page.locator('.select__single-value').innerText();
      if (display) substantive++;
    }
  } else {
    for (const [key, value] of Object.entries(desired)) {
      if (current[id(key)].value !== value || current[id(key)].invalid) errors++;
      if (current[id(key)].value && current[id(key)].value !== value) substantive++;
    }
  }
  const allowedFields = new Set(schoolTask ? [id(task.scenario === 'native' ? 'school' : 'schoolSearch')]
    : Object.keys(desired).map(id));
  let collateral = 0;
  for (const [key, value] of Object.entries(initial)) {
    if (!allowedFields.has(key) && current[key]?.value !== value.value) collateral++;
  }
  const events = await page.evaluate(() => window.events);
  const ambiguityClick = task.scenario === 'duplicate' && events.some(e => e.event === 'option_click');
  const recoverable = task.scenario === 'overwrite' && events.some(e => e.event === 'parser_overwrite');
  return { complete: errors === 0 && collateral === 0 && !ambiguityClick, substantive_errors: substantive + collateral + Number(ambiguityClick), unresolved_conditions: errors,
    collateral_errors: collateral, recovery_triggered: recoverable, expected_handoff: task.scenario === 'duplicate' ? 'blocked'
      : task.scenario === 'missing' ? 'needs_fact' : task.scenario === 'no-commit' ? 'blocked' : null,
    reads: tab.reads, writes: tab.writes, oracle: 'independent_dom/v1' };
}
async function dispatch(request) {
  if (request.method === 'init') return launch(request.task);
  if (!adapter) throw Error('Initialize controlled fixture first');
  if (request.method === 'oracle') return oracle();
  if (request.method === 'observe') return observe();
  if (request.method === 'act') {
    if (stopped) throw Error('Unknown effects forbid replay');
    if (!allowed.has(request.operation) || request.observation_id !== observationId) {
      return { ok: false, outcome: 'failed_no_effect', reason: 'operation_or_observation_rejected' };
    }
    observationId = null;
    let completed = false;
    try {
      const actionResult = await adapter.act(request.operation, request.arguments,
        { deadlineAt: Date.now() / 1000 + 20 });
      completed = true;
      return await observe(actionResult);
    } catch (error) {
      if (error instanceof ControlNotReady && !completed) return { ok: false, outcome: 'failed_no_effect', reason: 'preinput_rejected' };
      stopped = true;
      return { ok: false, outcome: 'outcome_unknown', reason: 'input_or_readback_unavailable' };
    }
  }
  throw Error('Unknown evaluator method');
}
async function close() { await browser?.close(); }
process.on('SIGTERM', async () => { await close(); process.exit(0); });
const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
try {
  for await (const line of input) {
    if (line.length > 4_000_000) throw Error('Oversized evaluator request');
    let req;
    try {
      req = JSON.parse(line);
      if (req.method === 'close') { await close(); console.log(JSON.stringify({ id: req.id, result: { closed: true } })); break; }
      console.log(JSON.stringify({ id: req.id, result: await dispatch(req) }));
    } catch (error) {
      console.log(JSON.stringify({ id: req?.id, error: error instanceof ControlNotReady ? 'preinput_rejected' : 'laboratory_error' }));
    }
  }
} finally { await close(); }
