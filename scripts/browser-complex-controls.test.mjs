import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { randomUUID } from 'node:crypto';
import vm from 'node:vm';
import { observeForm, operateObservedControl, structureChanges, changedFields, enrichLiveValues,
  ControlNotReady } from './browser-form-state.mjs';
import { operateFieldBatch } from './browser-field-batch.mjs';
import { browserAdapter, createVisualHost } from './visual-bridge-host.mjs';

const field = (overrides = {}) => ({ field_key: '#school', selector: '#school', label: 'School',
  group_key: '#education1', group: 'Education 1', native_tag: 'input', control: 'combobox',
  multiple: false, editable_combobox: true, value: '', selected_display: [], options: [], ...overrides });
const form = (...fields) => ({ page_url: 'https://fixture.test/apply', fields, protected_count: 0 });
const option = (value, overrides = {}) => ({ value, label: value, selector: `#${value}`, ...overrides });

// Run the real observation callback against a small inert DOM contract. This
// exercises association and exclusion, rather than supplying preclassified fields.
function domNode(tag, attributes = {}, children = [], text = '') {
  const node = { nodeType: 1, tagName: tag.toUpperCase(), attributes, children, childNodes: children,
    parentElement: null, id: attributes.id || '', name: attributes.name || '', value: '', checked: false,
    labels: [], get textContent() { return text + children.map(child => child.textContent).join(' '); },
    getAttribute: key => attributes[key] ?? null, hasAttribute: key => key in attributes,
    getClientRects: () => attributes.hidden ? [] : [{ width: 100, height: 20 }],
    matches(selector) {
      return selector.split(',').some(part => {
        part = part.trim();
        if (part === '*') return true;
        if (part.startsWith('.')) return (attributes.class || '').split(' ').includes(part.slice(1));
        const wanted = /^([a-z]+)?(?:\[([^=\]]+)="([^"]*)"\])?$/.exec(part);
        return !!wanted && (!wanted[1] || tag.toLowerCase() === wanted[1]) &&
          (!wanted[2] || attributes[wanted[2]] === wanted[3]);
      });
    },
    closest(selector) {
      for (let current = this; current; current = current.parentElement) if (current.matches(selector)) return current;
      return null;
    },
    querySelectorAll(selector) {
      return children.flatMap(child => [...(child.matches(selector) ? [child] : []), ...child.querySelectorAll(selector)]);
    },
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; },
  };
  for (const child of children) child.parentElement = node;
  return node;
}

function observedDomTab(children) {
  const html = domNode('html', {}, [domNode('body', {}, children)]);
  const document = { children: [html], documentElement: html, querySelectorAll: selector => [
    ...(html.matches(selector) ? [html] : []), ...html.querySelectorAll(selector),
  ], getElementById(id) { return this.querySelectorAll(`[id="${id}"]`)[0] || null; } };
  for (const node of document.querySelectorAll('*')) node.getRootNode = () => document;
  const writes = [];
  return { writes, tab: { playwright: {
    evaluate: async fn => structuredClone(vm.runInNewContext(`(${fn.toString()})()`, {
      document, location: { href: 'https://fixture.test/apply' }, getComputedStyle: () => ({ visibility: 'visible' }),
    })),
    locator: () => ({ count: async () => 1, click: async () => writes.push('click'), fill: async () => writes.push('fill'),
      setChecked: async () => writes.push('checked') }),
  } } };
}

test('initially empty combo inherits multiple from its associated listbox and refuses open/search', async () => {
  for (const association of ['aria-controls', 'aria-owns']) {
    for (const nested of [false, true]) {
      const listbox = domNode('div', { id: 'choices', role: 'listbox', 'aria-multiselectable': 'true' });
      const target = nested ? domNode('div', { id: 'menu' }, [listbox]) : listbox;
      const r = observedDomTab([domNode('input', { id: 'combo', role: 'combobox', 'aria-label': 'School',
        [association]: nested ? 'menu' : 'choices' }), target]);
      const observed = await observeForm(r.tab);
      assert.equal(observed.fields[0].multiple, true);
      assert.deepEqual(observed.fields[0].options, []);
      for (const operation of ['open_control', 'search_control']) {
        await assert.rejects(operateObservedControl(r.tab, observed, operation, { field_key: '[id="combo"]',
          ...(operation === 'search_control' ? { value: 'Q' } : {}) }), /supported observed single combobox/);
      }
      assert.deepEqual(r.writes, []);
    }
  }
});

test('legal or sensitive group semantics exclude plain Yes/No radios without protecting unrelated groups', async () => {
  for (const kind of ['legend', 'aria-label', 'aria-labelledby', 'nested_unnamed_group']) {
    const radios = [domNode('input', { id: 'yes', type: 'radio', name: 'answer', 'aria-label': 'Yes' }),
      domNode('input', { id: 'no', type: 'radio', name: 'answer', 'aria-label': 'No' })];
    const text = kind === 'aria-label' ? 'Passport and national ID confirmation' : 'I agree to terms and privacy policy';
    const legal = kind === 'aria-label' ? domNode('div', { role: 'group', 'aria-label': text }, radios)
      : kind === 'aria-labelledby' ? domNode('div', { role: 'group', 'aria-labelledby': 'legal-label' }, radios)
        : domNode('fieldset', {}, [domNode('legend', {}, [], text),
          ...(kind === 'nested_unnamed_group' ? [domNode('div', { role: 'group' }, radios)] : radios)]);
    const routine = domNode('fieldset', {}, [domNode('legend', {}, [], 'Preferred work mode'),
      domNode('input', { id: 'routine-yes', type: 'radio', name: 'routine', 'aria-label': 'Yes' }),
      domNode('input', { id: 'routine-no', type: 'radio', name: 'routine', 'aria-label': 'No' })]);
    const r = observedDomTab([domNode('form', {}, [legal, routine]), domNode('p', { id: 'legal-label' }, [], text)]);
    const observed = await observeForm(r.tab);
    assert.equal(observed.protected_count, 2);
    assert.deepEqual(observed.fields.map(f => f.field_key), ['[id="routine-yes"]', '[id="routine-no"]']);
    assert.equal(observed.fields.every(f => f.radio_group.fully_observed), true);
    await assert.rejects(operateObservedControl(r.tab, observed, 'set_checked', {
      field_key: '[id="yes"]', checked: true,
    }), /not in the current form observation/);
    assert.deepEqual(r.writes, []);
  }
});

function runtime(initial, { open = () => {}, search = () => {}, select = () => {}, checked = () => {} } = {}) {
  const state = structuredClone(initial);
  const writes = [];
  let failedRead = false;
  const tab = { id: 'complex-fixture', url: async () => state.page_url, title: async () => 'Fixture', playwright: {
    domSnapshot: async () => '- combobox "School"',
    evaluate: async () => {
      if (failedRead) throw new ControlNotReady('readback failed after input');
      return structuredClone(state);
    },
    locator: selector => ({
      count: async () => state.fields.filter(f => f.selector === selector || f.options.some(o => o.selector === selector)).length,
      click: async () => {
        writes.push(['click', selector]);
        const target = state.fields.find(f => f.selector === selector);
        if (target) open(target, state);
        else {
          const owner = state.fields.find(f => f.options.some(o => o.selector === selector));
          const chosen = owner.options.find(o => o.selector === selector);
          owner.value = '';
          owner.selected_display = [chosen.label];
          owner.options = [];
        }
      },
      fill: async value => {
        writes.push(['fill', selector, value]);
        const target = state.fields.find(f => f.selector === selector);
        target.value = value;
        search(target, state);
      },
      press: async key => { writes.push(['press', key]); },
      selectOption: async values => {
        writes.push(['select', values]);
        const target = state.fields.find(f => f.selector === selector);
        if (Array.isArray(values)) target.selected_values = values.map(o => o.value);
        else target.value = values.value;
        select(target, state);
      },
      setChecked: async value => {
        writes.push(['checked', selector, value]);
        const target = state.fields.find(f => f.selector === selector);
        target.checked = value;
        checked(target, state);
      },
    }),
  } };
  return { state, writes, tab, failReadAfterInput: () => { failedRead = true; } };
}

test('open then search yields fresh candidates without blur or claiming answer persistence', async () => {
  const initial = form(field());
  const r = runtime(initial, { open: f => { f.options = [option('Old')]; },
    search: f => { f.options = [option('Nanyang Technological University')]; } });
  const opened = await operateObservedControl(r.tab, initial, 'open_control', { field_key: '#school' });
  assert.equal(opened.persisted, null);
  assert.equal(opened.outcome, 'opened');
  assert.equal(opened.diagnostic, 'menu_is_not_selection');
  assert.equal(opened.observation.fields[0].options[0].value, 'Old');
  const searched = await operateObservedControl(r.tab, opened.observation, 'search_control', { field_key: '#school', value: 'Nanyang' });
  assert.equal(searched.persisted, null);
  assert.equal(searched.diagnostic, 'query_is_not_selection');
  assert.equal(searched.observation.fields[0].value, 'Nanyang');
  const selected = await operateObservedControl(r.tab, searched.observation, 'select_control', {
    field_key: '#school', value: 'Nanyang Technological University',
  });
  assert.equal(selected.persisted, true);
  assert.deepEqual(r.writes, [['click', '#school'], ['fill', '#school', 'Nanyang'],
    ['click', '#Nanyang Technological University']]);
});

test('search without suggestions remains query-only and cannot select a guessed answer', async () => {
  const initial = form(field());
  const r = runtime(initial);
  const searched = await operateObservedControl(r.tab, initial, 'search_control', { field_key: '#school', value: 'Query' });
  assert.equal(searched.persisted, null);
  assert.deepEqual(searched.observation.fields[0].options, []);
  await assert.rejects(operateObservedControl(r.tab, searched.observation, 'select_control', {
    field_key: '#school', value: 'Query',
  }), /missing or ambiguous/);
  assert.deepEqual(r.writes, [['fill', '#school', 'Query']]);
});

test('search rejects native selects, readonly, non-input ARIA and custom multiple before input', async () => {
  for (const overrides of [{ control: 'select' }, { readonly: true }, { native_tag: 'button', editable_combobox: false },
    { multiple: true }, { selected_display: ['One', 'Two'] }]) {
    const initial = form(field(overrides));
    const r = runtime(initial);
    await assert.rejects(operateObservedControl(r.tab, initial, 'search_control', { field_key: '#school', value: 'Query' }), ControlNotReady);
    assert.equal(r.writes.length, 0);
  }
});

test('missing, duplicate and asynchronous candidate identity drift reject before selection', async () => {
  for (const change of [s => { s.fields[0].options = []; }, s => { s.fields[0].options.push(option('NTU')); },
    s => { s.fields[0].options[0].dom_identity = 42; }]) {
    const initial = form(field({ options: [option('NTU', { dom_identity: 41 })] }));
    const r = runtime(initial);
    change(r.state);
    await assert.rejects(operateObservedControl(r.tab, initial, 'select_control', { field_key: '#school', value: 'NTU' }), ControlNotReady);
    assert.equal(r.writes.length, 0);
  }
});

test('candidate update during locator count is rejected before click', async () => {
  const initial = form(field({ options: [option('NTU')] }));
  const r = runtime(initial);
  const locator = r.tab.playwright.locator;
  r.tab.playwright.locator = selector => ({ ...locator(selector), count: async () => {
    if (selector === '#NTU') r.state.fields[0].options[0].label = 'Changed university';
    return 1;
  } });
  await assert.rejects(operateObservedControl(r.tab, initial, 'select_control', { field_key: '#school', value: 'NTU' }), /Options changed before click/);
  assert.equal(r.writes.length, 0);
});

test('combo value equal to a query cannot prove selection without display or ARIA selected option', async () => {
  const initial = form(field({ value: 'NTU', options: [option('NTU')] }));
  const r = runtime(initial);
  const locator = r.tab.playwright.locator;
  r.tab.playwright.locator = selector => ({ ...locator(selector), click: async () => {
    r.writes.push(['click', selector]);
  } });
  const result = await operateObservedControl(r.tab, initial, 'select_control', { field_key: '#school', value: 'NTU' });
  assert.equal(result.persisted, null);
});

const multi = overrides => field({ control: 'select', native_tag: 'select', multiple: true,
  editable_combobox: false, options: [option('a'), option('b'), option('c')],
  selected_values: ['a'], selection_source: 'dom_read', ...overrides });

test('native multiple replaces and verifies the complete nonempty set', async () => {
  for (const values of [['c', 'a'], ['b']]) {
    const initial = form(multi());
    const r = runtime(initial);
    const result = await operateObservedControl(r.tab, initial, 'select_control', { field_key: '#school', values });
    assert.equal(result.persisted, true);
    assert.deepEqual([...result.observation.fields[0].selected_values].sort(), [...values].sort());
    assert.deepEqual(r.writes[0], ['select', [...values].sort().map(value => ({ value }))]);
  }
});

test('native multiple cannot mistake first value or unknown selection for the complete set', async () => {
  for (const unavailable of [false, true]) {
    const initial = form(multi());
    const r = runtime(initial, { select: f => {
      f.value = 'a';
      f.selected_values = unavailable ? null : ['a'];
      if (unavailable) f.selection_source = 'unavailable';
    } });
    const result = await operateObservedControl(r.tab, initial, 'select_control', { field_key: '#school', values: ['a', 'c'] });
    assert.equal(result.persisted, unavailable ? null : false);
  }
});

test('multi selection rejects scalar, custom, missing, duplicate aliases and incomplete readback before input', async () => {
  for (const [f, args] of [[multi(), { value: 'a' }], [field({ multiple: true }), { values: ['a'] }],
    [multi(), { values: [] }], [multi(), { values: ['missing'] }], [multi(), { value: 'a', values: ['a'] }],
    [multi(), { values: ['a', 'a'] }], [multi({ selected_values: null, selection_source: 'unavailable' }), { values: ['a'] }],
    [multi({ options: [option('a', { label: 'Alias' })] }), { values: ['a', 'Alias'] }]]) {
    const initial = form(f);
    const r = runtime(initial);
    await assert.rejects(operateObservedControl(r.tab, initial, 'select_control', { field_key: '#school', ...args }), ControlNotReady);
    assert.equal(r.writes.length, 0);
  }
});

test('DOMSnapshot optionSelected recovers all selected options despite omitted live selected props', () => {
  const initial = form(multi({ selector: '[id="multi"]', selected_values: null, selection_source: 'unavailable' }));
  const snapshot = { strings: [initial.page_url, 'HTML', 'SELECT', 'OPTION', 'id', 'multi', 'value', 'a', 'b', 'c'], documents: [{
    documentURL: 0, nodes: { nodeType: [1, 1, 1, 1, 1], nodeName: [1, 2, 3, 3, 3], parentIndex: [-1, 0, 1, 1, 1],
      attributes: [[], [4, 5], [6, 7], [6, 8], [6, 9]], backendNodeId: [10, 11, 12, 13, 14], optionSelected: { index: [2, 4] } },
  }] };
  enrichLiveValues(initial, snapshot);
  assert.deepEqual(initial.fields[0].selected_values, ['a', 'c']);
  assert.equal(initial.fields[0].selection_source, 'live_dom_snapshot');
  assert.equal(initial.fields[0].dom_identity, 11);
  delete snapshot.documents[0].nodes.optionSelected;
  enrichLiveValues(initial, snapshot);
  assert.equal(initial.fields[0].selected_values, null);
  assert.equal(initial.fields[0].selection_source, 'unavailable');
});

const radio = (key, group, checked) => field({ field_key: key, selector: key, control: 'radio', checked,
  radio_group: { name: 'choice', form_key: group, root_key: 'document', peer_keys: [`${group}a`, `${group}b`], fully_observed: true } });

test('native radio selection verifies every peer and leaves same-name other form group alone', async () => {
  const initial = form(radio('onea', 'one', false), radio('oneb', 'one', true),
    radio('twoa', 'two', false), radio('twob', 'two', true));
  const r = runtime(initial, { checked: (target, state) => {
    for (const peer of state.fields) if (peer.radio_group.form_key === target.radio_group.form_key) peer.checked = peer === target;
  } });
  const result = await operateObservedControl(r.tab, initial, 'set_checked', { field_key: 'onea', checked: true });
  assert.equal(result.persisted, true);
  assert.deepEqual(r.state.fields.map(f => f.checked), [true, false, false, true]);
});

test('radio that fails to clear its peer is never reported persisted', async () => {
  const initial = form(radio('onea', 'one', false), radio('oneb', 'one', true));
  const r = runtime(initial);
  const result = await operateObservedControl(r.tab, initial, 'set_checked', { field_key: 'onea', checked: true });
  assert.equal(result.persisted, false);
});

test('radio false, custom native tag and incomplete/unnamed groups reject before input', async () => {
  for (const [overrides, checked] of [[{}, false], [{ native_tag: 'div' }, true],
    [{ radio_group: { fully_observed: false, name: 'choice' } }, true],
    [{ radio_group: { fully_observed: true, name: '', peer_keys: ['onea'] } }, true]]) {
    const initial = form({ ...radio('onea', 'one', false), ...overrides }, radio('oneb', 'one', true));
    const r = runtime(initial);
    await assert.rejects(operateObservedControl(r.tab, initial, 'set_checked', { field_key: 'onea', checked }), ControlNotReady);
    assert.equal(r.writes.length, 0);
  }
});

test('dynamic row creation, removal, order and backend rebind are bounded structural observations', () => {
  const before = form(field({ dom_identity: 1 }), field({ field_key: '#other', selector: '#other', dom_identity: 2 }));
  const after = form(field({ field_key: '#other', selector: '#other', dom_identity: 2 }),
    field({ dom_identity: 3, value: 'Rebound' }), ...Array.from({ length: 45 }, (_, i) => field({ field_key: `new${i}` })));
  const delta = structureChanges(before, after);
  assert.equal(delta.counts.added, 45);
  assert.equal(delta.field_keys.added.length, 40);
  assert.equal(delta.truncated, true);
  assert.deepEqual(delta.field_keys.identity_changed, ['#school']);
  assert.equal(delta.counts.reordered, 2);
  assert.deepEqual(changedFields(before, after), []);
  assert.deepEqual(structureChanges(after, before).counts.removed, 45);
});

test('added rows or lost backend identity reject stale input and batch parks after a new row', async () => {
  for (const change of [s => s.fields.push(field({ field_key: 'new' })), s => { delete s.fields[0].dom_identity; }]) {
    const initial = form(field({ dom_identity: 1 }));
    const r = runtime(initial);
    change(r.state);
    await assert.rejects(operateObservedControl(r.tab, initial, 'open_control', { field_key: '#school' }), ControlNotReady);
    assert.equal(r.writes.length, 0);
  }
  const initial = form(field({ control: 'text' }), field({ field_key: '#other', selector: '#other', control: 'text' }));
  const r = runtime(initial, { search: (_, state) => state.fields.push(field({ field_key: 'new' })) });
  const result = await operateFieldBatch(r.tab, initial, ['#school', '#other'].map(key => ({
    operation: 'fill_control', field_key: key, value: 'Fact',
  })));
  assert.equal(result.batch_result.status, 'parked');
  assert.equal(result.batch_result.completed, 1);
  assert.equal(result.batch_result.structure_changes.counts.added, 1);
  assert.equal(r.writes.filter(w => w[0] === 'fill').length, 1);
});

test('batch rejects new complex operation and native multiple before any partial write', async () => {
  for (const step of [{ operation: 'search_control', field_key: '#school', value: 'Q' },
    { operation: 'select_control', field_key: '#school', value: 'a' },
    { operation: 'select_control', field_key: '#school', values: ['a'] }]) {
    const initial = form(multi());
    const r = runtime(initial);
    await assert.rejects(operateFieldBatch(r.tab, initial, [step]), ControlNotReady);
    assert.equal(r.writes.length, 0);
  }
});

test('host Playwright surface exposes opened/search observation and structural feedback', async () => {
  const r = runtime(form(field()), { search: f => { f.options = [option('NTU')]; } });
  const adapter = browserAdapter(r.tab, { observationMode: 'playwright', reuseFormObservations: true });
  await adapter.observe();
  const ticket = await adapter.act('search_control', { field_key: '#school', value: 'NTU' });
  const content = await adapter.observe({ mode: 'dom', actionResult: ticket });
  const state = content.map(item => { try { return JSON.parse(item.text); } catch { return {}; } }).find(item => item.form_state);
  assert.equal(state.control_result.persisted, null);
  assert.equal(state.control_result.diagnostic, 'query_is_not_selection');
  assert.deepEqual(state.form_state.fields[0].options.map(o => o.value), ['NTU']);
  assert.equal(state.structure_changes.counts.options_changed, 1);
});

test('post-input typed failures have unknown outcome, never proven pre-input rejection', async () => {
  const initial = form(field());
  const r = runtime(initial, { search: () => r.failReadAfterInput() });
  await assert.rejects(operateObservedControl(r.tab, initial, 'search_control', { field_key: '#school', value: 'Query' }), error => {
    assert.ok(!(error instanceof ControlNotReady));
    assert.match(error.message, /outcome unknown/);
    return true;
  });
  assert.equal(r.writes.length, 1);
});

test('host stops on search readback failure and never permits a retry of unknown input', async t => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'applypilot-complex-test-'));
  t.after(async () => {
    assert.equal(path.dirname(path.resolve(directory)), path.resolve(os.tmpdir()));
    assert.ok(path.basename(directory).startsWith('applypilot-complex-test-'));
    await fs.rm(directory, { recursive: true, force: true });
  });
  const r = runtime(form(field()), { search: () => r.failReadAfterInput() });
  const host = await createVisualHost({ directory, target: { runtime: 'iab', tab_id: r.tab.id,
    application_url: 'https://fixture.test/apply' },
    adapter: browserAdapter(r.tab, { observationMode: 'playwright' }) });
  const request = async (operation, observation_id, args = {}) => {
    const request_id = randomUUID();
    await fs.writeFile(path.join(directory, 'pending', `${request_id}.json`), JSON.stringify({
      ...host.binding, request_id, operation, observation_id, arguments: args, deadline_at: Date.now() / 1000 + 30,
    }));
    return host.execute(request_id);
  };
  const before = await request('observe');
  const result = await request('search_control', before.observation_id, { field_key: '#school', value: 'Query' });
  assert.equal(result.ok, false);
  assert.equal(result.outcome, 'outcome_unknown');
  assert.equal(JSON.parse(await fs.readFile(path.join(directory, 'host.json'))).status, 'stopped');
  assert.equal(r.writes.length, 1);
  await assert.rejects(host.peek(), /closed/);
});
