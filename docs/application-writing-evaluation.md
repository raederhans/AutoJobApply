# Standalone writing evaluation

`scripts/evals/writing-cases.json` contains **24 application questions and 8 cover-letter cases**.
All candidate projects, employers and roles are synthetic. It contains no profile,
mailbox, real applicant documents, live listing, account or application data.

The question cases cover compound company motivation and role fit, ownership,
controlled trials, hypothetical technical choices, absent failure/conflict episodes,
word and UTF-16 limits, missing company context, absent or language-mismatched
voice samples, and two questions sharing different facets of the same project.
The cover cases are four initial letters and four revisions across frontend,
applied AI, product and client delivery. Company scale, stage and delivery model
are separate attributes backed by distinct fictional company-source quotations.

Each genre has development and holdout cases. Inspect development outputs to tune
prompts; reserve holdout outputs for reporting a change. Do not claim an unseen
holdout after using it to tune the writer. The declared required parts and critical
boundaries guide review, not fixed prose matching or an automatic quality oracle.

## Offline checks

`validate_suite(suite)` returns normalized contexts and questions, checking schema,
source quotations, context references, exact job binding and earlier sibling references.
`evaluate_recorded(suite, results)` accepts a list of result records or an earlier
report containing `records`. It recomputes checks against **current original inputs**:

- Original context and normalized question/request digests.
- Artifact context selection, current task, exact text and draft text consistency.
- Eligible citations, numeric tokens, explicit length limits and output surface.
- Exact sibling artifacts and the initial artifact used for a cover revision.
- The absence of submission authority.

Saved artifact `status`, `validation.passed` and model review are never sufficient
for a current contract pass. The two `recorded_negatives` deliberately claim a saved
pass while inventing a percentage and treating a style sample as personal evidence.
Evaluate them directly with `evaluate_recorded(suite, suite["recorded_negatives"])`;
both must fail recomputed checks.

The report separates deterministic `contract` results from `semantic_review`.
A recorded review has provenance `recorded_model_review_not_reexecuted`; it is not
live evidence or verified truth. A citation can exist while the prose exaggerates
its meaning. Deterministic passes never prove factual entailment, coverage,
naturalness, personal voice, readiness for submission or that an AI detector can
identify the text.

## Optional model runs

`run_benchmark(suite, client=client, case_ids=None, variant="new", max_repairs=0, editorial=True)`
runs the injected client sequentially and records failures per case. It constructs
no client itself and performs no browser, profile, database or application I/O.
The caller owns explicit output-file persistence. Select cases by exact case IDs;
include a referenced earlier sibling case in the same call before its dependent
question. A revision case generates its initial cover first and preserves that
artifact beside the revision.

The new variant uses one editorial pass after a structurally valid initial draft,
then validates and reviews the resulting prose. `editorial=False` (CLI
`--no-editorial`) disables only this extra pass for an ablation; it does not skip
factual review. The artifact retains the original, proposed revision, concrete
edit reasons and subsequent review/repair attempts. Record that setting with the
model and repair budget. Do not credit manual rewrites to the automated editor.

`variant="baseline"` uses the same injected client and JSON-call settings. Its
question prompt preserves the current generic 2–3 sentence instruction: connect a
JD detail with a real achievement. Its cover prompt preserves the documented
3–5 paragraph shape, strongest experience, complementary experience and specific
employer connection. This is a **prompt-shape baseline**, not parity with the live
browser workflow, profile-dependent cover builder or its retries and rescoring.
It reads only selected synthetic evidence. Baseline outputs remain raw `{text}`:
they receive no fabricated claims or citation-based contract pass.

Record the generator model, provider response metadata, exact input versions,
repair budget, selected split and failures when comparing runs. Model outputs may
vary; one small run cannot establish an improvement across all jobs or models.

## Optional pairwise review

`build_comparison_prompt(case, context, a, b)` creates a current-task rubric using
selected evidence; use the normalized case and context returned by `validate_suite`.
`compare_outputs(client, case, context, a, b, order="ab")`
asks an injected judge to choose `A`, `B`, `tie` or `both_fail`. Here `a` is the new
writer and `b` the baseline. The caller can randomize `order` between `ab` and `ba`;
the report records displayed order, original texts and original winner mapping.
`summarize_comparisons(comparisons)` reports exact win/tie/loss/both-fail counts.
Unjudged cases are never counted as ties.

`compare_outputs_balanced(client, case, context, a, b)` runs both AB and BA with
the same inputs. `win` consistently means `a` is preferred to `b`. It reports a
conclusive result only when the two normalized judgments agree; otherwise it
records `inconclusive` with `consensus="order_sensitive"`. The report preserves
both judgments, displayed order, exact inputs and response metadata. Summaries
count disagreements separately, never as ties or successful improvements. This
reduces reliance on one ordering; it does not eliminate model bias.

`tests/fixtures/writing-editor-calibration.json` supplies five small, fictional
pairs: necessary detail beats an incomplete short answer, concrete evidence beats
boilerplate, invented outcomes fail despite fluent wording, supported role-transfer
inference is allowed, and punctuation alone is weak evidence. Expected preferences
are authored calibration targets, not user ratings or measured model results.
The evaluation should not infer a general preference for brevity, a universal
company-name requirement, or full personal-voice imitation from these examples.

The rubric prioritizes factuality, ownership and critical task coverage, then
relevance, specific evidence, expression, length and eligible voice. An honest
missing-fact answer can still be incomplete. The judge is a fallible preference
signal, especially when the same model generated and judged both outputs. Review
the actual paragraphs and source boundaries; do not present judge counts as truth
or proof of naturalness. Every report retains `authority="none"` and
`submission_ready=false`.

## First local run: 2026-10-09

All 32 cases ran using the configured DeepSeek V4 Pro client. The final writing
run recorded 26 reviewed drafts, 5 missing-fact drafts and 1 draft needing a
revision. A later deterministic check for leaked internal workflow notes rejected
one more saved cover; current structural checks therefore pass 25 of 32 outputs.
That repair used an observed holdout failure, so it is not a fresh holdout result.

The same-model pairwise judge preferred the new answer on 19/24 questions and
4/8 covers (23 wins, 9 losses overall; holdout 9 wins, 7 losses). These figures
**do not establish quality acceptance**. The primary Codex agent's source-to-text
inspection found fabricated failure episodes, unrecorded team responsibilities,
and unsupported chronology or causal effects that the model reviewer missed.
One judgment even preferred invented team details because they sounded more
specific. Neither the primary-agent inspection nor the pairwise judge was a
human/user evaluation. Personal voice was not evaluated on a real applicant.

The workflow is usable for inspectable draft preparation and evaluation, but
the current default model review has not passed the semantic quality threshold.
Do not consume `reviewed_draft` as a factual approval or form-fill authorization.
Retain failures and test any review improvements against a new held-out set.
