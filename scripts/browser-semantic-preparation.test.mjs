/** Actual local Chromium DOM and actions, using the existing Python Playwright installation.
 * No model calls, applicant data or live employer routes. Observer/operations are NOT mocked.
 */
import assert from 'node:assert/strict';
import { after, before, test } from 'node:test';
import { spawnSync } from 'node:child_process';
import { createRequire } from 'node:module';
import fs from 'node:fs/promises';
import { observeForm, structureChanges } from './browser-form-state.mjs';
import { operatePreparationSubgoal } from './browser-preparation-subgoals.mjs';
const python = process.env.APPLYPILOT_TEST_PYTHON || 'python';
const location = spawnSync(python, ['-X', 'utf8', '-c', 'import pathlib,playwright;print(pathlib.Path(playwright.__file__).parent / "driver" / "package")'], { encoding: 'utf8' });
assert.equal(location.status, 0, location.stderr);
const { chromium } = createRequire(import.meta.url)(location.stdout.trim());
let browser, fixtureHtml;
before(async () => {
  fixtureHtml = await fs.readFile(new URL('../tests/fixtures/apply/semantic-preparation-lab.html', import.meta.url), 'utf8');
  browser = await chromium.launch({ headless: true, ...(process.env.APPLYPILOT_TEST_CHROMIUM ? { executablePath: process.env.APPLYPILOT_TEST_CHROMIUM } : {}) });
});
after(async () => { await browser?.close(); });
async function fixture(t, scenario = '') {
  const page = await browser.newPage();
  t.after(() => page.close());
  await page.route('**/*', route => route.abort());
  await page.setContent(fixtureHtml.replace("window.scenario = new URL(location.href).searchParams.get('scenario');",
    `window.scenario = ${JSON.stringify(scenario)};`));
  const cdp = await page.context().newCDPSession(page);
  const tab = { id: `test-${scenario}`, reads: 0, writes: [], beforeRead: null,
    url: () => page.url(), title: () => page.title(),
    capabilities: { get: async name => { assert.equal(name, 'cdp'); return cdp; } },
    dom_cua: { get_visible_dom: async () => '' },
    playwright: { domSnapshot: () => page.locator('body').ariaSnapshot(),
      evaluate: async cb => { tab.reads++; await tab.beforeRead?.(); return page.evaluate(cb); },
      locator: selector => {
        const locator = page.locator(selector);
        const result = { count: () => locator.count() };
        for (const action of ['fill', 'press', 'selectOption', 'setChecked', 'click']) result[action] = async (value, options) => {
          tab.writes.push({ action, selector });
          if (action === 'click') return locator.click({ timeout: value?.timeoutMs || 3000 });
          return locator[action](value, { timeout: options?.timeoutMs || 3000 });
        };
        return result;
      },
    },
  };
  return { page, tab };
}
const field = (form, id) => form.fields.find(f => f.field_key === `[id="${id}"]`);
const schoolPlan = (form, id = 'schoolSearch') => ({ kind: 'school_selection',
  field_key: field(form, id).field_key, record_key: field(form, id).semantic.record.key, value: 'Example University' });
const recordPlan = form => ({ kind: 'record_fields', record_key: field(form, 'company').semantic.record.key,
  steps: [['company', 'Example Corp'], ['title', 'Engineer'], ['start', '2021-01-01'], ['end', '2022-01-01']].map(([id, value]) =>
    ({ operation: 'fill_control', field_key: field(form, id).field_key, value })) });

test('B2: same-label dates in explicit repeated records have distinct identity-bound paths', async t => {
  const { tab } = await fixture(t); const form = await observeForm(tab);
  assert.equal(field(form, 'start').semantic.identification, 'identified');
  assert.notEqual(field(form, 'start').semantic.record.key, field(form, 'start2').semantic.record.key);
  assert.deepEqual(field(form, 'start').record_path.map(r => r.label), ['Employment', 'Employment 1']);
  assert.ok(field(form, 'start').record_path.every(r => Number.isInteger(r.dom_identity)));
  assert.equal(tab.reads, 1);
});
test('B2: duplicate labels in one record are ambiguous, not silently distinguished by coordinates', async t => {
  const { tab, page } = await fixture(t);
  await page.locator('#job1').evaluate(e => e.insertAdjacentHTML('beforeend', '<label for="duplicate">Company</label><input id="duplicate">'));
  const form = await observeForm(tab);
  assert.equal(field(form, 'company').semantic.identification, 'ambiguous');
  assert.ok(field(form, 'company').semantic.diagnostic_codes.includes('duplicate_label_in_record'));
});
test('B2: unresolved ancestor names and internal field names do not become identified semantics', async t => {
  const { tab, page } = await fixture(t);
  await page.locator('body').evaluate(e => e.insertAdjacentHTML('beforeend', '<fieldset id="unknown"><input id="internal"></fieldset>'));
  const f = field(await observeForm(tab), 'internal');
  assert.equal(f.semantic.identification, 'not_covered');
  assert.equal(f.semantic.record.status, 'ambiguous');
});
test('B2: conflicting ARIA and visible labels are explicit', async t => {
  const { tab, page } = await fixture(t); await page.locator('#company').evaluate(e => e.setAttribute('aria-label', 'University'));
  assert.ok(field(await observeForm(tab), 'company').semantic.diagnostic_codes.includes('conflicting_labels'));
});
test('B2: collapsed school options and unknown iframe/closed-shadow coverage remain explicit', async t => {
  const { tab, page } = await fixture(t); await page.locator('body').evaluate(e => e.insertAdjacentHTML('beforeend', '<iframe></iframe>'));
  const form = await observeForm(tab);
  assert.equal(field(form, 'schoolSearch').semantic.options.status, 'not_expanded');
  assert.equal(form.semantic_coverage.frames.observed_count, 1);
  assert.equal(form.semantic_coverage.closed_shadow, 'not_covered');
  assert.equal(form.semantic_coverage.all_steps, 'unverified');
});
test('B2: resume, cover letter and generic attachment purposes remain separate and advisory', async t => {
  const { tab } = await fixture(t); const form = await observeForm(tab);
  assert.equal(field(form, 'resume').semantic.attachment.purpose, 'resume');
  assert.equal(field(form, 'cover').semantic.attachment.purpose, 'cover_letter');
  assert.equal(field(form, 'generic').semantic.attachment.status, 'not_covered');
  assert.equal(field(form, 'resume').semantic.attachment.grants_upload_authority, false);
});
test('B2: mixed attachment label is ambiguous', async t => {
  const { tab, page } = await fixture(t); await page.locator('label[for="resume"]').evaluate(e => e.textContent = 'Resume or cover letter');
  assert.equal(field(await observeForm(tab), 'resume').semantic.attachment.status, 'ambiguous');
});
test('B2: record replacement invalidates identity even when its field element is preserved', async t => {
  const { tab, page } = await fixture(t); const before = await observeForm(tab);
  await page.locator('#job1').evaluate(e => { const replacement = e.cloneNode(false); replacement.append(...e.childNodes); e.replaceWith(replacement); });
  assert.equal(structureChanges(before, await observeForm(tab)).changed, true);
});
test('B2: headings never collect candidate input values', async t => {
  const { tab, page } = await fixture(t); await page.locator('#company').fill('PRIVATE_CANDIDATE_SENTINEL');
  const form = await observeForm(tab);
  assert.doesNotMatch(JSON.stringify(form.fields.map(f => [f.semantic, f.record_path])), /PRIVATE_CANDIDATE_SENTINEL/);
});
for (const scenario of ['', 'async']) test(`B3: custom school selection ${scenario || 'immediate'} verifies committed display, not query`, async t => {
  const { tab, page } = await fixture(t, scenario); const form = await observeForm(tab);
  const result = await operatePreparationSubgoal(tab, form, schoolPlan(form));
  assert.equal(result.subgoal_result.status, 'verified', JSON.stringify(result.subgoal_result));
  assert.equal(await page.locator('.select__single-value').innerText(), 'Example University');
  assert.equal(await page.locator('#schoolSearch').inputValue(), '');
  assert.equal(result.subgoal_result.actions, 3);
  assert.equal(result.subgoal_result.whole_application_verified, false);
});
test('B3: native school selector commits the unique option value', async t => {
  const { tab, page } = await fixture(t); const form = await observeForm(tab);
  const r = await operatePreparationSubgoal(tab, form, schoolPlan(form, 'school'));
  assert.equal(r.subgoal_result.status, 'verified'); assert.equal(await page.locator('#school').inputValue(), 'demo');
});
test('B3: observed selected school is retained without another input', async t => {
  const { tab, page } = await fixture(t); await page.selectOption('#school', 'demo');
  const form = await observeForm(tab); const r = await operatePreparationSubgoal(tab, form, schoolPlan(form, 'school'));
  assert.equal(r.subgoal_result.status, 'verified'); assert.equal(tab.writes.length, 0);
});
test('B3: duplicate school names never cause a guessed option click', async t => {
  const { tab, page } = await fixture(t, 'duplicate'); const form = await observeForm(tab);
  const r = await operatePreparationSubgoal(tab, form, schoolPlan(form));
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(r.subgoal_result.reason, 'ambiguous_target_option');
  assert.equal(await page.evaluate(() => events.filter(e => e.event === 'option_click').length), 0);
});
test('B3: missing asynchronous options yield bounded waiting, not repeated clicks', async t => {
  const { tab } = await fixture(t, 'waiting'); const form = await observeForm(tab);
  const r = await operatePreparationSubgoal(tab, form, schoolPlan(form));
  assert.equal(r.subgoal_result.status, 'waiting'); assert.equal(r.subgoal_result.wait_observations, 2);
  assert.equal(r.subgoal_result.actions, 2);
});
test('B3: input query without independent commitment cannot verify selection', async t => {
  const { tab } = await fixture(t, 'no-commit'); const form = await observeForm(tab);
  const r = await operatePreparationSubgoal(tab, form, schoolPlan(form));
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(r.subgoal_result.reason, 'business_postcondition_unproven');
});
test('B3: fills one four-field employment record including real native dates, leaving peer untouched', async t => {
  const { tab, page } = await fixture(t); const form = await observeForm(tab);
  const r = await operatePreparationSubgoal(tab, form, recordPlan(form));
  assert.equal(r.subgoal_result.status, 'verified', JSON.stringify(r.subgoal_result));
  assert.equal(await page.locator('#company').inputValue(), 'Example Corp');
  assert.equal(await page.locator('#end').inputValue(), '2022-01-01');
  assert.equal(await page.locator('#company2').inputValue(), 'Other company');
  assert.equal(await page.locator('#start2').inputValue(), '2020-01-01');
});
test('B3: cross-record plan is rejected before input', async t => {
  const { tab } = await fixture(t); const form = await observeForm(tab); const plan = recordPlan(form);
  plan.steps[0].field_key = field(form, 'company2').field_key;
  await assert.rejects(operatePreparationSubgoal(tab, form, plan), /record/); assert.equal(tab.writes.length, 0);
});
test('B3: missing required record facts are explicit and cause no input', async t => {
  const { tab } = await fixture(t); const form = await observeForm(tab); const plan = recordPlan(form); plan.steps.pop();
  const r = await operatePreparationSubgoal(tab, form, plan);
  assert.equal(r.subgoal_result.status, 'needs_fact'); assert.equal(tab.writes.length, 0);
});
test('B3: upload-like parser overwrite of a non-goal value parks before the next field', async t => {
  const { tab, page } = await fixture(t); const form = await observeForm(tab);
  await page.locator('#company').evaluate(e => e.addEventListener('input', () => document.querySelector('#city').value = 'Parsed city'));
  const r = await operatePreparationSubgoal(tab, form, recordPlan(form));
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(r.subgoal_result.actions, 1);
  assert.equal(await page.locator('#title').inputValue(), '');
});
test('B3: new row invalidates a captured subgoal before input', async t => {
  const { tab, page } = await fixture(t); const form = await observeForm(tab);
  await page.locator('#job1').evaluate(e => e.insertAdjacentHTML('beforeend', '<label for="new">New detail</label><input id="new">'));
  const r = await operatePreparationSubgoal(tab, form, recordPlan(form));
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(tab.writes.length, 0);
});
test('B3: a changed record purpose is not silently inherited by a stale plan', async t => {
  const { tab, page } = await fixture(t); const form = await observeForm(tab);
  await page.locator('#job1 legend').evaluate(e => e.textContent = 'Projects');
  const r = await operatePreparationSubgoal(tab, form, recordPlan(form));
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(tab.writes.length, 0);
});
test('B3: expired deadline never starts a control action', async t => {
  const { tab } = await fixture(t); const form = await observeForm(tab);
  const r = await operatePreparationSubgoal(tab, form, recordPlan(form), { deadlineAt: 0 });
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(tab.writes.length, 0);
});

test('B2: exact scoped file-group ARIA label identifies an attachment without inventing a field label', async t => {
  const { tab, page } = await fixture(t);
  await page.locator('body').evaluate(e => e.insertAdjacentHTML('beforeend', '<div class="file-upload" role="group" aria-labelledby="cv-label"><span id="cv-label">Resume</span><input id="scoped-file" type="file"></div>'));
  const f = field(await observeForm(tab), 'scoped-file');
  assert.equal(f.semantic.attachment.purpose, 'resume'); assert.equal(f.application_question.completeness, 'known');
});
test('B2: open-shadow record context and slotted text are read without inspecting closed shadows', async t => {
  const { tab, page } = await fixture(t);
  await page.locator('body').evaluate(body => {
    const host = document.createElement('article'); host.id = 'open-host';
    host.attachShadow({ mode: 'open' }).innerHTML = '<fieldset id="shadow-record"><legend>Education shadow</legend><label for="shadow-school">School</label><input id="shadow-school"></fieldset>';
    body.append(host);
    const closed = document.createElement('article'); closed.attachShadow({ mode: 'closed' }).innerHTML = '<input id="closed-secret">'; body.append(closed);
  });
  const form = await observeForm(tab); assert.equal(field(form, 'shadow-school').semantic.record.status, 'identified');
  assert.equal(field(form, 'closed-secret'), undefined); assert.equal(form.semantic_coverage.closed_shadow, 'not_covered');
});
test('B2: empty native OPTION attribute is decoded as empty, not the fallback label', async t => {
  const { tab } = await fixture(t); const f = field(await observeForm(tab), 'school');
  assert.equal(f.value, ''); assert.equal(f.value_source, 'live_dom_snapshot');
  assert.equal(f.options[0].selected, true); assert.ok(Number.isInteger(f.options[0].dom_identity));
});
test('B3: resume a partially prepared record by retaining exact verified values', async t => {
  const { tab, page } = await fixture(t); await page.locator('#company').fill('Example Corp');
  const form = await observeForm(tab); const r = await operatePreparationSubgoal(tab, form, recordPlan(form));
  assert.equal(r.subgoal_result.status, 'verified'); assert.equal(r.subgoal_result.actions, 3);
  assert.equal(tab.writes.filter(w => w.selector === '[id="company"]').length, 0);
});
test('B3: invalid known native date value cannot verify a record', async t => {
  const { tab } = await fixture(t); const form = await observeForm(tab); const plan = recordPlan(form); plan.steps[2].value = 'not-a-date';
  await assert.rejects(operatePreparationSubgoal(tab, form, plan));
  assert.equal(tab.writes.some(w => w.selector === '[id="end"]'), false);
});
test('B3: second combobox pre-click preflight catches changed unrelated field before the click', async t => {
  const { tab, page } = await fixture(t); const form = await observeForm(tab);
  tab.beforeRead = async () => { if (tab.reads === 8) await page.locator('#city').fill('Changed before click'); };
  const r = await operatePreparationSubgoal(tab, form, schoolPlan(form));
  assert.equal(r.subgoal_result.status, 'parked');
  assert.equal(await page.evaluate(() => events.filter(e => e.event === 'option_click').length), 0);
});
test('B3: changed country dependencies invalidate the old plan without refilling remaining fields', async t => {
  const { tab, page } = await fixture(t);
  await page.locator('#job1').evaluate(e => e.insertAdjacentHTML('beforeend', '<label for="country">Country</label><select id="country"><option value="a">A</option><option value="b">B</option></select>'));
  await page.locator('#country').evaluate(e => e.addEventListener('change', () => document.querySelector('#title').required = false));
  const form = await observeForm(tab); const plan = recordPlan(form);
  // Keep all existing required fields present by first filling company outside the captured plan.
  await page.locator('#company').fill('Example Corp');
  const latest = await observeForm(tab); const p = recordPlan(latest);
  p.steps[0] = { operation: 'select_control', field_key: field(latest, 'country').field_key, value: 'b' };
  const r = await operatePreparationSubgoal(tab, latest, p);
  assert.equal(r.subgoal_result.status, 'parked'); assert.equal(r.subgoal_result.actions, 1);
  assert.equal(await page.locator('#title').inputValue(), '');
});

// The real Host and file queue consume the same production adapter on the real local DOM.
import { browserAdapter, createVisualHost } from './visual-bridge-host.mjs';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
const formBlock = content => content.filter(b => b.type === 'text').map(b => {
  try { return JSON.parse(b.text); } catch { return null; }
}).find(b => b?.form_state);
async function hostFixture(t, { phase = 'prepare', mode = 'playwright' } = {}) {
  const { tab, page } = await fixture(t);
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-b23-'));
  const adapter = browserAdapter(tab, { observationMode: mode, allowPreparationSubgoals: phase === 'prepare' });
  const host = await createVisualHost({ directory, adapter, phase, submission_authorized: phase === 'submit',
    target: { runtime: 'iab', tab_id: tab.id, application_url: await tab.url() } });
  t.after(async () => { await host.close(); await fs.rm(directory, { recursive: true, force: true }); });
  const request = async (operation, args = {}, observation_id = null) => {
    const id = randomUUID(), b = host.binding;
    await fs.writeFile(path.join(directory, 'pending', `${id}.json`), JSON.stringify({
      schema_version: 1, request_id: id, session_id: b.session_id, token_epoch: b.token_epoch, surface: b.surface, phase: b.phase,
      target: b.target, created_at: Date.now() / 1000, deadline_at: Date.now() / 1000 + 20,
      operation, arguments: args, observation_id }));
    assert.ok((await host.peek()).some(r => r.request_id === id));
    return host.execute(id); // The test explicitly admits this one reviewed request; no pump.
  };
  return { tab, page, host, adapter, request, directory };
}
for (const mode of ['playwright', 'dom_cua']) test(`B3 Host ${mode}: one admitted request completes the bounded school goal`, async t => {
  const { request, page, host } = await hostFixture(t, { mode });
  const observation = await request('observe');
  const r = await request('prepare_subgoal', schoolPlan(formBlock(observation.content).form_state), observation.observation_id);
  assert.equal(r.ok, true); assert.equal(formBlock(r.content).subgoal_result.status, 'verified');
  assert.equal(await page.locator('.select__single-value').innerText(), 'Example University');
  assert.equal(host.binding.submission_authorized, false);
});
test('B3 Host: delayed final validation invalidates subgoal success', async t => {
  const { request, page, tab } = await hostFixture(t);
  const observation = await request('observe');
  const original = tab.playwright.domSnapshot;
  tab.playwright.domSnapshot = async () => {
    if (await page.locator('.select__single-value').innerText() === 'Example University') await page.locator('#schoolSearch').evaluate(e => e.setAttribute('aria-invalid', 'true'));
    return original();
  };
  const r = await request('prepare_subgoal', schoolPlan(formBlock(observation.content).form_state), observation.observation_id);
  assert.equal(r.ok, true); assert.equal(formBlock(r.content).subgoal_result.status, 'parked');
  assert.equal(formBlock(r.content).subgoal_result.reason, 'delayed_readback_changed');
});
test('B3 Host: loss of browser after an actual input stops as outcome_unknown', async t => {
  const { request, page, tab, directory, host } = await hostFixture(t);
  const observation = await request('observe');
  const locator = tab.playwright.locator;
  tab.playwright.locator = selector => {
    const actual = locator(selector), fill = actual.fill;
    actual.fill = async (...args) => { await fill(...args); await page.close(); throw new Error('Simulated lost response after real input'); };
    return actual;
  };
  const r = await request('prepare_subgoal', recordPlan(formBlock(observation.content).form_state), observation.observation_id);
  assert.equal(r.ok, false); assert.equal(r.outcome, 'outcome_unknown');
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'stopped');
  await assert.rejects(host.execute(randomUUID()), /unavailable/i);
  assert.equal(tab.writes.length, 1);
});
for (const phase of ['discovery', 'submit']) test(`B3 Host: ${phase} cannot execute a preparation subgoal`, async t => {
  const { request, tab } = await hostFixture(t, { phase }); const o = await request('observe');
  const r = await request('prepare_subgoal', schoolPlan(formBlock(o.content).form_state), o.observation_id);
  assert.equal(r.ok, false); assert.equal(r.outcome, 'failed'); assert.equal(tab.writes.length, 0);
});
test('B3 Host: stale observation cannot replay a verified goal', async t => {
  const { request, tab } = await hostFixture(t); const o = await request('observe');
  const plan = schoolPlan(formBlock(o.content).form_state);
  const first = await request('prepare_subgoal', plan, o.observation_id); assert.equal(first.ok, true);
  const writes = tab.writes.length;
  const stale = await request('prepare_subgoal', plan, o.observation_id);
  assert.equal(stale.ok, false); assert.equal(tab.writes.length, writes);
});

test('B3: identical local school goal uses one reviewed request instead of three, with the same committed result', async t => {
  const baseline = await hostFixture(t); let b = await baseline.request('observe');
  const key = field(formBlock(b.content).form_state, 'schoolSearch').field_key;
  b = await baseline.request('open_control', { field_key: key }, b.observation_id);
  b = await baseline.request('search_control', { field_key: key, value: 'Example University' }, b.observation_id);
  b = await baseline.request('select_control', { field_key: key, value: 'Example University' }, b.observation_id);
  assert.equal(formBlock(b.content).control_result.persisted, true);
  const candidate = await hostFixture(t); let c = await candidate.request('observe');
  c = await candidate.request('prepare_subgoal', schoolPlan(formBlock(c.content).form_state), c.observation_id);
  assert.equal(formBlock(c.content).subgoal_result.status, 'verified');
  assert.equal(await baseline.page.locator('.select__single-value').innerText(), await candidate.page.locator('.select__single-value').innerText());
  const requests = [baseline.host.metrics().lifetime.operations - 1, candidate.host.metrics().lifetime.operations - 1];
  assert.deepEqual(requests, [3, 1]);
  assert.ok(candidate.tab.reads <= baseline.tab.reads);
  t.diagnostic(JSON.stringify({ controlled_actions_not_model_benchmark: true,
    primitive_requests: requests[0], subgoal_requests: requests[1],
    primitive_form_reads: baseline.tab.reads, subgoal_form_reads: candidate.tab.reads }));
});
