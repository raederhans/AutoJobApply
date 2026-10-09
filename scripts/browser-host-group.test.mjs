import test from 'node:test';
import assert from 'node:assert/strict';
import { createBrowserHostGroup } from './browser-host-group.mjs';
import { setImmediate as nextTurn } from 'node:timers/promises';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import { createVisualHost } from './visual-bridge-host.mjs';

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function entry(job_id, execute) {
  return { job_id, host: {
    binding: { phase: 'prepare', target: { runtime: 'iab', tab_id: job_id } },
    execute,
  } };
}

const reviewed = job_id => ({ job_id, request_id: `${job_id}-request` });

async function isolatedHosts(t, actions) {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-host-group-'));
  const hosts = new Map();
  t.after(async () => {
    for (const host of hosts.values()) await host.close();
    assert.equal(path.dirname(directory), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('applypilot-host-group-'));
    await fs.rm(directory, { recursive: true, force: true });
  });
  for (const [job_id, act] of Object.entries(actions)) {
    hosts.set(job_id, await createVisualHost({
      directory: path.join(directory, job_id),
      target: { runtime: 'iab', tab_id: job_id },
      adapter: { surface: 'browser', tabId: job_id, act,
        observe: async () => [{ type: 'text', text: `form-${job_id}` }] },
    }));
  }
  const group = createBrowserHostGroup([...hosts].map(([job_id, host]) => ({ job_id, host })));
  async function request(job_id, operation = 'observe', observation_id) {
    const request_id = randomUUID();
    const now = Date.now() / 1000;
    await fs.writeFile(path.join(directory, job_id, 'pending', `${request_id}.json`), JSON.stringify({
      ...hosts.get(job_id).binding, request_id, operation, observation_id, arguments: {},
      created_at: now, deadline_at: now + 30,
    }));
    return { job_id, request_id };
  }
  const initial = await group.execute(await Promise.all([...hosts.keys()].map(id => request(id))));
  const observations = new Map(initial.map(result => [result.job_id, result.response.observation_id]));
  return { directory, hosts, group, request, observations };
}

test('group never executes from peek; one stopped host does not suppress another', async () => {
  const writes = [];
  const entries = ['a', 'b'].map(job_id => ({ job_id, host: {
    binding: { phase: 'prepare', target: { runtime: 'iab', tab_id: job_id } },
    peek: async () => { if (job_id === 'a') throw Error('stopped'); return [{ request_id: 'b1' }]; },
    metrics: () => ({}), execute: async id => { writes.push(id); return { ok: true }; },
  } }));
  const group = createBrowserHostGroup(entries);
  const pending = await group.peek();
  assert.equal(pending[0].error, 'stopped');
  assert.equal(writes.length, 0);
  await assert.rejects(group.execute([{ job_id: 'b', request_id: 'b1' }, { job_id: 'b', request_id: 'b2' }]));
  assert.equal(writes.length, 0);
  await group.execute([{ job_id: 'b', request_id: 'b1' }]);
  assert.deepEqual(writes, ['b1']);
  assert.throws(() => createBrowserHostGroup([entries[0], entries[0]]));
});

test('four workers share at most two simultaneous browser operations', async () => {
  let active = 0, peak = 0;
  const entries = ['a', 'b', 'c', 'd'].map(job_id => ({ job_id, host: {
    binding: { phase: 'prepare', target: { runtime: 'iab', tab_id: job_id } },
    execute: async () => { peak = Math.max(peak, ++active); await new Promise(resolve => setTimeout(resolve, 5)); active--; return { ok: true }; },
  } }));
  const replies = await createBrowserHostGroup(entries).execute(entries.map(e => ({ job_id: e.job_id, request_id: e.job_id })));
  assert.equal(peak, 2);
  assert.equal(replies.length, 4);
});

test('overlapping execute calls reserve a job before admission and never replay its review', async () => {
  const release = deferred();
  const started = deferred();
  const writes = [];
  const group = createBrowserHostGroup([entry('a', async id => {
    writes.push(id);
    started.resolve();
    await release.promise;
    return { request_id: id, ok: true };
  })]);
  const first = group.execute([reviewed('a')]);
  await started.promise;
  const second = group.execute([{ job_id: 'a', request_id: 'a-other' }]);
  try {
    await nextTurn();
    assert.deepEqual(writes, ['a-request']);
    const rejected = await second;
    assert.match(rejected[0].error, /already scheduled or executing/);
    assert.equal(rejected[0].job_id, 'a');
  } finally {
    release.resolve();
    await Promise.all([first, second]);
  }
  assert.equal((await group.execute([reviewed('a')]))[0].response.ok, true);
});

test('overlapping execute calls share two slots including jobs waiting for admission', async () => {
  const releases = new Map(['a', 'b', 'c', 'd'].map(id => [id, deferred()]));
  const starts = [];
  let active = 0, peak = 0;
  const group = createBrowserHostGroup([...releases.keys()].map(id => entry(id, async () => {
    starts.push(id);
    peak = Math.max(peak, ++active);
    await releases.get(id).promise;
    active--;
    return { owner: id };
  })));
  const first = group.execute(['a', 'b', 'c'].map(reviewed));
  const second = group.execute([reviewed('d')]);
  const duplicate = group.execute([{ job_id: 'c', request_id: 'c-other' }]);
  try {
    await nextTurn();
    assert.deepEqual(starts, ['a', 'b']);
    assert.equal(peak, 2);
    assert.match((await duplicate)[0].error, /already scheduled or executing/);
    releases.get('b').resolve();
    await nextTurn();
    assert.deepEqual(starts, ['a', 'b', 'c']);
    releases.get('c').resolve();
    await nextTurn();
    assert.deepEqual(starts, ['a', 'b', 'c', 'd']);
    assert.equal(peak, 2);
  } finally {
    for (const release of releases.values()) release.resolve();
    await Promise.all([first, second, duplicate]);
  }
  assert.deepEqual((await first).map(reply => reply.response.owner), ['a', 'b', 'c']);
  assert.equal((await second)[0].response.owner, 'd');
});

test('one slow job and one throwing job leave the other slot available and preserve result order', async () => {
  const slow = deferred();
  const failure = deferred();
  const starts = [];
  const group = createBrowserHostGroup(['a', 'b', 'c', 'd'].map(id => entry(id, async () => {
    starts.push(id);
    if (id === 'a') await slow.promise;
    if (id === 'b') { await failure.promise; throw Error('only-b-failed'); }
    return { owner: id };
  })));
  const executing = group.execute(['a', 'b', 'c', 'd'].map(reviewed));
  try {
    await nextTurn();
    assert.deepEqual(starts, ['a', 'b']);
    failure.resolve();
    await nextTurn();
    assert.deepEqual(starts, ['a', 'b', 'c', 'd']);
  } finally {
    failure.resolve();
    slow.resolve();
  }
  const results = await executing;
  assert.deepEqual(results, [
    { job_id: 'a', response: { owner: 'a' } },
    { job_id: 'b', error: 'only-b-failed' },
    { job_id: 'c', response: { owner: 'c' } },
    { job_id: 'd', response: { owner: 'd' } },
  ]);
});

test('peek refreshes each heartbeat during another job input without performing extra input', async t => {
  const started = deferred();
  const release = deferred();
  const inputs = [];
  const { directory, group, request, observations } = await isolatedHosts(t, {
    a: async () => { inputs.push('a'); started.resolve(); await release.promise; },
    b: async () => { inputs.push('b'); },
  });
  const a = await request('a', 'click', observations.get('a'));
  const b = await request('b', 'click', observations.get('b'));
  const running = group.execute([a]);
  await started.promise;
  try {
    // A stale heartbeat is refreshed by an explicit attended peek, without a pump.
    for (const id of ['a', 'b']) {
      const file = path.join(directory, id, 'host.json');
      const state = JSON.parse(await fs.readFile(file, 'utf8'));
      await fs.writeFile(file, JSON.stringify({ ...state, heartbeat_at: 0 }));
    }
    const pending = await group.peek();
    assert.deepEqual(pending.map(result => [result.job_id, result.requests.map(r => r.request_id)]), [
      ['a', []], ['b', [b.request_id]],
    ]);
    assert.deepEqual(inputs, ['a']);
    for (const id of ['a', 'b']) {
      const state = JSON.parse(await fs.readFile(path.join(directory, id, 'host.json'), 'utf8'));
      assert.equal(state.status, 'active');
      assert.ok(state.heartbeat_at > 0);
    }
    const completed = await group.execute([b]);
    assert.equal(completed[0].response.ok, true);
    assert.match(completed[0].response.content[1].text, /form-b/);
    assert.deepEqual(inputs, ['a', 'b']);
  } finally {
    release.resolve();
    await running;
  }
  assert.equal((await running)[0].response.ok, true);
});

test('cancelling an unclaimed queued request and closing another queued host remain isolated', async t => {
  const entered = { a: deferred(), b: deferred() };
  const releases = { a: deferred(), b: deferred() };
  const inputs = [];
  const { directory, hosts, group, request, observations } = await isolatedHosts(t,
    Object.fromEntries(['a', 'b', 'c', 'd'].map(id => [id, async () => {
      inputs.push(id);
      if (entered[id]) { entered[id].resolve(); await releases[id].promise; }
    }])));
  const requests = await Promise.all(['a', 'b', 'c', 'd'].map(id => request(id, 'click', observations.get(id))));
  const running = group.execute(requests.slice(0, 2));
  await Promise.all(Object.values(entered).map(gate => gate.promise));
  const queued = group.execute(requests.slice(2));
  try {
    const cancelled = `${requests[2].request_id}.json`;
    await fs.rename(path.join(directory, 'c', 'pending', cancelled), path.join(directory, 'c', 'cancelled', cancelled));
    await hosts.get('d').close();
    releases.b.resolve();
    const results = await queued;
    assert.equal(results[0].job_id, 'c');
    assert.match(results[0].error, /ENOENT/);
    assert.equal(results[1].job_id, 'd');
    assert.match(results[1].error, /unavailable/);
    assert.deepEqual([...inputs].sort(), ['a', 'b']);
    assert.equal((await fs.readdir(path.join(directory, 'c', 'claimed'))).length, 1); // Initial observe only.
    assert.equal((await fs.readdir(path.join(directory, 'c', 'cancelled'))).length, 1);
    const fresh = await request('b');
    assert.equal((await group.execute([fresh]))[0].response.ok, true);
    const pending = await group.peek();
    assert.match(pending.find(result => result.job_id === 'd').error, /closed/);
    assert.equal(pending.find(result => result.job_id === 'b').error, undefined);
  } finally {
    for (const gate of Object.values(releases)) gate.resolve();
    await Promise.all([running, queued]);
  }
  assert.ok((await running).every(result => result.response.ok));
});

test('shutdown during claimed input records uncertainty once while the other job continues', async t => {
  const entered = deferred();
  const release = deferred();
  const inputs = [];
  const { directory, hosts, group, request, observations } = await isolatedHosts(t, {
    a: async () => { inputs.push('a'); entered.resolve(); await release.promise; throw Error('runtime stopped'); },
    b: async () => { inputs.push('b'); },
  });
  const a = await request('a', 'click', observations.get('a'));
  const running = group.execute([a]);
  await entered.promise;
  try {
    await assert.rejects(hosts.get('a').close(), /executing operation/);
    // Once claimed, cancellation cannot steal the pending request or imply no input.
    await assert.rejects(fs.rename(path.join(directory, 'a', 'pending', `${a.request_id}.json`),
      path.join(directory, 'a', 'cancelled', `${a.request_id}.json`)), { code: 'ENOENT' });
    const b = await request('b', 'click', observations.get('b'));
    const healthy = (await group.execute([b]))[0].response;
    assert.equal(healthy.ok, true);
    assert.equal(healthy.request_id, b.request_id);
    assert.equal(healthy.session_id, hosts.get('b').binding.session_id);
  } finally { release.resolve(); }
  const stopped = (await running)[0].response;
  assert.equal(stopped.ok, false);
  assert.equal(stopped.outcome, 'outcome_unknown');
  assert.equal(stopped.request_id, a.request_id);
  const disk = JSON.parse(await fs.readFile(path.join(directory, 'a', 'responses', `${a.request_id}.json`), 'utf8'));
  assert.deepEqual(disk, stopped);
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'a', 'host.json'), 'utf8')).status, 'stopped');
  const retry = await group.execute([a, await request('b')]);
  assert.match(retry[0].error, /unavailable/);
  assert.equal(retry[1].response.ok, true);
  assert.deepEqual(inputs, ['a', 'b']);
});
