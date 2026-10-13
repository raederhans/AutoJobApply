/** Synthetic clock/surface; real Host, journal, browser adapter and control helpers.
 * No employer traffic, applicant data, model calls or mocked production modules.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { createInAppBrowserHost } from './visual-bridge-host.mjs';

const sha = raw => createHash('sha256').update(raw).digest('hex');
const write = (file, value) => fs.writeFile(file, JSON.stringify(value));
const field = (key, extra = {}) => ({ field_key: key, selector: `#${key}`, label: key,
  group: 'Employment', group_key: 'record', control: 'text', native_tag: 'input',
  value: '', value_source: 'dom_read', required: false, disabled: false, readonly: false,
  invalid: false, validation_message: '', options: [], files: [], selected_display: [],
  application_question: { text: key, completeness: 'known', text_sources: [{ source: 'label', text: key }] },
  record_path: [{ key: 'record', label: 'Employment', label_status: 'identified', dom_identity: 1 }],
  ...extra });

async function fixture(t, { continuity = true, first = {} } = {}) {
  let clock = Date.now();
  t.mock.method(Date, 'now', () => clock);
  const artifactRoot = process.env.APPLYPILOT_HOST_DEADLINE_ARTIFACTS;
  const directory = await fs.mkdtemp(path.join(artifactRoot || os.tmpdir(), 'applypilot-host-deadline-'));
  const file = path.join(directory, 'synthetic.txt');
  await fs.writeFile(file, 'Synthetic upload fixture');
  const state = { page_url: 'https://example.test/apply/123', protected_count: 0,
    coverage: { scope: 'visible_top_document_open_shadow', open_shadow_count: 0, iframe_count: 0 },
    fields: [field('a', first), field('b'), field('resume', { control: 'file' })] };
  const writes = [], hooks = {};
  let reads = 0;
  const record = async (action, selector, value) => {
    writes.push({ action, selector, at: Date.now() / 1000 });
    const current = state.fields.find(f => f.selector === selector);
    if (action === 'fill' || action === 'selectOption') current.value = value;
    if (action === 'setChecked') current.checked = value;
    await hooks.write?.(action, selector);
  };
  const tab = { id: randomUUID(), url: async () => state.page_url, title: async () => 'Synthetic form',
    dom_cua: {
      get_visible_dom: async () => '<input node_id=1 type="text" /><input node_id=2 type="file" />',
      click: args => record('click', args.node_id), type: args => record('type', null, args.text),
    },
    playwright: {
      domSnapshot: async () => '- textbox "a"\n- textbox "b"',
      evaluate: async () => { reads++; await hooks.read?.(reads); return structuredClone(state); },
      locator: selector => ({
        count: async () => { await hooks.count?.(selector); return 1; },
        fill: value => record('fill', selector, value), press: key => record('press', selector, key),
        selectOption: option => record('selectOption', selector, option.value),
        setChecked: value => record('setChecked', selector, value), click: () => record('click', selector),
      }),
      waitForEvent: async () => ({ setFiles: files => record('setFiles', '#resume', files) }),
    },
  };
  const host = await createInAppBrowserHost({ directory, tab, continuity, artifacts: { resume: file } });
  t.after(async () => {
    await host.close();
    if (!artifactRoot) {
      assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
      assert.ok(path.basename(directory).startsWith('applypilot-host-deadline-'));
      await fs.rm(directory, { recursive: true, force: true });
    }
  });
  const owner = { schema_version: 1, attempt_id: randomUUID(), job_url: state.page_url,
    host_session_id: host.binding.session_id, host_token_epoch: host.binding.token_epoch, tab_id: tab.id };
  if (continuity) await write(path.join(directory, 'continuity/owner.json'), owner);
  const envelope = (operation, args) => ({ ...host.binding, request_id: randomUUID(), operation,
    observation_id: 'reviewed', arguments: args, created_at: clock / 1000, deadline_at: clock / 1000 + 60 });
  async function prepare(operation = 'fill_control', args = { field_key: 'a', value: 'Synthetic' },
    { requestSeconds = 60, permitSeconds = requestSeconds } = {}) {
    const request = envelope(operation, args);
    request.deadline_at = clock / 1000 + requestSeconds;
    if (!continuity) {
      const observe = envelope('observe', {});
      await write(path.join(directory, 'pending', `${observe.request_id}.json`), observe);
      request.observation_id = (await host.execute(observe.request_id)).observation_id;
    }
    await write(path.join(directory, 'pending', `${request.request_id}.json`), request);
    if (continuity) {
      const review = await host.review(request.request_id);
      await write(path.join(directory, 'continuity/permits', `${request.request_id}.json`), {
        ...owner, request_id: request.request_id, review_id: review.review_id,
        request_sha256: sha(await fs.readFile(path.join(directory, 'pending', `${request.request_id}.json`))),
        review_sha256: sha(await fs.readFile(path.join(directory, 'continuity/reviews', `${review.review_id}.json`))),
        expires_at: clock / 1000 + permitSeconds,
      });
    }
    const deadline = clock / 1000 + Math.min(requestSeconds, continuity ? permitSeconds : requestSeconds);
    return { request, deadline, expire: () => { clock = deadline * 1000; } };
  }
  async function execute(queued) {
    const delivery = await host.execute(queued.request.request_id);
    if (!continuity) return delivery;
    assert.equal(delivery.awaiting_owner, true);
    return JSON.parse(await fs.readFile(path.join(directory, 'continuity/completed', `${queued.request.request_id}.json`))).feedback;
  }
  async function noReplay(queued) {
    const id = queued.request.request_id;
    const output = path.join(directory, continuity ? 'continuity/completed' : 'responses', `${id}.json`);
    const bytes = await fs.readFile(output);
    const previous = structuredClone(writes);
    await assert.rejects(host.execute(id));
    assert.deepEqual(writes, previous);
    assert.deepEqual(await fs.readFile(output), bytes);
    if (continuity) {
      await fs.access(path.join(directory, 'continuity/used', `${id}.json`));
      await assert.rejects(fs.access(path.join(directory, 'continuity/permits', `${id}.json`)), { code: 'ENOENT' });
      await assert.rejects(fs.access(path.join(directory, 'responses', `${id}.json`)), { code: 'ENOENT' });
    }
    t.diagnostic(JSON.stringify({ synthetic: true, deadline: queued.deadline, writes, output }));
  }
  return { state, writes, hooks, host, directory, prepare, execute, noReplay, get reads() { return reads; } };
}

const report = feedback => feedback.content.flatMap(block => {
  try { const value = JSON.parse(block.text); return value.form_state ? [value] : []; }
  catch { return []; }
})[0];
const steps = ['a', 'b'].map(field_key => ({ operation: 'fill_control', field_key, value: field_key.toUpperCase() }));

test('real Host rejects expiry in adapter preflight, using the earlier permit or request deadline', async t => {
  for (const continuity of [true, false]) for (const boundary of ['observation', 'locator']) {
    for (const requestFirst of continuity ? [false, true] : [true]) {
      await t.test(`${continuity ? 'continuity' : 'ordinary'} ${boundary} ${requestFirst ? 'request' : 'permit'}`, async t => {
        const f = await fixture(t, { continuity });
        const queued = await f.prepare(undefined, undefined,
          { requestSeconds: requestFirst ? 30 : 60, permitSeconds: requestFirst ? 60 : 30 });
        const preflight = f.reads + (continuity ? 2 : 1);
        if (boundary === 'observation') f.hooks.read = n => { if (n === preflight) queued.expire(); };
        else f.hooks.count = queued.expire;
        const feedback = await f.execute(queued);
        assert.equal(feedback.outcome, 'failed');
        assert.match(feedback.content[0].text, /deadline reached before input/);
        assert.deepEqual(f.writes, []);
        await f.noReplay(queued);
      });
    }
  }
});

test('batch parks with verified first-field progress when the next preflight expires', async t => {
  for (const boundary of ['observation', 'locator']) await t.test(boundary, async t => {
    const f = await fixture(t);
    const queued = await f.prepare('fill_batch', { steps }, { permitSeconds: 30 });
    const secondPreflight = f.reads + 4;
    if (boundary === 'observation') f.hooks.read = n => { if (n === secondPreflight) queued.expire(); };
    else f.hooks.count = selector => { if (selector === '#b') queued.expire(); };
    const feedback = await f.execute(queued);
    const batch = report(feedback).batch_result;
    assert.equal(feedback.outcome, 'completed'); // Transport completed; the batch did not.
    assert.equal(batch.status, 'parked');
    assert.equal(batch.reason, 'deadline_reached');
    assert.equal(batch.completed, 1);
    assert.equal(batch.requested, 2);
    assert.equal(batch.results[0].persisted, true);
    assert.equal(batch.reobserve_required, true);
    assert.deepEqual(f.writes.map(w => [w.action, w.selector]), [['fill', '#a'], ['press', '#a']]);
    assert.deepEqual(f.state.fields.slice(0, 2).map(f => f.value), ['A', '']);
    await f.noReplay(queued);
  });
});

test('all observed input kinds check expiry after their last asynchronous preparation', async t => {
  const option = { value: 'choice', label: 'Choice', selector: '#option' };
  const cases = [
    ['native select', 'select_control', { control: 'select', native_tag: 'select', options: [option] }, { value: 'choice' }],
    ['custom option', 'select_control', { control: 'combobox', options: [option] }, { value: 'choice' }],
    ['open menu', 'open_control', { control: 'combobox' }, {}],
    ['search menu', 'search_control', { control: 'combobox', editable_combobox: true }, { value: 'query' }],
    ['checkbox', 'set_checked', { control: 'checkbox' }, { checked: true }],
    ['file control', 'upload_artifact', {}, { field_key: 'resume', artifact_id: 'resume' }],
    ['file node', 'upload_artifact', {}, { node_id: '2', artifact_id: 'resume' }],
  ];
  for (const [name, operation, first, args] of cases) await t.test(name, async t => {
    const f = await fixture(t, { first });
    const queued = await f.prepare(operation, operation === 'upload_artifact' ? args : { field_key: 'a', ...args }, { permitSeconds: 30 });
    if (name === 'custom option' || name === 'file node') {
      const lastRead = f.reads + (name === 'custom option' ? 3 : 2);
      f.hooks.read = n => { if (n === lastRead) queued.expire(); };
    } else f.hooks.count = queued.expire;
    const feedback = await f.execute(queued);
    assert.equal(feedback.outcome, 'failed');
    assert.match(feedback.content[0].text, /deadline reached before input/);
    assert.deepEqual(f.writes, []);
    await f.noReplay(queued);
  });
});

test('expiry between actual inputs preserves unknown effects and stops the Host', async t => {
  for (const operation of ['fill_control', 'fill_batch', 'prepare_subgoal', 'type_text', 'upload_artifact']) await t.test(operation, async t => {
    const f = await fixture(t);
    const args = operation === 'fill_control' ? { field_key: 'a', value: 'Synthetic' }
      : operation === 'fill_batch' ? { steps }
      : operation === 'prepare_subgoal' ? { kind: 'record_fields', record_key: 'record', steps }
      : operation === 'type_text' ? { node_id: '1', text: 'Synthetic' }
      : { field_key: 'resume', artifact_id: 'resume' };
    const queued = await f.prepare(operation, args, { permitSeconds: 30 });
    f.hooks.write = queued.expire;
    const feedback = await f.execute(queued);
    assert.equal(feedback.outcome, 'outcome_unknown');
    assert.match(feedback.content[0].text, /deadline reached after earlier input/);
    assert.equal(f.writes.length, 1);
    assert.ok(f.writes.every(w => w.at < queued.deadline));
    assert.equal(JSON.parse(await fs.readFile(path.join(f.directory, 'host.json'))).status, 'stopped');
    const completion = JSON.parse(await fs.readFile(path.join(f.directory, 'continuity/completed', `${queued.request.request_id}.json`)));
    assert.equal(completion.inspection, undefined);
    await f.noReplay(queued);
  });
});

test('expiry during date focus readback forbids the next segmented Tab', async t => {
  const f = await fixture(t, { first: { control: 'date', focused: true } });
  const queued = await f.prepare('fill_control', { field_key: 'a', value: '2026-10-13' }, { permitSeconds: 30 });
  const focusRead = f.reads + 3;
  f.hooks.read = n => { if (n === focusRead) queued.expire(); };
  assert.equal((await f.execute(queued)).outcome, 'outcome_unknown');
  assert.deepEqual(f.writes.map(w => w.action), ['fill', 'press']);
  await f.noReplay(queued);
});

test('expiry after the last input still permits readback of the completed effect', async t => {
  const f = await fixture(t);
  const queued = await f.prepare();
  const readback = f.reads + 3;
  f.hooks.read = n => { if (n === readback) queued.expire(); };
  const feedback = await f.execute(queued);
  assert.equal(feedback.outcome, 'completed');
  assert.equal(report(feedback).control_result.persisted, true);
  assert.equal(f.writes.length, 2);
  assert.ok(f.writes.every(w => w.at < queued.deadline));
  await f.noReplay(queued);
});

test('subgoal checks the permit deadline again after the locator await', async t => {
  const f = await fixture(t);
  const queued = await f.prepare('prepare_subgoal', { kind: 'record_fields', record_key: 'record', steps }, { permitSeconds: 30 });
  f.hooks.count = queued.expire;
  const subgoal = report(await f.execute(queued)).subgoal_result;
  assert.equal(subgoal.status, 'parked');
  assert.equal(subgoal.actions, 0);
  assert.deepEqual(f.writes, []);
  await f.noReplay(queued);
});

test('unexpired single controls, batch and subgoal keep their successful behavior', async t => {
  for (const operation of ['fill_control', 'fill_batch', 'prepare_subgoal']) await t.test(operation, async t => {
    const f = await fixture(t);
    const args = operation === 'fill_control' ? { field_key: 'a', value: 'Synthetic' }
      : operation === 'fill_batch' ? { steps } : { kind: 'record_fields', record_key: 'record', steps };
    const queued = await f.prepare(operation, args, { permitSeconds: 30 });
    const feedback = await f.execute(queued);
    assert.equal(feedback.outcome, 'completed');
    const result = report(feedback);
    if (operation === 'fill_control') assert.equal(result.control_result.persisted, true);
    else assert.equal(result[operation === 'fill_batch' ? 'batch_result' : 'subgoal_result'].status, 'verified');
    assert.equal(f.writes.length, operation === 'fill_control' ? 2 : 4);
    assert.ok(f.writes.every(w => w.at < queued.deadline));
    await f.noReplay(queued);
  });
});
