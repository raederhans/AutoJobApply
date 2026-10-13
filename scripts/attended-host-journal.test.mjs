import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { createVisualHost } from './visual-bridge-host.mjs';

const sha = raw => createHash('sha256').update(raw).digest('hex');
const write = (file, value) => fs.writeFile(file, JSON.stringify(value));

async function fixture(t, continuity = true) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-js-contract-'));
  let inputs = 0;
  let onObserve = () => {};
  const adapter = { surface: 'browser', tabId: 'synthetic-tab',
    observe: async () => {
      await onObserve();
      return [{ type: 'text', text: JSON.stringify({ form_state: { page_url: 'https://example.test/apply', fields: [] } }) }];
    },
    act: async () => { inputs++; },
  };
  const host = await createVisualHost({ directory: root, adapter, continuity,
    target: { runtime: 'iab', tab_id: adapter.tabId, application_url: 'https://example.test/apply' } });
  t.after(async () => { await host.close(); await fs.rm(root, { recursive: true, force: true }); });
  const owner = { schema_version: 1, attempt_id: randomUUID(), job_url: 'https://example.test/job',
    host_session_id: host.binding.session_id, host_token_epoch: host.binding.token_epoch, tab_id: adapter.tabId };
  if (continuity) await write(path.join(root, 'continuity/owner.json'), owner);
  async function request(change = {}) {
    const id = randomUUID();
    const value = { ...host.binding, request_id: id, operation: 'fill_control', arguments: {},
      created_at: Date.now() / 1000, deadline_at: Date.now() / 1000 + 60, ...change };
    await write(path.join(root, 'pending', `${id}.json`), value);
    return { id, value };
  }
  async function permit(queued, change = {}) {
    const review = await host.review(queued.id);
    const raw = await fs.readFile(path.join(root, 'continuity/reviews', `${review.review_id}.json`));
    const value = { ...owner, request_id: queued.id, review_id: review.review_id,
      request_sha256: sha(await fs.readFile(path.join(root, 'pending', `${queued.id}.json`))),
      review_sha256: sha(raw), expires_at: queued.value.deadline_at, ...change };
    await write(path.join(root, 'continuity/permits', `${queued.id}.json`), value);
    return value;
  }
  return { root, host, owner, request, permit, inputs: () => inputs,
    onObserve: callback => { onObserve = callback; } };
}

test('ordinary Host rejects unsupported or unbound request envelopes before claim/input', async t => {
  for (const change of [{ schema_version: 2 }, { request_id: randomUUID() }, { deadline_at: undefined },
    { deadline_at: '9999999999' }, { arguments: null }]) {
    await t.test(JSON.stringify(change), async t => {
      const f = await fixture(t, false);
      const observed = await f.request({ operation: 'observe' });
      const response = await f.host.execute(observed.id);
      const queued = await f.request({ observation_id: response.observation_id, ...change });
      await assert.rejects(f.host.execute(queued.id));
      assert.equal(f.inputs(), 0);
      await fs.access(path.join(f.root, 'pending', `${queued.id}.json`));
    });
  }
});

test('continuity review rejects an envelope whose identity does not match its filename', async t => {
  const f = await fixture(t);
  const queued = await f.request({ request_id: randomUUID() });
  await assert.rejects(f.host.review(queued.id));
  assert.equal(f.inputs(), 0);
});

test('Host execution and continuity review require canonical request filenames', async t => {
  const f = await fixture(t);
  for (const id of ['-'.repeat(36), randomUUID().toUpperCase()]) {
    await assert.rejects(f.host.execute(id), /Invalid request id/);
    await assert.rejects(f.host.review(id), /Invalid request id/);
  }
  assert.equal(f.inputs(), 0);
});

test('continuity rejects unsupported, unbound and nonfinite permit envelopes before input', async t => {
  for (const change of [{ schema_version: 2 }, { tab_id: 'other-tab' }, { job_url: 'https://example.test/other' },
    { expires_at: undefined }, { expires_at: '9999999999' }, { expires_at: null }]) {
    await t.test(JSON.stringify(change), async t => {
      const f = await fixture(t);
      const queued = await f.request();
      await f.permit(queued, change);
      await assert.rejects(f.host.execute(queued.id));
      assert.equal(f.inputs(), 0);
      await fs.access(path.join(f.root, 'continuity/permits', `${queued.id}.json`));
      await assert.rejects(fs.access(path.join(f.root, 'continuity/used', `${queued.id}.json`)), { code: 'ENOENT' });
    });
  }
});

test('a permit expiring during fresh observation makes no input and journals original failure', async t => {
  const f = await fixture(t);
  const queued = await f.request();
  const permit = await f.permit(queued);
  const originalNow = Date.now;
  t.after(() => { Date.now = originalNow; });
  f.onObserve(() => { Date.now = () => (permit.expires_at + 1) * 1000; });
  // The worker deadline remains later than the owner permit deadline.
  permit.expires_at = queued.value.deadline_at - 30;
  await write(path.join(f.root, 'continuity/permits', `${queued.id}.json`), permit);
  const delivery = await f.host.execute(queued.id);
  assert.equal(delivery.awaiting_owner, true);
  assert.equal(f.inputs(), 0);
  const completion = JSON.parse(await fs.readFile(path.join(f.root, 'continuity/completed', `${queued.id}.json`)));
  assert.equal(completion.feedback.outcome, 'failed');
  assert.match(completion.feedback.content[0].text, /permit.*expired/i);
});

test('valid reviewed continuity executes once and withholds worker response until the owner', async t => {
  const f = await fixture(t);
  const queued = await f.request();
  await f.permit(queued);
  assert.equal((await f.host.execute(queued.id)).awaiting_owner, true);
  assert.equal(f.inputs(), 1);
  await assert.rejects(fs.access(path.join(f.root, 'responses', `${queued.id}.json`)), { code: 'ENOENT' });
  const completed = await fs.readFile(path.join(f.root, 'continuity/completed', `${queued.id}.json`));
  assert.equal(JSON.parse(completed).feedback.outcome, 'completed');
  await assert.rejects(f.host.execute(queued.id));
  assert.equal(f.inputs(), 1);
  assert.deepEqual(await fs.readFile(path.join(f.root, 'continuity/completed', `${queued.id}.json`)), completed);
});

test('changed reviewed request bytes or revoked permit cannot reach input', async t => {
  for (const change of ['request', 'revocation']) {
    await t.test(change, async t => {
      const f = await fixture(t);
      const queued = await f.request();
      await f.permit(queued);
      if (change === 'request') await write(path.join(f.root, 'pending', `${queued.id}.json`),
        { ...queued.value, arguments: { value: 'changed after review' } });
      else await fs.unlink(path.join(f.root, 'continuity/permits', `${queued.id}.json`));
      await assert.rejects(f.host.execute(queued.id));
      assert.equal(f.inputs(), 0);
      await assert.rejects(fs.access(path.join(f.root, 'continuity/used', `${queued.id}.json`)), { code: 'ENOENT' });
      await assert.rejects(fs.access(path.join(f.root, 'responses', `${queued.id}.json`)), { code: 'ENOENT' });
    });
  }
});

test('continuity records unknown effect after input/readback disconnect and never replays it', async t => {
  const f = await fixture(t);
  const queued = await f.request();
  await f.permit(queued);
  f.onObserve(() => { if (f.inputs()) throw Error('Synthetic readback disconnect'); });
  assert.equal((await f.host.execute(queued.id)).awaiting_owner, true);
  const file = path.join(f.root, 'continuity/completed', `${queued.id}.json`);
  const raw = await fs.readFile(file);
  const completion = JSON.parse(raw);
  assert.equal(completion.feedback.outcome, 'outcome_unknown');
  assert.equal(completion.inspection, undefined);
  assert.equal(f.inputs(), 1);
  assert.equal(JSON.parse(await fs.readFile(path.join(f.root, 'host.json'))).status, 'stopped');
  await assert.rejects(f.host.execute(queued.id), /unavailable/);
  await assert.rejects(f.host.peek(), /closed/);
  await assert.rejects(f.host.review(queued.id), /unavailable/);
  assert.equal(f.inputs(), 1);
  assert.deepEqual(await fs.readFile(file), raw);
  await assert.rejects(fs.access(path.join(f.root, 'responses', `${queued.id}.json`)), { code: 'ENOENT' });
});
