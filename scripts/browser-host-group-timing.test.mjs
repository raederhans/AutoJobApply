import test from 'node:test';
import assert from 'node:assert/strict';
import { setImmediate as nextTurn } from 'node:timers/promises';
import { createBrowserHostGroup } from './browser-host-group.mjs';

function gate() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}
const entry = (job_id, execute) => ({ job_id, host: {
  binding: { phase: 'prepare', target: { runtime: 'iab', tab_id: job_id } },
  execute, peek: async () => [], metrics: () => ({}),
} });
const reviewed = job_id => ({ job_id, request_id: `${job_id}-private-request` });

test('group separates slot waiting from host service across overlapping calls', async () => {
  let now = 0;
  const released = Object.fromEntries(['a', 'b', 'c'].map(id => [id, gate()]));
  const starts = [];
  const group = createBrowserHostGroup(['a', 'b', 'c'].map(id => entry(id, async () => {
    starts.push(id); await released[id].promise; return { owner: id };
  })), { clock: () => now });
  const first = group.execute(['a', 'b'].map(reviewed));
  const second = group.execute([reviewed('c')]);
  try {
    await nextTurn();
    assert.deepEqual(starts, ['a', 'b']);
    assert.equal(group.metrics().active, 2);
    assert.equal(group.metrics().waiting, 1);
    assert.equal(group.metrics().scheduled, 3);
    assert.match((await group.execute([reviewed('c')]))[0].error, /already scheduled/);
    now = 10; released.b.resolve(); await nextTurn();
    assert.deepEqual(starts, ['a', 'b', 'c']); // Slow A does not hold the free slot.
    now = 25; released.c.resolve(); await nextTurn();
    now = 40; released.a.resolve();
    assert.deepEqual(await first, [{ job_id: 'a', response: { owner: 'a' } },
      { job_id: 'b', response: { owner: 'b' } }]); // Existing result shape unchanged.
    assert.deepEqual(await second, [{ job_id: 'c', response: { owner: 'c' } }]);
    const m = group.metrics();
    assert.deepEqual([m.admitted, m.settled, m.duplicate_rejections, m.active_highwater], [3, 3, 1, 2]);
    assert.deepEqual([m.active, m.waiting, m.scheduled], [0, 0, 0]);
    assert.deepEqual(m.spans.group_slot_wait, { samples: 3, unavailable: 0, total_ms: 10, max_ms: 10 });
    assert.deepEqual(m.spans.host_execute, { samples: 3, unavailable: 0, total_ms: 65, max_ms: 40 });
    assert.equal(m.additive_wall_time, false); // 65 is not the 40ms batch wall time.
  } finally {
    for (const g of Object.values(released)) g.resolve();
    await Promise.all([first, second]);
  }
});

test('timing metrics contain no identities, page data or mutable internal state', async () => {
  const privateId = 'private-applicant@example.test';
  const group = createBrowserHostGroup([entry(privateId, async () => ({ value: 'private-answer' }))]);
  await group.peek();
  assert.equal(group.metrics().admitted, 0); // Inspection does not execute anything.
  await group.execute([reviewed(privateId)]);
  const m = group.metrics();
  assert.doesNotMatch(JSON.stringify(m), /private|example|answer|request_id|job_id/);
  m.admitted = 500;
  m.spans.host_execute.samples = 500;
  assert.equal(group.metrics().admitted, 1);
  assert.equal(group.metrics().spans.host_execute.samples, 1);
});

test('host rejection releases its slot and timing cannot turn it into success', async () => {
  const group = createBrowserHostGroup([entry('a', async () => { throw Error('synthetic host rejection'); }),
    entry('b', async () => ({ ok: false, outcome: 'outcome_unknown' }))]);
  const out = await group.execute(['a', 'b'].map(reviewed));
  assert.deepEqual(out, [{ job_id: 'a', error: 'synthetic host rejection' },
    { job_id: 'b', response: { ok: false, outcome: 'outcome_unknown' } }]);
  assert.equal(group.metrics().host_errors, 1); // Counts thrown errors, not business outcomes.
  assert.equal(group.metrics().settled, 2);
  assert.equal(group.metrics().scheduled, 0);
  assert.equal(group.metrics().spans.host_execute.samples, 2);
});

for (const [name, clock] of [['throwing', () => { throw Error('clock unavailable'); }],
  ['NaN', () => NaN], ['infinite', () => Infinity], ['non-number', () => '1']]) {
  test(`${name} clock leaves execution intact and timing explicitly unavailable`, async () => {
    let calls = 0;
    const group = createBrowserHostGroup([entry('a', async () => { calls++; return { ok: true }; })], { clock });
    assert.equal((await group.execute([reviewed('a')]))[0].response.ok, true);
    assert.equal(calls, 1);
    for (const span of Object.values(group.metrics().spans)) {
      assert.equal(span.samples, 0); assert.equal(span.unavailable, 1);
    }
    assert.equal(group.metrics().active, 0);
  });
}

test('a regressing clock is not negative latency', async () => {
  let now = 10;
  const group = createBrowserHostGroup([entry('a', async () => { now = 0; return {}; })], { clock: () => now });
  await group.execute([reviewed('a')]);
  assert.deepEqual(group.metrics().spans.host_execute, { samples: 0, unavailable: 1, total_ms: 0, max_ms: 0 });
  assert.equal(group.metrics().settled, 1);
});

test('invalid review does not admit work or create timing samples', async () => {
  let writes = 0;
  const group = createBrowserHostGroup([entry('a', async () => { writes++; })]);
  await assert.rejects(group.execute([reviewed('a'), reviewed('a')]));
  await assert.rejects(group.execute([reviewed('unbound')]));
  assert.equal(writes, 0);
  assert.equal(group.metrics().admitted, 0);
  assert.equal(group.metrics().spans.group_slot_wait.samples, 0);
});
