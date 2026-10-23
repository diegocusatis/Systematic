"""Opt-in complete Windows install/update/server test, entirely in a chosen folder.

Requires test dependencies in the invoking interpreter and internet or pip's
cache for the two clean app environments. Does not touch real AppData or Desktop.
"""
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import threading
import time
import urllib.request

from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from windows import installer
from test_windows_installer import seeded, exported


def smoke(setup_exe, root):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=False)
    payload = root / "payload"
    subprocess.run([str(Path(setup_exe).resolve()), "--extract", str(payload)], check=True, timeout=60)
    # Exercise the compiled Python probe, including a missing executable.
    probe = root / "probe.txt"
    subprocess.run([str(Path(setup_exe).resolve()), "--probe-python", sys.executable, str(probe)],
                   check=True, timeout=30)
    assert probe.read_text() != "UNSUPPORTED"
    unsupported = subprocess.run([str(Path(setup_exe).resolve()), "--probe-python",
                                 str(root / "absent-python.exe"), str(probe)], timeout=30)
    assert unsupported.returncode == 2 and probe.read_text() == "UNSUPPORTED"
    previous_project = root / "previous project"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(previous_project / ".venv")], check=True)
    subprocess.run([str(Path(setup_exe).resolve()), "--detect-python", str(previous_project), str(probe)],
                   check=True, timeout=90)
    assert probe.read_text() != "UNSUPPORTED"
    # The payload's automatic search, exactly as the setup window runs it.
    search_root = root / "Documents"
    previous_data = search_root / "GitHub" / "Systematic" / ".review-data"
    seeded(previous_data)
    roots_file = root / "roots.json"
    roots_file.write_text(json.dumps([str(search_root)]), encoding="utf-8")
    discovered = subprocess.run([sys.executable, "-I", "-u", str(payload / "installer.py"), "--discover",
                                 "--roots-json", str(roots_file), "--home", str(root / "program"),
                                 "--data", str(root / "permanent reviews")],
                                check=True, capture_output=True, text=True, encoding="utf-8", timeout=60).stdout
    found = json.loads(discovered.strip().splitlines()[-1].split("=", 1)[1])["candidates"]
    assert Path(found[0]["path"]) == previous_data.resolve() and found[0]["summary"]["reviews"] == 1
    source, home, data = root / "old reviews", root / "program", root / "permanent reviews"
    seeded(source)
    original = installer.record_fingerprints(source / installer.DB)
    expected = exported(source)
    command = [sys.executable, "-I", "-u", str(payload / "installer.py"), "--payload", str(payload),
               "--home", str(home), "--data", str(data)]
    def run_setup(arguments, offline=False):
        started = time.monotonic()
        env = dict(os.environ)
        if offline:
            env["PIP_NO_INDEX"] = "1"
        completed = subprocess.run(arguments, check=False, env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, encoding="utf-8")
        print(completed.stdout, flush=True)
        completed.check_returncode()
        events = [json.loads(line.split("=", 1)[1]) for line in completed.stdout.splitlines()
                  if line.startswith("SYSTEMATIC_PROGRESS=")]
        percentages = [event["percent"] for event in events]
        assert percentages == sorted(percentages) and percentages[-1] == 99
        assert all(0 <= percent < 100 for percent in percentages)
        if offline:
            assert "Reusing verified package downloads" in completed.stdout
        if "--source" in arguments:
            assert "Carried over: " in completed.stdout
        return round(time.monotonic() - started, 2)
    fresh_seconds = run_setup(command + ["--source", str(source)])
    first = installer.read_json(home / "current.json")
    assert exported(data) == expected
    first_records = installer.record_fingerprints(data / installer.DB)
    assert (home / "Systematic.exe").read_bytes() == (payload / "Systematic.exe").read_bytes()
    # Stand in for an older executable so the upgrade must replace actual bytes.
    (home / "Systematic.exe").write_bytes(b"synthetic old launcher to replace")
    # Simulate a genuinely newer package, not just a reinstall of the same wheel.
    manifest = installer.read_json(payload / "release.json")
    newer_version = manifest["version"] + ".post1"
    project_file = payload / "project" / "pyproject.toml"
    project_file.write_text(re.sub(r'(?m)^version\s*=\s*"[^"]+"$', 'version = "' + newer_version + '"',
                                  project_file.read_text(), count=1), encoding="utf-8")
    manifest["version"] = newer_version
    manifest["payload_id"] = hashlib.sha256(project_file.read_bytes() + manifest["payload_id"].encode()).hexdigest()
    installer.atomic_json(payload / "release.json", manifest)
    # A second install uses the permanent data folder without importing again.
    upgrade_seconds = run_setup(command, offline=True)
    second = installer.read_json(home / "current.json")
    assert first["installation_id"] != second["installation_id"]
    assert second["version"] == newer_version
    assert (home / "Systematic.exe").read_bytes() == (payload / "Systematic.exe").read_bytes()
    assert second["launcher_icon"] != first["launcher_icon"]
    assert Path(second["launcher_icon"]).read_bytes() == (payload / "Systematic.ico").read_bytes()
    installed_version = subprocess.check_output([second["python"], "-c",
        "from importlib.metadata import version; print(version('review-extractor'))"], text=True).strip()
    assert installed_version == newer_version
    assert first_records == installer.record_fingerprints(data / installer.DB)
    assert exported(data) == expected
    assert installer.record_fingerprints(source / installer.DB) == original
    assert (home / "backups" / second["installation_id"] / "data" / installer.DB).is_file()
    process = subprocess.Popen([second["python"], "-u", second["runner"], "--home", str(home)],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8")
    lines = queue.Queue()
    def read():
        for line in process.stdout:
            lines.put(line.strip())
        lines.put(None)
    threading.Thread(target=read, daemon=True).start()
    try:
        deadline = time.monotonic() + 60
        url = None
        while time.monotonic() < deadline:
            line = lines.get(timeout=max(.1, deadline - time.monotonic()))
            if line is None:
                raise RuntimeError("The installed server exited before becoming ready.")
            print(line, flush=True)
            if line.startswith("SYSTEMATIC_READY="):
                url = line.split("=", 1)[1]
                break
        assert url, "Launcher did not report a ready server"
        with urllib.request.urlopen(url + "/api/health", timeout=10) as response:
            assert response.status == 200
        with urllib.request.urlopen(url + "/api/reviews", timeout=10) as response:
            assert b"Synthetic review" in response.read()
        # Fetch a real paper endpoint to prove rewritten paths are used by the installed app.
        with urllib.request.urlopen(url + "/api/reviews/review-1/papers/paper-1/pdf", timeout=10) as response:
            assert response.read().startswith(b"%PDF")
        process.stdin.write("stop\n")
        process.stdin.flush()
        assert process.wait(timeout=30) == 0
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    after_launch = installer.record_fingerprints(data / installer.DB)
    for table in ("fields", "outcome_fields", "evidence", "items", "outcomes"):
        assert after_launch[table] == first_records[table]
    result = dict(fresh_install=True, upgrade=True, native_python_check=True,
                  original_unchanged=True, exact_exports=True, pdf_endpoint=True,
                  graceful_shutdown=True, retained_backup=True, offline_cached_upgrade=True,
                  old_launcher_replaced=True, versioned_shortcut_icon=True,
                  monotonic_progress=True, automatic_discovery=True, fresh_seconds=fresh_seconds, upgrade_seconds=upgrade_seconds)
    (root / "smoke-result.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    args = parser.parse_args()
    smoke(args.setup, args.work)
