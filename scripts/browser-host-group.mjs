/** Explicit attended scheduling only. No polling loop and no automatic execution. */
export function createBrowserHostGroup(entries) {
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
  async function acquire() {
    if (active < 2) { active++; return; }
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
    if (scheduled.has(job_id)) return { job_id, error: 'Host already scheduled or executing' };
    scheduled.add(job_id);
    await acquire();
    try { return { job_id, response: await jobs.get(job_id).execute(request_id) }; }
    catch (error) { return { job_id, error: String(error.message) }; }
    finally { scheduled.delete(job_id); release(); }
  }
  return {
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
