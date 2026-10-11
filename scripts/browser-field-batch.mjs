/** Bounded preparation on one observed page. No clicks, uploads or submission. */
import { operateObservedControl, structureChanges, ControlNotReady } from './browser-form-state.mjs';

const textControls = new Set(['text', 'textarea', 'email', 'tel', 'url', 'search', 'number']);
const shape = form => JSON.stringify([form.page_url, form.protected_count, form.fields.map(f =>
  [f.field_key, f.selector, f.label, f.group_key, f.group, f.control, f.disabled, f.readonly,
    f.options.map(o => [o.value, o.label, o.selector, o.disabled, o.dom_identity])])]);
const value = f => JSON.stringify([f.value, f.checked, f.files, f.selected_display, f.selected_values]);

// Existing question metadata carries record purpose and native constraints.
// Compare it transiently; diagnostics below retain keys/counts, never answers,
// question text, validation messages or other candidate material.
const requirements = f => JSON.stringify([f.required, f.required_source,
  f.application_question?.text, f.application_question?.help_text,
  f.application_question?.section_path, f.application_question?.constraints,
  f.application_question?.completeness, f.application_question?.language]);
const validation = f => JSON.stringify([f.invalid, f.validation_message,
  f.value_source === 'unavailable', f.selection_source === 'unavailable']);
const coverage = form => JSON.stringify([form.coverage?.scope,
  form.coverage?.open_shadow_count, form.coverage?.iframe_count,
  form.question_coverage?.scope, form.question_coverage?.page_only,
  form.question_coverage?.whole_form, form.question_coverage?.iframe_count,
  form.question_coverage?.fields_truncated]);

function dependencyChanges(before, after, activeFieldKey) {
  const current = new Map(after.fields.map(f => [f.field_key, f]));
  const lists = { requirements_changed: [], validation_changed: [] };
  for (const old of before.fields) {
    const fresh = current.get(old.field_key);
    if (!fresh) continue; // Existing structural guard handles removed controls.
    if (requirements(old) !== requirements(fresh)) lists.requirements_changed.push(old.field_key);
    // Clearing the field being filled is expected. Changes to other fields
    // (including earlier verified steps) invalidate the independent batch plan.
    if (old.field_key !== activeFieldKey && validation(old) !== validation(fresh)) {
      lists.validation_changed.push(old.field_key);
    }
  }
  const counts = Object.fromEntries(Object.entries(lists).map(([key, items]) => [key, items.length]));
  const coverageChanged = coverage(before) !== coverage(after);
  return { changed: coverageChanged || Object.values(counts).some(n => n > 0),
    coverage_changed: coverageChanged, counts,
    field_keys: Object.fromEntries(Object.entries(lists).map(([key, items]) => [key, items.slice(0, 40)])),
    truncated: Object.values(counts).some(n => n > 40) };
}

function changedValues(before, after, activeFieldKey) {
  const current = new Map(after.fields.map(f => [f.field_key, f]));
  return before.fields.some(f => f.field_key !== activeFieldKey &&
    value(f) !== value(current.get(f.field_key) || {}));
}

export function validateFieldBatch(snapshot, steps) {
  if (!snapshot || !Array.isArray(steps) || steps.length < 1 || steps.length > 4) {
    throw new ControlNotReady('Field batch requires an observation and one to four routine fields');
  }
  const seen = new Set();
  for (const step of steps) {
    if (!step || !['fill_control', 'select_control'].includes(step.operation) ||
        Object.keys(step).sort().join(',') !== 'field_key,operation,value' ||
        typeof step.value !== 'string' || step.value.length > 12000 || seen.has(step.field_key)) {
      throw new ControlNotReady('Batch requires unique observed text/native-select fields');
    }
    seen.add(step.field_key);
    const field = snapshot.fields.find(f => f.field_key === step.field_key);
    if (!field || field.disabled || field.readonly || field.value_source === 'unavailable' ||
        (step.operation === 'fill_control' ? !textControls.has(field.control) : field.control !== 'select' || field.multiple === true)) {
      throw new ControlNotReady('Batch contains an unsupported or unavailable control');
    }
    if (step.operation === 'select_control' && field.options.filter(o => !o.disabled &&
        (o.value === step.value || o.label === step.value)).length !== 1) {
      throw new ControlNotReady('Batch option is not uniquely observed');
    }
  }
}

export async function operateFieldBatch(tab, snapshot, steps, operate = operateObservedControl) {
  validateFieldBatch(snapshot, steps); // Validate the whole plan before any write.
  let current = snapshot;
  const results = [];
  for (const step of steps) {
    let result;
    let rejectedObservation;
    let rejectedDependencies;
    try {
      result = await operate(tab, current, step.operation,
        { field_key: step.field_key, value: step.value }, fresh => {
          const dependencies = dependencyChanges(current, fresh);
          if (dependencies.changed || structureChanges(current, fresh).changed ||
              shape(current) !== shape(fresh) || changedValues(current, fresh)) {
            rejectedObservation = fresh;
            rejectedDependencies = dependencies;
            throw new ControlNotReady('Batch dependencies changed; observe again before input');
          }
        });
    } catch (error) {
      if (!(error instanceof ControlNotReady)) throw error; // Unknown write outcome stops the host.
      if (!results.length) throw error;
      return { observation: rejectedObservation || current, batch_result: { status: 'parked', completed: results.length,
        requested: steps.length, results, reason: 'control_changed', reobserve_required: true,
        ...(rejectedDependencies ? { dependency_changes: rejectedDependencies } : {}) } };
    }
    const { observation: after, ...report } = result;
    results.push(report);
    const structural = structureChanges(current, after);
    const dependencies = dependencyChanges(current, after, step.field_key);
    const unexpectedChange = structural.changed || dependencies.changed ||
      shape(current) !== shape(after) || changedValues(current, after, step.field_key);
    current = after;
    if (report.persisted !== true || report.invalid === true || unexpectedChange) {
      return { observation: current, batch_result: { status: 'parked', completed: results.length,
        requested: steps.length, results, reason: unexpectedChange ? 'form_changed' : 'readback_not_verified',
        structure_changes: structural, dependency_changes: dependencies,
        reobserve_required: true } };
    }
  }
  return { observation: current, batch_result: { status: 'verified', completed: results.length,
    requested: steps.length, results, immediate_readback_only: true } };
}
