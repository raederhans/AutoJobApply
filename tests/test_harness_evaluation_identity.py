"""Source provenance checks use public copies and a synthetic, offline client."""
import importlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from applypilot_lab import harness_evaluation as evaluation
from applypilot_lab import harness_experiment_plan as planning

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "docs/active/agent-harness-p0-20261011/p1-c0-budget-template.json"


def copy_sources(destination):
    for relative in evaluation.source_manifest(ROOT):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    return destination


def identity(root=ROOT):
    return {"schema_version": "harness-source-runtime/v1", "source_root": str(root.resolve()),
            "source_digest": evaluation.digest(evaluation.source_manifest(root))}


def forbid_client(monkeypatch):
    from applypilot import llm

    def forbidden(*args, **kwargs):
        pytest.fail("no client/probe/decision is allowed in this test")

    monkeypatch.setattr(llm, "get_client", forbidden)
    monkeypatch.setattr(evaluation, "model_decision", forbidden)


def test_manifest_covers_and_detects_changes_to_entire_local_browser_import_closure(tmp_path):
    source = copy_sources(tmp_path / "public-source")
    before = evaluation.source_manifest(source)
    visited, pending = set(), [source / "scripts/harness-evaluation-browser.mjs"]
    while pending:
        path = pending.pop().resolve()
        if path in visited:
            continue
        visited.add(path)
        relative = path.relative_to(source).as_posix()
        assert relative in before
        text = path.read_text(encoding="utf-8")
        for dependency in re.findall(r"(?:from\s+|import\s*(?:\(\s*)?)['\"](\.[^'\"]+)['\"]", text):
            pending.append(path.parent / dependency)
    assert source / "scripts/browser-observation-feedback.mjs" in visited
    for path in visited:
        original = path.read_bytes()
        path.write_bytes(original + b"\n// synthetic source mutation\n")
        assert evaluation.digest(evaluation.source_manifest(source)) != evaluation.digest(before)
        path.write_bytes(original)


@pytest.mark.parametrize("entry", ["evaluation", "planning"])
def test_wrong_root_rejected_before_output_and_model_probe(tmp_path, monkeypatch, entry):
    wrong = copy_sources(tmp_path / "other-checkout")
    evaluator_file = wrong / evaluation.PYTHON_SOURCES["applypilot_lab.harness_evaluation"]
    evaluator_file.write_bytes(evaluator_file.read_bytes() + b"\nSYNTHETIC_DIFFERENT_SOURCE = True\n")
    output = tmp_path / "must-not-exist"
    forbid_client(monkeypatch)
    args = [entry, "--root", str(wrong), "--output", str(output)]
    if entry == "evaluation":
        args += ["--model", "synthetic", "--run-model"]
        main = evaluation.main
    else:
        args += ["--template", str(TEMPLATE)]
        main = planning.main
    monkeypatch.setattr(sys, "argv", args)
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("entry", ["evaluation", "planning"])
def test_matching_preflight_is_offline_and_has_truthful_manifest(tmp_path, monkeypatch, entry):
    forbid_client(monkeypatch)
    output = tmp_path / "not-run"
    args = [entry, "--root", str(ROOT), "--output", str(output)]
    if entry == "evaluation":
        args += ["--model", "synthetic"]
        main, filename = evaluation.main, "manifest.json"
    else:
        args += ["--template", str(TEMPLATE)]
        main, filename = planning.main, "experiment-plan.json"
    monkeypatch.setattr(sys, "argv", args)
    main()
    report = json.loads((output / filename).read_text(encoding="utf-8"))
    assert report["status"] == "not_run" and report["model_calls"] == 0
    assert report["source_hashes"] == evaluation.source_manifest(ROOT)
    assert report["source_digest"] == evaluation.digest(report["source_hashes"])


@pytest.mark.parametrize("name", list(evaluation.PYTHON_SOURCES))
def test_loaded_python_sources_must_belong_to_requested_root(tmp_path, monkeypatch, name):
    # No get_client/profile call: even byte-identical files in another checkout
    # cannot stand in for the package actually selected by this interpreter.
    module = importlib.import_module(name)
    monkeypatch.setattr(module, "__file__", str(tmp_path / "foreign.py"))
    with pytest.raises(ValueError, match="source root/runtime mismatch"):
        evaluation.bound_source_manifest(ROOT)


def test_lazy_client_resolution_is_checked_without_importing_it(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("applypilot.llm", tmp_path / "foreign-client.py")
    real_find_spec = importlib.util.find_spec
    monkeypatch.delitem(sys.modules, "applypilot.llm", raising=False)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: spec if name == "applypilot.llm" else real_find_spec(name))
    with pytest.raises(ValueError, match="applypilot.llm"):
        evaluation.bound_source_manifest(ROOT)
    assert "applypilot.llm" not in sys.modules


def test_offline_planner_checks_its_own_entry_file(tmp_path, monkeypatch):
    output = tmp_path / "no-plan"
    monkeypatch.setattr(planning, "__file__", str(tmp_path / "foreign-planner.py"))
    monkeypatch.setattr(sys, "argv", ["plan", "--root", str(ROOT), "--output", str(output), "--template", str(TEMPLATE)])
    with pytest.raises(SystemExit) as exc:
        planning.main()
    assert exc.value.code == 2 and not output.exists()


@pytest.mark.parametrize("problem", ["missing", "wrong-root", "changed-digest", "foreign-client"])
def test_worker_rejects_unbound_identity_before_get_client(tmp_path, monkeypatch, problem):
    forbid_client(monkeypatch)
    binding = identity()
    if problem == "missing":
        binding = None
    elif problem == "wrong-root":
        binding["source_root"] = str(copy_sources(tmp_path / "wrong"))
    elif problem == "changed-digest":
        binding["source_digest"] = "0" * 64
    else:
        from applypilot import llm
        monkeypatch.setattr(llm, "__file__", str(tmp_path / "foreign-client.py"))
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"runtime_identity": binding, "probe": True})))
    with pytest.raises(ValueError):
        evaluation.model_worker()


def test_model_decision_requires_binding_before_spawning(monkeypatch):
    monkeypatch.setattr(evaluation.subprocess, "run", lambda *a, **k: pytest.fail("unbound worker spawned"))
    with pytest.raises(ValueError, match="bound source runtime identity required"):
        evaluation.model_decision({"probe": True}, 1)


def run_isolated(script, root, *args, cwd=None, pythonpath=None):
    # Do not inherit provider credentials, profile settings or plugins. A
    # regression selecting the ambient editable install still cannot call out.
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP") if key in os.environ}
    env["APPLYPILOT_DIR"] = str(root / "unused-synthetic-workspace")
    if pythonpath is not None:
        env["PYTHONPATH"] = str(pythonpath)
    result = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(root), *map(str, args)],
                            cwd=cwd or root, env=env, text=True, encoding="utf-8", capture_output=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(result.stdout)


def test_installed_package_cannot_claim_identical_checkout_sources(tmp_path):
    checkout = copy_sources(tmp_path / "checkout")
    installed = tmp_path / "site-packages"
    shutil.copytree(checkout / "src/applypilot", installed / "applypilot")
    shutil.copytree(checkout / "src/applypilot_lab", installed / "applypilot_lab")
    script = '''import json, sys
from pathlib import Path
root, installed, output = map(Path, sys.argv[1:])
sys.path.insert(0, str(installed))
from applypilot_lab import harness_evaluation as e
assert Path(e.__file__).resolve() == installed / "applypilot_lab/harness_evaluation.py"
sys.argv = ["preflight", "--root", str(root), "--output", str(output), "--model", "synthetic", "--run-model"]
try:
    e.main()
except SystemExit as exc:
    assert exc.code == 2
else:
    raise AssertionError("installed evaluator claimed a source checkout")
assert not output.exists()
assert "applypilot.llm" not in sys.modules
print(json.dumps({"rejected": True}))
'''
    assert run_isolated(script, checkout, installed, tmp_path / "no-output")["rejected"]


def test_real_worker_stays_bound_despite_cwd_and_pythonpath_shadow_package(tmp_path):
    selected = copy_sources(tmp_path / "selected checkout")
    client = selected / "src/applypilot/llm.py"
    client.write_text('''class Client:
    model = "synthetic"
    base_url = "https://offline.invalid"
    def chat_once(self, messages, **kwargs):
        return {"content": "synthetic reply", "model": self.model, "usage": None}
def get_client():
    return Client()
''', encoding="utf-8")
    shadow = tmp_path / "ambient-package"
    (shadow / "applypilot").mkdir(parents=True)
    (shadow / "applypilot/__init__.py").write_text("raise AssertionError('ambient package selected')", encoding="utf-8")
    script = '''import json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
sys.path.insert(0, str(root / "src"))
from applypilot_lab import harness_evaluation as e
assert Path(e.__file__).resolve() == root / "src/applypilot_lab/harness_evaluation.py"
binding = {"schema_version": "harness-source-runtime/v1", "source_root": str(root),
           "source_digest": e.digest(e.bound_source_manifest(root))}
reply = e.model_decision({"config": {"model": "synthetic", "temperature": 0, "max_tokens": 128},
                          "messages": []}, 15, runtime_identity=binding)
assert "applypilot.llm" not in sys.modules
print(json.dumps(reply))
'''
    reply = run_isolated(script, selected, cwd=shadow, pythonpath=shadow)
    assert reply["raw"] == "synthetic reply" and reply["model"] == "synthetic"


def test_module_entrypoints_preflight_without_importing_client(tmp_path):
    script = '''import json, runpy, sys
from pathlib import Path
root, template, output, entry = map(Path, sys.argv[1:])
sys.path.insert(0, str(root / "src"))
from applypilot_lab import harness_evaluation as e
assert Path(e.__file__).resolve() == root / "src/applypilot_lab/harness_evaluation.py"
sys.argv = ["preflight", "--root", str(root), "--output", str(output)]
if str(entry) == "evaluation":
    sys.argv.extend(["--model", "synthetic"])
else:
    sys.argv.extend(["--template", str(template)])
runpy.run_module("applypilot_lab.harness_" + ("evaluation" if str(entry) == "evaluation" else "experiment_plan"), run_name="__main__")
assert "applypilot.llm" not in sys.modules
assert "applypilot.config" not in sys.modules
'''
    for entry in ("evaluation", "planning"):
        report = run_isolated(script, ROOT, TEMPLATE, tmp_path / entry, entry)
        assert report["status"] == "not_run" and report["model_calls"] == 0
