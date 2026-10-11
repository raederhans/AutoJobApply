import assert from 'node:assert/strict';
import test from 'node:test';
import { operateFieldBatch } from './browser-field-batch.mjs';
import { ControlNotReady } from './browser-form-state.mjs';

// Deterministic browser-state double; production batch and control adapters run.
// This does not execute a DOM parser, a model or a real employer application.
function fixture({ afterWrite = () => {}, beforeRead = () => {} } = {}) {
  const initial = { page_url: 'https://batch-lab.test/application/1', protected_count: 0,
    coverage: { scope: 'visible_top_document_open_shadow', iframe_count: 0, open_shadow_count: 0 },
    question_coverage: { scope: 'visible_top_document_open_shadow', whole_form: 'unknown',
      iframe_count: 0, fields_truncated: false, page_only: true },
    fields: ['a', 'b'].map(key => ({ field_key: key, selector: key, label: key,
      control: 'text', value: '', options: [], disabled: false, readonly: false,
      required: false, required_source: 'not_asserted', invalid: false,
      validation_message: '', value_source: 'dom_read',
      application_question: { text: 'Description', help_text: '', section_path: ['Projects'],
        constraints: [], completeness: 'known' } })) };
  const state = structuredClone(initial);
  const writes = [];
  let reads = 0;
  const tab = { playwright: {
    evaluate: async () => { beforeRead(state, ++reads); return structuredClone(state); },
    locator: key => ({ count: async () => 1,
      fill: async text => { state.fields.find(f => f.field_key === key).value = text;
        writes.push(key); afterWrite(state, key); },
      press: async () => {},
    }),
  } };
  const steps = ['a', 'b'].map(key => ({ operation: 'fill_control', field_key: key, value: key.toUpperCase() }));
  return { initial, state, writes, tab, steps, reads: () => reads };
}

const dependencyChanges = [
  ['required condition', s => { s.fields[1].required = true; }],
  ['required evidence source', s => { s.fields[1].required_source = 'aria'; }],
  ['question text', s => { s.fields[1].application_question.text = 'Employment description'; }],
  ['record purpose', s => { s.fields[1].application_question.section_path = ['Employment']; }],
  ['question instructions', s => { s.fields[1].application_question.help_text = 'Paid employment only'; }],
  ['question constraints', s => { s.fields[1].application_question.constraints = [{ kind: 'max', unit: 'utf16', value: 1 }]; }],
  ['question completeness', s => { s.fields[1].application_question.completeness = 'partial'; }],
  ['validation state', s => { s.fields[1].invalid = true; }],
  ['validation message', s => { s.fields[1].validation_message = 'Review dependency'; }],
  ['value visibility', s => { s.fields[1].value_source = 'unavailable'; }],
  ['frame coverage', s => { s.coverage.iframe_count = 1; }],
  ['question truncation', s => { s.question_coverage.fields_truncated = true; }],
];
for (const [name, mutate] of dependencyChanges) {
  test(`a ${name} change after input parks before the next field`, async () => {
    const r = fixture({ afterWrite: (s, key) => { if (key === 'a') mutate(s); } });
    const out = await operateFieldBatch(r.tab, r.initial, r.steps);
    assert.equal(out.batch_result.status, 'parked');
    assert.deepEqual(r.writes, ['a']);
    // Independent final state, not the returned persisted flags, is the oracle.
    assert.equal(r.state.fields[0].value, 'A');
    assert.equal(r.state.fields[1].value, '');
    assert.equal(out.batch_result.reobserve_required, true);
  });
}

test('later input invalidating an earlier field cannot return verified', async () => {
  const r = fixture({ afterWrite: (s, key) => {
    if (key === 'b') { s.fields[0].invalid = true; s.fields[0].validation_message = 'Cross-field conflict'; }
  } });
  const out = await operateFieldBatch(r.tab, r.initial, r.steps);
  assert.deepEqual(r.writes, ['a', 'b']);
  assert.equal(r.state.fields[0].invalid, true);
  assert.equal(out.batch_result.status, 'parked');
});

for (const [name, mutate] of [dependencyChanges[0], dependencyChanges[3], dependencyChanges[7],
  ['parser overwrites a previous value', s => { s.fields[0].value = 'parser value'; }]]) {
  test(`${name} between readback and the next preflight prevents that write`, async () => {
    const r = fixture({ beforeRead: (s, read) => { if (read === 3) mutate(s); } });
    const out = await operateFieldBatch(r.tab, r.initial, r.steps);
    assert.equal(out.batch_result.status, 'parked');
    assert.deepEqual(r.writes, ['a']);
    assert.equal(r.state.fields[1].value, '');
  });
}

test('dependency change at first preflight produces zero writes', async () => {
  const r = fixture({ beforeRead: (s, read) => {
    if (read === 1) s.fields[0].application_question.section_path = ['Employment'];
  } });
  await assert.rejects(operateFieldBatch(r.tab, r.initial, r.steps), ControlNotReady);
  assert.deepEqual(r.writes, []);
});

test('own validation clearing is expected and causes no extra observation', async () => {
  const r = fixture({ afterWrite: (s, key) => {
    const f = s.fields.find(f => f.field_key === key); f.invalid = false; f.validation_message = '';
  } });
  for (const f of [...r.initial.fields, ...r.state.fields]) { f.invalid = true; f.validation_message = 'Required'; }
  const out = await operateFieldBatch(r.tab, r.initial, r.steps);
  assert.equal(out.batch_result.status, 'verified');
  assert.deepEqual(r.state.fields.map(f => [f.value, f.invalid]), [['A', false], ['B', false]]);
  assert.equal(r.reads(), 4); // Existing preflight/readback is reused, not doubled.
  assert.equal(out.batch_result.immediate_readback_only, true);
});

test('unresolved whole-form coverage is not silently upgraded or a blanket fill ban', async () => {
  const r = fixture();
  const out = await operateFieldBatch(r.tab, r.initial, r.steps);
  assert.equal(out.batch_result.status, 'verified');
  assert.equal(out.observation.question_coverage.whole_form, 'unknown');
  assert.equal(out.batch_result.immediate_readback_only, true);
  assert.equal(r.reads(), 4);
});

test('partial progress is not replayed on unknown input outcome', async () => {
  const r = fixture();
  r.tab.playwright.locator = key => ({ count: async () => 1, press: async () => {},
    fill: async text => { r.writes.push(key); r.state.fields.find(f => f.field_key === key).value = text;
      if (key === 'b') throw new Error('Synthetic connection lost after input'); },
  });
  await assert.rejects(operateFieldBatch(r.tab, r.initial, r.steps), /connection lost/);
  assert.deepEqual(r.writes, ['a', 'b']);
});
