/** Reviewed prepare-only subgoals. Uses existing observed-control operations, never submission. */
import { observeForm, operateObservedControl, ControlNotReady } from './browser-form-state.mjs';
import { fieldBatchChanges, validateFieldBatch } from './browser-field-batch.mjs';

const plain = value => value && typeof value === 'object' && !Array.isArray(value);
const bounded = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 12000;
const find = (form, key) => form.fields.find(f => f.field_key === key);
const matches = (field, value) => field.options.filter(o => !o.disabled && (o.value === value || o.label === value));
const selected = (f, value) => {
  if (!f || f.invalid || f.selection_source === 'unavailable') return false;
  if (f.control === 'combobox') {
    const display = f.selected_display || [];
    if (display.length === 1) return display[0] === value;
    const options = f.options.filter(o => o.selected === true);
    return display.length === 0 && options.length === 1 && (options[0].value === value || options[0].label === value);
  }
  const option = matches(f, value);
  return f.value_source !== 'unavailable' && option.length === 1 && f.value === option[0].value;
};

export function validatePreparationSubgoal(form, args) {
  if (!form || !plain(args) || !bounded(args.record_key)) throw new ControlNotReady('An observed record is required');
  const keys = Object.keys(args).filter(k => k !== 'mode').sort().join(',');
  let fields;
  if (args.kind === 'school_selection') {
    if (!['field_key,kind,record_key,value', 'field_key,kind,query,record_key,value'].includes(keys) ||
        !bounded(args.field_key) || !bounded(args.value) || ('query' in args && !bounded(args.query))) {
      throw new ControlNotReady('School subgoal requires one exact target and optional bounded query');
    }
    fields = [find(form, args.field_key)];
    const f = fields[0];
    if (!f || !['select', 'combobox'].includes(f.control) || f.multiple ||
        (f.control === 'select' && f.value_source === 'unavailable') ||
        !/\b(school|university|college|institution)\b|学校|院校|大学/i.test(f.application_question?.text || '')) {
      throw new ControlNotReady('School selection requires an identified single school selector');
    }
  } else if (args.kind === 'record_fields') {
    if (keys !== 'kind,record_key,steps') throw new ControlNotReady('Record subgoal requires bounded scalar steps');
    // Reuse the ordinary batch shape and native-option validator. Dates/months
    // use the existing segmented-date operation but do not expand fill_batch.
    const scalarView = { ...form, fields: form.fields.map(f =>
      ['date', 'month'].includes(f.control) ? { ...f, control: 'text' } : f) };
    validateFieldBatch(scalarView, args.steps);
    fields = args.steps.map(step => find(form, step.field_key));
  } else throw new ControlNotReady('Unsupported preparation subgoal');
  if (fields.some(f => !f || f.disabled || f.readonly ||
      f.semantic?.identification !== 'identified' || f.semantic?.record.status !== 'identified' ||
      f.semantic.record.key !== args.record_key || !f.record_path?.length ||
      f.record_path.some(r => r.dom_identity === null || r.dom_identity === undefined))) {
    throw new ControlNotReady('Subgoal record is ambiguous, unavailable or not identity-bound');
  }
  if (fields.some(f => /\b(salary|sponsorship|visa|authorization|eligibility|declaration)\b/i.test(f.application_question?.text || ''))) {
    throw new ControlNotReady('Subgoal does not handle eligibility or declaration questions');
  }
}

// Menu options may change as the direct result of opening/searching/selecting
// this exact combobox. All other identity, requirements, values and coverage
// remain dependencies. No exemption is used before input or on the final refresh.
function afterActionChanges(before, after, fieldKey, allowMenu) {
  let normalized = after;
  if (allowMenu) normalized = { ...after, fields: after.fields.map(f =>
    f.field_key === fieldKey ? { ...f, options: find(before, fieldKey)?.options || [] } : f) };
  return fieldBatchChanges(before, normalized, fieldKey);
}

export async function operatePreparationSubgoal(tab, snapshot, args, { deadlineAt = Date.now() / 1000 + 30 } = {}) {
  validatePreparationSubgoal(snapshot, args); // Admit the complete plan before any input.
  const plan = structuredClone(args);
  let current = snapshot;
  let actions = 0;
  let waitReads = 0;
  const progress = [];
  const checkTime = () => {
    if (!Number.isFinite(deadlineAt) || Date.now() / 1000 >= deadlineAt) throw new ControlNotReady('Subgoal deadline reached before input');
  };
  const finish = (status, reason, extra = {}) => ({ observation: current, subgoal_result: {
    kind: plan.kind, record_key: plan.record_key, status, reason, actions,
    completed_fields: progress.filter(p => p.verified).map(p => p.field_key),
    progress, wait_observations: waitReads, ...extra,
    scope: plan.kind === 'school_selection' ? 'one_school_selection' : 'requested_record_fields',
    immediate_readback_only: true, whole_application_verified: false,
    reobserve_required: status !== 'verified', grants_submission_authority: false,
  } });
  const refresh = async (allowMenu = false) => {
    checkTime();
    const fresh = await observeForm(tab);
    const change = allowMenu ? afterActionChanges(current, fresh, plan.field_key, true) : fieldBatchChanges(current, fresh);
    current = fresh;
    if (change.changed) return finish('parked', 'dependencies_changed', { dependency_changes: change.dependency_changes });
    validatePreparationSubgoal(current, plan);
    return null;
  };
  const run = async (operation, fieldKey, value) => {
    checkTime();
    const previous = current;
    const call = { field_key: fieldKey, ...(value === undefined ? {} : { value }) };
    let result;
    try {
      result = await operateObservedControl(tab, previous, operation, call, fresh => {
        checkTime();
        const change = fieldBatchChanges(previous, fresh);
        if (change.changed) {
          current = fresh;
          throw new ControlNotReady('Subgoal dependencies changed before input');
        }
        validatePreparationSubgoal(fresh, plan);
      });
    } catch (error) {
      if (!(error instanceof ControlNotReady)) throw error; // Unknown effects stop the outer Host.
      return finish('parked', 'preinput_rejected');
    }
    actions++;
    current = result.observation;
    const committed = result.persisted === true && result.invalid === false;
    progress.push({ field_key: fieldKey, operation, verified: committed });
    const delta = afterActionChanges(previous, current, fieldKey, plan.kind === 'school_selection');
    if (delta.changed) return finish('parked', 'dependencies_changed', { dependency_changes: delta.dependency_changes });
    if (!['open_control', 'search_control'].includes(operation) && !committed) return finish('parked', 'business_postcondition_unproven');
    return null;
  };
  try {
    const changed = await refresh();
    if (changed) return changed;
    if (plan.kind === 'record_fields') {
      const keys = new Set(plan.steps.map(s => s.field_key));
      const missing = current.fields.filter(f => f.semantic?.record.key === plan.record_key &&
        f.required && !keys.has(f.field_key) && (f.invalid || f.value_source === 'unavailable' || !f.value));
      if (missing.length) return finish('needs_fact', 'required_record_fields_not_supplied', {
        missing_field_keys: missing.map(f => f.field_key).slice(0, 40) });
      for (const step of plan.steps) {
        const f = find(current, step.field_key);
        if (!f.invalid && f.value_source !== 'unavailable' &&
            (step.operation === 'select_control' ? selected(f, step.value) : f.value === step.value)) {
          progress.push({ field_key: step.field_key, operation: 'retain_value', verified: true });
          continue;
        }
        const failure = await run(step.operation, step.field_key, step.value);
        if (failure) return failure;
      }
      const verified = plan.steps.every(step => {
        const f = find(current, step.field_key);
        return f && !f.invalid && f.value_source !== 'unavailable' &&
          (step.operation === 'select_control' ? selected(f, step.value) : f.value === step.value);
      });
      return finish(verified ? 'verified' : 'parked', verified ? 'record_postconditions_verified' : 'business_postcondition_unproven');
    }
    if (selected(find(current, plan.field_key), plan.value)) {
      progress.push({ field_key: plan.field_key, operation: 'retain_selection', verified: true });
      return finish('verified', 'selection_already_verified');
    }
    const optionFailure = () => {
      const f = find(current, plan.field_key);
      const found = matches(f, plan.value);
      if (found.length > 1 || (found.length === 1 && f.options.filter(o => o.value === found[0].value).length > 1)) {
        return finish('parked', 'ambiguous_target_option');
      }
      return null;
    };
    let failure = optionFailure();
    if (failure) return failure;
    let f = find(current, plan.field_key);
    if (f.control === 'combobox' && !matches(f, plan.value).length) {
      if (f.expanded !== true) {
        failure = await run('open_control', plan.field_key);
        if (failure) return failure;
      }
      failure = optionFailure();
      if (failure) return failure;
      f = find(current, plan.field_key);
      if (!matches(f, plan.value).length && f.editable_combobox) {
        failure = await run('search_control', plan.field_key, plan.query || plan.value);
        if (failure) return failure;
      }
      // Bound the loading wait; do not confuse an empty asynchronous list with
      // a failed action, retry clicks, or invent an option. A later call starts fresh.
      for (; waitReads < 2 && !matches(find(current, plan.field_key), plan.value).length; ) {
        checkTime();
        await new Promise(resolve => setTimeout(resolve, 100));
        waitReads++;
        failure = await refresh(true);
        if (failure) return failure;
      }
    }
    failure = optionFailure();
    if (failure) return failure;
    if (matches(find(current, plan.field_key), plan.value).length !== 1) {
      return finish(find(current, plan.field_key).control === 'select' ? 'parked' : 'waiting', 'target_option_not_observed');
    }
    failure = await run('select_control', plan.field_key, plan.value);
    if (failure) return failure;
    const verified = selected(find(current, plan.field_key), plan.value);
    return finish(verified ? 'verified' : 'parked', verified ? 'selection_postcondition_verified' : 'business_postcondition_unproven');
  } catch (error) {
    if (!(error instanceof ControlNotReady)) throw error;
    return finish('parked', 'preinput_rejected');
  }
}
