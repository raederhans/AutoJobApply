/** DOM-backed form observation and bounded, observed-control operations for IAB.
 * Values are transient host observations, not applicant facts or submit authority.
 */
export async function observeForm(tab) {
  const form = await tab.playwright.evaluate(() => {
    const clean = value => String(value || '').replace(/\s+/g, ' ').trim();
    const labelledText = element => clean([element.textContent,
      ...[...element.querySelectorAll('slot')].flatMap(slot => slot.assignedNodes({ flatten: true }).map(node => node.textContent)),
    ].join(' '));
    const visibleText = element => {
      if (!element.getClientRects().length || getComputedStyle(element).visibility === 'hidden') return '';
      return [...element.childNodes].map(node => node.nodeType === 3 ? node.textContent
        : node.nodeType === 1 ? visibleText(node) : '').join('');
    };
    const scopedVisibleLabel = element => {
      const group = element.closest('.form-group');
      if (!group) return '';
      const controls = [...group.querySelectorAll('input, textarea, select, [role="combobox"]')];
      const labels = [...group.querySelectorAll('label')];
      // An unbound label is usable only in an isolated one-control group.
      // A custom-file uploader may add its own filename display label.
      if (controls.length !== 1 || controls[0] !== element) return '';
      let label = labels.length === 1 ? labels[0] : null;
      if (!label && element.matches('input[type="file"]')) {
        const primary = labels.filter(candidate => candidate.parentElement === group);
        const uploader = element.closest('.custom-file');
        if (primary.length === 1 && uploader && uploader.closest('.form-group') === group &&
            labels.every(candidate => candidate === primary[0] ||
              (candidate.classList.contains('custom-file-label') && candidate.closest('.custom-file') === uploader &&
                candidate.closest('.form-group') === group &&
                (!candidate.getAttribute('for') || candidate.getAttribute('for') === element.id)))) {
          label = primary[0];
        }
      }
      if (!label || label.closest('.form-group') !== group) return '';
      const target = label.getAttribute('for');
      if (target && target !== element.id) return '';
      return clean(visibleText(label));
    };
    const scopedQuestionLabel = element => {
      const question = element.closest('.application-question');
      if (!question) return '';
      const controls = [...question.querySelectorAll('input, textarea, select, [role="combobox"]')];
      const labels = [...question.querySelectorAll('.application-label')];
      if (controls.length !== 1 || controls[0] !== element || labels.length !== 1 ||
          labels[0].closest('.application-question') !== question) return '';
      return clean(visibleText(labels[0]));
    };
    const isAuxiliaryRequiredInput = element => {
      if (!element.matches('input') || !['', 'text'].includes(element.getAttribute('type') || '') ||
          element.required !== true || element.getAttribute('aria-hidden') !== 'true' ||
          element.getAttribute('tabindex') !== '-1' || element.id || element.name ||
          element.labels?.length || element.getAttribute('aria-label') || element.getAttribute('aria-labelledby')) return false;
      const shell = element.parentElement;
      if (!shell?.matches('.select-shell')) return false;
      const controls = [...shell.querySelectorAll('input, textarea, select, [role="combobox"]')];
      const primary = controls.filter(control => control !== element);
      return primary.length === 1 && primary[0].matches('input.select__input[role="combobox"]') &&
        primary[0].getAttribute('aria-hidden') !== 'true' &&
        (primary[0].required === true || primary[0].getAttribute('aria-required') === 'true');
    };
    const scopedFileGroup = (element, root) => {
      if (!element.matches('input[type="file"]')) return null;
      const group = element.closest('.file-upload[role="group"]');
      if (!group) return null;
      const controls = [...group.querySelectorAll('input, textarea, select, [role="combobox"]')];
      if (controls.length !== 1 || controls[0] !== element) return null;
      const ids = (group.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
      const labels = ids.map(id => [...root.querySelectorAll('[id]')].filter(node => node.id === id));
      if (!ids.length || labels.some(matches => matches.length !== 1 || !group.contains(matches[0]))) return null;
      const label = clean(labels.map(matches => visibleText(matches[0])).join(' '));
      return label ? { group, label } : null;
    };
    const quote = value => String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
    const roots = [document];
    const rootFor = new Map();
    const hostFor = new Map();
    const elements = [];
    const idCounts = new Map();
    const tagIdCounts = new Map();
    for (let i = 0; i < roots.length; i++) {
      for (const el of roots[i].querySelectorAll('*')) {
        rootFor.set(el, roots[i]);
        elements.push(el);
        if (el.id) {
          idCounts.set(el.id, (idCounts.get(el.id) || 0) + 1);
          const key = `${el.tagName}/${el.id}`;
          tagIdCounts.set(key, (tagIdCounts.get(key) || 0) + 1);
        }
        if (el.shadowRoot) { roots.push(el.shadowRoot); hostFor.set(el.shadowRoot, el); }
      }
    }
    const selectorFor = element => {
      if (element.id) {
        const selector = `[id="${quote(element.id)}"]`;
        if (idCounts.get(element.id) === 1) return selector;
        if (tagIdCounts.get(`${element.tagName}/${element.id}`) === 1) return element.tagName.toLowerCase() + selector;
      }
      const parts = [];
      for (let node = element; node && node.nodeType === 1; node = node.parentElement) {
        const root = rootFor.get(node);
        const siblings = [...(node.parentElement?.children || root?.children || [node])].filter(x => x.tagName === node.tagName);
        parts.unshift(`${node.tagName.toLowerCase()}:nth-of-type(${siblings.indexOf(node) + 1})`);
        if (!node.parentElement && hostFor.has(root)) return selectorFor(hostFor.get(root)) + ' ' + parts.join(' > ');
      }
      return parts.join(' > ');
    };
    const uploadTriggerFor = (element, fileGroup) => {
      if (!fileGroup || !element.id || idCounts.get(element.id) !== 1) return null;
      const rect = element.getClientRects()[0];
      // A clipped file input can expose a separately bound visible trigger.
      // Select it from observation once; never click the input and then retry.
      if (!rect || rect.width > 1 || rect.height > 1) return null;
      const parent = element.parentElement;
      if (!parent || !fileGroup.group.contains(parent)) return null;
      const controls = [...parent.querySelectorAll('input, textarea, select, [role="combobox"]')];
      const buttons = [...parent.querySelectorAll('button, input[type="button"], input[type="submit"], [role="button"]')];
      const labels = [...parent.querySelectorAll('label')];
      if (controls.length !== 1 || controls[0] !== element || buttons.length !== 1 || labels.length !== 1) return null;
      const button = buttons[0];
      const label = labels[0];
      if (button.parentElement !== parent || label.parentElement !== parent ||
          !button.matches('button[type="button"]') || label.getAttribute('for') !== element.id) return null;
      const name = clean(visibleText(button));
      const buttonRect = button.getClientRects()[0];
      if (!name || name !== clean(label.textContent) || !buttonRect || buttonRect.width <= 1 || buttonRect.height <= 1 ||
          button.getAttribute('aria-hidden') === 'true' ||
          /\b(?:submit|apply|confirm|consent|agree|accept)\b|terms|privacy|declaration/i.test(name)) return null;
      return { selector: selectorFor(button), label: name,
        disabled: button.disabled === true || button.getAttribute('aria-disabled') === 'true' };
    };
    const nodes = elements.filter(el => el.matches('input, textarea, select, [role="combobox"]'));
    const visibleQuestionText = element => {
      if (element.nodeType === 3) return element.textContent || '';
      if (element.nodeType !== 1 || element.matches('input,textarea,select,[role="combobox"]')) return '';
      if ((element.tagName !== 'SLOT' && !element.getClientRects().length) || getComputedStyle(element).visibility === 'hidden') return '';
      const assigned = element.tagName === 'SLOT' ? element.assignedNodes({ flatten: true }) : [];
      return [...(assigned.length ? assigned : element.childNodes)].map(visibleQuestionText).join('');
    };
    // Writing metadata is lossless and independent of the bounded fill identity.
    const rawQuestionText = element => {
      if (element.nodeType === 3) return element.textContent || '';
      if (element.nodeType !== 1 || element.matches('input,textarea,select,[role="combobox"]')) return '';
      const assigned = element.tagName === 'SLOT' ? element.assignedNodes({ flatten: true }) : [];
      return [...(assigned.length ? assigned : element.childNodes)].map(rawQuestionText).join('');
    };
    const questionMetadata = (el, fieldKey) => {
      const root = el.getRootNode();
      const sources = [];
      let complete = true;
      const add = (source, text) => { if (text) sources.push({ source, text: String(text) }); };
      const references = attribute => (el.getAttribute(attribute) || '').split(/\s+/).filter(Boolean).map(id => {
        const matches = [...root.querySelectorAll('[id]')].filter(node => node.id === id);
        if (matches.length !== 1) { complete = false; sources.push({ source: `${attribute}:${id}`, text: '' }); return ''; }
        const text = rawQuestionText(matches[0]);
        add(`${attribute}:${id}`, text);
        return text;
      });
      add('aria-label', el.getAttribute('aria-label'));
      const named = references('aria-labelledby');
      for (const label of el.labels || []) add('label', rawQuestionText(label));
      const question = el.closest('.application-question, .form-group, [data-qa*="field"], [class*="form-item"]');
      if (question && question.querySelectorAll('input,textarea,select,[role="combobox"]').length === 1) {
        for (const label of question.querySelectorAll('.application-label,label')) add('scoped_label', visibleQuestionText(label));
      }
      const text = el.getAttribute('aria-label') || named.filter(Boolean).join(' ') ||
        sources.find(item => ['label', 'scoped_label'].includes(item.source))?.text || '';
      const described = references('aria-describedby');
      const hints = question && question.querySelectorAll('input,textarea,select,[role="combobox"]').length === 1
        ? [...question.querySelectorAll('[class*="help"],[class*="hint"],[class*="description"],.application-instructions')]
          .map(node => visibleQuestionText(node)).filter(Boolean) : [];
      const help = [...new Set([...described.filter(Boolean), ...hints])];
      for (const hint of hints) add('visible_instruction', hint);
      const sections = [];
      for (let owner = el.parentElement; owner; owner = owner.parentElement) {
        if (!owner.matches('fieldset,[role="group"],section')) continue;
        const legend = [...owner.children].find(node => node.matches('legend,h1,h2,h3,h4,h5,h6'));
        const ownerRoot = owner.getRootNode();
        const named = (owner.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean)
          .map(id => [...ownerRoot.querySelectorAll('[id]')].filter(node => node.id === id))
          .filter(matches => matches.length === 1).map(matches => rawQuestionText(matches[0])).join(' ');
        const value = owner.getAttribute('aria-label') || named || (legend ? rawQuestionText(legend) : '');
        if (value) sections.unshift(String(value));
      }
      const constraints = [];
      if (el.matches('textarea,input:not([type]),input[type="text"],input[type="search"],input[type="url"],input[type="tel"],input[type="email"],input[type="password"]')) {
        for (const [attribute, kind] of [['maxlength', 'max'], ['minlength', 'min']]) {
          const value = el.getAttribute(attribute);
          const nativeValue = attribute === 'maxlength' ? el.maxLength : el.minLength;
          if (value !== null && /^\d+$/.test(value) && Number.isSafeInteger(nativeValue) && nativeValue >= 0) {
            constraints.push({ kind, unit: 'utf16', value: nativeValue, source: `native:${attribute}` });
          }
        }
      }
      return { field_key: fieldKey, text: text || String(el.placeholder || el.name || el.id || ''),
        help_text: help.join('\n'), text_sources: sources, constraints, section_path: sections,
        language: el.closest('[lang]')?.getAttribute('lang') || document.documentElement.lang || 'unknown',
        completeness: complete && !!text ? 'known' : 'partial',
        ...(el.tagName === 'SELECT' ? { options: [...el.options].map(option => ({ value: option.value,
          label: option.label, disabled: option.disabled || option.parentElement?.disabled === true })) } : {}) };
    };
    const fields = [];
    let protectedCount = 0;
    for (const el of nodes) {
      if (!el.getClientRects().length || getComputedStyle(el).visibility === 'hidden') continue;
      const type = (el.getAttribute('type') || '').toLowerCase();
      if (['hidden', 'submit', 'button', 'reset', 'image'].includes(type)) continue;
      if (isAuxiliaryRequiredInput(el)) continue;
      const root = rootFor.get(el);
      const labelled = (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => root.getElementById(id)?.textContent || '').join(' ');
      const labels = [...(el.labels || [])].map(labelledText).join(' ');
      const questionLabel = scopedQuestionLabel(el);
      const fileGroup = scopedFileGroup(el, root);
      const label = clean(el.getAttribute('aria-label') || labelled || questionLabel || fileGroup?.label || labels || el.getAttribute('placeholder') || el.name || el.id);
      const visibleLabel = questionLabel || fileGroup?.label || scopedVisibleLabel(el);
      const group = el.closest('fieldset, [role="group"]');
      const ownedGroupLabel = owner => {
        if (owner.getAttribute('aria-label')) return clean(owner.getAttribute('aria-label'));
        const ids = (owner.getAttribute('aria-labelledby') || '').split(/\s+/).filter(Boolean);
        const labels = ids.map(id => [...root.querySelectorAll(`[id="${quote(id)}"]`)]);
        if (ids.length && labels.every(matches => matches.length === 1)) {
          return clean(labels.map(matches => labelledText(matches[0])).join(' '));
        }
        // A descendant fieldset's legend does not label its enclosing group.
        const legend = [...owner.children].find(child => child.tagName === 'LEGEND');
        return owner.tagName === 'FIELDSET' && legend ? clean(labelledText(legend)) : '';
      };
      const groupLabels = [];
      for (let owner = group; owner; owner = owner.parentElement?.closest('fieldset, [role="group"]')) {
        groupLabels.push(ownedGroupLabel(owner));
      }
      const groupLabel = group ? ownedGroupLabel(group) || fileGroup?.label || '' : '';
      const identity = `${type} ${label} ${visibleLabel} ${groupLabels.join(' ')} ${el.name || ''} ${el.id || ''} ${el.autocomplete || ''}`;
      if (/password|passcode|one.time|\botp\b|verification.code|security.code|passport|\bnric\b|\bssn\b|\bfin\b|national.id|credit.card|bank.account|consent|declaration|terms|privacy|agree|accept/i.test(identity)) {
        protectedCount++;
        continue;
      }
      const control = el.tagName === 'SELECT' ? 'select' : el.tagName === 'TEXTAREA' ? 'textarea' : el.getAttribute('role') === 'combobox' ? 'combobox' : type || 'text';
      let multiple = el.tagName === 'SELECT' ? el.hasAttribute('multiple') :
        el.getAttribute('aria-multiselectable') === 'true';
      let options = control === 'select' ? [...el.options].map(o => ({ value: o.value, label: clean(o.label),
        selected: typeof o.selected === 'boolean' ? o.selected : null,
        disabled: o.disabled || o.parentElement?.disabled === true })) : [];
      if (control === 'combobox') {
        const lists = (el.getAttribute('aria-controls') || el.getAttribute('aria-owns') || '').split(/\s+/).filter(Boolean).map(id => {
          for (let scope = root; scope; scope = rootFor.get(hostFor.get(scope))) {
            const matches = [...scope.querySelectorAll(`[id="${quote(id)}"]`)];
            if (matches.length) return matches.length === 1 ? matches[0] : null;
          }
          return null;
        }).filter(Boolean);
        multiple ||= lists.some(list => list.getAttribute('aria-multiselectable') === 'true' ||
          [...list.querySelectorAll('[role="listbox"]')].some(box => box.getAttribute('aria-multiselectable') === 'true'));
        const inList = option => {
          for (let node = option; node; node = node.parentElement || hostFor.get(rootFor.get(node))) {
            if (lists.includes(node)) return true;
          }
          return false;
        };
        const optionLabel = option => {
          const own = clean(option.getAttribute('aria-label') || labelledText(option));
          if (own) return own;
          const scope = rootFor.get(option);
          const host = hostFor.get(scope);
          return host && scope.querySelectorAll('[role="option"]').length === 1 ? clean(host.textContent) : '';
        };
        options = elements.filter(o => o.getAttribute('role') === 'option' && inList(o))
          .filter(o => o.getClientRects().length && getComputedStyle(o).visibility !== 'hidden')
          .map(o => ({ value: optionLabel(o), label: optionLabel(o), selector: selectorFor(o) + '[role="option"]',
            selected: o.getAttribute('aria-selected') === 'true', disabled: o.getAttribute('aria-disabled') === 'true' }))
          .filter(o => o.label);
      }
      const key = selectorFor(el);
      // React Select keeps its search input empty after committing an option.
      // Preserve the visible selection separately, rather than calling it blank.
      const selectedDisplay = control === 'combobox'
        ? [...(el.closest('.select__value-container')?.querySelectorAll('.select__single-value, .select__multi-value') || [])].map(o => clean(o.textContent))
        : [];
      const requiredSource = el.required === true ? 'native'
        : el.getAttribute('aria-required') === 'true' ? 'aria'
        : fileGroup?.group.getAttribute('aria-required') === 'true' ? 'group_aria'
        : /[＊*✱]$/.test(visibleLabel) ? 'visible_label' : 'not_asserted';
      const formOwner = element => {
        const formId = element.getAttribute('form');
        if (!formId) return element.closest('form');
        const matches = [...root.querySelectorAll('form')].filter(form => form.id === formId);
        return matches.length === 1 ? matches[0] : undefined;
      };
      const owner = control === 'radio' ? formOwner(el) : null;
      const peers = control === 'radio' ? elements.filter(peer => peer.matches('input[type="radio"]') &&
        rootFor.get(peer) === root && formOwner(peer) === owner && peer.name === el.name) : [];
      fields.push({ field_key: key, selector: key, label, group: groupLabel,
        application_question: questionMetadata(el, key),
        group_key: group ? selectorFor(group) : '', control,
        native_tag: el.tagName.toLowerCase(), multiple: multiple ||
          (control === 'combobox' && !!el.closest('.select__value-container')?.querySelector('.select__multi-value')),
        editable_combobox: control === 'combobox' && el.tagName === 'INPUT' &&
          ['', 'text', 'search'].includes(type),
        ...(control === 'radio' ? { radio_group: { name: el.name || '',
          root_key: hostFor.has(root) ? selectorFor(hostFor.get(root)) : 'document',
          form_key: owner ? selectorFor(owner) : '', peer_keys: peers.map(selectorFor),
          owner_known: owner !== undefined,
          fully_observed: false } } : {}),
        ...(control === 'select' && multiple ? {
          selected_values: options.every(o => typeof o.selected === 'boolean') ? options.filter(o => o.selected).map(o => o.value) : null,
          selection_source: options.every(o => typeof o.selected === 'boolean') ? 'dom_read' : 'unavailable',
        } : {}),
        selected_display: selectedDisplay,
        value: control === 'file' ? '' : String(el.getAttribute('aria-valuetext') ?? el.value ?? ''),
        checked: ['checkbox', 'radio'].includes(control) ? el.checked : null,
        focused: el === root.activeElement,
        required: requiredSource !== 'not_asserted', required_source: requiredSource,
        disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true', readonly: el.readOnly === true,
        invalid: el.validity?.valid === false || el.getAttribute('aria-invalid') === 'true',
        validation_message: el.validationMessage || '', options,
        files: control === 'file' ? [...(el.files || [])].map(f => ({ name: f.name, size: f.size })) : [],
        ...(control === 'file' ? { upload_trigger: uploadTriggerFor(el, fileGroup) } : {}),
      });
    }
    for (const field of fields.filter(f => f.control === 'radio')) {
      field.radio_group.fully_observed = field.radio_group.owner_known && !!field.radio_group.name && field.radio_group.peer_keys.length > 0 &&
        field.radio_group.peer_keys.every(key => fields.filter(f => f.field_key === key && f.control === 'radio').length === 1);
    }
    return { page_url: location.href, fields, protected_count: protectedCount,
      question_coverage: { scope: 'visible_top_document_open_shadow', page_only: true, whole_form: 'unknown',
        iframe_count: elements.filter(el => el.tagName === 'IFRAME').length, fields_truncated: false },
      coverage: { scope: 'visible_top_document_open_shadow', open_shadow_count: roots.length - 1,
        iframe_count: elements.filter(el => el.tagName === 'IFRAME').length } };
  });
  // The IAB read-only DOM scope can omit live input properties. Use its supported
  // DOM snapshot capability, never a page script mutation or a browser side channel.
  if (typeof tab.capabilities?.get === 'function') {
    const cdp = await tab.capabilities.get('cdp');
    const snapshot = await cdp.send('DOMSnapshot.captureSnapshot', { computedStyles: [] });
    enrichLiveValues(form, snapshot);
  }
  return form;
}

export function enrichLiveValues(form, snapshot) {
  const strings = snapshot.strings || [];
  const doc = snapshot.documents?.find(item => strings[item.documentURL] === form.page_url);
  // This read may run after an input; never classify its failure as proven no-op.
  if (!doc) throw new Error('Live form document changed; observe again');
  const nodes = doc.nodes;
  const attrs = (nodes.attributes || []).map(row => Object.fromEntries(Array.from({ length: row.length / 2 }, (_, i) => [strings[row[i * 2]], strings[row[i * 2 + 1]]])));
  const quote = value => String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"');
  const selectors = new Map();
  const paths = [];
  const canonical = [];
  const ids = new Map();
  const tagIds = new Map();
  const siblings = new Map();
  for (let i = 0; i < attrs.length; i++) if (attrs[i].id) {
    ids.set(attrs[i].id, (ids.get(attrs[i].id) || 0) + 1);
    const key = `${nodes.nodeName[i]}/${attrs[i].id}`;
    tagIds.set(key, (tagIds.get(key) || 0) + 1);
  }
  for (let index = 0; index < nodes.nodeType.length; index++) {
    if (nodes.nodeType[index] !== 1) continue;
    const name = strings[nodes.nodeName[index]].toLowerCase();
    const parent = nodes.parentIndex[index];
    const siblingKey = `${parent}/${nodes.nodeName[index]}`;
    const ordinal = (siblings.get(siblingKey) || 0) + 1;
    siblings.set(siblingKey, ordinal);
    const shadow = nodes.nodeType[parent] === 11;
    paths[index] = [shadow ? canonical[nodes.parentIndex[parent]] : paths[parent], `${name}:nth-of-type(${ordinal})`].filter(Boolean).join(shadow ? ' ' : ' > ');
    canonical[index] = paths[index];
    selectors.set(paths[index], index);
    if (attrs[index]?.id && tagIds.get(`${nodes.nodeName[index]}/${attrs[index].id}`) === 1) {
      canonical[index] = `${name}[id="${quote(attrs[index].id)}"]`;
      selectors.set(canonical[index], index);
    }
    if (attrs[index]?.id && ids.get(attrs[index].id) === 1) {
      canonical[index] = `[id="${quote(attrs[index].id)}"]`;
      selectors.set(canonical[index], index);
    }
  }
  const rare = key => new Map((nodes[key]?.index || []).map((index, i) => [index, strings[nodes[key].value[i]] ?? '']));
  const values = rare('inputValue');
  const textarea = rare('textValue');
  for (const field of form.fields) {
    const index = selectors.get(field.selector);
    field.dom_identity = index === undefined ? null : nodes.backendNodeId?.[index] ?? null;
    if (field.control === 'file') continue;
    if (field.control === 'select') {
      const optionNodes = [];
      if (index !== undefined) {
        for (let n = 0; n < nodes.nodeType.length; n++) {
          if (strings[nodes.nodeName[n]] !== 'OPTION') continue;
          let parent = nodes.parentIndex[n];
          while (parent >= 0 && parent !== index) parent = nodes.parentIndex[parent];
          if (parent === index) optionNodes.push(n);
        }
      }
      const optionText = option => {
        const parts = [];
        for (let n = option + 1; n < nodes.nodeType.length; n++) {
          let parent = nodes.parentIndex[n];
          while (parent >= 0 && parent !== option) parent = nodes.parentIndex[parent];
          if (parent === option && nodes.nodeType[n] === 3) parts.push(strings[nodes.nodeValue?.[n]] || '');
        }
        return parts.join('').replace(/\s+/g, ' ').trim();
      };
      const matched = optionNodes.length === field.options.length && optionNodes.every((n, i) =>
        (attrs[n].value ?? optionText(n)) === field.options[i].value);
      // optionSelected is a sparse boolean array: absence from its indices is
      // false only when the snapshot supplies the array and all options match.
      const known = matched && !!nodes.optionSelected;
      for (let i = 0; i < field.options.length; i++) {
        field.options[i].dom_identity = matched ? nodes.backendNodeId?.[optionNodes[i]] ?? null : null;
        field.options[i].selected = known ? nodes.optionSelected.index.includes(optionNodes[i]) : null;
      }
      const selected = known ? field.options.filter(o => o.selected).map(o => o.value) : null;
      if (field.multiple) {
        field.selected_values = selected;
        field.selection_source = known ? 'live_dom_snapshot' : 'unavailable';
        field.value_source = known ? 'live_dom_snapshot' : 'unavailable';
      } else {
        field.value = selected?.length === 1 ? selected[0] : null;
        field.value_source = selected?.length === 1 ? 'live_dom_snapshot' : 'unavailable';
      }
      continue;
    }
    if (field.control === 'combobox') {
      for (const option of field.options) {
        const optionIndex = selectors.get(option.selector.replace(/\[role="option"\]$/, ''));
        option.dom_identity = optionIndex === undefined ? null : nodes.backendNodeId?.[optionIndex] ?? null;
      }
    }
    const source = field.control === 'textarea' ? textarea : values;
    if (index !== undefined && source.has(index)) {
      field.value = source.get(index);
      field.value_source = 'live_dom_snapshot';
    } else if (['text', 'textarea', 'email', 'tel', 'url', 'search', 'number', 'date', 'month'].includes(field.control)) {
      field.value = null;
      field.value_source = 'unavailable';
    } else field.value_source = 'dom_read';
    if (['checkbox', 'radio'].includes(field.control) && index !== undefined) {
      field.checked = (nodes.inputChecked?.index || []).includes(index);
    }
  }
}

const identity = field => JSON.stringify([field.selector, field.label, field.group_key, field.group, field.control,
  field.native_tag, field.multiple, field.editable_combobox, field.radio_group, field.dom_identity]);
const selectedDisplay = field => Array.isArray(field.selected_display) ? field.selected_display.filter(value => typeof value === 'string') : [];
const stateValue = field => {
  if (field.control === 'select' && field.multiple) return field.selected_values;
  if (field.control === 'combobox') {
    const display = selectedDisplay(field);
    if (display.length === 1) return display[0];
    if (display.length > 1) return display;
  }
  return ['checkbox', 'radio'].includes(field.control) ? field.checked : field.value;
};
const sameStateValue = (left, right) => JSON.stringify(left) === JSON.stringify(right);
const optionIdentity = options => JSON.stringify((options || []).map(o =>
  [o.value, o.label, o.selector, o.disabled, o.dom_identity]));

export function changedFields(before, after) {
  if (!before || before.page_url !== after.page_url) return [];
  const previous = new Map(before.fields.map(field => [field.field_key, field]));
  return after.fields.flatMap(field => {
    const old = previous.get(field.field_key);
    if (!old || identity(old) !== identity(field) || field.control === 'file') return [];
    const displayChanged = JSON.stringify(selectedDisplay(old)) !== JSON.stringify(selectedDisplay(field));
    if (((!displayChanged && old.value_source === 'unavailable') || (!displayChanged && field.value_source === 'unavailable')) ||
      (!displayChanged && sameStateValue(stateValue(old), stateValue(field)))) return [];
    return [{ field_key: field.field_key, label: field.label, group: field.group,
      previous_value: stateValue(old), observed_value: stateValue(field),
      requires_fact_check: true }];
  });
}

/** Structural changes are observations, never reusable answers for new rows. */
export function structureChanges(before, after) {
  const limit = 40;
  const lists = { added: [], removed: [], identity_changed: [], options_changed: [], reordered: [] };
  if (before) {
    const old = new Map(before.fields.map(f => [f.field_key, f]));
    const current = new Map(after.fields.map(f => [f.field_key, f]));
    for (const [key, field] of old) {
      if (!current.has(key)) lists.removed.push(key);
      else {
        const fresh = current.get(key);
        if (identity(field) !== identity(fresh)) lists.identity_changed.push(key);
        if (optionIdentity(field.options) !== optionIdentity(fresh.options)) lists.options_changed.push(key);
      }
    }
    for (const key of current.keys()) if (!old.has(key)) lists.added.push(key);
    const commonBefore = before.fields.filter(f => current.has(f.field_key)).map(f => f.field_key);
    const commonAfter = after.fields.filter(f => old.has(f.field_key)).map(f => f.field_key);
    commonAfter.forEach((key, i) => { if (key !== commonBefore[i]) lists.reordered.push(key); });
  }
  const counts = Object.fromEntries(Object.entries(lists).map(([key, items]) => [key, items.length]));
  return { page_changed: !!before && before.page_url !== after.page_url,
    changed: !!before && (before.page_url !== after.page_url || Object.values(counts).some(count => count > 0)),
    counts, field_keys: Object.fromEntries(Object.entries(lists).map(([key, items]) => [key, items.slice(0, limit)])),
    truncated: Object.values(counts).some(count => count > limit) };
}

export async function operateObservedControl(tab, snapshot, operation, args) {
  if (!snapshot) throw new ControlNotReady('Observe form controls before input');
  const keys = Object.keys(args).filter(key => key !== 'mode').sort().join(',');
  const shapes = { fill_control: ['field_key,value'], select_control: ['field_key,value', 'field_key,values'],
    open_control: ['field_key'], search_control: ['field_key,value'], set_checked: ['checked,field_key'] };
  if (!shapes[operation]?.includes(keys) || typeof args.field_key !== 'string' || !args.field_key.trim()) {
    throw new ControlNotReady('Invalid observed control arguments');
  }
  const old = snapshot.fields.find(field => field.field_key === args.field_key);
  if (!old) throw new ControlNotReady('Control is not in the current form observation');
  const fresh = await observeForm(tab);
  const field = fresh.fields.find(item => item.field_key === args.field_key);
  if (fresh.page_url !== snapshot.page_url || !field || identity(field) !== identity(old)) throw new ControlNotReady('Form control changed; observe again before input');
  if (structureChanges(snapshot, fresh).changed) throw new ControlNotReady('Options changed or form structure changed; observe again before input');
  if (field.disabled || field.readonly) throw new ControlNotReady('Control is not writable');
  const locator = tab.playwright.locator(field.selector);
  if (await locator.count() !== 1) throw new ControlNotReady('Form control is ambiguous');
  // ControlNotReady is reserved for proven pre-input rejection. A typed error
  // from the runtime or readback after input must still stop the host.
  const input = async action => {
    try { return await action(); }
    catch (error) {
      if (error instanceof ControlNotReady) throw new Error('Control input outcome unknown', { cause: error });
      throw error;
    }
  };
  const readAfterInput = () => input(() => observeForm(tab));
  let expected;
  if (operation === 'open_control' || operation === 'search_control') {
    if (field.control !== 'combobox' || field.multiple || selectedDisplay(field).length > 1) {
      throw new ControlNotReady('Open/search requires a supported observed single combobox');
    }
    if (operation === 'open_control') await input(() => locator.click({ timeoutMs: 10000 }));
    else {
      if (field.native_tag !== 'input' || field.editable_combobox !== true ||
          typeof args.value !== 'string' || args.value.length > 12000) {
        throw new ControlNotReady('Search requires an observed editable ARIA combobox and string query');
      }
      await input(() => locator.fill(args.value, { timeoutMs: 10000 }));
    }
    const after = await readAfterInput();
    const actual = after.fields.find(item => item.field_key === field.field_key);
    const sameControl = after.page_url === fresh.page_url && !!actual && identity(actual) === identity(field);
    return { field_key: field.field_key, operation, persisted: null,
      outcome: operation === 'open_control' ? 'opened' : 'searched',
      diagnostic: sameControl ? (operation === 'search_control' ? 'query_is_not_selection' : 'menu_is_not_selection')
        : 'control_changed_after_action',
      invalid: actual?.invalid ?? null, validation_message: actual?.validation_message || '', observation: after };
  } else if (operation === 'fill_control') {
    if (!['text', 'textarea', 'email', 'tel', 'url', 'search', 'number', 'date', 'month'].includes(field.control)) throw new ControlNotReady('Use an appropriate control operation');
    if (typeof args.value !== 'string' || args.value.length > 12000) throw new ControlNotReady('Invalid control value');
    expected = args.value;
    await input(() => locator.fill(expected, { timeoutMs: 10000 }));
    await input(() => locator.press('Tab', { timeoutMs: 10000 }));
    // Native date inputs can have several keyboard segments. One Tab may move
    // within the input without firing blur; verify focus before advancing again.
    if (['date', 'month'].includes(field.control)) {
      for (let remaining = 3; remaining > 0; remaining--) {
        const focus = await readAfterInput();
        if (!focus.fields.find(item => item.field_key === field.field_key)?.focused) break;
        await input(() => locator.press('Tab', { timeoutMs: 10000 }));
      }
    }
  } else if (operation === 'select_control') {
    const many = keys === 'field_key,values';
    if (many && Array.isArray(args.values) && args.values.length === 0) {
      throw new ControlNotReady('Clearing native multiple selection is unsupported; values must be nonempty');
    }
    if (!['select', 'combobox'].includes(field.control) ||
        (many ? !Array.isArray(args.values) || args.values.length < 1 || args.values.length > 80 || args.values.some(v => typeof v !== 'string' || v.length > 12000)
          : typeof args.value !== 'string' || args.value.length > 12000)) throw new ControlNotReady('Select requires observed options');
    if (many ? field.control !== 'select' || field.native_tag !== 'select' || field.multiple !== true
      : field.multiple === true || selectedDisplay(field).length > 1) {
      throw new ControlNotReady('Use values only for native SELECT multiple; custom multiple selection is unsupported');
    }
    if (many && (field.selection_source === 'unavailable' || !Array.isArray(field.selected_values))) {
      throw new ControlNotReady('Native multiple selection cannot be observed completely');
    }
    if (JSON.stringify(field.options) !== JSON.stringify(old.options)) throw new ControlNotReady('Options changed; observe options again');
    const options = (many ? args.values : [args.value]).map(value => {
      const matches = field.options.filter(o => !o.disabled && (o.value === value || o.label === value));
      if (matches.length !== 1 || field.options.filter(o => o.value === matches[0].value).length !== 1) {
        throw new ControlNotReady('Option is missing or ambiguous; observe options again');
      }
      return matches[0];
    });
    if (new Set(options.map(o => o.value)).size !== options.length) throw new ControlNotReady('Selection contains duplicate options');
    expected = many ? options.map(o => o.value).sort() : options[0].value;
    if (field.control === 'select') {
      await input(() => locator.selectOption(many ? expected.map(value => ({ value })) : { value: expected }, { timeoutMs: 10000 }));
      await input(() => locator.press('Tab', { timeoutMs: 10000 }));
    } else {
      const optionLocator = tab.playwright.locator(options[0].selector);
      if (await optionLocator.count() !== 1) throw new ControlNotReady('Combobox option is ambiguous');
      const ready = await observeForm(tab);
      const readyField = ready.fields.find(item => item.field_key === field.field_key);
      if (ready.page_url !== fresh.page_url || !readyField || identity(readyField) !== identity(field) ||
          JSON.stringify(readyField.options) !== JSON.stringify(field.options)) {
        throw new ControlNotReady('Options changed before click; observe options again');
      }
      await input(() => optionLocator.click({ timeoutMs: 10000 }));
    }
  } else if (operation === 'set_checked') {
    if (!['checkbox', 'radio'].includes(field.control) || typeof args.checked !== 'boolean') throw new ControlNotReady('Checked state requires an ordinary observed checkbox or native radio');
    if (field.control === 'radio' && (args.checked !== true || field.native_tag !== 'input' ||
        !field.radio_group?.name || field.radio_group.fully_observed !== true ||
        !field.radio_group.peer_keys?.includes(field.field_key))) {
      throw new ControlNotReady('Radio requires checked=true and a completely observed native group');
    }
    expected = args.checked;
    await input(() => locator.setChecked(expected, { timeoutMs: 10000 }));
  } else throw new ControlNotReady('Unsupported form operation');
  const after = await readAfterInput();
  const actual = after.fields.find(item => item.field_key === field.field_key);
  let persisted = after.page_url === fresh.page_url && !!actual && identity(actual) === identity(field);
  if (persisted && field.control === 'combobox') {
    const display = selectedDisplay(actual);
    // A combobox with several visible selections has ambiguous replace/add
    // semantics. Keep the result unknown instead of claiming a replacement.
    if (display.length > 1) persisted = null;
    else if (display.length === 1) {
      // Menus commonly unmount after selection; verify against the exact option
      // observed before the click, not the now-closed popup's option list.
      const selected = field.options.filter(option => !option.disabled &&
        (option.value === expected || option.label === expected));
      persisted = selected.length === 1 &&
        (display[0] === selected[0].label || display[0] === selected[0].value || display[0] === expected);
    } else {
      const selected = actual.options.filter(option => option.selected === true);
      // Input text may be an uncommitted search query even after an option click.
      // A display or a unique ARIA selection must independently prove commitment.
      persisted = selected.length === 1 ? selected[0].value === expected || selected[0].label === expected : null;
    }
  } else if (persisted && field.control === 'select' && field.multiple) {
    persisted = actual.selection_source === 'unavailable' || !Array.isArray(actual.selected_values) ? null
      : sameStateValue([...actual.selected_values].sort(), expected);
  } else if (persisted && field.control === 'radio') {
    const peers = field.radio_group.peer_keys.map(key => after.fields.find(item => item.field_key === key));
    persisted = peers.every((peer, i) => !!peer && identity(peer) === identity(fresh.fields.find(item => item.field_key === field.radio_group.peer_keys[i]))) &&
      peers.every(peer => peer.checked === (peer.field_key === field.field_key));
  } else if (persisted) persisted = stateValue(actual) === expected;
  const displayProvesSingleComboboxSelection = actual?.control === 'combobox' && selectedDisplay(actual).length === 1 && persisted === true;
  return { field_key: field.field_key, operation,
    persisted: actual?.value_source === 'unavailable' && !displayProvesSingleComboboxSelection ? null : persisted,
    invalid: actual?.invalid ?? null,
    validation_message: actual?.validation_message || '',
    observation: after };
}

export class ControlNotReady extends Error {}
