import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { randomUUID } from 'node:crypto';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../src/applypilot/frontend/career.js', import.meta.url), 'utf8').replace(/\r\n/g, '\n');
const interviewSource = readFileSync(new URL('../src/applypilot/frontend/career-interviews.js', import.meta.url), 'utf8');
const startup = '  start();\n})();';
assert(source.includes(startup), 'Expected the private workbench startup boundary');
// Inspect the real closure in the test VM only; the shipped script exposes no test API.
const instrumented = source.replace(startup, `  globalThis.workbench = {
  state, loadTarget, loadRoute, refresh, start, resultOfRun, selectionContext,
  updateProgress, taskAction, handleAction, intent, viewMaterial, pollRuns,
  showError, writeReminder, renderJobProgress
};\n})();`);

class Element {
  constructor(id = '') {
    this.id = id;
    this.value = '';
    this.innerHTML = '';
    this.textContent = '';
    this.options = [];
    this.children = [];
    this.listeners = new Map();
    this.dataset = {};
    this.open = false;
    this.isConnected = true;
  }
  addEventListener(type, handler) {
    const handlers = this.listeners.get(type) || [];
    this.listeners.set(type, [...handlers, handler]);
  }
  async dispatch(type, extra = {}) {
    const event = { target: this, preventDefault() {}, ...extra };
    await Promise.all((this.listeners.get(type) || []).map(handler => handler(event)));
  }
  querySelectorAll() { return []; }
  querySelector() { return null; }
  append(child) { this.children.push(child); }
  replaceChildren(...children) { this.children = children; }
  insertBefore(child) { this.children.push(child); }
  setAttribute() {}
  removeAttribute() {}
  remove() {}
  focus() {}
  showModal() { this.open = true; }
  close() { this.open = false; }
  reset() {}
}

function target(id) {
  return { target_id: id, label: id, company_id: 'fixture', revision: 1,
    material_ids: [], latest_diagnosis_id: `D-${id}`, latest_route_id: `R-${id}` };
}
function diagnosis(id) {
  return { diagnosis_id: `D-${id}`, target_id: id, target: target(id),
    requirements: [], skills: [], summary: {}, evidence: { materials: [] }, stale: false };
}
function progress(id, routeId = `R-${id}`, revision = 1) {
  return { route: { route_id: routeId, target_id: id, tasks: [], budget: {} },
    revision, tasks: [], historical_routes: [], history: [], boundaries: [] };
}
const flush = async () => { for (let i = 0; i < 15; i++) await Promise.resolve(); };

function runtime(initial = 'A', now = Date.now()) {
  const nodes = new Map(), requests = [], replies = new Map(), failures = new Map(), deferred = new Map(), storage = new Map();
  const element = id => {
    if (!nodes.has(id)) nodes.set(id, new Element(id));
    return nodes.get(id);
  };
  const document = new Element();
  document.getElementById = element;
  document.createElement = () => new Element();
  const response = (data, status = 200) => ({ ok: status < 400, status, json: async () => data });
  const fetch = async (url, options) => {
    const path = url.replace('/api/career/v1', '');
    requests.push({ path, body: options.body ? JSON.parse(options.body) : undefined, token: options.headers['X-Career-Token'] });
    const wait = deferred.get(path)?.shift();
    if (wait) return wait.promise;
    if (failures.has(path)) {
      const error = failures.get(path);
      return response({ error }, error.status);
    }
    assert(replies.has(path), `Unexpected mock API request: ${path}`);
    const value = replies.get(path);
    return response(typeof value === 'function' ? value() : value);
  };
  const context = vm.createContext({
    document, window: new Element(), fetch, URL, Intl, TextEncoder,
    Date: class extends Date { static now() { return now; } },
    crypto: { randomUUID }, setTimeout: () => 1, clearTimeout() {},
    localStorage: { getItem: key => storage.get(key) || null, setItem: (key, value) => storage.set(key, value) },
  });
  vm.runInContext(interviewSource, context);
  vm.runInContext(instrumented, context);
  const app = context.workbench;
  Object.assign(app.state, { token: 'fixture-token', target: target(initial),
    diagnosis: diagnosis(initial), progress: progress(initial), targets: [target('A'), target('B')] });
  element('current-target').value = initial;
  for (const id of ['A', 'B']) {
    replies.set(`/targets/${id}`, { target: target(id) });
    replies.set(`/diagnoses/D-${id}`, diagnosis(id));
    replies.set(`/routes/R-${id}`, progress(id));
  }
  replies.set('/targets', { targets: [target('A'), target('B')] });
  return {
    app, element, document, requests, replies, failures, storage,
    defer(path) {
      let resolve, reject;
      const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
      const queue = deferred.get(path) || [];
      deferred.set(path, [...queue, { promise }]);
      return { resolve: data => resolve(response(data)), reject };
    },
    async requested(path) {
      for (let i = 0; i < 40; i++) {
        if (requests.some(request => request.path === path)) return;
        await flush();
      }
      assert.fail(`No request for ${path}`);
    },
  };
}
function assertSelection(r, id, routeId = `R-${id}`) {
  assert.equal(r.app.state.target.target_id, id);
  assert.equal(r.app.state.diagnosis.target_id, id);
  assert.equal(r.app.state.progress.route.target_id, id);
  assert.equal(r.app.state.progress.route.route_id, routeId);
  assert.equal(r.app.state.selectionPending, false);
}

test('slow A diagnosis cannot mix with selected B; a pending selection cannot plan using old state', async () => {
  const r = runtime(), old = r.defer('/diagnoses/D-A');
  const loadingA = r.app.loadTarget('A');
  await r.requested('/diagnoses/D-A');
  assert.equal(r.app.state.target.target_id, 'A');
  assert.equal(r.app.state.progress.route.route_id, 'R-A');
  await r.app.handleAction('plan', new Element());
  assert.equal(r.requests.filter(request => request.body).length, 0);
  await r.app.loadTarget('B');
  old.resolve(diagnosis('A'));
  assert.equal(await loadingA, false);
  assertSelection(r, 'B');
  r.replies.set('/diagnoses/D-B/routes', { job_id: 'new-B', status: 'pending' });
  await r.app.handleAction('plan', new Element());
  const posted = r.requests.find(request => request.body);
  assert.equal(posted.path, '/diagnoses/D-B/routes');
  assert.equal(posted.body.expected_revision, 1);
});

test('a late target response does not issue child requests or overwrite B', async () => {
  const r = runtime(), old = r.defer('/targets/A');
  const loadingA = r.app.loadTarget('A');
  await r.app.loadTarget('B');
  old.resolve({ target: target('A') });
  assert.equal(await loadingA, false);
  assertSelection(r, 'B');
  assert(!r.requests.some(request => request.path === '/diagnoses/D-A'));
});

test('bootstrap publishes token, catalog and selection together; older refresh is ignored', async () => {
  const r = runtime(), oldBootstrap = r.defer('/bootstrap'), newBootstrap = r.defer('/bootstrap');
  const oldRefresh = r.app.refresh(), newRefresh = r.app.refresh();
  const slowB = r.defer('/diagnoses/D-B');
  newBootstrap.resolve({ token: 'new-token', targets: [target('B')], catalog: { companies: [], jobs: [] }, materials: [], runs: [], workspace_label: 'new' });
  await r.requested('/diagnoses/D-B');
  assert.equal(r.app.state.token, 'fixture-token');
  assert.equal(r.app.state.diagnosis.target_id, 'A');
  slowB.resolve(diagnosis('B'));
  assert.equal(await newRefresh, true);
  oldBootstrap.resolve({ token: 'old-token', targets: [target('A')], workspace_label: 'old' });
  assert.equal(await oldRefresh, false);
  assertSelection(r, 'B');
  assert.equal(r.app.state.token, 'new-token');
  assert.equal(r.element('workspace-label').textContent, 'new');
});

test('startup bootstrap cannot reset a target selected while it was waiting', async () => {
  const r = runtime(), bootstrap = r.defer('/bootstrap');
  const starting = r.app.start();
  await r.app.loadTarget('B');
  bootstrap.resolve({ token: 'old', targets: [target('A')] });
  assert.equal(await starting, false);
  assertSelection(r, 'B');
});

test('refresh during selection of B never reuses the still-published A route', async () => {
  const r = runtime(), slowB = r.defer('/targets/B');
  r.element('current-target').value = 'B';
  const selecting = r.app.loadTarget('B');
  r.replies.set('/bootstrap', { token: 'new', targets: [target('A'), target('B')], materials: [], runs: [] });
  assert.equal(await r.app.refresh(), true);
  slowB.resolve({ target: target('B') });
  assert.equal(await selecting, false);
  assertSelection(r, 'B');
  assert(!r.requests.some(request => request.path === '/routes/R-A'));
});

test('persisted cross-target route is replaced with the selected target latest route', async () => {
  const r = runtime();
  Object.assign(r.app.state, { target: null, diagnosis: null, progress: null });
  r.element('current-target').value = '';
  r.storage.set('career-workbench-selection', JSON.stringify({ targetId: 'B', routeId: 'R-A' }));
  r.replies.set('/bootstrap', { token: 'new', targets: [target('A'), target('B')], materials: [], runs: [] });
  assert.equal(await r.app.start(), true);
  assertSelection(r, 'B');
  assert(r.requests.some(request => request.path === '/routes/R-A'));
  assert(r.requests.some(request => request.path === '/routes/R-B'));
});

test('foreign diagnosis or route responses cannot be published as a valid selection', async () => {
  const r = runtime();
  r.replies.set('/diagnoses/D-B', diagnosis('A'));
  await assert.rejects(r.app.loadTarget('B'), /诊断与所选目标不一致/);
  assertSelection(r, 'A');
  r.replies.set('/routes/foreign', progress('B', 'foreign'));
  await assert.rejects(r.app.loadRoute('foreign'), /该路线不属于所选目标/);
  assertSelection(r, 'A');
});

test('run-result target list and delayed diagnosis cannot navigate over a newer selection', async () => {
  for (const delayedPath of ['/targets', '/diagnoses/run-A']) {
    const r = runtime(), old = r.defer(delayedPath);
    r.replies.set('/diagnoses/run-A', { ...diagnosis('A'), diagnosis_id: 'run-A' });
    const showingRun = r.app.resultOfRun({ result_refs: { target_id: 'A', diagnosis_id: 'run-A', route_id: 'R-A' } }, true);
    await r.requested(delayedPath);
    await r.app.loadTarget('B');
    old.resolve(delayedPath === '/targets' ? { targets: [target('A')] } : { ...diagnosis('A'), diagnosis_id: 'run-A' });
    assert.equal(await showingRun, false);
    assertSelection(r, 'B');
    assert.equal(r.app.state.stage, 'materials');
  }
});

test('background run completion is also selection-scoped', async () => {
  const r = runtime(), old = r.defer('/diagnoses/run-A');
  const showingRun = r.app.resultOfRun({ result_refs: { target_id: 'A', diagnosis_id: 'run-A' } });
  await r.requested('/diagnoses/run-A');
  await r.app.loadTarget('B');
  old.resolve({ ...diagnosis('A'), diagnosis_id: 'run-A' });
  assert.equal(await showingRun, false);
  assertSelection(r, 'B');
});

test('a delayed background snapshot cannot replace a newer saved revision on the same route', async () => {
  const r = runtime(), old = r.defer('/routes/R-A');
  const run = { result_refs: { target_id: 'A', diagnosis_id: 'D-A', route_id: 'R-A' } };
  const reading = r.app.resultOfRun(run);
  await r.requested('/routes/R-A');
  assert.equal(await r.app.updateProgress({ progress: progress('A', 'R-A', 2) }), true);
  old.resolve(progress('A', 'R-A', 1));
  assert.equal(await reading, false);
  assert.equal(r.app.state.progress.revision, 2);
  r.replies.set('/routes/R-A', progress('A', 'R-A', 2));
  assert.equal(await r.app.resultOfRun(run), true);
  assert.equal(r.app.state.progress.revision, 2);
});

test('a background route result cannot invalidate an unfinished POST or discard its success', async () => {
  const r = runtime(), old = r.defer('/routes/next-A');
  const reading = r.app.resultOfRun({ result_refs: { target_id: 'A', route_id: 'next-A' } });
  await r.requested('/routes/next-A');
  const posted = r.defer('/routes/R-A/events');
  const saving = r.app.intent('/routes/R-A/events', { expected_revision: 1 }, r.app.updateProgress, new Element());
  await r.requested('/routes/R-A/events');
  old.resolve(progress('A', 'next-A'));
  assert.equal(await reading, false);
  assertSelection(r, 'A');
  posted.resolve({ progress: progress('A', 'R-A', 2) });
  await saving;
  assertSelection(r, 'A');
  assert.equal(r.app.state.progress.revision, 2);
  assert.equal(r.app.state.busy, false);
});

test('polling retains a completed result while POST is busy, then reads it after the saved revision', async () => {
  const r = runtime(), posted = r.defer('/routes/R-A/events');
  const run = { job_id: 'run-A', status: 'running', result_refs: { target_id: 'A', route_id: 'R-A' } };
  r.app.state.runs = [run];
  r.replies.set('/runs', { runs: [{ ...run, status: 'completed' }] });
  const saving = r.app.intent('/routes/R-A/events', { expected_revision: 1 }, r.app.updateProgress, new Element());
  await r.requested('/routes/R-A/events');
  await r.app.pollRuns();
  assert.equal(r.app.state.pendingResults.size, 1);
  assert.equal(r.app.state.observedRuns.has('run-A'), false);
  assert.equal(r.requests.some(request => request.path === '/routes/R-A'), false);
  posted.resolve({ progress: progress('A', 'R-A', 2) });
  await saving;
  r.replies.set('/routes/R-A', progress('A', 'R-A', 2));
  await r.app.pollRuns();
  assert.equal(r.app.state.progress.revision, 2);
  assert.equal(r.app.state.pendingResults.size, 0);
  assert.equal(r.app.state.observedRuns.has('run-A'), true);
});

test('slow route history cannot restore A after B, or replace a newer history selection', async () => {
  const r = runtime(), old = r.defer('/routes/history-A');
  const loadingHistory = r.app.loadRoute('history-A');
  await r.app.loadTarget('B');
  old.resolve(progress('A', 'history-A'));
  assert.equal(await loadingHistory, false);
  assertSelection(r, 'B');
  const first = r.defer('/routes/history-1'), second = r.defer('/routes/history-2');
  const one = r.app.loadRoute('history-1'), two = r.app.loadRoute('history-2');
  second.resolve(progress('B', 'history-2'));
  assert.equal(await two, true);
  first.resolve(progress('B', 'history-1'));
  assert.equal(await one, false);
  assertSelection(r, 'B', 'history-2');
});

test('updateProgress publishes progress and diagnosis atomically and cannot close the newer task dialog', async () => {
  const r = runtime(), old = r.defer('/diagnoses/D-A');
  const updating = r.app.updateProgress({ progress: progress('A', 'R-A', 2) }, r.app.selectionContext());
  await r.requested('/diagnoses/D-A');
  assert.equal(r.app.state.progress.revision, 1);
  await r.app.loadTarget('B');
  r.app.state.progress.tasks = [{ task_id: 'B-task', title: 'B task', ready: true }];
  await r.app.taskAction('started', 'B-task');
  old.resolve({ ...diagnosis('A'), stale: true });
  assert.equal(await updating, false);
  assertSelection(r, 'B');
  assert.equal(r.element('task-dialog').open, true);
  assert(r.element('task-dialog-content').innerHTML.includes('B task'));
});

test('late POST success is ignored even after selecting B and returning to the same A IDs', async () => {
  const r = runtime(), posted = r.defer('/routes/R-A/events');
  const saving = r.app.intent('/routes/R-A/events', { expected_revision: 1 }, r.app.updateProgress, new Element());
  await r.app.loadTarget('B');
  await r.app.loadTarget('A');
  const diagnosisReads = r.requests.filter(request => request.path === '/diagnoses/D-A').length;
  posted.resolve({ progress: progress('A', 'R-A', 99) });
  await saving;
  assertSelection(r, 'A');
  assert.equal(r.app.state.progress.revision, 1);
  assert.equal(r.requests.filter(request => request.path === '/diagnoses/D-A').length, diagnosisReads);
});

test('current progress save publishes its revision and refreshed diagnosis together', async () => {
  const r = runtime(), observed = r.defer('/diagnoses/D-A');
  r.element('task-dialog').showModal();
  const updating = r.app.updateProgress({ progress: progress('A', 'R-A', 2) }, r.app.selectionContext());
  await r.requested('/diagnoses/D-A');
  assert.equal(r.app.state.progress.revision, 1);
  assert.equal(r.app.state.diagnosis.stale, false);
  observed.resolve({ ...diagnosis('A'), stale: true });
  assert.equal(await updating, true);
  assert.equal(r.app.state.progress.revision, 2);
  assert.equal(r.app.state.diagnosis.stale, true);
  assert.equal(r.element('task-dialog').open, false);
});

test('slow submission preview cannot replace B dialog, or a newer task on the same route', async () => {
  const r = runtime();
  r.app.state.progress.tasks = [{ task_id: 'A-task', title: 'A task' }];
  const old = r.defer('/routes/R-A/tasks/A-task/submission');
  const preview = r.app.taskAction('view-submission', 'A-task');
  await r.app.loadTarget('B');
  r.app.state.progress.tasks = [{ task_id: 'one', title: 'one' }, { task_id: 'two', title: 'two' }];
  const slowOne = r.defer('/routes/R-B/tasks/one/submission');
  const one = r.app.taskAction('view-submission', 'one');
  r.replies.set('/routes/R-B/tasks/two/submission', { materials: [{ id: 'two', title: 'new material', text: 'new text' }] });
  await r.app.taskAction('view-submission', 'two');
  old.resolve({ materials: [{ title: 'old A', text: 'old A' }] });
  slowOne.resolve({ materials: [{ title: 'old one', text: 'old one' }] });
  await Promise.all([preview, one]);
  assert.equal(r.element('task-dialog').open, true);
  assert(r.element('task-dialog-content').innerHTML.includes('new text'));
  assert(!r.element('task-dialog-content').innerHTML.includes('old'));
});

test('Escape cancels an outstanding dialog read; late material previews cannot reopen after selection', async () => {
  const r = runtime();
  r.app.state.progress.tasks = [{ task_id: 'A-task' }];
  const old = r.defer('/routes/R-A/tasks/A-task/submission');
  const preview = r.app.taskAction('view-submission', 'A-task');
  await r.document.dispatch('keydown', { key: 'Escape' });
  old.resolve({ materials: [{ text: 'old' }] });
  await preview;
  assert.equal(r.element('task-dialog').open, false);
  const material = r.defer('/materials/old');
  const opening = r.app.viewMaterial('old');
  await r.app.loadTarget('B');
  material.resolve({ id: 'old', title: 'old material', text: 'old text' });
  await opening;
  assert.equal(r.element('source-dialog').open, false);
});

test('a stale error and detached task form cannot affect the new selection', async () => {
  const r = runtime(), old = r.defer('/targets/A');
  const loading = r.app.loadTarget('A');
  await r.app.loadTarget('B');
  old.reject(new Error('old network error'));
  assert.equal(await loading, false);
  assertSelection(r, 'B');
  r.app.state.progress.tasks = [{ task_id: 'B-task', title: 'B task' }];
  await r.app.taskAction('started', 'B-task');
  const form = r.element('event-form');
  await r.app.loadTarget('A');
  await form.dispatch('submit', { submitter: new Element() });
  assert.equal(r.requests.filter(request => request.body).length, 0);
});

test('target creation list lookup cannot steal a subsequent user selection', async () => {
  const r = runtime();
  r.app.state.catalog.jobs = [{ id: 'job-A', title: 'Job A' }];
  r.element('job').value = 'job-A';
  r.element('target-materials').querySelectorAll = () => [{ value: 'material' }];
  r.replies.set('/targets', { target: target('A') });
  // The POST response and following GET have the same path, but different payloads.
  const post = r.defer('/targets'), listing = r.defer('/targets');
  const creating = r.element('target-form').dispatch('submit', { submitter: new Element() });
  post.resolve({ target: target('A') });
  await flush();
  await r.app.loadTarget('B');
  listing.resolve({ targets: [target('A'), target('B')] });
  await creating;
  assertSelection(r, 'B');
  assert.equal(r.app.state.stage, 'materials');
});

test('selected progress rounds survive missing response options, and explicit uncheck clears the draft', async () => {
  const r = runtime();
  r.app.state.jobReportQuery.statuses = ['interview_1'];
  r.app.state.jobReport = { available: false, rows: [], status_options: [] };
  r.app.renderJobProgress();
  assert.match(r.element('job-progress-status-options').innerHTML, /value="interview_1" checked/);
  // The choice belongs to UI state even when another result response has no rows/options.
  r.element('job-progress-status-options').querySelectorAll = () => [{ value: 'interview_2' }];
  await r.element('job-progress-status-options').dispatch('change');
  r.app.renderJobProgress();
  assert.match(r.element('job-progress-status-options').innerHTML, /value="interview_2" checked/);
  assert(!r.element('job-progress-status-options').innerHTML.includes('interview_1'));
  r.element('job-progress-status-options').querySelectorAll = () => [];
  await r.element('job-progress-status-options').dispatch('change');
  r.app.renderJobProgress();
  assert.equal(r.app.state.jobReportStatusDraft.length, 0);
  assert(!r.element('job-progress-status-options').innerHTML.includes('checked'));
});

for (const path of ['/job-progress/reminders', '/job-progress/reminders/item/snooze', '/job-progress/reminders/item/dismiss']) {
  test(`session refresh never replays a reminder write: ${path}`, async () => {
    const r = runtime();
    r.app.state.stage = 'job-progress';
    r.element('job-reminder-dialog').showModal();
    r.element('reminder-due-date').value = '2026-12-01';
    r.element('reminder-next-step').value = 'Synthetic retained draft';
    r.failures.set(path, { status: 403, code: 'session_required' });
    await r.app.writeReminder(path, { due_date: '2026-12-01' });
    assert.equal(r.app.state.sessionExpired, true);
    assert.equal(r.requests.filter(request => request.body).length, 1);
    r.replies.set('/bootstrap', { token: 'replacement-token', targets: [], materials: [], runs: [] });
    r.replies.set('/job-progress?q=&date_field=updated_at&offset=0', { available: false, rows: [] });
    r.replies.set('/job-progress/reminders?limit=100', {
      groups: { overdue: [], today: [], future: [], undated: [] },
      counts: { overdue: 0, today: 0, future: 0, undated: 0 },
    });
    // The normal page Refresh is also a session recovery action after the 403.
    assert.equal(await r.app.refresh(), true);
    assert.equal(r.app.state.token, 'replacement-token');
    assert.equal(r.app.state.sessionExpired, false);
    assert.equal(r.element('job-reminder-dialog').open, true);
    assert.equal(r.element('reminder-due-date').value, '2026-12-01');
    assert.equal(r.element('reminder-next-step').value, 'Synthetic retained draft');
    assert.equal(r.requests.filter(request => request.body).length, 1);
    r.failures.delete(path);
    r.replies.set(path, {});
    await r.app.writeReminder(path, { due_date: '2026-12-01' }, { closeDialog: true });
    assert.equal(r.requests.filter(request => request.body).length, 2);
    assert.equal(r.requests.at(-2).token, 'replacement-token');
    assert.equal(r.element('job-reminder-dialog').open, false);
  });
}

test('conflict recovery closes a stale inbox form, retains its draft and preserves task-dialog recovery', async () => {
  for (const name of ['inbox', 'task']) {
    const r = runtime();
    r.element(`${name}-dialog`).showModal();
    r.app.state.inboxDrafts.set('item', 'Retained synthetic answer');
    r.replies.set('/bootstrap', { token: 'new', targets: [], materials: [], runs: [] });
    r.app.showError({ status: 409 });
    const action = r.element('feedback').children[1];
    assert.equal(action.textContent, '刷新最新数据');
    await action.dispatch('click');
    assert.equal(r.element(`${name}-dialog`).open, false);
    assert.equal(r.app.state.inboxDrafts.get('item'), 'Retained synthetic answer');
    assert.equal(r.requests.filter(request => request.body).length, 0);
  }
});

const interviewNow = Date.parse('2026-10-15T00:00:00Z');
const visibleInterviews = r => [...r.element('interview-list').innerHTML.matchAll(/data-interview-view="([^"]+)"/g)].map(match => match[1]);
async function interviewRuntime(items) {
  const r = runtime('A', interviewNow);
  r.app.state.stage = 'interviews';
  r.replies.set('/interviews', { items, pending_invitations: [{ event_id: 'unlinked', summary: '邀请仍待关联' }] });
  await r.app.refresh();
  return r;
}
async function interviewFilter(r, scope, sort = 'scheduled') {
  r.element('interview-filter').value = scope;
  r.element('interview-sort').value = sort;
  await r.element('interview-filter').dispatch('change');
}

test('interview upcoming window compares instants, excludes terminal rounds and preserves API order', async () => {
  const items = [
    { id: 'missing', status: 'pending' },
    { id: 'end', status: 'scheduled', scheduled_at: '2026-10-22T00:00:00Z' },
    { id: 'offset', status: 'scheduled', scheduled_at: '2026-10-15T09:00:00+08:00' },
    { id: 'past', status: 'scheduled', scheduled_at: '2026-10-14T23:59:59Z' },
    { id: 'now', status: 'scheduled', scheduled_at: '2026-10-15T00:00:00Z' },
    { id: 'completed', status: 'completed', scheduled_at: '2026-10-16T00:00:00Z' },
    { id: 'cancelled', status: 'cancelled', scheduled_at: '2026-10-16T00:00:00Z' },
    { id: 'invalid', status: 'scheduled', scheduled_at: 'invalid' },
  ];
  const original = JSON.stringify(items), r = await interviewRuntime(items);
  assert.deepEqual(visibleInterviews(r), ['past', 'now', 'offset', 'end', 'invalid', 'missing']);
  assert.match(r.element('interview-list').innerHTML, /面试时间已过，待核对结果或改期/);
  await interviewFilter(r, 'upcoming');
  assert.deepEqual(visibleInterviews(r), ['now', 'offset']);
  assert.match(r.element('interview-summary').textContent, /显示 2 \/ 8 轮/);
  assert.match(r.element('interview-pending').innerHTML, /邀请仍待关联/);
  assert.equal(JSON.stringify(items), original);
  assert.equal(r.requests.length, 1, 'local filtering does not fetch or write');
});

test('interview overdue and open scopes include terminal followups but exclude completed or undated tasks', async () => {
  const action = (due_at, completed_at = null) => ({ due_at, completed_at });
  const items = [
    { id: 'undated', status: 'pending', actions: [action(null), action('invalid')] },
    { id: 'done', status: 'completed', actions: [action('2026-10-10T00:00:00Z', '2026-10-11T00:00:00Z')] },
    { id: 'cancelled', status: 'cancelled', actions: [action('2026-10-13T00:00:00Z')] },
    { id: 'completed', status: 'completed', actions: [action('2026-10-14T23:00:00Z')] },
    { id: 'current', status: 'scheduled', actions: [action('2026-10-15T08:00:00+08:00')] },
  ];
  const r = await interviewRuntime(items);
  await interviewFilter(r, 'overdue-actions', 'due');
  assert.deepEqual(visibleInterviews(r), ['cancelled', 'completed']);
  assert.equal((r.element('interview-list').innerHTML.match(/1 项待办已逾期/g) || []).length, 2);
  assert.match(r.element('interview-summary').textContent, /2 项逾期待办/);
  await interviewFilter(r, 'open-actions', 'due');
  assert.deepEqual(visibleInterviews(r), ['cancelled', 'completed', 'current', 'undated']);
  await interviewFilter(r, 'completed', 'due');
  assert.deepEqual(visibleInterviews(r), ['completed', 'done']);
  await interviewFilter(r, 'overdue-actions', 'due');
  items[2].actions[0].completed_at = '2026-10-15T00:00:00Z';
  items[3].actions[0].completed_at = '2026-10-15T00:00:00Z';
  await r.app.refresh();
  assert.deepEqual(visibleInterviews(r), []);
  assert.equal(r.element('interview-filter').value, 'overdue-actions');
  assert.equal(r.element('interview-sort').value, 'due');
  assert.match(r.element('interview-list').innerHTML, /可切换查看范围/);
  assert.match(r.element('interview-summary').textContent, /0 项逾期待办/);
});

test('interview detail prioritizes unfinished deadlines and escapes task text', async () => {
  const item = { id: 'round', status: 'completed', actions: [
    { action_id: 'done', summary: 'DONE', due_at: '2026-10-10T00:00:00Z', completed_at: '2026-10-11T00:00:00Z' },
    { action_id: 'later', summary: 'LATER', due_at: '2026-10-16T00:00:00Z' },
    { action_id: 'old', summary: '<img src=x onerror=alert(1)>', due_at: '2026-10-14T00:00:00Z' },
    { action_id: 'missing', summary: 'UNDATED', due_at: null },
  ] };
  const original = JSON.stringify(item.actions), r = await interviewRuntime([item]);
  r.replies.set('/interviews/round', { item });
  const button = { dataset: { interviewView: 'round' }, hasAttribute: () => false };
  await r.document.dispatch('click', { target: { closest: () => button } });
  const markup = r.element('interview-dialog-content').innerHTML;
  assert.deepEqual([...markup.matchAll(/data-interview-complete="([^"]+)"/g)].map(match => match[1]), ['old', 'later', 'missing']);
  assert(markup.indexOf('UNDATED') < markup.indexOf('DONE'));
  assert.match(markup, /已逾期 · 待完成/);
  assert.match(markup, /截止时间未确定/);
  assert.match(markup, /&lt;img/);
  assert(!markup.includes('<img'));
  assert.equal(JSON.stringify(item.actions), original);
});

async function interviewSearch(r, value) {
  r.element('interview-query').value = value;
  await r.element('interview-query').dispatch('input');
}

test('interview keyword search composes company role and round with time and todo scopes locally', async () => {
  const items = [
    { id: 'match', company: 'Acme', title: 'Data Engineer', round_name: '技术面', round: 2, status: 'scheduled', scheduled_at: '2026-10-16T00:00:00Z', actions: [{ due_at: '2026-10-14T00:00:00Z' }] },
    { id: 'other-round', company: 'ACME', title: 'Data Engineer', round: 1, status: 'scheduled', scheduled_at: '2026-10-17T00:00:00Z' },
    { id: 'past', company: 'Acme', title: 'Data Engineer', round_name: '技术面', round: 2, status: 'completed', scheduled_at: '2026-10-12T00:00:00Z' },
    { id: 'missing', status: 'pending' },
  ];
  const r = await interviewRuntime(items), original = JSON.stringify(items);
  await interviewFilter(r, 'upcoming');
  await interviewSearch(r, '  ＡＣＭＥ\tengineer  技术面 ');
  assert.deepEqual(visibleInterviews(r), ['match']);
  await interviewSearch(r, 'acme 第２轮');
  assert.deepEqual(visibleInterviews(r), ['match']);
  await interviewFilter(r, 'overdue-actions', 'due');
  assert.deepEqual(visibleInterviews(r), ['match']);
  await interviewSearch(r, '  ');
  assert.deepEqual(visibleInterviews(r), ['match']);
  assert.equal(JSON.stringify(items), original);
  assert.equal(r.requests.length, 1);
});

test('interview search empty recovery and refresh preserve filters, unsaved forms and drafts', async () => {
  const r = await interviewRuntime([{ id: 'round', company: 'Acme', title: 'Engineer', status: 'scheduled' }]);
  const draft = { summary: 'Unsaved preparation', due_at: '2026-10-20T12:00' };
  r.app.state.interviewDrafts.set('actions:round', draft);
  r.element('interview-dialog').open = true;
  r.element('interview-dialog-content').innerHTML = '<form>Unsaved preparation</form>';
  await interviewFilter(r, 'active', 'due');
  await interviewSearch(r, '<img src=x>');
  assert.deepEqual(visibleInterviews(r), []);
  assert.match(r.element('interview-list').innerHTML, /当前范围没有匹配/);
  assert(!r.element('interview-list').innerHTML.includes('<img'));
  assert.match(r.element('interview-list').innerHTML, /data-interview-clear-search/);
  assert.equal(r.element('interview-search-clear').disabled, false);
  assert.match(r.element('interview-pending').innerHTML, /邀请仍待关联/);
  await r.app.refresh();
  assert.equal(r.element('interview-query').value, '<img src=x>');
  assert.deepEqual(visibleInterviews(r), []);
  const button = { dataset: {}, hasAttribute: name => name === 'data-interview-clear-search' };
  await r.document.dispatch('click', { target: { closest: () => button } });
  assert.deepEqual(visibleInterviews(r), ['round']);
  assert.equal(r.element('interview-query').value, '');
  assert.equal(r.element('interview-search-clear').disabled, true);
  assert.equal(r.element('interview-filter').value, 'active');
  assert.equal(r.element('interview-sort').value, 'due');
  assert.equal(r.element('interview-dialog').open, true);
  assert.equal(r.element('interview-dialog-content').innerHTML, '<form>Unsaved preparation</form>');
  assert.equal(r.app.state.interviewDrafts.get('actions:round'), draft);
  assert.equal(r.requests.length, 2, 'only initial load and explicit refresh fetch data');
});
