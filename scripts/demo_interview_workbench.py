"""Observe the interview workflow on fictional data, or serve that local demo."""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sqlite3
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='New evidence directory; never overwritten.')
    parser.add_argument('--serve', action='store_true', help='Keep the fictional workbench open until Ctrl+C.')
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    workspace = Path(tempfile.mkdtemp(prefix='interview-demo-', dir=output))
    os.environ['APPLYPILOT_DIR'] = str(workspace)
    root = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(root / 'src'), str(root)]
    import applypilot
    assert Path(applypilot.__file__).resolve().is_relative_to(root / 'src')
    from applypilot import followup
    from applypilot.career import interviews, server
    from applypilot.database import close_connection, init_db

    db = workspace / 'applypilot.db'
    conn = init_db(db)
    resume = workspace / 'fictional-resume.txt'
    resume.write_text('Projects\nBuilt Python tooling to validate small datasets.\n'
                      'Implemented data checks and explained results to a fictional team.\n', encoding='utf-8')
    urls = ['https://fictional.example/jobs/analyst', 'https://fictional.example/jobs/engineer']
    for index, url in enumerate(urls):
        conn.execute('INSERT INTO jobs(url,title,company_name,full_description,tailored_resume_path,apply_status) VALUES(?,?,?,?,?,?)',
                     (url, ['Fictional Analyst', 'Fictional Engineer'][index], 'Fictional Co',
                      'Responsibilities\nBuild Python tooling and explain data checks.\nRequirements\nPython and testing.',
                      str(resume), 'applied'))
    followup.import_events(conn, [{'provider': 'fictional', 'message_id': 'invitation-one',
        'event_type': 'interview_invited', 'company': 'Fictional Co', 'title': 'Fictional Analyst',
        'round': 1, 'scheduled_at': '2026-10-20T10:00:00+08:00',
        'occurred_at': '2026-10-13T00:00:00Z', 'evidence_ref': 'fictional-invitation',
        'summary': 'Fictional invitation: discuss a Python project.'}])
    conn.commit()
    close_connection(db)
    instance = server.start_background(workspace)
    result = {'workspace': str(workspace), 'url': instance.origin, 'status': 'running',
              'model_calls': 0, 'real_applications': 0, 'external_messages': 0, 'checks': []}
    (output / 'entry.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    if args.serve:
        print(json.dumps({'url': instance.origin, 'workspace': str(workspace), 'fictional': True}), flush=True)
        try:
            import threading
            threading.Event().wait()
        except KeyboardInterrupt:
            return
        finally:
            instance.shutdown()
            instance.server_close()
    requests, errors = [], []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True, args=['--disable-background-networking'])
            try:
                context = browser.new_context(viewport={'width': 1400, 'height': 1050}, timezone_id='Asia/Singapore')
                context.route('**/*', lambda route: route.continue_() if route.request.url.startswith(instance.origin + '/') else route.abort())
                page = context.new_page()
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('response', lambda response: requests.append({'path': response.url.removeprefix(instance.origin),
                                                                     'status': response.status, 'method': response.request.method}))
                page.goto(instance.origin)
                page.wait_for_function("() => document.querySelector('#workspace-label').textContent !== '正在连接…'")
                page.locator('[data-stage="interviews"]').click()
                page.locator('[data-interview-resolve]').wait_for()
                page.locator('[data-interview-resolve]').click()
                page.locator('#interview-job').select_option(urls[0])

                def submit(suffix):
                    with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith(suffix)) as observed:
                        page.locator('#interview-form button[type="submit"]').click()
                    assert observed.value.status == 200, observed.value.text()
                    page.locator('[data-interview-edit="reschedule"]').wait_for()

                submit('/resolve')
                with sqlite3.connect(db) as conn:
                    first = interviews.list_items(conn, workspace)['items'][0]['id']
                result['checks'].append('existing invitation resolves to exact job and round')
                with page.expect_response(lambda response: response.url.endswith('/prepare')) as observed:
                    page.locator('[data-interview-prepare]').click()
                assert observed.value.status == 200
                page.get_by_role('heading', name='岗位重点').wait_for()
                page.screenshot(path=str(output / 'preparation.png'))
                result['checks'].append('local source-bound preparation is visible')
                page.locator('[data-interview-edit="actions"]').click()
                page.locator('#interview-action-summary').fill('练习项目讲解')
                page.locator('#interview-action-due').fill('2026-10-19T12:00')
                submit('/actions')
                page.locator('[data-interview-reminders]').click()
                page.locator('[data-reminder-interview]').wait_for()
                page.locator('[data-reminder-interview]').click()
                page.locator('.interview-todo').get_by_text('练习项目讲解', exact=True).wait_for()
                result['checks'].append('existing reminder links to the exact interview todo without exposing its body')
                page.locator('[data-interview-edit="reschedule"]').click()
                page.locator('#interview-time').fill('2026-10-21T15:30')
                page.locator('#interview-source').fill('Fictional source confirms a new time.')
                submit('/reschedule')
                with sqlite3.connect(db) as conn:
                    item = interviews.detail(conn, workspace, first)
                    assert item['scheduled_at'].startswith('2026-10-21T07:30')
                    assert len(followup.scheduled_interviews(followup._events(conn, job_url=urls[0]))) == 1
                result['checks'].append('reschedule preserves one round and historical invitation')
                page.locator('[data-interview-edit="result"]').click()
                page.locator('#interview-result').select_option('next_round')
                page.locator('#interview-source').fill('Fictional result: proceed to the next round.')
                submit('/result')
                page.locator('[data-interview-edit="actions"]').click()
                page.locator('#interview-action-summary').fill('Prepare for the fictional next round')
                page.locator('#interview-action-due').fill('2026-10-22T12:00')
                submit('/actions')
                page.locator('.interview-todo').get_by_text('Prepare for the fictional next round', exact=True).wait_for()
                with sqlite3.connect(db) as conn:
                    completed = interviews.detail(conn, workspace, first)
                    assert completed['status'] == 'completed' and completed['result'] == 'next_round'
                    assert len(completed['actions']) == 2
                result['checks'].append('completed interview retains its result and accepts a follow-up todo')
                page.locator('[data-close="interview-dialog"]').click()
                page.locator('[data-interview-new]').click()
                page.locator('#interview-job').select_option(urls[1])
                page.locator('#interview-round').fill('2')
                page.locator('#interview-round-name').fill('技术面')
                page.locator('#interview-source').fill('Fictional second invitation; no time agreed yet.')
                # Exercise an actual duplicate submit event while the first intent is busy.
                with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/interviews')) as observed:
                    page.locator('#interview-form').evaluate("form => {form.requestSubmit(); form.requestSubmit();}")
                assert observed.value.status == 200
                page.locator('[data-interview-edit="cancel"]').wait_for()
                with sqlite3.connect(db) as conn:
                    assert len(interviews.list_items(conn, workspace)['items']) == 2
                page.locator('[data-interview-edit="cancel"]').click()
                page.locator('#interview-source').fill('Fictional invitation was withdrawn.')
                submit('/cancel')
                result['checks'].append('duplicate click creates one round; cancellation persists')
                page.locator('[data-close="interview-dialog"]').click()
                page.reload()
                page.locator('[data-stage="interviews"]').click()
                page.locator('#interview-filter').select_option('all')
                page.wait_for_function("() => document.querySelectorAll('.interview-card').length === 2")
                page.screenshot(path=str(output / 'interviews.png'), full_page=True)
                result['checks'].append('reload restores completed and cancelled rounds')
                page.locator('[data-interview-new]').click()
                page.locator('#interview-job').select_option(urls[0])
                page.locator('#interview-round').fill('3')
                page.locator('#interview-source').fill('Fictional draft survives session expiry.')
                instance.token = secrets.token_urlsafe(32)
                with page.expect_response(lambda response: response.request.method == 'POST' and response.url.endswith('/interviews')) as rejected:
                    page.locator('#interview-form button[type="submit"]').click()
                assert rejected.value.status == 403
                page.locator('#interview-dialog [data-dialog-feedback] button').click()
                page.wait_for_function("() => !document.querySelector('#interview-form fieldset').disabled")
                assert page.locator('#interview-source').input_value() == 'Fictional draft survives session expiry.'
                submit('/interviews')
                result['checks'].append('expired session refresh keeps draft and requires explicit save')
                assert not errors, errors
                assert sum(request['method'] == 'POST' and request['path'].endswith('/interviews') and request['status'] == 200 for request in requests) == 2
                with sqlite3.connect(db) as conn:
                    assert {row[0] for row in conn.execute('SELECT apply_status FROM jobs')} == {'applied'}
                result.update(status='passed', page_errors=errors, requests=requests)
            finally:
                browser.close()
    except BaseException as error:
        result.update(status='failed', error=str(error), page_errors=errors, requests=requests)
        raise
    finally:
        instance.shutdown()
        instance.server_close()
        (output / 'outcome.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': result['status'], 'checks': len(result['checks']), 'output': str(output)}))


if __name__ == '__main__':
    main()
