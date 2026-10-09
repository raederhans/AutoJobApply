import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { createVisualHost, createInAppBrowserHost, browserAdapter } from './visual-bridge-host.mjs';
import { ControlNotReady } from './browser-form-state.mjs';

test('an unobservable target is never advertised as an active host', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-visual-probe-'));
  await assert.rejects(createVisualHost({
    directory, target: { tab_id: 'fixture' },
    adapter: { surface: 'computer_use', observe: async () => { throw Error('URL observation unavailable'); } },
  }), /URL observation unavailable/);
  await assert.rejects(fs.readFile(path.join(directory, 'host.json')), { code: 'ENOENT' });
});

test('IAB review, pause and resume keep the actual tab; navigation uses observed links only', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-iab-'));
  let current = 'https://jobs.example.test/job/1';
  let snapshot = '- heading "Job 1"\n- link "Guest":\n  - /url: /apply/1';
  const tab = {
    id: 'actual-7', url: async () => current, title: async () => 'Job 1',
    goto: async url => { current = url; snapshot = '- textbox "Name"'; },
    dom_cua: { get_visible_dom: async () => '<a node_id=1 href="/apply/1">Guest</a>' },
    playwright: { domSnapshot: async () => snapshot },
  };
  const host = await createInAppBrowserHost({ directory, tab });
  assert.deepEqual(host.binding.target, { runtime: 'iab', tab_id: tab.id, application_url: current });
  await assert.rejects(createInAppBrowserHost({ directory: directory + '-other', tab }), /active owner/);
  async function request(operation, observation_id, args = {}) {
    const request_id = randomUUID();
    await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
      ...host.binding, request_id, operation, observation_id, arguments: args, deadline_at: Date.now() / 1000 + 30,
    }));
    return host.execute(request_id);
  }
  const observation = await request('observe');
  await host.pause('auth_required');
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'paused');
  await host.resume();
  assert.equal((await request('navigate', observation.observation_id, { url: 'https://jobs.example.test/apply/1' })).ok, false);
  const fresh = await request('observe');
  assert.equal((await request('navigate', fresh.observation_id, { url: 'https://jobs.example.test/apply/1' })).ok, true);
  const review = await host.inspect();
  assert.equal(review.target.tab_id, tab.id);
  assert.equal(JSON.parse(review.content[0].text).page_url, 'https://jobs.example.test/apply/1');
  assert.equal(review.submission_authorized, false);
  assert.match(review.content[2].text, /Name/);
  const adapter = browserAdapter(tab);
  await adapter.observe();
  await assert.rejects(adapter.act('navigate', { url: 'https://unobserved.example.test' }), /current observation/);
  await host.close();
});

test('host rejects stale observations and stops after an interrupted input', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-visual-host-'));
  let inputs = 0;
  const adapter = {
    surface: 'browser',
    observe: async () => [{ type: 'text', text: 'fixture state' }],
    act: async () => { inputs++; throw Error('runtime stopped'); },
  };
  const host = await createVisualHost({ directory, adapter, target: { tab_id: 'fixture' } });
  async function request(operation, observation_id) {
    const request_id = randomUUID();
    await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
      ...host.binding, request_id, operation, observation_id, arguments: {}, deadline_at: Date.now() / 1000 + 30,
    }));
    return host.execute(request_id);
  }
  const first = await request('observe');
  host.invalidate();
  assert.equal((await request('click', first.observation_id)).ok, false);
  assert.equal(inputs, 0);
  const fresh = await request('observe');
  const interrupted = await request('click', fresh.observation_id);
  assert.equal(interrupted.outcome, 'outcome_unknown');
  assert.equal(inputs, 1);
  await assert.rejects(host.peek(), /closed/);
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'stopped');
});

test('targeted text entry focuses an observed input and refuses action controls', async () => {
  const actions = [];
  const adapter = browserAdapter({
    id: 'input-test', url: async () => 'https://example.test/form', title: async () => 'Form',
    dom_cua: {
      get_visible_dom: async () => '<input node_id=1 required="true" /><input node_id=2 type="submit" />',
      click: async args => actions.push(['click', args]),
      type: async args => actions.push(['type', args]),
    },
    playwright: { domSnapshot: async () => '- textbox "Test name"' },
  });
  await adapter.observe();
  await adapter.act('type_text', { node_id: '1', text: 'Test', mode: 'dom' });
  assert.deepEqual(actions, [['click', { node_id: '1' }], ['type', { text: 'Test' }]]);
  await assert.rejects(adapter.act('type_text', { node_id: '2', text: 'Test' }), /observed text input/);
  assert.equal(actions.length, 2);
});

test('a proven pre-input control rejection invalidates observation but allows recovery', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-control-recovery-'));
  const host = await createVisualHost({ directory, target: { tab_id: 'recovery' }, adapter: {
    surface: 'browser', observe: async () => [{ type: 'text', text: 'fixture' }],
    act: async () => { throw new ControlNotReady('Options changed; observe again'); },
  } });
  t.after(async () => { await host.close(); await fs.rm(directory, { recursive: true, force: true }); });
  async function request(operation, observation_id) {
    const request_id = randomUUID();
    await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
      ...host.binding, request_id, operation, observation_id, arguments: {}, deadline_at: Date.now() / 1000 + 30,
    }));
    return host.execute(request_id);
  }
  const observed = await request('observe');
  const rejected = await request('select_control', observed.observation_id);
  assert.equal(rejected.ok, false);
  assert.equal(rejected.outcome, 'failed');
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'active');
  const fresh = await request('observe');
  assert.equal(fresh.ok, true);
  assert.notEqual(fresh.observation_id, observed.observation_id);
});

test('submit attachment requires explicit authorization and inspection preserves it', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-submit-'));
  const tab = {
    id: 'submit-tab', url: async () => 'https://example.test/apply', title: async () => 'Apply',
    dom_cua: { get_visible_dom: async () => '<button node_id=1>Submit</button>' },
    playwright: { domSnapshot: async () => '- button "Submit"' },
  };
  for (const authorization of [false, 'true', 1]) {
    await assert.rejects(createInAppBrowserHost({ directory, tab, phase: 'submit', submission_authorized: authorization }), /explicit/);
  }
  await assert.rejects(createInAppBrowserHost({ directory, tab, phase: 'prepare', submission_authorized: true }), /requires submit phase/);
  const host = await createInAppBrowserHost({ directory, tab, phase: 'submit', submission_authorized: true });
  assert.equal(host.binding.submission_authorized, true);
  assert.equal((await host.inspect()).submission_authorized, true);
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).submission_authorized, true);
  await host.close();
});

test('artifact upload registers chooser before click and exposes only artifact references', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-upload-'));
  const file = path.join(directory, 'resume.pdf');
  await fs.writeFile(file, 'fixture');
  const calls = [];
  const adapter = browserAdapter({
    id: 'upload-tab', url: async () => 'https://example.test/apply', title: async () => 'Apply',
    dom_cua: {
      get_visible_dom: async () => '<button node_id=7>Upload resume</button>',
      click: async args => calls.push(['click', args]),
    },
    playwright: {
      domSnapshot: async () => '- button "Upload resume"',
      waitForEvent: (event, options) => {
        calls.push(['wait', event, options]);
        return Promise.resolve({ setFiles: async files => calls.push(['files', files]) });
      },
    },
  }, { artifacts: { resume: file, directory, missing: path.join(directory, 'absent.pdf') } });
  const observation = await adapter.observe();
  assert.deepEqual(JSON.parse(observation[0].text).artifact_ids, ['resume', 'directory', 'missing']);
  assert.equal(JSON.stringify(observation).includes(file), false);
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'unknown', node_id: '7' }), /Unknown artifact/);
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'resume', node_id: '8' }), /current DOM observation/);
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'directory', node_id: '7' }), /regular file/);
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'missing', node_id: '7' }), /Artifact is unavailable/);
  assert.deepEqual(calls, []);
  await adapter.act('upload_artifact', { artifact_id: 'resume', node_id: '7' });
  assert.deepEqual(calls, [
    ['wait', 'filechooser', { timeoutMs: 10000 }], ['click', { node_id: '7' }], ['files', [file]],
  ]);
  assert.deepEqual(JSON.parse((await adapter.observe())[0].text).upload_result, {
    artifact_id: 'resume', node_id: '7', status: 'file_selection_done', webpage_acceptance: 'unverified',
  });
});

test('failed chooser click does not leave an unhandled waiter rejection', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-chooser-error-'));
  const file = path.join(directory, 'resume.pdf');
  await fs.writeFile(file, 'fixture');
  let rejectWaiter;
  const adapter = browserAdapter({
    id: 'chooser-error', url: async () => 'https://example.test/apply', title: async () => 'Apply',
    dom_cua: {
      get_visible_dom: async () => '<button node_id=7>Upload</button>',
      click: async () => { throw Error('click stopped'); },
    },
    playwright: {
      domSnapshot: async () => '- button "Upload"',
      waitForEvent: () => new Promise((resolve, reject) => { rejectWaiter = reject; }),
    },
  }, { artifacts: { resume: file } });
  await adapter.observe();
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'resume', node_id: '7' }), /click stopped/);
  rejectWaiter(Error('chooser timeout'));
  await new Promise(resolve => setImmediate(resolve));
});

function playwrightOnlyTab() {
  const current = { page_url: 'https://example.test/apply', protected_count: 0, fields: [
    { field_key: '#city', selector: '#city', label: 'City', control: 'text', value: '', options: [],
      disabled: false, readonly: false, invalid: false },
    { field_key: '#country', selector: '#country', label: 'Country', control: 'select', value: '',
      options: [{ value: 'SG', label: 'Singapore', disabled: false }], disabled: false, readonly: false, invalid: false },
  ] };
  const writes = [];
  const tab = { id: randomUUID(), url: async () => current.page_url, title: async () => 'Apply', playwright: {
    domSnapshot: async () => '- textbox "City"\n- combobox "Country"\n- button "Submit"',
    evaluate: async () => structuredClone(current),
    locator: selector => ({ count: async () => 1,
      fill: async value => { writes.push(['fill', selector, value]); current.fields.find(f => f.selector === selector).value = value; },
      selectOption: async ({ value }) => { writes.push(['select', selector, value]); current.fields.find(f => f.selector === selector).value = value; },
      press: async key => writes.push(['press', selector, key]),
    }),
  } };
  return { tab, writes };
}

test('Playwright-only IAB attaches and verifies a routine batch without node or coordinate APIs', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-playwright-host-'));
  const { tab, writes } = playwrightOnlyTab();
  const host = await createInAppBrowserHost({ directory, tab, observationMode: 'playwright' });
  t.after(async () => { await host.close(); await fs.rm(directory, { recursive: true, force: true }); });
  async function request(operation, observation_id, args = {}) {
    const request_id = randomUUID();
    await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
      ...host.binding, request_id, operation, observation_id, arguments: args, deadline_at: Date.now() / 1000 + 30,
    }));
    return host.execute(request_id);
  }
  const observed = await request('observe');
  assert.equal(observed.ok, true);
  const filled = await request('fill_batch', observed.observation_id, { steps: [
    { operation: 'fill_control', field_key: '#city', value: 'Singapore' },
    { operation: 'select_control', field_key: '#country', value: 'SG' },
  ] });
  assert.equal(filled.ok, true);
  const report = filled.content.map(item => {
    try { return JSON.parse(item.text); } catch { return null; }
  }).find(item => item?.form_state);
  assert.equal(report.batch_result.status, 'verified');
  assert.equal(report.batch_result.completed, 2);
  assert.deepEqual(report.form_state.fields.map(f => f.value), ['Singapore', 'SG']);
  assert.deepEqual(writes, [['fill', '#city', 'Singapore'], ['press', '#city', 'Tab'],
    ['select', '#country', 'SG'], ['press', '#country', 'Tab']]);
  const review = await host.inspect();
  assert.equal(review.target.tab_id, tab.id);
  assert.equal(review.session_id, host.binding.session_id);
  assert.equal(review.submission_authorized, false);
  assert.match(review.content[1].text, /Submit/);
});

test('Playwright mode rejects unsupported actions before input and keeps host available', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-playwright-rejection-'));
  const { tab, writes } = playwrightOnlyTab();
  const host = await createInAppBrowserHost({ directory, tab, observationMode: 'playwright' });
  t.after(async () => { await host.close(); await fs.rm(directory, { recursive: true, force: true }); });
  async function request(operation, observation_id, args = {}) {
    const request_id = randomUUID();
    await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
      ...host.binding, request_id, operation, observation_id, arguments: args, deadline_at: Date.now() / 1000 + 30,
    }));
    return host.execute(request_id);
  }
  for (const [operation, args] of [
    ['click', { node_id: 'invented' }], ['click', { x: 10, y: 10 }], ['scroll', { x: 10, y: 10 }],
    ['type_text', { node_id: 'invented', text: 'Test' }], ['press_key', { key: 'Enter' }],
    ['navigate', { url: 'https://example.test/other' }], ['upload_artifact', { artifact_id: 'resume', node_id: 'invented' }],
  ]) {
    const observed = await request('observe');
    const rejected = await request(operation, observed.observation_id, args);
    assert.equal(rejected.ok, false, operation);
    assert.equal(rejected.outcome, 'failed', operation);
    assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'active');
    assert.deepEqual(writes, []);
  }
  const rejectedScreenshot = await request('observe', undefined, { mode: 'screenshot' });
  assert.equal(rejectedScreenshot.ok, false);
  assert.equal(rejectedScreenshot.outcome, 'failed');
  assert.equal((await request('observe')).ok, true);
});

test('explicit observation mode validation fails before an active host is advertised', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-playwright-missing-'));
  t.after(() => fs.rm(directory, { recursive: true, force: true }));
  const { tab } = playwrightOnlyTab();
  assert.throws(() => browserAdapter(tab, { observationMode: 'unknown' }), /Unsupported observationMode/);
  delete tab.playwright.evaluate;
  await assert.rejects(createInAppBrowserHost({ directory, tab, observationMode: 'playwright' }), /requires domSnapshot, evaluate and locator/);
  await assert.rejects(fs.readFile(path.join(directory, 'host.json')), { code: 'ENOENT' });
});

test('Playwright single-control readback retains a current page snapshot', async () => {
  const { tab, writes } = playwrightOnlyTab();
  const adapter = browserAdapter(tab, { observationMode: 'playwright', reuseFormObservations: true });
  await adapter.observe();
  const readback = await adapter.act('fill_control', { field_key: '#city', value: 'Singapore' });
  const observation = await adapter.observe({ actionResult: readback });
  assert.match(observation[1].text, /Submit/);
  const report = JSON.parse(observation[2].text);
  assert.equal(report.control_result.persisted, true);
  assert.equal(report.observation_feedback.form_readback_reused, true);
  assert.equal(report.observation_feedback.kind, 'action_readback_with_playwright_snapshot');
  assert.deepEqual(writes, [['fill', '#city', 'Singapore'], ['press', '#city', 'Tab']]);
});

function fileUploadTab({ failure, onCount, onFiles } = {}) {
  const state = { page_url: 'https://example.test/apply', protected_count: 0, fields: [{
    field_key: '#resume', selector: '#resume', label: 'Resume', group: 'Materials', group_key: '#materials',
    control: 'file', disabled: false, readonly: false, files: [], value: '', options: [],
  }] };
  const calls = [];
  let count = 1;
  let rejectWaiter;
  const tab = { id: randomUUID(), url: async () => state.page_url, title: async () => 'Apply', playwright: {
    domSnapshot: async () => '- button "Upload resume"\n- button "Submit"',
    evaluate: async () => structuredClone(state),
    locator: selector => ({
      count: async () => { onCount?.(state); return count; },
      click: async options => {
        calls.push(['click', selector, options]);
        if (failure === 'click') throw Error('click stopped');
      },
    }),
    waitForEvent: (event, options) => {
      calls.push(['wait', event, options]);
      if (failure === 'click') return new Promise((resolve, reject) => { rejectWaiter = reject; });
      if (failure === 'chooser') return Promise.reject(Error('chooser stopped'));
      return Promise.resolve({ setFiles: async files => {
        calls.push(['files', files]);
        if (failure === 'setFiles') throw Error('setFiles stopped');
        onFiles?.(state);
        // A chooser success intentionally supplies no webpage acceptance evidence.
      } });
    },
  } };
  return { tab, state, calls, setCount: value => { count = value; },
    rejectPending: () => rejectWaiter?.(Error('late chooser timeout')) };
}

async function uploadFiles(t) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-observed-upload-'));
  const closeHosts = [];
  t.after(async () => {
    for (const host of closeHosts) await host.close();
    await fs.rm(directory, { recursive: true, force: true });
  });
  const file = path.join(directory, 'fixture.pdf');
  await fs.writeFile(file, 'synthetic upload fixture');
  return { directory, file, closeHosts };
}

async function bridgeRequest(host, directory, operation, observation_id, args = {}) {
  const request_id = randomUUID();
  await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
    ...host.binding, request_id, operation, observation_id, arguments: args, deadline_at: Date.now() / 1000 + 30,
  }));
  return host.execute(request_id);
}

test('Playwright host uploads an observed file control and reports selection without acceptance', async t => {
  const { directory, file, closeHosts } = await uploadFiles(t);
  const { tab, calls } = fileUploadTab();
  const host = await createInAppBrowserHost({ directory, tab, observationMode: 'playwright', artifacts: { resume: file } });
  closeHosts.push(host);
  const observation = await bridgeRequest(host, directory, 'observe');
  const uploaded = await bridgeRequest(host, directory, 'upload_artifact', observation.observation_id,
    { artifact_id: 'resume', field_key: '#resume' });
  assert.equal(uploaded.ok, true);
  assert.equal(uploaded.outcome, 'completed');
  const context = JSON.parse(uploaded.content[1].text);
  assert.deepEqual(context.upload_result, { artifact_id: 'resume', field_key: '#resume',
    status: 'file_selection_done', webpage_acceptance: 'unverified' });
  assert.equal(JSON.stringify(uploaded).includes(file), false);
  assert.deepEqual(calls, [['wait', 'filechooser', { timeoutMs: 10000 }],
    ['click', '#resume', { timeoutMs: 10000 }], ['files', [file]]]);
  assert.equal(JSON.parse((await host.inspect()).content[0].text).upload_result, undefined);
});

test('observed file upload rejects unknown, stale, ambiguous and unavailable controls before input', async t => {
  const { file } = await uploadFiles(t);
  const cases = [
    ['unknown', fixture => {}, { field_key: '#invented' }],
    ['wrong original type', fixture => { fixture.state.fields[0].control = 'submit'; }, {}, true],
    ['changed type', fixture => { fixture.state.fields[0].control = 'text'; }],
    ['changed label', fixture => { fixture.state.fields[0].label = 'Cover letter'; }],
    ['changed selector', fixture => { fixture.state.fields[0].selector = '#submit'; }],
    ['changed group', fixture => { fixture.state.fields[0].group_key = '#other'; }],
    ['changed DOM identity', fixture => { fixture.state.fields[0].dom_identity = 'backend:202'; }, {}, false, 'backend:101'],
    ['missing known DOM identity', fixture => { delete fixture.state.fields[0].dom_identity; }, {}, false, 'backend:101'],
    ['new trigger', fixture => { fixture.state.fields[0].upload_trigger = { selector: '#attach', label: 'Attach', disabled: false }; }],
    ['changed URL', fixture => { fixture.state.page_url += '/other'; }],
    ['missing', fixture => { fixture.state.fields = []; }],
    ['disabled', fixture => { fixture.state.fields[0].disabled = true; }],
    ['readonly', fixture => { fixture.state.fields[0].readonly = true; }],
    ['duplicate observation', fixture => { fixture.state.fields.push({ ...fixture.state.fields[0] }); }, {}, true],
    ['duplicate fresh field', fixture => { fixture.state.fields.push({ ...fixture.state.fields[0] }); }],
    ['ambiguous locator', fixture => fixture.setCount(2)],
    ['missing locator', fixture => fixture.setCount(0)],
    ['both targets', fixture => {}, { node_id: '7' }],
    ['arbitrary selector', fixture => {}, { selector: '#resume' }],
    ['arbitrary path', fixture => {}, { path: file }],
  ];
  for (const [name, mutate, extra = {}, before = false, domIdentity] of cases) {
    await t.test(name, async () => {
      const fixture = fileUploadTab();
      if (domIdentity !== undefined) fixture.state.fields[0].dom_identity = domIdentity;
      const adapter = browserAdapter(fixture.tab, { observationMode: 'playwright', artifacts: { resume: file } });
      if (before) mutate(fixture);
      await adapter.observe();
      if (!before) mutate(fixture);
      await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume', ...extra }), ControlNotReady);
      assert.deepEqual(fixture.calls, []);
    });
  }
});

test('host rejects missing or nonfile artifacts before input and permits fresh observation', async t => {
  const { directory, file, closeHosts } = await uploadFiles(t);
  const { tab, calls } = fileUploadTab();
  const host = await createInAppBrowserHost({ directory, tab, observationMode: 'playwright', artifacts: {
    resume: file, missing: path.join(directory, 'missing.pdf'), directory, relative: 'fixture.pdf',
  } });
  closeHosts.push(host);
  for (const artifact_id of ['unknown', 'missing', 'directory', 'relative']) {
    const observation = await bridgeRequest(host, directory, 'observe');
    const rejected = await bridgeRequest(host, directory, 'upload_artifact', observation.observation_id,
      { artifact_id, field_key: '#resume' });
    assert.equal(rejected.ok, false);
    assert.equal(rejected.outcome, 'failed');
    assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'active');
    assert.deepEqual(calls, []);
  }
  const stale = await bridgeRequest(host, directory, 'observe');
  host.invalidate();
  const rejected = await bridgeRequest(host, directory, 'upload_artifact', stale.observation_id,
    { artifact_id: 'resume', field_key: '#resume' });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.outcome, 'failed');
  assert.deepEqual(calls, []);
});

for (const failure of ['click', 'chooser', 'setFiles']) {
  test(`upload ${failure} failure reports unknown, stops host and never reports success`, async t => {
    const { directory, file } = await uploadFiles(t);
    const fixture = fileUploadTab({ failure });
    const host = await createInAppBrowserHost({ directory, tab: fixture.tab, observationMode: 'playwright', artifacts: { resume: file } });
    const observation = await bridgeRequest(host, directory, 'observe');
    const result = await bridgeRequest(host, directory, 'upload_artifact', observation.observation_id,
      { artifact_id: 'resume', field_key: '#resume' });
    assert.equal(result.ok, false);
    assert.equal(result.outcome, 'outcome_unknown');
    assert.equal(JSON.stringify(result).includes('file_selection_done'), false);
    assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'stopped');
    await assert.rejects(host.peek(), /closed/);
    fixture.rejectPending();
    await new Promise(resolve => setImmediate(resolve));
    assert.equal(fixture.calls.filter(call => call[0] === 'click').length, 1);
    assert.equal(fixture.calls.filter(call => call[0] === 'files').length, failure === 'setFiles' ? 1 : 0);
    await host.close();
  });
}

test('two concurrent upload adapters keep their artifact allowlists and chooser state separate', async t => {
  const { directory, file } = await uploadFiles(t);
  const secondFile = path.join(directory, 'second.pdf');
  await fs.writeFile(secondFile, 'second synthetic upload fixture');
  const fixtures = [fileUploadTab(), fileUploadTab()];
  const adapters = fixtures.map((fixture, index) => browserAdapter(fixture.tab,
    { observationMode: 'playwright', artifacts: { resume: index === 0 ? file : secondFile } }));
  await Promise.all(adapters.map(adapter => adapter.observe()));
  await Promise.all(adapters.map(adapter => adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' })));
  assert.deepEqual(fixtures.map(fixture => fixture.calls.find(call => call[0] === 'files')[1]), [[file], [secondFile]]);
});

test('observed bound upload trigger is clicked once and changes are rejected before input', async t => {
  const { file } = await uploadFiles(t);
  for (const change of [null, 'disabled', 'label', 'selector', 'removed']) {
    const fixture = fileUploadTab();
    fixture.state.fields[0].upload_trigger = { selector: '#attach', label: 'Attach', disabled: false };
    const adapter = browserAdapter(fixture.tab, { observationMode: 'playwright', artifacts: { resume: file } });
    await adapter.observe();
    if (change === 'removed') fixture.state.fields[0].upload_trigger = null;
    else if (change) fixture.state.fields[0].upload_trigger[change] = change === 'disabled' ? true : 'different';
    if (change) {
      await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' }), ControlNotReady);
      assert.deepEqual(fixture.calls, []);
    } else {
      await adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' });
      assert.deepEqual(fixture.calls, [['wait', 'filechooser', { timeoutMs: 10000 }],
        ['click', '#attach', { timeoutMs: 10000 }], ['files', [file]]]);
    }
  }
});

test('page change after locator validation is rejected before registering a chooser', async t => {
  const { file } = await uploadFiles(t);
  const fixture = fileUploadTab({ onCount: state => { state.page_url += '/other'; } });
  const adapter = browserAdapter(fixture.tab, { observationMode: 'playwright', artifacts: { resume: file } });
  await adapter.observe();
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' }), ControlNotReady);
  assert.deepEqual(fixture.calls, []);
});

test('Playwright upload rejects screenshot response mode before input and allows a fresh DOM request', async t => {
  const { directory, file, closeHosts } = await uploadFiles(t);
  const { tab, calls } = fileUploadTab();
  const host = await createInAppBrowserHost({ directory, tab, observationMode: 'playwright', artifacts: { resume: file } });
  closeHosts.push(host);
  const observed = await bridgeRequest(host, directory, 'observe');
  const rejected = await bridgeRequest(host, directory, 'upload_artifact', observed.observation_id,
    { artifact_id: 'resume', field_key: '#resume', mode: 'screenshot' });
  assert.equal(rejected.ok, false);
  assert.equal(rejected.outcome, 'failed');
  assert.match(rejected.content[0].text, /DOM response mode only/);
  assert.deepEqual(calls, []);
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'active');
  const fresh = await bridgeRequest(host, directory, 'observe');
  const uploaded = await bridgeRequest(host, directory, 'upload_artifact', fresh.observation_id,
    { artifact_id: 'resume', field_key: '#resume', mode: 'dom' });
  assert.equal(uploaded.ok, true);
  assert.equal(JSON.parse(uploaded.content[1].text).upload_result.status, 'file_selection_done');
  assert.equal(calls.filter(call => call[0] === 'click').length, 1);
  assert.equal(calls.filter(call => call[0] === 'files').length, 1);
});

test('ControlNotReady during observation after a completed upload stops the host with unknown outcome', async t => {
  const { directory, file, closeHosts } = await uploadFiles(t);
  const { tab, calls } = fileUploadTab();
  const adapter = browserAdapter(tab, { observationMode: 'playwright', artifacts: { resume: file } });
  const observe = adapter.observe;
  adapter.observe = async args => {
    if (calls.some(call => call[0] === 'files')) throw new ControlNotReady('Post-upload observation unavailable');
    return observe(args);
  };
  const host = await createVisualHost({ directory, adapter,
    target: { runtime: 'iab', tab_id: tab.id, application_url: await tab.url() } });
  closeHosts.push(host);
  const observed = await bridgeRequest(host, directory, 'observe');
  const result = await bridgeRequest(host, directory, 'upload_artifact', observed.observation_id,
    { artifact_id: 'resume', field_key: '#resume' });
  assert.equal(result.ok, false);
  assert.equal(result.outcome, 'outcome_unknown');
  assert.match(result.content[0].text, /Post-upload observation unavailable/);
  assert.equal(JSON.stringify(result).includes('file_selection_done'), false);
  assert.equal(calls.filter(call => call[0] === 'files').length, 1);
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'stopped');
  await assert.rejects(host.peek(), /closed/);
  await assert.rejects(host.execute(randomUUID()), /unavailable/);
  assert.equal(calls.filter(call => call[0] === 'files').length, 1);
});

const uploadFormReport = content => content.map(item => {
  try { return JSON.parse(item.text); } catch { return null; }
}).find(item => item?.form_state);

function uploadDeltaFixture() {
  const fixture = fileUploadTab();
  fixture.state.fields.push({ field_key: '#city', selector: '#city', label: 'City', group: '', group_key: '',
    control: 'text', value: 'Before', disabled: false, readonly: false, options: [] });
  const locator = fixture.tab.playwright.locator;
  fixture.tab.playwright.locator = selector => ({ ...locator(selector),
    fill: async value => { fixture.state.fields.find(field => field.selector === selector).value = value; },
    press: async () => {},
  });
  fixture.tab.dom_cua = {
    get_visible_dom: async () => '<button node_id=7>Upload</button><a href="/other">Other</a>',
    click: async args => fixture.calls.push(['dom_click', args]),
  };
  fixture.tab.goto = async url => { fixture.state.page_url = url; };
  fixture.tab.screenshot = async () => Buffer.from('synthetic image');
  return fixture;
}

test('upload deltas use fresh pre-upload values and retain consecutive read-only asynchronous parsing', async t => {
  const { file } = await uploadFiles(t);
  for (const target of ['field_key', 'node_id']) {
    const fixture = uploadDeltaFixture();
    const adapter = browserAdapter(fixture.tab, { observationMode: target === 'field_key' ? 'playwright' : 'dom_cua',
      artifacts: { resume: file } });
    await adapter.observe();
    fixture.state.fields[1].value = 'Changed before upload';
    await adapter.act('upload_artifact', { artifact_id: 'resume', [target]: target === 'field_key' ? '#resume' : '7' });
    assert.deepEqual(uploadFormReport(await adapter.observe()).post_upload_changes, [], target);
    fixture.state.fields[1].value = 'First async parse';
    const first = uploadFormReport(await adapter.observe()).post_upload_changes;
    assert.equal(first[0].previous_value, 'Changed before upload');
    assert.equal(first[0].observed_value, 'First async parse');
    fixture.state.fields[1].value = 'Later async parse';
    const later = uploadFormReport(await adapter.observe()).post_upload_changes;
    assert.equal(later[0].previous_value, 'Changed before upload');
    assert.equal(later[0].observed_value, 'Later async parse');
  }
});

test('fill, click and navigation end attribution to the prior upload', async t => {
  const { file } = await uploadFiles(t);
  for (const [operation, args] of [
    ['fill_control', { field_key: '#city', value: 'Manual correction' }],
    ['click', { node_id: '7' }], ['navigate', { url: 'https://example.test/other' }],
  ]) {
    const fixture = uploadDeltaFixture();
    const adapter = browserAdapter(fixture.tab, { artifacts: { resume: file } });
    await adapter.observe();
    await adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' });
    fixture.state.fields[1].value = 'Parsed city';
    assert.equal(uploadFormReport(await adapter.observe()).post_upload_changes.length, 1);
    await adapter.act(operation, args);
    fixture.state.page_url = 'https://example.test/apply';
    fixture.state.fields[1].value = 'Value after later write';
    assert.deepEqual(uploadFormReport(await adapter.observe()).post_upload_changes, [], operation);
  }
});

test('observing a different page retires the upload baseline even when returning to the original URL', async t => {
  const { file } = await uploadFiles(t);
  for (const mode of ['dom', 'screenshot']) {
    const fixture = uploadDeltaFixture();
    const adapter = browserAdapter(fixture.tab, { artifacts: { resume: file } });
    await adapter.observe();
    await adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' });
    fixture.state.page_url = 'https://example.test/other';
    await adapter.observe({ mode });
    fixture.state.page_url = 'https://example.test/apply';
    fixture.state.fields[1].value = 'Changed after return';
    assert.deepEqual(uploadFormReport(await adapter.observe()).post_upload_changes, [], mode);
  }
});

test('a second upload attempt rejected before input retires the prior upload baseline', async t => {
  const { file } = await uploadFiles(t);
  const fixture = uploadDeltaFixture();
  const adapter = browserAdapter(fixture.tab, { observationMode: 'playwright', artifacts: { resume: file } });
  await adapter.observe();
  await adapter.act('upload_artifact', { artifact_id: 'resume', field_key: '#resume' });
  fixture.state.fields[1].value = 'First parsed city';
  assert.equal(uploadFormReport(await adapter.observe()).post_upload_changes.length, 1);
  await assert.rejects(adapter.act('upload_artifact', { artifact_id: 'missing', field_key: '#resume' }), ControlNotReady);
  fixture.state.fields[1].value = 'Value after rejected second upload';
  assert.deepEqual(uploadFormReport(await adapter.observe()).post_upload_changes, []);
  assert.equal(fixture.calls.filter(call => call[0] === 'files').length, 1);
});

test('failed responses permit reobservation only for an active host and require handoff after unknown input', async t => {
  for (const failure of [undefined, 'setFiles']) {
    const { directory, file, closeHosts } = await uploadFiles(t);
    const fixture = fileUploadTab({ failure });
    const host = await createInAppBrowserHost({ directory, tab: fixture.tab, observationMode: 'playwright', artifacts: { resume: file } });
    closeHosts.push(host);
    const observed = await bridgeRequest(host, directory, 'observe');
    const result = await bridgeRequest(host, directory, 'upload_artifact', observed.observation_id,
      { artifact_id: failure ? 'resume' : 'unknown', field_key: '#resume' });
    const feedback = JSON.parse(result.content[0].text);
    assert.equal(result.outcome, failure ? 'outcome_unknown' : 'failed');
    assert.equal(feedback.reobserve_before_retry, !failure);
    assert.equal(feedback.host_state, failure ? 'stopped' : 'active');
    assert.equal(feedback.handoff_required, Boolean(failure));
    if (failure) await assert.rejects(host.peek(), /closed/);
    else assert.equal((await bridgeRequest(host, directory, 'observe')).ok, true);
  }
});
