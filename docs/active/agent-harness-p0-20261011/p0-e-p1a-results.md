# E and P1-A: interactive preparation evaluation and validated procedural advice

## Baseline and honest scope

Continue PR #21 from `d948efde62a8f80a2d4ca97d055776cc0ed4a7c1`.
Main read at the start: `64d2ba6a76457abe8648e8b5cf34665d206be6a3`.
Career M1-M4 and A/B/C/D are retained. This is a source implementation, not a
release, user-installation update, merge, or employer submission.

E now has an executable interactive evaluation lane. The model selects actions
from live observations through the repository's existing production browserAdapter.
This is not runtime_evaluation.py's scripted replay renamed as a model benchmark.
The first contrast is deliberately **browser preparation**, not the whole application
pipeline: C/D orchestration, uploads, navigation, submission and actual hiring
outcomes are outside its measured scope. All arms retain identical low-level
safety guards and offer identical browser tool schemas, including bounded subgoals.

P1-A implements value-free procedure/failure candidates, evidence-gated promotion,
frozen current-context lookup and opt-in hints in the existing worker MCP response.
It does not learn free-form rules or automatically execute/replay a remembered plan.
Real-user progress stays with attended/Episode state; applicant facts stay with the
existing fact/material authorities. Neither is copied into general procedure memory.

## Evidence and reuse

- Existing recipe_experience.py only admitted routine text/native-select templates.
  Its SQLite transaction, event deduplication, lifecycle and capacity logic are
  reused through small template/decoder/promotion hooks, not another database engine.
  Routine v1 behavior retains its single-validation policy. New procedure policy
  requires two different execution identities with independent host postconditions.
- Existing B2/B3 supplies record scope, controls, semantic uncertainty and business
  postconditions. Both the new laboratory and procedure applicability use it.
- Existing LLMClient.chat_once supplies one request with usage metadata. The model
  driver does not retry/fallback silently. API credentials are never read from user
  files, printed, written into reports or accepted from model tool arguments.
- Existing browser-semantic-preparation fixture is reused in a fresh Chromium
  context. Holdout changes the employer's fictional values, all relevant element
  identifiers, and fieldsets into ARIA-group/heading records.
- C/D currently remains an explicit attending-Host integration seam. A pure advice
  cache or browser-only benchmark does not establish that the complete production
  loop automatically invokes recover/bind-consistency. This is a next integration
  priority, not a reason to claim the entire agent has already become autonomous.

## E: runnable entry and comparison semantics

From a source checkout with its ordinary development dependencies and Chromium:

```powershell
python -m applypilot.apply.harness_evaluation --root . --output ..\eval-results\preflight-01 --model <configured-model> --repeats 1
```

This is preflight only. It makes no model requests and reports `not_run`, recording
source hashes, tasks, allowed tools, safety policy, task-order seed and budgets.
It never loads the applicant profile or the production jobs database. Output must
be a new explicit directory, not an existing run to overwrite.

To use an already-configured model environment, with the exact same model name:

```powershell
python -m applypilot.apply.harness_evaluation --root . --output ..\eval-results\model-01 --model <configured-model> --run-model --train --repeats 1 --max-turns 8 --max-decisions 128
```

`--run-model` explicitly permits actual model requests. Missing configuration is a
visible `not_run` failure, never a silent reference-driver fallback. Model transport
uses a subprocess deadline and the existing single-call client. Browser processes
are isolated per trial and disposed, including a bounded process-tree cleanup.
A model reply arriving after the trial deadline cannot trigger late input.

Seven scenario types: native school, asynchronous custom school, repeated employment,
parser overwrite requiring correction, duplicate options, missing required fact and
an option click without committed selection. The last three require a truthful
handoff rather than guessed completion. They are not counted as autonomous successes.

Arms are basic_skill, current_harness, harness_plus_procedure. The first lacks the
optional B2 annotation and extended preparation guidance; all use the same physical
controls, action allowlist, preflight guards and facts. The third differs from the
second only by applicable validated advice. Cold/warm cohorts use the same frozen
snapshot; advice is actually exposed only in the third arm's warm cohort, with hit
counts recorded. A warm label with zero available hints is not an effective warm hit.
Thus this estimates the stated preparation guidance/experience contrasts, not the
value of removing all Harness safety or of changing to a different model/tool set.

Task order is reproducibly randomized and recorded. Each task/repetition has all
three arms in both cohorts. Comparisons reject missing/duplicate arms and unequal
model config, tool schema, safety policy, initial **observed DOM** hash, memory hash
or Chromium version. Configured model/endpoint identity is checked on each response;
the provider's resolved model-weights revision is not available from the current client.
A global decision cap prevents unbounded spend. An incomplete cohort remains visible
and cannot publish an apparently complete paired improvement.

The evaluator-only oracle independently reads final native values/selection display,
validation, untouched peer fields and parser events. It is not a model tool and does
not accept the model's ready flag as truth. False-ready, substantive wrong values,
unresolved conditions, collateral changes, correct handoff, autonomous/assisted
completion, recovery, intervention requests/time, attempts, reads/writes, elapsed
and known usage/cost are separate. A test-only injected human callback measures its
own elapsed time; default unattended runs record handoff wait as unavailable, not zero.

Optional explicit input/output/cached prices produce labelled estimates only when
usage is sufficient. Missing pricing, failed responses or unsupported counters leave
cost unknown. Raw provider-error messages and credentials are not persisted. Context
encoding and driver/browser wall spans are recorded; coordination-only and page-load
attribution remain unavailable. Nested spans must not be added to total elapsed time.
Paired output includes task-cluster bootstrap intervals where meaningful, with explicit
small-fixture scope. Repeated runs of one fixture are not independent real employers.

Outputs: manifest.json, append-only trials.jsonl, frozen-procedures.json, summary.json,
comparisons.json and, only for training, a separate value-free training-procedures.db.
No generated results/database are committed to the source repository.

## P1-A: storage, review, applicability and production consumption

ProcedureTemplate permits only known subgoals, record categories, control kinds,
version identifiers and code-maintained lesson IDs. There is no slot for CV text,
answers, company names, screenshots, selectors, coordinates or arbitrary reflections.
Failure lessons describe independently observed symptoms, not invented causes.

Trusted Host code calls ProcedureExperienceStore.record_outcome with a fresh observed
form, bounded plan/result, execution identity and a callable independent verifier.
One verified observation remains candidate. Two distinct verified runs promote it;
re-reading the same run does not. Structure-only evidence cannot promote a procedure.
A failed independent check invalidates it. Deduplication saturation cannot manufacture
another independent check. Invalidated procedures are not revived by capacity eviction;
when tombstones fill capacity, new writes fail instead of silently weakening invalidation.

freeze() takes an immutable value-free snapshot of validated entries. Lookup checks
current explicit record identity, supported controls, visibility/coverage and current
adapter/policy. Taint, unknown effects, missing facts or begun submission suppress use.
Frozen lookup never updates the store. Holdout cannot receive a training-store writer;
employer, page-family and task splits are checked before execution.

For production worker hints, the attending process can explicitly set the absolute
`APPLYPILOT_PROCEDURE_MEMORY_DB` path to its validated private procedure database before
starting the existing visual bridge MCP. Use a dedicated procedure database: the
legacy routine-store writer retains its original eviction policy and must not share
this live write destination. With no setting, behavior is unchanged.
The MCP adds bounded procedure_advice to successful same-session/epoch IAB preparation
observations only. It does not approve requests, select facts, change outcomes, write
experience from worker claims or issue browser input. Corrupt/unavailable optional
memory leaves the original response intact. Unknown-effect and submit-phase responses
never gain preparation hints. This is an opt-in rollout, not an automatic installation
or claim that all live attempts now use memory.

## Validation actually performed before upload

The container could not resolve GitHub for cloning. Focused previous delivery files
were recovered locally, and the current recipe store and MCP sources were retrieved
through GitHub and verified against exact blob hashes before edits:

- recipe_experience.py: `c9a2c3cdf2b8ce645d5fab93e1c50f49a6bffccf`
- visual_bridge_mcp.py: `251022fa3211bfd62f0d2b495ac9cebb5baa8bbf`

Local command:

```text
PYTHONPATH=src python -m pytest -q tests/test_procedure_experience.py tests/test_harness_evaluation.py -m 'not browser' -k 'not mcp'
62 passed, 3 deselected
```

The three exclusions are the actual Chromium pipeline test and two full-repository
MCP integration tests. Local partial checkout does not contain all existing production
browser/transport modules. Compilation, Node syntax and focused diff checks passed.
The actual browser integration test is authored for the repository's existing browser
CI tier: six training trials, seven unseen-DOM holdouts and a false-ready negative
control. Its driver is explicitly reference_driver, not a real model. Remote exact-head
CI results must be read separately; prior green C/D results do not verify this change.

No model service is configured in this execution environment. Therefore **no live-model
ablation, real ATS completion improvement, time/cost saving or successful autonomous
recovery rate is claimed**. E's runnable machinery and deterministic/DOM regression
evidence are distinct from the still-required empirical live-model result.


## Review corrections

The first E/P1-A CI run (`38144884438`, head `6cd78a0`) stopped the core
lane at five lint findings. Type exceptions, successive-pair iteration and
loop-variable capture were corrected without changing lint configuration.
A separately reproduced grading bug allowed a requested handoff after wrong
final values to count as correct. Correct handoff now also requires no
independently observed substantive error and no unknown effect. The added
regression failed before correction and passes afterwards. Optional provider
wall time is retained separately; unavailable cache usage keeps cost unknown.
The final exact-head CI result is recorded in the PR conversation.
