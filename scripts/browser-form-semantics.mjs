/** Transient semantic diagnostics from existing observations. No values, writes or authority. */
const normal = value => String(value || '').normalize('NFKC').replace(/[＊*✱:：]/g, '')
  .replace(/\s+/g, ' ').trim().toLowerCase();
const codes = { identified: 0, ambiguous: 0, not_expanded: 0, not_covered: 0 };
const textKinds = new Set(['text', 'textarea', 'email', 'tel', 'url', 'search', 'number', 'date', 'month']);

function attachmentPurpose(field) {
  if (field.control !== 'file') return null;
  // An internal name, id or generic upload label does not identify a document purpose.
  const names = field.application_question?.text_sources?.filter(s =>
    s.source === 'label' || s.source === 'scoped_label' || s.source === 'aria-label' ||
    s.source.startsWith('aria-labelledby:')).map(s => normal(s.text)) || [];
  const candidates = new Set();
  for (const label of names) {
    if (/\b(resume|résumé|cv|curriculum vitae)\b|简历|履歷/.test(label)) candidates.add('resume');
    if (/\b(cover letter|covering letter|motivation letter)\b|求职信|求職信/.test(label)) candidates.add('cover_letter');
  }
  return { status: candidates.size === 1 ? 'identified' : candidates.size > 1 ? 'ambiguous' : 'not_covered',
    purpose: candidates.size === 1 ? [...candidates][0] : null, source: 'observed_question_label',
    grants_upload_authority: false };
}

export function annotateFormSemantics(form) {
  const duplicate = new Map();
  for (const f of form.fields) {
    const context = f.record_path?.at(-1)?.key || '';
    const key = JSON.stringify([context, normal(f.application_question?.text || f.label)]);
    duplicate.set(key, (duplicate.get(key) || 0) + 1);
  }
  const summary = { ...codes };
  const optionStates = { ...codes };
  for (const f of form.fields) {
    const path = f.record_path || [];
    const nearest = path.at(-1);
    const reasons = [];
    const question = f.application_question;
    if (question?.completeness !== 'known') reasons.push('question_not_fully_identified');
    const labels = (question?.text_sources || []).filter(s =>
      ['aria-label', 'label', 'scoped_label'].includes(s.source)).map(s => normal(s.text)).filter(Boolean);
    if (new Set(labels).size > 1) reasons.push('conflicting_labels');
    if (duplicate.get(JSON.stringify([nearest?.key || '', normal(question?.text || f.label)])) > 1) {
      reasons.push('duplicate_label_in_record');
    }
    if (f.record_path_truncated) reasons.push('record_path_truncated');
    if (path.some(p => p.label_status !== 'identified')) reasons.push('record_label_unresolved');
    let identification = reasons.length ? 'ambiguous' : 'identified';
    if (!question?.text_sources?.length) identification = 'not_covered';
    const record = { status: !nearest ? 'not_covered' :
      reasons.includes('record_path_truncated') || reasons.includes('record_label_unresolved') ? 'ambiguous' : 'identified',
    key: nearest?.key || null, path_keys: path.map(p => p.key) };
    let options = null;
    if (['select', 'combobox'].includes(f.control)) {
      const values = f.options.filter(o => !o.disabled);
      const duplicateOptions = new Set(values.map(o => o.value)).size !== values.length ||
        new Set(values.map(o => normal(o.label))).size !== values.length;
      options = { status: f.control === 'combobox' && f.multiple ? 'not_covered' :
        f.control === 'combobox' && f.expanded === false ? 'not_expanded' :
        duplicateOptions ? 'ambiguous' : values.length ? 'identified' : 'not_covered',
      observed_count: f.options.length, duplicate_options: duplicateOptions,
      diagnostic: f.control === 'combobox' && f.multiple ? 'custom_multiselect_unsupported' :
        f.control === 'combobox' && !values.length ? 'options_not_observed' : null };
    }
    if (options) optionStates[options.status]++;
    f.semantic = { identification, diagnostic_codes: reasons, record,
      options, attachment: attachmentPurpose(f),
      routine_writable: identification === 'identified' && !f.disabled && !f.readonly &&
        (textKinds.has(f.control) || (f.control === 'select' && !f.multiple)),
      grants_fact_authority: false };
    summary[identification]++;
  }
  form.semantic_coverage = { schema_version: 1, field_states: summary, option_states: optionStates,
    scope: form.coverage?.scope || 'unknown',
    frames: { status: 'not_covered', observed_count: form.coverage?.iframe_count ?? null },
    closed_shadow: 'not_covered', all_steps: 'unverified',
    diagnostic_codes: [...(form.coverage?.iframe_count ? ['frame_contents_not_inspected'] : []),
      'closed_shadow_contents_not_inspected', 'whole_application_not_verified'] };
  return form;
}
