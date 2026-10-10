import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import test from 'node:test';
import { observeForm } from './browser-form-state.mjs';

// Use the project's existing Playwright runtime to execute the observer callback
// against a local synthetic DOM. No live profile, portal or write operation.
const python = process.env.APPLYPILOT_TEST_PYTHON || 'python';
const runner = `
import json, sys
from playwright.sync_api import sync_playwright
data = json.loads(sys.stdin.buffer.read().decode('utf-8'))
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    try:
        page = browser.new_page()
        page.set_content(data['html'])
        print(json.dumps(page.evaluate(data['callback']), ensure_ascii=True))
    finally:
        browser.close()
`;

test('IAB question metadata keeps full compound text, scoped instructions and UTF-16 limits', async () => {
  const raw = '请说明经验😀\nAND outcomes. '.repeat(40);
  const html = `<html lang="zh"><body><fieldset><legend>Motivation</legend>
    <div class="application-question"><label for="answer">${raw}</label>
    <p id="hint">At most 150 words.</p><p class="application-instructions">Maximum 300 characters.</p>
    <textarea id="answer" aria-describedby="hint" maxlength="500" minlength="2" required></textarea></div>
    <div><span id="part1">Describe your role.</span><span id="part2">And its outcome?</span>
    <textarea id="compound" aria-labelledby="part1 part2" aria-describedby="missing"></textarea></div>
    <div class="form-group"><label for="wrapped">Explain the result.<textarea id="wrapped">PRIVATE DEFAULT ANSWER</textarea></label></div>
    <select id="choice" aria-label="Choose"><option value="a">${'Full option '.repeat(40)}</option><option value="b" label="Software engineering">internal-track-a</option></select>
    </fieldset><iframe></iframe></body></html>`;
  const tab = { playwright: { evaluate: async callback => {
    const result = spawnSync(python, ['-c', runner], {
      input: JSON.stringify({ html, callback: `(${callback.toString()})()` }),
      encoding: 'utf8', windowsHide: true, cwd: path.dirname(fileURLToPath(import.meta.url)),
    });
    assert.equal(result.status, 0, result.stderr || result.error?.message);
    return JSON.parse(result.stdout);
  } } };
  const observation = await observeForm(tab);
  const answer = observation.fields.find(field => field.field_key === '[id="answer"]');
  assert.equal(answer.application_question.text, raw);
  assert.equal(answer.application_question.help_text, 'At most 150 words.\nMaximum 300 characters.');
  assert.deepEqual(answer.application_question.constraints, [
    { kind: 'max', unit: 'utf16', value: 500, source: 'native:maxlength' },
    { kind: 'min', unit: 'utf16', value: 2, source: 'native:minlength' },
  ]);
  assert.deepEqual(answer.application_question.section_path, ['Motivation']);
  assert.equal(answer.application_question.language, 'zh');
  assert.ok(answer.application_question.text_sources.some(source => source.source === 'aria-describedby:hint'));
  const compound = observation.fields.find(field => field.field_key === '[id="compound"]');
  assert.equal(compound.application_question.text, 'Describe your role. And its outcome?');
  assert.equal(compound.application_question.completeness, 'partial');
  assert.equal(observation.question_coverage.page_only, true);
  assert.equal(observation.question_coverage.whole_form, 'unknown');
  assert.equal(observation.question_coverage.iframe_count, 1);
  const choice = observation.fields.find(field => field.field_key === '[id="choice"]');
  assert.equal(choice.application_question.options[0].label, 'Full option '.repeat(40).trim());
  assert.equal(choice.application_question.options[1].label, 'Software engineering');
  assert.equal(answer.selector, '[id="answer"]');
  assert.equal(answer.required, true);
  const wrapped = observation.fields.find(field => field.field_key === '[id="wrapped"]');
  assert.equal(wrapped.application_question.text, 'Explain the result.');
  assert.ok(!JSON.stringify(wrapped.application_question).includes('PRIVATE DEFAULT ANSWER'));
});
