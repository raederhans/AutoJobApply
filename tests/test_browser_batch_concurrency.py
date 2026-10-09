"""Offline scheduler evidence from real worker/CLI processes and their timelines."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from applypilot.apply import browser_batch as module
from applypilot.apply.visual_bridge import write_host_metadata


@pytest.fixture
def process_batch(tmp_path, monkeypatch):
    """Replace only the model command; retain the actual worker/lease/timeout path."""
    cli = tmp_path / "synthetic_cli.py"
    cli.write_text(
        "import json,os,sys,time\n"
        "from pathlib import Path\n"
        "config=json.loads(Path(sys.argv[1]).read_text())\n"
        "root=Path(config['evidence']); root.mkdir(exist_ok=True)\n"
        "event={'pid':os.getpid(),'start':time.monotonic()}\n"
        "path=root/(config['job']+'.json')\n"
        "path.write_text(json.dumps(event))\n"
        "deadline=time.monotonic()+8\n"
        "while len(list(root.glob('*.json'))) < config['barrier']:\n"
        " if time.monotonic()>deadline: raise SystemExit(9)\n"
        " time.sleep(0.01)\n"
        "time.sleep(config['duration'])\n"
        "event['end']=time.monotonic(); path.write_text(json.dumps(event))\n"
        "raise SystemExit(config['exit'])\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "synthetic_worker.py"
    wrapper.write_text(
        "import sys,time,os\nfrom pathlib import Path\n"
        "from applypilot.apply import browser_worker as w\n"
        "real_kill=w._kill_process_tree\n"
        "def traced_kill(pid):\n"
        " print('kill_start',pid,'wrapper_pid',os.getpid(),time.monotonic(),flush=True)\n"
        " result=real_kill(pid)\n"
        " print('kill_end',pid,result,time.monotonic(),flush=True)\n"
        " return result\n"
        "w._kill_process_tree=traced_kill\n"
        f"w.build_browser_worker_command=lambda **kw: [sys.executable,{str(cli)!r},sys.argv[2]]\n"
        "raise SystemExit(w.run_browser_worker(bridge_dir=Path(sys.argv[1]),"
        "task_file=Path(sys.argv[2]),phase='prepare',timeout_seconds=float(sys.argv[3])))\n",
        encoding="utf-8",
    )
    real_popen = subprocess.Popen
    children = []

    def launch(command, **kwargs):
        if command[:3] != [sys.executable, "-m", "applypilot.apply.browser_worker"]:
            return real_popen(command, **kwargs)
        child = real_popen(
            [sys.executable, str(wrapper), command[command.index("--bridge-dir") + 1],
             command[command.index("--task-file") + 1], command[command.index("--timeout-seconds") + 1]],
            **kwargs,
        )
        children.append(child)
        return child

    monkeypatch.setattr(module.subprocess, "Popen", launch)

    def create(hosts, *, workers=2, host_limit=1, timeout=12, modes=None, barrier=None):
        jobs = []
        for index, url in enumerate(hosts):
            bridge = tmp_path / f"bridge{index}"
            bridge.mkdir()
            write_host_metadata(
                bridge, session_id=f"session{index}", token_epoch=f"epoch{index}",
                surface="browser", phase="prepare",
                target={"runtime": "iab", "tab_id": f"tab{index}", "application_url": url},
            )
            mode = (modes or {}).get(index, {})
            task = tmp_path / f"task{index}.json"
            task.write_text(json.dumps({"job": f"j{index}", "evidence": str(tmp_path / "events"),
                                        "barrier": barrier or 1, "duration": mode.get("duration", 0.4),
                                        "exit": mode.get("exit", 0), "padding": "x" * mode.get("padding", 0)}),
                            encoding="utf-8")
            jobs.append({"job_id": f"j{index}", "bridge_dir": str(bridge), "task_file": str(task)})
        manifest = tmp_path / "manifest.json"
        manifest.write_text(json.dumps({"jobs": jobs, "max_workers": workers,
                                        "per_origin_limit": host_limit, "timeout_seconds": timeout}),
                            encoding="utf-8")
        return module.BrowserBatch(manifest, tmp_path / "output", min_ram_mb=0)

    try:
        yield create, children, tmp_path
    finally:
        for child in children:
            module.stop_batch_worker(child)


def timelines(root):
    return {path.stem: json.loads(path.read_text()) for path in (root / "events").glob("*.json")}


def highwater(events):
    points = [(event["start"], 1) for event in events.values()]
    points += [(event["end"], -1) for event in events.values() if "end" in event]
    active = peak = 0
    for _, change in sorted(points):
        active += change
        peak = max(peak, active)
    return peak


def process_alive(pid):
    if os.name == "nt":
        import _winapi

        try:
            handle = _winapi.OpenProcess(0x1000, False, pid)  # Query this test's owned PID.
        except OSError:
            return False
        try:
            return _winapi.GetExitCodeProcess(handle) == 259  # STILL_ACTIVE
        finally:
            _winapi.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not (stat.exists() and stat.read_text().split(") ", 1)[1].startswith("Z"))


def assert_clean(batch, children):
    assert all(child.poll() is not None for child in children)
    assert all(not (job.bridge_dir / name).exists()
               for job in batch.jobs for name in (".batch_lease", ".worker-owner"))
    report = json.loads(batch.status_path.read_text())
    assert report["counts"]["queued"] == report["counts"]["running"] == 0
    assert all(not process_alive(event["pid"]) for event in timelines(batch.output_dir.parent).values())
    return report


@pytest.mark.parametrize("workers", [1, 2, 4])
def test_actual_worker_overlap_at_each_capacity(process_batch, workers):
    create, children, root = process_batch
    batch = create([f"https://host{i}.example.test/job" for i in range(8)],
                   workers=workers, barrier=workers)
    assert batch.run() is True
    events = timelines(root)
    assert len(events) == len({event["pid"] for event in events.values()}) == 8
    assert highwater(events) == workers
    assert assert_clean(batch, children)["highwater"] == workers
    print(f"real_worker_capacity={workers}; cli_overlap_highwater={highwater(events)}; children=8")


@pytest.mark.parametrize("host_limit", [1, 2])
def test_hostname_cap_fifo_fairness_and_no_head_of_line_block(process_batch, host_limit):
    create, children, root = process_batch
    # Different ports, casing and URL paths still share one hostname bucket.
    urls = ["https://same.example.test/a", "https://SAME.example.test:8443/b",
            "https://same.example.test/c"]
    urls += [f"https://other{i}.example.test/job" for i in range(3)]
    batch = create(urls, workers=4, host_limit=host_limit, barrier=4)
    assert batch.run() is True
    events = timelines(root)
    same_host = {jid: events[jid] for jid in ("j0", "j1", "j2")}
    assert highwater(same_host) == host_limit
    assert highwater(events) == 4
    for jid in ("j3", "j4"):
        assert events[jid]["start"] < events["j0"]["end"]
    if host_limit == 1:
        assert events["j0"]["end"] <= events["j1"]["start"]
        assert events["j1"]["end"] <= events["j2"]["start"]
    assert_clean(batch, children)
    print(f"hostname_limit={host_limit}; actual_host_highwater={highwater(same_host)}; "
          f"actual_global_highwater={highwater(events)}; blocked_queue_bypassed=True")


@pytest.mark.parametrize("stop", ["failure", "timeout", "cancel_binding"])
def test_one_stopped_job_does_not_abandon_other_hosts_or_queue(process_batch, stop):
    create, children, root = process_batch
    modes = ({0: {"exit": 7}} if stop == "failure"
             else {0: {"duration": 8, "padding": 8000}} if stop == "timeout" else {})
    batch = create(["https://same.example.test/first", "https://other.example.test/job",
                    "https://same.example.test/next"], timeout=1.5, modes=modes)
    if stop == "cancel_binding":
        write_host_metadata(batch.jobs[0].bridge_dir, session_id="replacement", token_epoch="replacement",
                            surface="browser", phase="prepare", target=batch.jobs[0].target)
    assert batch.run() is False
    expected = {"failure": "failed", "timeout": "timed_out", "cancel_binding": "cancelled"}[stop]
    assert batch.states["j0"].status == expected
    assert all(batch.states[jid].status == "completed" for jid in ("j1", "j2"))
    events = timelines(root)
    assert ("j0" in events) is (stop != "cancel_binding")
    assert len(children) == (2 if stop == "cancel_binding" else 3)
    if stop == "timeout":
        assert batch.states["j0"].exit_code == 124
        assert "end" not in events["j0"]  # Dummy CLI was actually terminated.
        assert events["j1"]["end"] < events["j2"]["start"]
    assert_clean(batch, children)
    print(f"single_job={stop}; j0={expected}; remaining_completed=2; actual_wrappers={len(children)}")


@pytest.mark.parametrize("duplicate", ["bridge", "tab", "session"])
def test_duplicate_resources_rejected_before_any_process_launch(process_batch, duplicate):
    create, children, _ = process_batch
    batch = create(["https://one.example.test/job", "https://two.example.test/job"])
    manifest = json.loads(batch.manifest_path.read_text())
    if duplicate == "bridge":
        manifest["jobs"][1]["bridge_dir"] = manifest["jobs"][0]["bridge_dir"]
    else:
        first, second = batch.jobs
        target = dict(second.target)
        if duplicate == "tab":
            target["tab_id"] = first.target["tab_id"]
        write_host_metadata(second.bridge_dir,
                            session_id=first.session_id if duplicate == "session" else second.session_id,
                            token_epoch=second.token_epoch, surface="browser", phase="prepare", target=target)
    batch.manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(module.BrowserBatchError, match="Duplicate"):
        module.BrowserBatch(batch.manifest_path, batch.output_dir.parent / "rejected", min_ram_mb=0)
    assert not children


@pytest.mark.parametrize("lease_kind", ["batch", "worker"])
def test_independent_process_lease_conflict_and_partial_claim_cleanup(tmp_path, lease_kind):
    roots = [tmp_path / "free", tmp_path / "held"]
    for root in roots:
        root.mkdir()
    ready = tmp_path / "ready"
    release = tmp_path / "release"
    code = (
        "import os,time\nfrom pathlib import Path\n"
        "from applypilot.apply.browser_batch import claim_worker_bridges\n"
        "from applypilot.apply.browser_worker import _worker_lease\n"
        f"root=Path({str(roots[1])!r})\n"
        f"context=claim_worker_bridges([root]) if {lease_kind!r} == 'batch' else _worker_lease(root, {{}})\n"
        "with context:\n"
        f" Path({str(ready)!r}).write_text(str(os.getpid()))\n"
        " deadline=time.monotonic()+10\n"
        f" while not Path({str(release)!r}).exists() and time.monotonic()<deadline: time.sleep(0.01)\n"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(module.__file__).resolve().parents[2])
    child = subprocess.Popen([sys.executable, "-c", code], env=env)
    held_file = roots[1] / (".batch_lease" if lease_kind == "batch" else ".worker-owner")
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "Lease holder failed to start"
        # Windows venv executables can be launchers for another Python PID.
        assert int(ready.read_text()) > 0 and int(ready.read_text()) != os.getpid()
        token = held_file.read_text()
        with pytest.raises(module.BrowserBatchError, match="already"), module.claim_worker_bridges(roots):
            pytest.fail("A competing process must retain its exclusive lease")
        assert not (roots[0] / ".batch_lease").exists()
        assert held_file.read_text() == token
        release.touch()
        assert child.wait(timeout=5) == 0
        assert not held_file.exists()
        print(f"real_process_lease={lease_kind}; contender_rejected=True; partial_cleanup=True")
    finally:
        release.touch()
        module.stop_batch_worker(child)
