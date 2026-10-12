/** Private transport for the existing attended SQLite owner. No planner or pump. */
import fs from 'node:fs/promises';
import path from 'node:path';
import { createHash, randomUUID } from 'node:crypto';
import { ControlNotReady } from './browser-form-state.mjs';

const sha = value => createHash('sha256').update(value).digest('hex');
const uuid = value => typeof value === 'string' && /^[a-f0-9-]{36}$/.test(value);
const form = inspection => {
  const forms = inspection.content.flatMap(block => {
    if (block.type !== 'text') return [];
    try { const value = JSON.parse(block.text); return value.form_state ? [value.form_state] : []; }
    catch { return []; }
  });
  if (forms.length !== 1) throw new ControlNotReady('Continuity requires one actual form observation');
  return forms[0];
};

export async function createAttendedHostJournal({ root, binding, adapter, writeJson }) {
  const base = path.join(root, 'continuity');
  for (const name of ['reviews', 'permits', 'used', 'completed']) await fs.mkdir(path.join(base, name), { recursive: true });
  const inspect = async () => ({ session_id: binding.session_id, target: structuredClone(binding.target),
    phase: binding.phase, submission_authorized: false, content: await adapter.observe({ mode: 'dom' }) });
  const read = async file => { const raw = await fs.readFile(file, 'utf8'); return { value: JSON.parse(raw), sha: sha(raw) }; };
  const owner = async () => {
    const { value } = await read(path.join(base, 'owner.json'));
    if (value.schema_version !== 1 || value.host_session_id !== binding.session_id ||
        value.host_token_epoch !== binding.token_epoch || value.tab_id !== binding.target.tab_id || !value.attempt_id) {
      throw Error('Attended owner binding mismatch');
    }
    return value;
  };
  const validate = request => {
    if (!uuid(request.request_id) || request.session_id !== binding.session_id ||
        request.token_epoch !== binding.token_epoch || request.surface !== binding.surface ||
        request.phase !== binding.phase || JSON.stringify(request.target) !== JSON.stringify(binding.target) ||
        !Number.isFinite(request.deadline_at) || request.deadline_at <= Date.now() / 1000) {
      throw Error('Expired or mismatched continuity request');
    }
  };
  return {
    async check(requestId) {
      await owner();
      await fs.access(path.join(base, 'permits', `${requestId}.json`));
    },
    async review(requestId) {
      if (!uuid(requestId)) throw Error('Invalid request id');
      const bound = await owner();
      const request = await read(path.join(root, 'pending', `${requestId}.json`));
      validate(request.value);
      const inspection = await inspect();
      form(inspection);
      const review = { schema_version: 1, review_id: randomUUID(), attempt_id: bound.attempt_id,
        job_url: bound.job_url, host_token_epoch: binding.token_epoch, request: request.value,
        request_sha256: request.sha, inspection, observed_at: new Date().toISOString() };
      await writeJson(path.join(base, 'reviews', `${review.review_id}.json`), review);
      return review;
    },
    async admit(request) {
      validate(request);
      const bound = await owner();
      const permitPath = path.join(base, 'permits', `${request.request_id}.json`);
      const { value: permit } = await read(permitPath);
      if (!uuid(permit.review_id) || permit.attempt_id !== bound.attempt_id || permit.request_id !== request.request_id ||
          permit.host_session_id !== binding.session_id || permit.host_token_epoch !== binding.token_epoch ||
          permit.expires_at <= Date.now() / 1000) throw Error('Invalid or expired attended permit');
      const review = await read(path.join(base, 'reviews', `${permit.review_id}.json`));
      const pending = await read(path.join(root, 'claimed', `${request.request_id}.json`));
      if (review.sha !== permit.review_sha256 || pending.sha !== permit.request_sha256 ||
          JSON.stringify(pending.value) !== JSON.stringify(request)) throw Error('Reviewed request changed');
      // Consume once before any possible browser effect. SQLite already holds
      // this outstanding request; a crash cannot create fresh input permission.
      await fs.rename(permitPath, path.join(base, 'used', `${request.request_id}.json`));
      const current = await inspect();
      if (JSON.stringify(form(current)) !== JSON.stringify(form(review.value.inspection))) {
        throw new ControlNotReady('Page changed after owner review; no input performed');
      }
      return permit;
    },
    async complete(request, response, fresh = true) {
      const bound = await owner();
      const { value: permit } = await read(path.join(base, 'used', `${request.request_id}.json`));
      const completion = { schema_version: 1, attempt_id: bound.attempt_id, job_url: bound.job_url,
        review_id: permit.review_id, request_sha256: permit.request_sha256,
        observed_at: new Date().toISOString(), feedback: response,
        ...(fresh ? { inspection: await inspect() } : {}) };
      completion.observed_at = new Date().toISOString();
      await writeJson(path.join(base, 'completed', `${request.request_id}.json`), completion);
      // Only the Python owner publishes responses/ after committing C/D.
      return { request_id: request.request_id, attempt_id: bound.attempt_id, awaiting_owner: true };
    },
  };
}
