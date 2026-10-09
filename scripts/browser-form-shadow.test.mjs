import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { existsSync } from 'node:fs';
import { mkdtemp, rm } from 'node:fs/promises';
import { homedir } from 'node:os';
import path from 'node:path';
import test from 'node:test';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { observeForm, operateObservedControl } from './browser-form-state.mjs';

const fixture = fileURLToPath(new URL('../tests/fixtures/apply/ats_shadow.html', import.meta.url));

function chromiumPath() {
  const explicit = process.env.APPLYPILOT_TEST_CHROMIUM_EXECUTABLE;
  if (explicit) return existsSync(explicit) ? explicit : undefined;
  const root = process.env.PLAYWRIGHT_BROWSERS_PATH || path.join(homedir(), 'AppData', 'Local', 'ms-playwright');
  const candidates = ['chromium-1234', 'chromium-1228', 'chromium-1223', 'chromium-1208'];
  const suffix = process.platform === 'win32' ? path.join('chrome-win64', 'chrome.exe') : path.join('chrome-linux', 'chrome');
  return candidates.map(name => path.join(root, name, suffix)).find(value => existsSync(value));
}

class Cdp {
  constructor(url) {
    this.socket = new WebSocket(url);
    this.nextId = 1;
    this.pending = new Map();
    this.ready = new Promise((resolve, reject) => {
      const timer = setTimeout(() => reject(Error('CDP WebSocket connection timed out')), 10000);
      const clearTimer = () => clearTimeout(timer);
      this.socket.addEventListener('open', () => { clearTimer(); resolve(); }, { once: true });
      this.socket.addEventListener('error', event => {
        clearTimer();
        const error = event.error || Error('CDP WebSocket error');
        reject(error);
        this.rejectPending(error);
      });
      this.socket.addEventListener('close', () => {
        clearTimer();
        const error = Error('CDP WebSocket closed');
        reject(error);
        this.rejectPending(error);
      });
    });
    this.socket.addEventListener('message', event => {
      const message = JSON.parse(event.data);
      if (!message.id) return;
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(message.error.message));
      else pending.resolve(message.result);
    });
  }

  rejectPending(error) {
    for (const [id, pending] of this.pending) {
      this.pending.delete(id);
      clearTimeout(pending.timer);
      pending.reject(error);
    }
  }

  async send(method, params = {}, sessionId) {
    await this.ready;
    const id = this.nextId++;
    const message = { id, method, params };
    if (sessionId) message.sessionId = sessionId;
    const result = new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(Error(`CDP request timed out: ${method}`));
      }, 10000);
      this.pending.set(id, { resolve, reject, timer });
    });
    try { this.socket.send(JSON.stringify(message)); }
    catch (error) {
      const pending = this.pending.get(id);
      if (pending) {
        this.pending.delete(id);
        clearTimeout(pending.timer);
        pending.reject(error);
      }
    }
    return result;
  }

  async close() {
    this.rejectPending(Error('CDP client closed'));
    if (this.socket.readyState === WebSocket.CLOSED) return;
    const closed = new Promise(resolve => this.socket.addEventListener('close', resolve, { once: true }));
    try { this.socket.close(); } catch { /* The browser may already have closed the socket. */ }
    let timer;
    await Promise.race([closed, new Promise(resolve => { timer = setTimeout(resolve, 1000); })]);
    clearTimeout(timer);
  }
}

async function launchFixture(t, { withSnapshot = true } = {}) {
  const executable = chromiumPath();
  assert.ok(executable, 'A local Playwright Chromium executable is required');
  const userData = await mkdtemp(path.join(process.env.TEMP || '/tmp', 'applypilot-shadow-'));
  let child;
  const runtime = { child: null, browser: null, userData };
  try {
    child = spawn(executable, [
      '--headless=new', '--no-sandbox', '--disable-gpu', '--disable-background-networking',
      '--disable-component-update', '--disable-default-apps', '--no-first-run',
      '--allow-file-access-from-files', '--remote-debugging-port=0', `--user-data-dir=${userData}`,
    ], { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
  } catch (error) {
    assertOwnedUserData(userData);
    await rm(userData, { recursive: true, force: true });
    throw error;
  }
  runtime.child = child;
  t.after(() => closeFixture(runtime));
  const devtoolsUrl = await new Promise((resolve, reject) => {
    let output = '';
    const timer = setTimeout(() => reject(Error(`Chromium did not announce CDP: ${output}`)), 10000);
    const onData = chunk => {
      output += String(chunk);
      const match = output.match(/DevTools listening on (ws:\/\/[^\s]+)/);
      if (match) { clearTimeout(timer); resolve(match[1]); }
    };
    child.stderr.on('data', onData);
    child.stdout.on('data', onData);
    child.once('exit', code => { clearTimeout(timer); reject(Error(`Chromium exited before CDP startup (${code}): ${output}`)); });
    child.once('error', error => { clearTimeout(timer); reject(error); });
  });
  const browser = new Cdp(devtoolsUrl);
  runtime.browser = browser;
  const fixtureUrl = pathToFileURL(fixture).href;
  const target = await browser.send('Target.createTarget', { url: fixtureUrl });
  const attached = await browser.send('Target.attachToTarget', { targetId: target.targetId, flatten: true });
  const sessionId = attached.sessionId;
  await browser.send('Runtime.enable', {}, sessionId);
  await browser.send('DOMSnapshot.enable', {}, sessionId).catch(() => {});

  const evaluate = async (expression, awaitPromise = true) => {
    const result = await browser.send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise }, sessionId);
    if (result.exceptionDetails) throw Error(result.exceptionDetails.text || 'Runtime evaluation failed');
    return result.result?.value;
  };
  const tab = {
    playwright: {
      evaluate: fn => evaluate(`(${fn.toString()})()`),
      locator: selector => locatorFor(evaluate, selector),
    },
  };
  if (withSnapshot) {
    tab.capabilities = { get: async name => name === 'cdp' ? { send: (method, params) => browser.send(method, params, sessionId) } : null };
  }
  // A new target can still be on about:blank with readyState complete.
  const deadline = Date.now() + 10000;
  while (!await evaluate(`location.href === ${JSON.stringify(fixtureUrl)} && document.readyState === "complete"`)) {
    if (Date.now() >= deadline) throw Error('Synthetic fixture navigation did not complete');
    await new Promise(resolve => setTimeout(resolve, 20));
  }
  return { ...runtime, tab, evaluate };
}

async function waitForChildExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return true;
  return new Promise(resolve => {
    const onExit = () => { clearTimeout(timer); resolve(true); };
    const timer = setTimeout(() => {
      child.removeListener('exit', onExit);
      resolve(false);
    }, timeoutMs);
    child.once('exit', onExit);
  });
}

function assertOwnedUserData(userData) {
  const tempRoot = path.resolve(process.env.TEMP || '/tmp');
  assert.equal(path.dirname(path.resolve(userData)), tempRoot);
  assert.ok(path.basename(userData).startsWith('applypilot-shadow-'));
}

async function closeFixture(runtime) {
  // Graceful shutdown lets Chromium release its Crashpad files before cleanup.
  if (runtime.browser) {
    await runtime.browser.send('Browser.close').catch(() => {});
    await runtime.browser.close();
  }
  if (runtime.child && !await waitForChildExit(runtime.child, 3000)) {
    runtime.child.kill('SIGKILL');
    if (!await waitForChildExit(runtime.child, 3000)) throw Error('Owned Chromium process did not exit after kill');
  }
  await new Promise(resolve => setTimeout(resolve, 100));
  assertOwnedUserData(runtime.userData);
  await rm(runtime.userData, { recursive: true, force: true });
}

function locatorFor(evaluate, selector) {
  const args = JSON.stringify(selector);
  const resolveOne = `(() => {
    const wanted = ${args};
    const roots = [document];
    for (let i = 0; i < roots.length; i++) {
      for (const el of roots[i].querySelectorAll('*')) if (el.shadowRoot) roots.push(el.shadowRoot);
    }
    const found = new Set();
    for (const root of roots) {
      for (const candidate of root.querySelectorAll(wanted)) found.add(candidate);
      // Production selectors cross open shadow boundaries with a space-separated
      // host path. The last segment is sufficient to identify this fixture's
      // unique observed control while retaining ordinary CSS semantics first.
      const last = wanted.trim().split(/\\s+/).at(-1);
      if (last && last !== wanted) for (const candidate of root.querySelectorAll(last)) found.add(candidate);
    }
    return [...found];
  })()`;
  return {
    count: async () => evaluate(`${resolveOne}.length`),
    fill: async value => evaluate(`(() => { const nodes = ${resolveOne}; if (nodes.length !== 1) throw Error('locator fill ambiguous'); const el = nodes[0]; el.focus(); const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set || Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set; if (setter) setter.call(el, ${JSON.stringify(value)}); else el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('input', { bubbles: true, composed: true })); el.dispatchEvent(new Event('change', { bubbles: true, composed: true })); })()`),
    press: async key => evaluate(`(() => { const nodes = ${resolveOne}; if (nodes.length !== 1) throw Error('locator press ambiguous'); const el = nodes[0]; el.dispatchEvent(new KeyboardEvent('keydown', { key: ${JSON.stringify(key)}, bubbles: true, composed: true })); if (${JSON.stringify(key)} === 'Tab') el.blur(); el.dispatchEvent(new KeyboardEvent('keyup', { key: ${JSON.stringify(key)}, bubbles: true, composed: true })); })()`),
    selectOption: async ({ value }) => evaluate(`(() => { const nodes = ${resolveOne}; if (nodes.length !== 1) throw Error('locator select ambiguous'); const el = nodes[0]; el.value = ${JSON.stringify(value)}; el.dispatchEvent(new Event('change', { bubbles: true, composed: true })); })()`),
    setChecked: async value => evaluate(`(() => { const nodes = ${resolveOne}; if (nodes.length !== 1) throw Error('locator check ambiguous'); const el = nodes[0]; el.checked = ${Boolean(value)}; el.dispatchEvent(new Event('change', { bubbles: true, composed: true })); })()`),
    click: async () => evaluate(`(() => { const nodes = ${resolveOne}; if (nodes.length !== 1) throw Error('locator click ambiguous'); nodes[0].click(); })()`),
  };
}

test('observeForm traverses nested open roots and keeps duplicate and anonymous controls stable', async t => {
  const runtime = await launchFixture(t);
  const form = await observeForm(runtime.tab);
  assert.equal(form.coverage.scope, 'visible_top_document_open_shadow');
  assert.ok(form.coverage.open_shadow_count >= 4);
  assert.equal(form.protected_count, 2);
  assert.equal(form.fields.filter(field => field.label === 'Full name').length, 1);
  const name = form.fields.find(field => field.label === 'Full name');
  assert.match(name.field_key, /input\[id="duplicate-id"\]/);
  assert.equal(name.field_key, 'input[id="duplicate-id"]');
  const dates = form.fields.filter(field => field.control === 'date');
  assert.equal(dates.length, 2);
  assert.notEqual(dates[0].field_key, dates[1].field_key);
  const topNote = form.fields.find(field => field.field_key === '[id="top-note"]');
  assert.equal(topNote.value, 'visible');
  assert.equal(topNote.value_source, 'live_dom_snapshot');
  // DOMSnapshot flattens shadow descendants; an anonymous shadow selector
  // that cannot be reconciled is reported as unknown rather than as empty.
  assert.ok(dates.every(field => field.value === null && field.value_source === 'unavailable'));
  assert.equal(form.fields.some(field => field.label === 'Account password'), false);
  assert.equal(form.fields.some(field => field.label === 'One-time code'), false);
});

test('operateObservedControl fills the second anonymous date in its nested shadow root', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  const before = await observeForm(runtime.tab);
  const dates = before.fields.filter(field => field.control === 'date');
  const result = await operateObservedControl(runtime.tab, before, 'fill_control', { field_key: dates[1].field_key, value: '2027-06-30' });
  assert.equal(result.persisted, true);
  const values = await runtime.evaluate(`(() => [...document.querySelectorAll('spl-dates')].flatMap(host => [...host.shadowRoot.querySelectorAll('input')].map(input => input.value)))()`);
  assert.deepEqual(values, ['', '2027-06-30']);
});

test('React Select single selection persists by selected display when input value stays blank', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  const before = await observeForm(runtime.tab);
  const country = before.fields.find(field => field.label === 'Preferred country');
  assert.ok(country);
  assert.equal(country.value, '');
  assert.deepEqual(country.selected_display, ['China']);
  const result = await operateObservedControl(runtime.tab, before, 'select_control', { field_key: country.field_key, value: 'Singapore' });
  assert.equal(result.persisted, true);
  const after = result.observation.fields.find(field => field.field_key === country.field_key);
  assert.equal(after.value, '');
  assert.deepEqual(after.selected_display, ['Singapore']);
  assert.deepEqual(after.options, []);
});

test('cross-shadow aria-controls resolves the unique visible ancestor-root list', async t => {
  const runtime = await launchFixture(t);
  const form = await observeForm(runtime.tab);
  const country = form.fields.find(field => field.label === 'Country');
  assert.ok(country);
  assert.equal(country.control, 'combobox');
  // The input and linked list are in different open roots of the same widget.
  assert.deepEqual(country.options.map(option => option.label), ['Singapore', 'China']);
});

test('visible required markers stay bound to one unambiguous form-group control', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  await runtime.evaluate(`document.body.innerHTML = ${JSON.stringify(`
    <div class="form-group"><label for="">Full Name: <span>*</span></label><div><input id="name" placeholder="Full Name" name="922716"></div></div>
    <div class="form-group"><label for="email">Email: <span>*</span></label><input id="email" type="email"></div>
    <div class="form-group"><label for="">Resume: <span>*</span></label><div class="custom-file" style="width: fit-content; display: block; height: auto;"><input type="file" placeholder="Resume" name="922720" id="resume" class="custom-file-input"><label class="custom-file-label">Choose file</label><div></div></div></div>
    <div class="form-group"><label>Phone: <span>＊</span></label><input id="phone" type="tel"></div>
    <div class="form-group"><label>Optional city</label><input id="city"></div>
    <label>Unrelated *</label><input id="outside">
    <div class="form-group"><label for="different">Other field *</label><input id="wrong-target"></div>
    <div class="form-group"><label>First *</label><input id="multiple-a"><input id="multiple-b"></div>
    <div class="form-group"><label>First *</label><label>Second</label><input id="multiple-labels"></div>
    <div class="form-group"><label>File *</label><div class="custom-file"><input id="ambiguous-file" type="file"><label>Unrelated</label></div></div>
    <div class="form-group"><label>File *</label><div class="custom-file"><input id="wrong-file-label" type="file"><label class="custom-file-label" for="different-file">Choose file</label></div></div>
    <div class="form-group"><label>Text *</label><div class="custom-file"><input id="text-file-label"><label class="custom-file-label">Choose file</label></div></div>
    <div class="form-group"><label>Hidden marker <span style="display:none">*</span></label><input id="hidden-marker"></div>
    <div class="form-group"><label>Hidden label *</label><input id="hidden-label"></div>
    <div class="form-group"><label>Outer *</label><div class="form-group"><input id="nested"></div></div>
    <div class="form-group"><label>Terms agreement *</label><input id="terms" type="checkbox" name="123"></div>
    <input id="native" required><input id="aria" aria-required="true">
  `)}; document.querySelector('#hidden-label').previousElementSibling.style.display = 'none';`);
  const form = await observeForm(runtime.tab);
  const byId = id => form.fields.find(field => field.selector === '[id="' + id + '"]');
  for (const id of ['name', 'email', 'resume', 'phone']) {
    assert.equal(byId(id).required, true, id);
    assert.equal(byId(id).required_source, 'visible_label', id);
  }
  // Keep the existing field naming contract while adding required provenance.
  assert.equal(byId('name').label, 'Full Name');
  for (const id of ['city', 'outside', 'wrong-target', 'multiple-a', 'multiple-b', 'multiple-labels',
    'ambiguous-file', 'wrong-file-label', 'text-file-label', 'hidden-marker', 'hidden-label', 'nested']) {
    assert.equal(byId(id).required, false, id);
    assert.equal(byId(id).required_source, 'not_asserted', id);
  }
  assert.equal(byId('native').required_source, 'native');
  assert.equal(byId('aria').required_source, 'aria');
  assert.equal(byId('terms'), undefined);
  assert.equal(form.protected_count, 1);
});

test('isolated application questions expose their visible title and required marker', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  await runtime.evaluate(`document.body.innerHTML = ${JSON.stringify(`
    <ul>
      <li class="application-question"><label><div class="application-label">Resume/CV <span class="required">✱</span></div><div class="application-field"><a><span>Attach</span><input id="question-file" type="file"></a><span style="display:none">Analyzing resume...</span></div></label></li>
      <li class="application-question custom-question"><div><div class="application-label"><div class="text">Commitment period<span class="required">✱</span></div></div><div class="application-field"><div><select id="question-select" name="cards[fixture][field0]" required><option>Select...</option></select></div></div></div></li>
      <li class="application-question"><div class="application-label">Unasserted field</div><div class="application-field"><input id="question-unasserted" placeholder="Type your response"></div></li>
      <li class="application-question"><div class="application-label">Hidden marker<span style="display:none">✱</span></div><input id="question-hidden"></li>
      <li class="application-question"><div class="application-label">Multiple controls ✱</div><input id="question-a"><input id="question-b"></li>
      <li class="application-question"><div class="application-label">Title ✱</div><div class="application-label">Second title</div><input id="question-ambiguous"></li>
      <li class="application-question"><div class="application-label">Terms agreement ✱</div><input id="question-protected" type="checkbox"></li>
      <li class="application-question"><div class="application-label">Outer ✱</div><div class="application-question"><input id="question-nested"></div></li>
    </ul>
  `)};`);
  const form = await observeForm(runtime.tab);
  const byId = id => form.fields.find(field => field.selector === '[id="' + id + '"]');
  assert.equal(byId('question-file').label, 'Resume/CV ✱');
  assert.equal(byId('question-file').required_source, 'visible_label');
  assert.equal(byId('question-select').label, 'Commitment period✱');
  assert.equal(byId('question-select').required_source, 'native');
  assert.equal(byId('question-unasserted').label, 'Unasserted field');
  for (const id of ['question-unasserted', 'question-hidden', 'question-a', 'question-b', 'question-ambiguous', 'question-nested']) {
    assert.equal(byId(id).required_source, 'not_asserted', id);
  }
  assert.equal(byId('question-protected'), undefined);
  assert.equal(form.protected_count, 1);
});

test('required select helpers are excluded only beside one asserted primary combobox', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  const helper = '<input required tabindex="-1" aria-hidden="true">';
  const combo = '<input class="select__input" role="combobox" aria-label="Choice" aria-required="true">';
  await runtime.evaluate(`document.body.innerHTML = ${JSON.stringify(`
    <div class="select-shell" id="valid-shell">${combo}${helper}</div>
    <div class="select-shell" id="unasserted-shell">${combo.replace('aria-required="true"', '')}${helper}</div>
    <div class="select-shell" id="multi-shell">${combo}${combo}${helper}</div>
    <div class="select-shell" id="named-shell">${combo}${helper.replace('<input ', '<input name="independent" ')}</div>
    <div class="select-shell" id="visible-shell">${combo}${helper.replace('aria-hidden="true"', '')}</div>
    <div class="select-shell" id="nested-helper-shell">${combo}<div>${helper}</div></div>
    <div id="outside-shell">${combo}${helper}</div>
  `)};`);
  const form = await observeForm(runtime.tab);
  assert.equal(form.fields.length, 14);
  // Anonymous controls retain full document selectors; inspect their role/count.
  assert.equal(form.fields.filter(field => field.control === 'combobox').length, 8);
  assert.equal(form.fields.filter(field => field.control === 'text').length, 6);
  assert.ok(form.fields.filter(field => field.control === 'text').every(field => field.required_source === 'native'));
  assert.equal(form.protected_count, 0);
});

test('single file upload groups inherit their bound visible title and aria requirement', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  const uploader = (id, title, required = '') => `<div class="file-upload" role="group" aria-labelledby="title-${id}" ${required}><div id="title-${id}" class="upload-label">${title}</div><div><button type="button">Attach</button><label for="${id}">Attach</label><input id="${id}" type="file"></div></div>`;
  await runtime.evaluate(`document.body.innerHTML = ${JSON.stringify([
    uploader('resume-fixture', 'Resume/CV*', 'aria-required="true"'),
    uploader('cover-fixture', 'Cover Letter', 'aria-required="false"'),
    uploader('transcript-fixture', 'Grade transcript*', 'aria-required="true"'),
    uploader('unasserted-fixture', 'Supporting document'),
    uploader('multi-fixture', 'Multiple *', 'aria-required="true"').replace('</div></div>', '<input id="second-file" type="file"></div></div>'),
    uploader('outside-fixture', 'Outside *', 'aria-required="true"').replace('aria-labelledby="title-outside-fixture"', 'aria-labelledby="outside-title"'),
    '<div id="outside-title">Other question *</div>',
    uploader('duplicate-fixture', 'Duplicate *', 'aria-required="true"'),
    '<div id="title-duplicate-fixture">Other title *</div>',
    uploader('hidden-fixture', 'Hidden *', 'aria-required="true"').replace('class="upload-label"', 'class="upload-label" style="display:none"'),
  ].join(''))};`);
  const form = await observeForm(runtime.tab);
  const byId = id => form.fields.find(field => field.selector === '[id="' + id + '"]');
  for (const [id, label] of [['resume-fixture', 'Resume/CV*'], ['transcript-fixture', 'Grade transcript*']]) {
    assert.equal(byId(id).label, label);
    assert.equal(byId(id).group, label);
    assert.equal(byId(id).required_source, 'group_aria');
  }
  assert.equal(byId('cover-fixture').label, 'Cover Letter');
  for (const id of ['cover-fixture', 'unasserted-fixture', 'multi-fixture', 'outside-fixture', 'duplicate-fixture', 'hidden-fixture']) {
    assert.equal(byId(id).required_source, 'not_asserted', id);
  }
  for (const id of ['multi-fixture', 'outside-fixture', 'duplicate-fixture', 'hidden-fixture']) assert.equal(byId(id).label, 'Attach', id);
});

test('clipped file inputs expose only a uniquely bound visible upload trigger', async t => {
  const runtime = await launchFixture(t, { withSnapshot: false });
  const html = '<div class="file-upload" role="group" aria-labelledby="upload-title">' +
    '<div id="upload-title">Resume/CV*</div><div><button id="attach" type="button">Attach</button>' +
    '<label for="resume" style="position:absolute;width:1px;height:1px;overflow:hidden">Attach</label>' +
    '<input id="resume" type="file" style="position:absolute;width:1px;height:1px;padding:0;border:0"></div></div>';
  const cases = [
    ['bound', html, true],
    ['ordinary sized input', html.replace('width:1px;height:1px;padding', 'width:20px;height:20px;padding'), false],
    ['submit type', html.replace('type="button"', 'type="submit"'), false],
    ['submit name', html.replaceAll('Attach', 'Submit'), false],
    ['apply name', html.replaceAll('Attach', 'Apply'), false],
    ['confirm name', html.replaceAll('Attach', 'Confirm'), false],
    ['consent name', html.replaceAll('Attach', 'Accept terms'), false],
    ['label text mismatch', html.replace('>Attach</label>', '>Other</label>'), false],
    ['wrong label target', html.replace('for="resume"', 'for="different"'), false],
    ['multiple buttons', html.replace('</button>', '</button><button type="button">Attach</button>'), false],
    ['multiple labels', html.replace('</label>', '</label><label for="resume">Attach</label>'), false],
    ['nested button', html.replace('<button', '<span><button').replace('</button>', '</button></span>'), false],
    ['hidden button', html.replace('id="attach"', 'id="attach" style="display:none"'), false],
    ['missing file group', html.replace('class="file-upload"', 'class="other-group"'), false],
    ['multiple file controls', html.replace('</div></div>', '<input type="file"></div></div>'), false],
    ['duplicate input id', html + '<div id="resume"></div>', false],
  ];
  for (const [name, markup, allowed] of cases) {
    await t.test(name, async () => {
      await runtime.evaluate(`document.body.innerHTML = ${JSON.stringify(markup)};`);
      const form = await observeForm(runtime.tab);
      const field = form.fields.find(item => item.control === 'file');
      assert.ok(field, name);
      assert.deepEqual(field.upload_trigger, allowed ? { selector: '[id="attach"]', label: 'Attach', disabled: false } : null, name);
    });
  }
  await runtime.evaluate(`document.body.innerHTML = ${JSON.stringify(html.replace('id="attach"', 'id="attach" disabled'))};`);
  assert.equal((await observeForm(runtime.tab)).fields.find(item => item.control === 'file').upload_trigger.disabled, true);
});
