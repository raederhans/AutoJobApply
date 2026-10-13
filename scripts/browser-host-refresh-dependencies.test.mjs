/** Host-boundary regressions with controlled observations, not DOM/model benchmarks.
 * Run: node --experimental-test-module-mocks --test <this file>
 * The form-state boundary is mocked; browserAdapter, field batch, queue protocol
 * and observation feedback are production modules. No browser or network starts.
 */
import assert from 'node:assert/strict';
import { mock, test } from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { ControlNotReady, createInputGuard } from './browser-form-state.mjs';

const copy = value => structuredClone(value);
const structuralState = form => JSON.stringify(form?.fields.map(f =>
  [f.field_key, f.selector, f.control, f.dom_identity, f.options]));
const structureChanges = (before, after) => ({ changed: !!before && structuralState(before) !== structuralState(after) });
async function observeForm(tab) {
  tab.reads++;
  if (tab.failRead) throw Error('Synthetic observation transport failed');
  return copy(tab.form);
}
async function operateObservedControl(tab, _before, operation, args, beforeInput) {
  const fresh = await observeForm(tab);
  beforeInput?.(fresh);
  const field = tab.form.fields.find(f => f.field_key === args.field_key);
  if (!field) throw new ControlNotReady('Synthetic missing control');
  tab.writes.push({ operation, field_key: args.field_key, value: args.value });
  field.value = args.value;
  if (operation === 'select_control') field.selected_display = args.value;
  field.invalid = false;
  field.validation_message = '';
  tab.afterWrite?.(field);
  const observation = await observeForm(tab);
  return { observation, field_key: args.field_key, persisted: !field.invalid,
    invalid: field.invalid, validation_message: field.validation_message };
}
mock.module(new URL('./browser-form-state.mjs', import.meta.url).href, {
  namedExports: { observeForm, operateObservedControl, ControlNotReady, createInputGuard, structureChanges,
    changedFields: () => [] },
});
const { browserAdapter, createVisualHost } = await import('./visual-bridge-host.mjs');

function fixture() {
  const field = (key, label) => ({ field_key: key, selector: key, label,
    group_key: 'record-education', group: 'Education', control: 'text', dom_identity: key,
    value: '', value_source: 'dom', selection_source: 'dom', checked: false,
    selected_display: '', selected_values: [], files: [], options: [],
    disabled: false, readonly: false, required: true, required_source: 'native',
    invalid: false, validation_message: '',
    application_question: { text: label, help_text: '', section_path: ['Education'],
      constraints: { max_length: 100 }, completeness: 'complete', language: 'en' },
  });
  const country = field('#country', 'Country');
  country.control = 'select';
  country.options = [{ value: 'Example', label: 'Example', disabled: false }];
  const tab = { id: randomUUID(), reads: 0, snapshots: 0, writes: [],
    form: { page_url: 'https://ats.example.test/application/controlled', protected_count: 0,
      coverage: { scope: 'top_and_open_shadow', iframe_count: 0, open_shadow_count: 0 },
      question_coverage: { scope: 'page', page_only: true, whole_form: false,
        iframe_count: 0, fields_truncated: false },
      fields: [field('#school', 'School'), country, field('#city', 'City')] },
    async url() { return this.form.page_url; },
    async title() { return 'Controlled application'; },
    async screenshot() { return Buffer.from('fixture'); },
    dom_cua: { async get_visible_dom() { return ''; } },
    playwright: { evaluate() {}, locator() {}, async domSnapshot() {
      tab.snapshots++;
      tab.beforeSnapshot?.();
      return '';
    } },
  };
  return tab;
}
const steps = [
  { operation: 'fill_control', field_key: '#school', value: 'Example University' },
  { operation: 'select_control', field_key: '#country', value: 'Example' },
];
function resultBlock(content) {
  return content.filter(b => b.type === 'text').map(b => {
    try { return JSON.parse(b.text); } catch { return null; }
  }).find(b => b?.form_state);
}

const mutations = [
  ['requiredness', f => { f.fields[0].required = false; }],
  ['requirement provenance', f => { f.fields[0].required_source = 'not_asserted'; }],
  ['record purpose', f => { f.fields[0].application_question.section_path = ['Employment']; }],
  ['question constraints', f => { f.fields[0].application_question.constraints.max_length = 5; }],
  ['question help', f => { f.fields[0].application_question.help_text = 'New question instructions'; }],
  ['question completeness', f => { f.fields[0].application_question.completeness = 'partial'; }],
  ['same-value invalidation', f => { f.fields[0].invalid = true; f.fields[0].validation_message = 'Rejected'; }],
  ['other-field validation', f => { f.fields[2].invalid = true; }],
  ['parsed non-batch value', f => { f.fields[2].value = 'Changed by parser'; }],
  ['iframe coverage', f => { f.coverage.iframe_count = 1; }],
  ['question truncation', f => { f.question_coverage.fields_truncated = true; }],
  ['selection evidence unavailable', f => { f.fields[1].selection_source = 'unavailable'; }],
  ['selected display', f => { f.fields[1].selected_display = 'Another country'; }],
  ['disabled control', f => { f.fields[0].disabled = true; }],
  ['protected scope', f => { f.protected_count = 1; }],
  ['removed record field', f => { f.fields.pop(); }],
  ['replaced DOM node', f => { f.fields[0].dom_identity = 'replacement'; }],
  ['changed options', f => { f.fields[1].options.push({ value: 'Other', label: 'Other' }); }],
  ['changed batch value', f => { f.fields[0].value = 'Replacement'; }],
  ['changed page', f => { f.page_url += '/other'; }],
];
for (const observationMode of ['dom_cua', 'playwright']) {
  for (const [name, mutate] of mutations) {
    test(`${observationMode}: delayed ${name} cannot retain batch verification`, async () => {
      const tab = fixture();
      const adapter = browserAdapter(tab, { allowFieldBatch: true, reuseFormObservations: true, observationMode });
      await adapter.observe();
      await adapter.act('fill_batch', { steps });
      const originalWrites = copy(tab.writes);
      assert.equal(originalWrites.length, 2);
      mutate(tab.form); // Between the final control readback and Host full refresh.
      const reply = resultBlock(await adapter.observe());
      assert.equal(reply.batch_result.status, 'parked');
      assert.equal(reply.batch_result.reason, 'delayed_readback_changed');
      assert.equal(reply.batch_result.reobserve_required, true);
      assert.equal(reply.batch_result.completed, 2);
      assert.equal(reply.batch_result.results.length, 2); // Preserve historical progress.
      assert.deepEqual(tab.writes, originalWrites); // No repair or retry is hidden in observation.
      assert.equal(tab.reads, 6); // Initial + two preflight/readbacks + the existing final refresh.
    });
  }
  test(`${observationMode}: stable reply remains immediate-only and is consumed once`, async () => {
    const tab = fixture();
    const adapter = browserAdapter(tab, { allowFieldBatch: true, reuseFormObservations: true, observationMode });
    await adapter.observe();
    await adapter.act('fill_batch', { steps });
    const reply = resultBlock(await adapter.observe());
    assert.equal(reply.batch_result.status, 'verified');
    assert.equal(reply.batch_result.immediate_readback_only, true);
    assert.equal(tab.reads, 6);
    assert.equal(resultBlock(await adapter.observe()).batch_result, null);
    assert.equal(tab.writes.length, 2);
  });
}

test('an already parked partial batch is not promoted when its later page settles', async () => {
  const tab = fixture();
  tab.afterWrite = () => { tab.form.fields[2].required = false; };
  const adapter = browserAdapter(tab, { allowFieldBatch: true });
  await adapter.observe();
  await adapter.act('fill_batch', { steps });
  tab.form.fields[2].required = true;
  const reply = resultBlock(await adapter.observe());
  assert.equal(reply.batch_result.status, 'parked');
  assert.equal(reply.batch_result.completed, 1);
  assert.equal(tab.writes.length, 1);
  assert.equal(tab.form.fields[1].value, '');
});

test('delayed dependency diagnostics are bounded and do not copy question or validation text', async () => {
  const tab = fixture();
  for (let i = 0; i < 65; i++) {
    const field = copy(tab.form.fields[2]);
    field.field_key = field.selector = field.dom_identity = `#extra-${i}`;
    tab.form.fields.push(field);
  }
  const adapter = browserAdapter(tab, { allowFieldBatch: true });
  await adapter.observe();
  await adapter.act('fill_batch', { steps });
  for (const field of tab.form.fields) {
    field.application_question.help_text = 'PRIVATE_QUESTION_SENTINEL';
    field.validation_message = 'PRIVATE_VALIDATION_SENTINEL';
  }
  const report = resultBlock(await adapter.observe()).batch_result;
  assert.equal(report.status, 'parked');
  assert.equal(report.dependency_changes.counts.requirements_changed, 68);
  assert.equal(report.dependency_changes.counts.validation_changed, 68);
  assert.equal(report.dependency_changes.field_keys.requirements_changed.length, 40);
  assert.equal(report.dependency_changes.truncated, true);
  assert.doesNotMatch(JSON.stringify(report.dependency_changes), /PRIVATE_/);
});

async function hostFixture(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-refresh-'));
  const tab = fixture();
  const adapter = browserAdapter(tab, { allowFieldBatch: true, reuseFormObservations: true, observationMode: 'playwright' });
  const host = await createVisualHost({ directory, adapter,
    target: { runtime: 'iab', tab_id: tab.id, application_url: await tab.url() } });
  t.after(async () => { await host.close(); await fs.rm(directory, { recursive: true, force: true }); });
  const request = async (operation, args = {}, observation_id = null) => {
    const id = randomUUID();
    const b = host.binding;
    await fs.writeFile(path.join(directory, 'pending', `${id}.json`), JSON.stringify({
      schema_version: 1, request_id: id, session_id: b.session_id, token_epoch: b.token_epoch,
      surface: b.surface, phase: b.phase, target: b.target, observation_id,
      created_at: Date.now() / 1000, deadline_at: Date.now() / 1000 + 30,
      operation, arguments: args,
    }));
    return host.execute(id);
  };
  return { host, tab, directory, request };
}

test('actual Host queue returns parked after delayed invalidation, with no write replay', async t => {
  const { host, tab, request } = await hostFixture(t);
  const observed = await request('observe');
  tab.beforeSnapshot = () => { if (tab.writes.length === 2) tab.form.fields[0].invalid = true; };
  const response = await request('fill_batch', { steps }, observed.observation_id);
  assert.equal(response.ok, true); // Transport completed, not application readiness.
  assert.equal(response.outcome, 'completed');
  assert.equal(resultBlock(response.content).batch_result.status, 'parked');
  assert.equal(tab.writes.length, 2);
  assert.equal(host.binding.submission_authorized, false);
  const stale = await request('fill_batch', { steps }, observed.observation_id);
  assert.equal(stale.ok, false);
  assert.equal(stale.outcome, 'failed');
  assert.equal(tab.writes.length, 2);
});

test('lost final observation after input stops the Host as outcome_unknown', async t => {
  const { host, tab, directory, request } = await hostFixture(t);
  const observed = await request('observe');
  tab.beforeSnapshot = () => { if (tab.writes.length === 2) tab.failRead = true; };
  const response = await request('fill_batch', { steps }, observed.observation_id);
  assert.equal(response.ok, false);
  assert.equal(response.outcome, 'outcome_unknown');
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'), 'utf8')).status, 'stopped');
  await assert.rejects(host.execute(randomUUID()), /unavailable/i);
  assert.equal(tab.writes.length, 2);
  assert.equal(host.binding.submission_authorized, false);
});
