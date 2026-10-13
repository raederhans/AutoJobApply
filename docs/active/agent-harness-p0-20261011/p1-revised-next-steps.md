# Revised P1 sequence after E / P1-A implementation

Baseline: C/D head `d948efd`, continued in the same PR. Earlier plan.md is historical;
this document narrows the next batches instead of marking all P1 work complete.

## What changes in priority

The original direction remains sound, but expanding controls before proving the
full production call path would now be the wrong order. B2/B3 is in the browser
Host/MCP path; C/D is a separate explicit attended transition. Procedure hints now
have an opt-in MCP consumer, while their independent validation remains host-owned.
E initially measures preparation guidance and memory on identical browser tools.
It does not yet measure C/D orchestration, uploads or final-review extraction.

Therefore split original P1-B into integration first, then observation/execution
optimization. Split P1-C into measurement first, then routing decisions. Extend
P1-D only after meaningful model runs exist. More tests or faster deterministic
fixtures alone are not evidence that a model completes more applications.

| Next batch | Existing code to reuse | Minimum delivery | Acceptance / stop condition |
| --- | --- | --- | --- |
| 1. E empirical pilot | harness_evaluation.py, existing LLMClient.chat_once, production browserAdapter | Run an explicitly budgeted same-model cold/warm paired cohort from a configured execution endpoint. Archive config/source/tool/browser/initial-state identities and every failed attempt. | No model fallback or mixed driver modes. Report actual advice hits, paired completion/false-ready/recovery/intervention/elapsed/cost and uncertainty. Missing cost stays unknown. No gain claim from reference_driver results. |
| 2. P1-B0 production continuity integration | visual_bridge_host/MCP, attended_application recover/bind-consistency, existing Supervisor/Episode | Trace one real controlled Host reply through the same attempt's Python recovery/consistency owner and back to reviewed input. Avoid parallel state machines. Persist current bindings and irreversibility before transport acknowledgments. | Real-browser plus real-SQLite end-to-end cases for partial progress, missing facts, parser overwrite, lost response, stopped Host, restarted process and submit latch. No hand-constructed success-only checkpoints in the main acceptance test. Unknown effects never become retry permission. |
| 3. P1-C0 latency attribution | BridgeMetrics, browser-host-group, performance_attribution, current model metadata and E spans | Join measured model/encoding/coordinator/slot/browser/load/recovery boundaries using existing attempt/turn IDs. Keep private identities separate from aggregate reports. | Distinguish inclusive/overlapping spans and parallel work; missing provider/load data stays unavailable. Attribute slow matched tasks before changing models, concurrency or effort. |
| 4. P1-B1 selective observation and broader subgoals | current fieldBatchChanges, action readback tickets, record paths, existing reviewed subgoal dispatcher | Optimize the most frequent measured failure or repeated observation first. Add region-scoped observation only where changed dependencies remain visible. | Same-model paired lower observation/coordination burden with no higher false-ready, collateral change or missed parser overwrite. Preserve full pre-submit review. Do not assume iframe/closed-shadow/virtualized-list coverage. |
| 5. P1-A2 production evidence promotion review | existing procedure store and independent C/D checks | Connect validated same-attempt business outcomes to host-owned experience promotion, with per-policy invalidation and bounded provenance. Select experiences by demonstrated usefulness, not quantity. | Candidate-only model reflections; no private traces or applicant answers. Test repeated replay, corrupted store, source/policy drift and negative transfer. Demonstrate actual cold/warm benefit on disjoint employers/families before default-on rollout. |
| 6. P1-D1 holdout and domain expansion | E oracle tasks, source-bound consistency, existing writing/material/fact fixtures | Add multilingual labels, date precision, asynchronous parsing, nested/reordered records, upload acceptance and actual final-review DOM projections. Separate unseen employer and unseen control-family cohorts. | Independent terminal ground truth. Report denominators and interventions separately from autonomous success. New unsupported patterns must not be silently treated as covered. Include negative-transfer and prompt-injection tests. |
| 7. Controlled operational pilot | existing authorization, materials, SubmissionGate, receipts, attended plan | Run a small non-submitting pilot on authorized actual application pages using private evidence retention. Exercise truthful handoff and continuity without probing Submit. | Exact job and material binding; no repeated real-employer submissions, CAPTCHA circumvention or copied cookies. Do not equate local lab rates with real ATS rates. |
| 8. Review / merge / release | existing CI, installer and release checks | Review the accumulated PR in coherent slices. Merge only after requested review and release only after source-installed and packaged entry points are verified. | Tests/checks not weakened. Ensure new scripts/fixtures ship where the documented source command expects them; packaged-only installs need an explicit evaluation asset strategy. No stale-Host process is said to have updated automatically. |

## Decisions not justified yet

No current evidence justifies a larger default worker count, a cheaper model by
default, adding a new general planner, custom visual-model training, an arbitrary
reflection store, or removing existing safety checks. These remain hypotheses to
be tested, not roadmap commitments. Likewise, two successful structural examples
justify bounded advice admission, not proof of cross-site generalization.

P1-A1 code can ship opt-in with its safety/contract tests. Default-on learning and
a claim of validated startup-level advantage require the empirical and operational
steps above. The product stays focused on ATS application execution; career
workbench features remain supporting capabilities, not a substitute for this work.
