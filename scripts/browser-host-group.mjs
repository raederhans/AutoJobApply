/** Explicit attended scheduling only. No polling loop and no automatic execution. */
export function createBrowserHostGroup(entries, { clock = () => performance.now() } = {}) {
  if (!Array.isArray(entries) || !entries.length) throw Error('Host entries required');
  const jobs = new Map();
  const tabs = new Set();
  for (const { job_id, host } of entries) {
    const binding = host.binding;
    if (!job_id || jobs.has(job_id) || tabs.has(binding.target.tab_id) ||
        binding.phase !== 'prepare' || binding.target.runtime !== 'iab') {
      throw Error('Unique jobs and IAB prepare tabs required');
    }
    jobs.set(job_id, host);
    tabs.add(binding.target.tab_id);
  }
  const scheduled = new Set();
  const waiting = [];
  let active = 0;
  const counts = { admitted: 0, settled: 0, duplicate_rejections: 0, host_errors: 0, active_highwater: 0 };
  const spans = Object.fromEntries(['group_slot_wait', 'host_execute'].map(name =>
    [name, { samples: 0, unavailable: 0, total_ms: 0, max_ms: 0 }]));
  // Timing is advisory: an unavailable or regressing clock cannot affect input.
  const stamp = () => {
    try { const n = clock(); return Number.isFinite(n) ? n : null; }
    catch { return null; }
  };
  function recordSpan(name, start, end) {
    const span = spans[name];
    const elapsed = start === null || end === null ? null : end - start;
    if (elapsed === null || !Number.isFinite(elapsed) || elapsed < 0 ||
        !Number.isFinite(span.total_ms + elapsed)) { span.unavailable++; return; }
    span.samples++;
    span.total_ms += elapsed;
    span.max_ms = Math.max(span.max_ms, elapsed);
  }
  async function acquire() {
    if (active < 2) { active++; counts.active_highwater = Math.max(counts.active_highwater, active); return; }
    await new Promise(resolve => waiting.push(resolve));
  }
  function release() {
    const next = waiting.shift();
    if (next) next();
    else active--;
  }
  async function executeOne({ job_id, request_id }) {
    // Reserve before waiting for a slot: a second caller must review afresh,
    // rather than replaying its old observation after the first write finishes.
    if (scheduled.has(job_id)) {
      counts.duplicate_rejections++;
      return { job_id, error: 'Host already scheduled or executing' };
    }
    scheduled.add(job_id);
    counts.admitted++;
    const queuedAt = stamp();
    await acquire();
    const startedAt = stamp();
    recordSpan('group_slot_wait', queuedAt, startedAt);
    try { return { job_id, response: await jobs.get(job_id).execute(request_id) }; }
    catch (error) { counts.host_errors++; return { job_id, error: String(error.message) }; }
    finally {
      recordSpan('host_execute', startedAt, stamp());
      counts.settled++;
      scheduled.delete(job_id); release();
    }
  }
  return {
    // Bounded aggregates only; no job IDs, request IDs, values or page content.
    // Host execute includes its own validation/readback and possible waits. It
    // is not pure browser time; parallel span totals are not batch wall time.
    metrics() {
      return { schema_version: 1, scope: 'reviewed_group_slot_and_host_execute',
        additive_wall_time: false, ...counts, active, waiting: waiting.length,
        scheduled: scheduled.size,
        spans: Object.fromEntries(Object.entries(spans).map(([name, span]) => [name, { ...span }])) };
    },
    // Caller reviews each returned request against the latest page observation.
    async peek() {
      return Promise.all([...jobs].map(async ([job_id, host]) => {
        try { return { job_id, requests: await host.peek(), metrics: host.metrics() }; }
        catch (error) { return { job_id, requests: [], error: String(error.message) }; }
      }));
    },
    async execute(reviewed) {
      const seen = new Set();
      for (const item of reviewed) {
        if (!jobs.has(item.job_id) || seen.has(item.job_id) || typeof item.request_id !== 'string') {
          throw Error('Select at most one reviewed request per known job');
        }
        seen.add(item.job_id);
      }
      // Both slots are shared across calls. A completed operation releases its
      // slot immediately even if the other job is slow; results retain review order.
      return Promise.all(reviewed.map(executeOne));
    },
  };
}
