"""Local workbench application boundary; no caller-selected server paths."""

from __future__ import annotations

import base64
import io
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

from applypilot.career import learning, service
from applypilot.career.contracts import InputChangedError, json_text, required_text
from applypilot.career.evidence import digest
from applypilot.storage import career as store
from applypilot.storage import career_learning as learning_store
from applypilot.storage.transactions import write_transaction

MAX_MATERIAL_BYTES = 2_000_000


def public(value):
    """Keep private filesystem selections out of the HTTP contract."""
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k not in {"selection", "path", "raw_bytes"}}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def _text(value, name, maximum=1000):
    value = required_text(value, name).strip()
    if len(value) > maximum:
        raise ValueError(f"{name}_too_long")
    return value


def _material_id(material):
    return "mat-" + digest({k: material.get(k) for k in ("id", "kind", "path", "snapshot_digest")})[:32]


class Workbench:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.root = self.workspace / "career" / "workbench"

    def material_index(self, conn):
        """Only previously selected evidence and explicit browser uploads enter here."""
        index = {}
        for (encoded,) in conn.execute("SELECT payload_json FROM career_source_snapshots "
                                       "WHERE kind='candidate_evidence' ORDER BY sequence DESC"):
            payload = json.loads(encoded)
            for material in payload.get("materials", []):
                mid = _material_id(material)
                entry = index.setdefault(mid, {"material": material, "records": {}})
                for record in payload.get("evidence", []):
                    if any(r["material_id"] == material["id"] for r in record["source_refs"]):
                        entry["records"][digest(record)] = record
        for mid, encoded in conn.execute("SELECT material_id,payload_json FROM career_workbench_materials"):
            payload = json.loads(encoded)
            index[mid] = {"material": payload["material"],
                          "records": {digest(r): r for r in payload["evidence"]}}
        return index

    @staticmethod
    def material_view(mid, entry, *, full=False):
        material = entry["material"]
        title = material["title"]
        if title == material["id"]:
            title = {"candidate_fact_master": "求职事实主档", "project_fact_context": "项目事实与贡献范围"}.get(
                material["kind"], Path(material.get("path") or title).name)
        result = {"id": mid, "title": title, "kind": material["kind"],
                  "preview": material["text"][:200], "claim_count": len(entry["records"])}
        if full:
            result["text"] = material["text"]
        return result

    def materials(self, conn):
        return [self.material_view(mid, row) for mid, row in self.material_index(conn).items()]

    def material(self, conn, mid):
        row = self.material_index(conn).get(mid)
        if row is None:
            raise KeyError("material_not_found")
        return self.material_view(mid, row, full=True)

    def upload(self, conn, data):
        name = _text(data.get("name"), "name", 180)
        if Path(name).name != name or any(c in name for c in ("/", "\\", ":", "\x00")):
            raise ValueError("material_name_invalid")
        kind = data.get("kind")
        if kind not in {"generated", "project_evidence", "resume"}:
            raise ValueError("material_kind_invalid")
        if "data_base64" in data:
            if "text" in data:
                raise ValueError("material_content_ambiguous")
            encoded = data["data_base64"]
            if not isinstance(encoded, str) or len(encoded) > 2_700_000:
                raise ValueError("material_too_large")
            raw = base64.b64decode(encoded, validate=True)
            if len(raw) > MAX_MATERIAL_BYTES:
                raise ValueError("material_too_large")
            if name.lower().endswith(".pdf"):
                from pypdf import PdfReader
                from pypdf.errors import PdfReadError
                try:
                    document = PdfReader(io.BytesIO(raw))
                    if document.is_encrypted or len(document.pages) > 100:
                        raise ValueError("pdf_requires_text_export")
                    text = "\n".join(page.extract_text() or "" for page in document.pages)
                    if not text.strip():
                        raise ValueError("pdf_requires_text_export")
                except (PdfReadError, KeyError, ValueError) as exc:
                    raise ValueError("pdf_requires_text_export") from exc
            else:
                text = raw.decode("utf-8-sig")
        else:
            text = data.get("text")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("material_text_required")
        text = text.replace("\r\n", "\n").removeprefix("\ufeff")
        if len(text.encode("utf-8")) > MAX_MATERIAL_BYTES or "\x00" in text:
            raise ValueError("material_too_large_or_binary")
        claim = _text(data.get("claim"), "claim", 4000)
        ownership = _text(data.get("ownership"), "ownership", 2000)
        quote = _text(data.get("quote"), "quote", 20000).replace("\r\n", "\n")
        if quote not in text:
            raise ValueError("evidence_quote_not_found")
        identity = digest({"name": name, "text": text, "kind": kind, "claim": claim,
                           "ownership": ownership, "quote": quote})
        path = self.root / "uploads" / (identity + ".txt")
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text(text, encoding="utf-8")
        elif path.read_text(encoding="utf-8") != text:
            raise InputChangedError("upload_file_changed")
        material = {"id": "upload-" + identity[:24], "title": name, "kind": kind,
                    "path": str(path), "text": text, "snapshot_digest": digest(text)}
        mid = _material_id(material)
        payload = {"material": material, "evidence": [{"id": "record-" + identity[:24],
                   "claim": claim, "ownership": ownership, "support_type": "user_selected_material",
                   "does_not_prove": ["上传内容及贡献描述尚未经过独立事实核验；不是技能测评。"],
                   "source_refs": [{"material_id": material["id"], "quote": quote}]}]}
        with write_transaction(conn):
            conn.execute("INSERT OR IGNORE INTO career_workbench_materials VALUES(?,?,?)",
                         (mid, json_text(payload), datetime.now(UTC).isoformat()))
        return {"material": self.material_view(mid, {"material": material,
                                                    "records": payload["evidence"]}, full=True)}

    def registry(self, conn, selected):
        if (not isinstance(selected, list) or not selected or len(selected) > 50
                or any(not isinstance(mid, str) for mid in selected) or len(set(selected)) != len(selected)):
            raise ValueError("material_selection_required")
        index = self.material_index(conn)
        if any(mid not in index for mid in selected):
            raise KeyError("material_not_found")
        materials, records = {}, {}
        for mid in sorted(selected):
            material = index[mid]["material"]
            old = materials.get(material["id"])
            if old is not None and old != material:
                raise ValueError("material_versions_conflict")
            materials[material["id"]] = material
        for mid in sorted(selected):
            for record in index[mid]["records"].values():
                if all(ref["material_id"] in materials and
                       (not ref.get("snapshot_digest") or ref["snapshot_digest"] == materials[ref["material_id"]]["snapshot_digest"])
                       for ref in record["source_refs"]):
                    previous = records.get(record["id"])
                    if previous is not None and previous != record:
                        raise ValueError("evidence_versions_conflict")
                    records[record["id"]] = record
        if not records:
            raise ValueError("selected_materials_have_no_complete_evidence")
        payload = {"candidate_label": "Local selected candidate", "materials": list(materials.values()),
                   "evidence": list(records.values()),
                   "boundaries": ["材料记录不等于技能测试；事实来源及贡献范围继续保留。"]}
        path = self.root / "registries" / (digest(payload) + ".json")
        path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json_text(payload)
        if not path.exists():
            path.write_text(encoded, encoding="utf-8")
        elif path.read_text(encoding="utf-8") != encoded:
            raise InputChangedError("selected_registry_changed")
        # Roots come only from already registered selections, never HTTP input.
        roots = sorted({str(Path(m["path"]).resolve().parent) for m in materials.values() if m.get("path")})
        return path, roots

    def targets(self, conn):
        return [self.target(conn, t["target_id"]) for t in store.list_targets(conn)]

    def target(self, conn, target_id):
        target = store.get_target(conn, target_id)
        if target is None:
            raise KeyError("target_not_found")
        diagnoses = store.list_diagnoses(conn, target_id)
        routes = learning_store.list_routes(conn, target_id)
        source = store.get_snapshot(conn, target["bindings"]["evidence_snapshot_id"])
        return {**target, "material_ids": [_material_id(m) for m in source["payload"]["materials"]],
                "latest_diagnosis_id": diagnoses[0]["diagnosis_id"] if diagnoses else None,
                "latest_route_id": routes[-1]["route_id"] if routes else None}

    def bootstrap(self, conn):
        companies = store.list_companies(conn)
        return {"workspace_label": self.workspace.name, "catalog": {
            "companies": [{**c, "name": c.get("display_name", c.get("name", c["company_id"]))} for c in companies],
            "jobs": [{k: v for k, v in job.items() if k != "body_text"} for job in store.list_jobs(conn)]},
            "materials": self.materials(conn), "targets": self.targets(conn)}

    def submission(self, conn, route_id, task_id):
        task = next((t for t in learning.read_progress(conn, route_id)["tasks"] if t["task_id"] == task_id), None)
        if task is None or not task["submission"]:
            raise KeyError("submission_not_found")
        event = task["submission"]
        artifact = store.get_snapshot(conn, event["payload"]["artifact_evidence_snapshot_id"])["payload"]
        return {"submission_id": event["event_id"], "materials": public(artifact["materials"])}

    def mutate(self, conn, operation, data, *, target_id=None, route_id=None):
        if operation == "target":
            registry, roots = self.registry(conn, data.get("material_ids"))
            source = service.import_evidence(conn, registry, allowed_roots=roots)
            target = service.create_target(conn, evidence_snapshot_id=source["evidence_snapshot_id"],
                                           job_ids=[_text(data.get("job_id"), "job_id")],
                                           label=_text(data.get("label"), "label", 200))
            return {"target": self.target(conn, target["target_id"])}
        if operation == "upload":
            return self.upload(conn, data)
        revision = data.get("expected_revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("expected_revision_required")
        task_id = _text(data.get("task_id"), "task_id")
        key = _text(data.get("request_key"), "request_key", 200)
        if operation == "event":
            learning.record_event(conn, route_id, task_id, event_type=data.get("event"),
                                  reason=_text(data.get("reason"), "reason", 2000),
                                  event_key=key, expected_revision=revision)
        elif operation == "submit":
            registry, roots = self.registry(conn, data.get("material_ids"))
            learning.submit_task(conn, route_id, task_id, registry, allowed_roots=roots,
                                 event_key=key, expected_revision=revision)
        elif operation == "review":
            learning.review_task(conn, route_id, task_id, data.get("review"),
                                 review_key=key, expected_revision=revision)
        else:
            raise ValueError("operation_not_supported")
        return {"progress": learning.read_progress(conn, route_id)}

    def queue_payload(self, conn, operation, data, *, target_id=None, route_id=None, diagnosis_id=None):
        if route_id:
            route = learning_store.get_route(conn, route_id)
            if route is None:
                raise KeyError("route_not_found")
            target_id = route["target_id"]
        if diagnosis_id:
            diagnosis = service.read_diagnosis(conn, diagnosis_id)
            target_id = diagnosis["target_id"]
        target = self.target(conn, target_id)
        revision = data.get("expected_revision")
        expected = learning_store.route_revision(conn, route_id) if operation == "rediagnose" else target["revision"]
        if type(revision) is not int or revision != expected:
            raise InputChangedError("input_changed: revision")
        lease = {"target_id": target_id, "target_revision": target["revision"],
                 "input_fingerprint": target["input_fingerprint"]}
        if route_id:
            lease.update(route_id=route_id, route_revision=learning_store.route_revision(conn, route_id))
        result = {"target_id": target_id, "_input_lease": lease}
        if diagnosis_id:
            result["diagnosis_id"] = diagnosis_id
        if route_id:
            result["route_id"] = route_id
        if operation == "supplement":
            # Resolve only opaque IDs in the worker. Never put caller paths in a queue.
            self.registry(conn, data.get("material_ids"))
            result["material_ids"] = data["material_ids"]
            parent_id = data.get("parent_route_id")
            if parent_id:
                parent = learning_store.get_route(conn, parent_id)
                if parent is None or parent["target_id"] != target_id:
                    raise ValueError("supplement_parent_target_mismatch")
                result["parent_route_id"] = parent_id
                lease.update(route_id=parent_id, route_revision=learning_store.route_revision(conn, parent_id))
        return result

    def execute(self, conn, operation, payload, guard):
        target_id = payload["target_id"]
        if operation == "diagnose":
            result = service.diagnose(conn, target_id, publication_guard=guard)
            return {"target_id": target_id, "diagnosis_id": result["diagnosis_id"]}
        if operation == "route":
            result = learning.create_route(conn, target_id, diagnosis_id=payload["diagnosis_id"], publication_guard=guard)
            return {"target_id": target_id, "route_id": result["route_id"], "diagnosis_id": result["diagnosis_id"]}
        # These multi-stage local operations publish target/diagnosis/route atomically.
        # They perform no network or model calls inside the guarded transaction.
        registry, roots = (self.registry(conn, payload["material_ids"]) if operation == "supplement" else (None, None))
        guard()
        with guard.publication():
            if operation == "rediagnose":
                result = learning.rediagnose(conn, payload["route_id"])
            elif operation == "supplement":
                result = learning.supplement_materials(conn, target_id, registry, allowed_roots=roots,
                    expected_revision=payload["_input_lease"]["target_revision"], parent_route_id=payload.get("parent_route_id"))
            else:
                raise ValueError("operation_not_supported")
            refs = {"target_id": target_id, "diagnosis_id": result["diagnosis"]["diagnosis_id"],
                    "route_id": result["route"]["route_id"]}
            guard.record_result(refs)
            return refs


def previous_request(conn, key, operation, data):
    key = _text(key, "request_key", 200)
    row = conn.execute("SELECT operation,input_json,result_json FROM career_workbench_requests WHERE request_key=?", (key,)).fetchone()
    if row is None:
        return None
    if row[:2] != (operation, json_text(data)):
        raise ValueError("request_key_conflict")
    return json.loads(row[2])


def save_request(conn, key, operation, data, result):
    conn.execute("INSERT INTO career_workbench_requests VALUES(?,?,?,?,?)",
                 (key, operation, json_text(data), json_text(deepcopy(result)), datetime.now(UTC).isoformat()))
