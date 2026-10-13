"""Loopback-only career HTTP host, separate from the read-only dashboard."""

from __future__ import annotations

import json
import mimetypes
import os
import secrets
import sqlite3
import threading
import urllib.error
import urllib.request
import webbrowser
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

from applypilot.career import job_progress, learning, reminders, service
from applypilot.career.application_inbox import ApplicationInbox
from applypilot.career.workbench import Workbench, previous_request, public, save_request
from applypilot.career.worker import CareerWorker
from applypilot.database import close_connection, init_db
from applypilot.storage.transactions import write_transaction

PREFIX = "/api/career/v1"
MAX_BODY = 3_000_000
FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


def active_instance(workspace):
    """Read and verify a local instance; do not initialize a DB or start a process."""
    workspace = Path(workspace).resolve()
    path = workspace / "career" / "workbench" / "server.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        url = urlsplit(value["url"])
        if (value.get("workspace") != str(workspace) or url.scheme != "http"
                or url.hostname != "127.0.0.1" or not url.port or url.username or url.password
                or url.path not in {"", "/"} or url.query or url.fragment):
            return None
        # Explicit loopback: no environment proxy and no redirects to another host.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *args, **kwargs):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(value["url"].rstrip("/") + PREFIX + "/health", timeout=.5) as response:
            health = json.loads(response.read(4096))
        return value if health.get("instance_id") == value["instance_id"] else None
    except (OSError, ValueError, KeyError, TypeError, urllib.error.URLError):
        return None


class CareerServer(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False

    def __init__(self, workspace, *, port=0):
        self.workspace = Path(workspace).resolve()
        self.db_path = self.workspace / "applypilot.db"
        self.workbench = Workbench(self.workspace)
        self.application_inbox = ApplicationInbox(self.workspace)
        self.token, self.instance_id = secrets.token_urlsafe(32), secrets.token_hex(16)
        # Migration is startup-only. Request handlers never call init_db.
        init_db(self.db_path)
        close_connection(self.db_path)
        self.worker = CareerWorker(self.workspace, self.workbench.execute)
        super().__init__(("127.0.0.1", port), CareerHandler)
        self.origin = f"http://127.0.0.1:{self.server_port}"
        try:
            self.worker.start()
        except BaseException:
            super().server_close()
            raise

    @contextmanager
    def connection(self, *, writing=False):
        mode = "rw" if writing else "ro"
        conn = sqlite3.connect(f"{self.db_path.as_uri()}?mode={mode}", uri=True, timeout=10)
        try:
            yield conn
        finally:
            conn.close()

    def server_close(self):
        self.worker.close()
        super().server_close()

    def describe(self):
        return {"url": self.origin + "/", "workspace": str(self.workspace),
                "instance_id": self.instance_id, "pid": os.getpid()}


def _error(exc):
    raw = str(exc)
    reminder_errors = {
        "reminder_store_invalid": (409, "本地提醒文件无法读取，已保留原文件。请核对文件后再编辑。"),
        "reminder_busy": (409, "另一项本地提醒编辑尚未完成，请稍后刷新清单。"),
        "reminder_record_changed": (409, "这条记录或提醒已变化，请刷新清单后重新选择。"),
        "reminder_ambiguous": (409, "同一记录关联了多个本地提醒，请从清单选择具体提醒。"),
        "reminder_date_before_current": (422, "延后日期不能早于当前提醒日期。"),
        "reminder_step_invalid": (422, "请输入 1–200 字的下一步短标签，不要填写凭据或仅填写链接。"),
        "reminder_date_invalid": (422, "请选择有效的 UTC 日期。"),
        "reminder_input_invalid": (422, "请核对提醒日期、下一步和所选记录。"),
    }
    if raw in reminder_errors:
        status, message = reminder_errors[raw]
        return status, raw, message
    if raw == "progress_filter_invalid":
        return 422, "invalid_progress_filter", "请核对关键词、UTC 日期范围、日期字段和状态选项。"
    if raw == "progress_report_unavailable":
        return 422, "progress_unavailable", "当前工作区尚无可读取的求职进度记录。"
    if isinstance(exc, KeyError) or raw in {"target_not_found", "diagnosis_not_found", "route_not_found"}:
        return 404, "not_found", "这条记录已不存在，请刷新后重新选择。"
    if any(s in raw for s in ("changed", "stale", "revision", "conflict", "reused")):
        return 409, "input_changed", "资料或进度版本已变化，请刷新后核对再操作。"
    if "queue is full" in raw:
        return 429, "queue_full", "已有多项任务等待处理，请稍后重试。"
    if "quote" in raw:
        return 422, "quote_not_found", "引用原文未在选中的材料中找到，请核对材料与原文。"
    if "not_ready" in raw or "prerequisites" in raw:
        return 422, "task_not_ready", "请先完成并评审通过前置任务。"
    if "no_complete_evidence" in raw:
        return 422, "evidence_incomplete", "所选材料缺少完整证据引用，请一起选中关联材料或补充引用。"
    if "too_large" in raw:
        return 413, "material_too_large", "材料上限为 2 MB，请选取相关内容。"
    if "pdf" in raw or isinstance(exc, UnicodeError):
        return 422, "text_export_required", "无法读取可用文本，请粘贴原文或上传文本版。"
    if isinstance(exc, (ValueError, TypeError)):
        return 422, "invalid_input", "请检查必填项、材料格式和逐项验收记录。"
    return 500, "local_operation_failed", "本地操作未完成，请刷新记录后重试。"


class CareerHandler(BaseHTTPRequestHandler):
    server: CareerServer
    protocol_version = "HTTP/1.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        # Avoid writing private target/material contents or session tokens to logs.
        pass

    def send(self, status, value, *, content_type="application/json; charset=utf-8", download_name=None):
        body = value if isinstance(value, bytes) else json.dumps(public(value), ensure_ascii=False, allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if download_name is not None:
            self.send_header("Content-Disposition", f'attachment; filename="{download_name}"')
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; "
                         "connect-src 'self'; img-src 'self' data:; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)
        self.close_connection = True

    def fail(self, status, code, message):
        self.send(status, {"error": {"code": code, "message": message}})

    def permitted(self, *, writing=False):
        if self.headers.get("Host") != urlsplit(self.server.origin).netloc:
            self.fail(403, "invalid_host", "仅支持此工作台的本地地址。")
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin != self.server.origin:
            self.fail(403, "invalid_origin", "请从本地工作台页面操作。")
            return False
        document_navigation = (not writing and self.path in {"/", "/career.html"}
                               and self.headers.get("Sec-Fetch-Mode") == "navigate"
                               and self.headers.get("Sec-Fetch-Dest") == "document")
        # A user may open the entry from the existing file:// dashboard. Only
        # the public HTML entry accepts that navigation, never an API request.
        if self.headers.get("Sec-Fetch-Site") == "cross-site" and not document_navigation:
            self.fail(403, "cross_site_request", "请从本地工作台页面操作。")
            return False
        if writing and (origin != self.server.origin or not secrets.compare_digest(
                self.headers.get("X-Career-Token", ""), self.server.token)):
            self.fail(403, "session_required", "页面会话已变化，请刷新后再操作。")
            return False
        return True

    def parts(self):
        path = urlsplit(self.path)
        filtered_query = self.command == "GET" and path.path in {
            PREFIX + "/application-inbox", PREFIX + "/job-progress", PREFIX + "/job-progress/download",
            PREFIX + "/job-progress/reminders"}
        if (path.query and not filtered_query) or path.fragment or not path.path.startswith(PREFIX + "/"):
            raise KeyError("endpoint_not_found")
        return [unquote(part) for part in path.path[len(PREFIX) + 1:].split("/")]

    def inbox_query(self):
        values = parse_qs(urlsplit(self.path).query, keep_blank_values=True, strict_parsing=True, max_num_fields=4)
        if set(values) - {"q", "scope", "sort", "offset"} or any(len(value) != 1 for value in values.values()):
            raise ValueError("invalid_inbox_query")
        result = {name: value[0] for name, value in values.items()}
        if "offset" in result:
            if not result["offset"].isascii() or not result["offset"].isdigit() or len(result["offset"]) > 7:
                raise ValueError("invalid_inbox_offset")
            result["offset"] = int(result["offset"])
        return result

    def progress_query(self, *, downloading=False):
        try:
            values = parse_qs(urlsplit(self.path).query, keep_blank_values=True, strict_parsing=True, max_num_fields=40)
            allowed = {"q", "since", "until", "date_field", "status", "format" if downloading else "offset"}
            if set(values) - allowed or any(len(value) != 1 for name, value in values.items() if name != "status"):
                raise ValueError("invalid_query")
            result = {name: value[0] for name, value in values.items() if name != "status"}
            result["statuses"] = [value for value in values.get("status", []) if value]
            for name in ("since", "until"):
                if result.get(name) == "":
                    result.pop(name)
            if "offset" in result:
                if not result["offset"].isascii() or not result["offset"].isdigit() or len(result["offset"]) > 7:
                    raise ValueError("invalid_offset")
                result["offset"] = int(result["offset"])
            if downloading and "format" not in result:
                raise ValueError("format_required")
            return result
        except ValueError as exc:
            raise ValueError("progress_filter_invalid") from exc

    def do_GET(self):
        if not self.permitted():
            return
        try:
            assets = {"/": "career.html", "/career.html": "career.html", "/career.css": "career.css", "/career.js": "career.js"}
            if self.path in assets:
                path = FRONTEND / assets[self.path]
                self.send(200, path.read_bytes(), content_type=(mimetypes.guess_type(path.name)[0] or "text/plain") + "; charset=utf-8")
                return
            p = self.parts()
            if p == ["health"]:
                self.send(200, {"instance_id": self.server.instance_id})
                return
            if p == ["job-progress", "reminders"]:
                try:
                    query = parse_qs(urlsplit(self.path).query, keep_blank_values=True, strict_parsing=True, max_num_fields=1)
                    value = query.get("limit", ["100"])[0]
                    if set(query) - {"limit"} or not value.isascii() or not value.isdigit() or len(value) > 3:
                        raise ValueError
                    limit = int(value)
                except ValueError as exc:
                    raise ValueError("reminder_input_invalid") from exc
                self.send(200, reminders.list_items(self.server.workspace, limit=limit))
                return
            wb = self.server.workbench
            with self.server.connection() as conn:
                if p == ["bootstrap"]:
                    result = {**wb.bootstrap(conn), "token": self.server.token, "runs": self.server.worker.list_jobs()}
                elif p == ["application-inbox"]:
                    result = self.server.application_inbox.list_items(conn, **self.inbox_query())
                elif len(p) == 2 and p[0] == "application-inbox":
                    result = self.server.application_inbox.detail(conn, p[1])
                elif p == ["job-progress"]:
                    result = job_progress.preview(conn, **self.progress_query())
                elif p == ["job-progress", "download"]:
                    body, content_type, filename = job_progress.download(conn, **self.progress_query(downloading=True))
                    self.send(200, body, content_type=content_type, download_name=filename)
                    return
                elif p == ["materials"]:
                    result = {"materials": wb.materials(conn)}
                elif len(p) == 2 and p[0] == "materials":
                    result = wb.material(conn, p[1])
                elif p == ["targets"]:
                    result = {"targets": wb.targets(conn)}
                elif len(p) == 2 and p[0] == "targets":
                    result = {"target": wb.target(conn, p[1])}
                elif len(p) == 2 and p[0] == "diagnoses":
                    result = service.read_diagnosis(conn, p[1])
                elif len(p) == 2 and p[0] == "routes":
                    result = learning.read_progress(conn, p[1])
                elif len(p) == 5 and p[0] == "routes" and p[2] == "tasks" and p[4] == "submission":
                    result = wb.submission(conn, p[1], p[3])
                elif p == ["runs"]:
                    result = {"runs": self.server.worker.list_jobs()}
                elif len(p) == 2 and p[0] == "runs":
                    result = self.server.worker.get(p[1])
                    if result is None:
                        raise KeyError("run_not_found")
                else:
                    raise KeyError("endpoint_not_found")
            self.send(200, result)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
            self.fail(*_error(exc))

    def body(self):
        if self.headers.get("Transfer-Encoding") or self.headers.get_content_type() != "application/json":
            raise ValueError("json_body_required")
        length = int(self.headers.get("Content-Length", "0"))
        if not 0 < length <= MAX_BODY:
            raise ValueError("body_too_large")
        data = json.loads(self.rfile.read(length).decode("utf-8"),
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError("invalid_json")))
        if not isinstance(data, dict):
            raise TypeError("object_required")
        # No endpoint accepts authority, workspace, arbitrary source paths, or model switches.
        if any(k in data for k in ("path", "workspace", "allowed_roots", "registry", "use_model", "retry_unknown", "_input_lease")):
            raise ValueError("unsupported_input_field")
        return data

    def do_POST(self):
        if not self.permitted(writing=True):
            return
        try:
            p, data = self.parts(), self.body()
            wb, worker = self.server.workbench, self.server.worker
            if len(p) == 3 and p[0] == "runs" and p[2] in {"cancel", "resume"}:
                result = worker.cancel(p[1]) if p[2] == "cancel" else worker.resume(p[1], data.get("request_key"))
                self.send(200 if p[2] == "cancel" else 202, result)
                return
            if p == ["job-progress", "reminders"]:
                self.send(200, reminders.mutate(self.server.workspace, "set", data))
                return
            if len(p) == 4 and p[:2] == ["job-progress", "reminders"] and p[3] in {"snooze", "dismiss"}:
                self.send(200, reminders.mutate(self.server.workspace, p[3], data, reminder_id=p[2]))
                return
            with worker.mutation(), self.server.connection(writing=True) as conn:
                if len(p) == 3 and p[0] == "application-inbox" and p[2] in {"respond", "resume"}:
                    result = self.server.application_inbox.mutate(conn, p[1], p[2], data)
                    self.send(200, result)
                    return
                operation = "/".join(p)
                key = data.get("request_key")
                # Uploads are content-addressed and do not need a client request key.
                if p == ["materials"]:
                    self.send(201, wb.upload(conn, data))
                    return
                old = previous_request(conn, key, operation, data)
                if old is not None:
                    self.send(old["status"], old["value"])
                    return
                queue_args, queue_operation = {}, None
                if len(p) == 3 and p[0] == "targets" and p[2] in {"diagnoses", "supplement"}:
                    queue_operation = "diagnose" if p[2] == "diagnoses" else "supplement"
                    queue_args = {"target_id": p[1]}
                elif len(p) == 3 and p[0] == "diagnoses" and p[2] == "routes":
                    queue_operation, queue_args = "route", {"diagnosis_id": p[1]}
                elif len(p) == 3 and p[0] == "routes" and p[2] == "rediagnose":
                    queue_operation, queue_args = "rediagnose", {"route_id": p[1]}
                if queue_operation:
                    payload = wb.queue_payload(conn, queue_operation, data, **queue_args)
                    result = worker.submit(queue_operation, payload, key)
                    status = 202
                    with write_transaction(conn):
                        save_request(conn, key, operation, data, {"status": status, "value": result})
                else:
                    with write_transaction(conn):
                        if p == ["targets"]:
                            result, status = wb.mutate(conn, "target", data), 201
                        elif len(p) == 3 and p[0] == "routes" and p[2] in {"events", "submissions", "reviews"}:
                            name = {"events": "event", "submissions": "submit", "reviews": "review"}[p[2]]
                            result, status = wb.mutate(conn, name, data, route_id=p[1]), 200
                        else:
                            raise KeyError("endpoint_not_found")
                        save_request(conn, key, operation, data, {"status": status, "value": result})
            self.send(status, result)
        except (OSError, ValueError, TypeError, KeyError, RuntimeError, sqlite3.Error) as exc:
            self.fail(*_error(exc))

    def do_OPTIONS(self):
        self.fail(405, "method_not_allowed", "仅支持本地页面操作。")


def serve(workspace: Path, port: int = 0, open_browser: bool = False) -> None:
    existing = active_instance(workspace)
    if existing:
        if open_browser:
            webbrowser.open(existing["url"])
        print(existing["url"], flush=True)
        return
    preferred = port
    if port == 0:
        # Keep the origin stable across restarts so browser selections survive.
        try:
            record = json.loads((Path(workspace) / "career" / "workbench" / "server.json").read_text(encoding="utf-8"))
            url = urlsplit(record["url"])
            if record.get("workspace") == str(Path(workspace).resolve()) and url.hostname == "127.0.0.1" and url.scheme == "http":
                preferred = url.port or 0
        except (OSError, ValueError, TypeError, KeyError):
            pass
    try:
        server = CareerServer(workspace, port=preferred)
    except OSError as exc:
        if port != 0 or preferred == 0 or exc.errno not in {48, 98, 10048}:
            raise
        server = CareerServer(workspace, port=0)
    metadata = server.workbench.root / "server.json"
    metadata.parent.mkdir(parents=True, exist_ok=True)
    metadata.write_text(json.dumps(server.describe()), encoding="utf-8")
    print(server.origin + "/", flush=True)
    if open_browser:
        webbrowser.open(server.origin + "/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        # Keep only non-secret instance metadata for the next port preference.
        # active_instance verifies health and identity before returning a link.


def start_background(workspace: Path, port: int = 0):
    """Embedded host for integration tests; production CLI owns serve_forever."""
    server = CareerServer(workspace, port=port)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
