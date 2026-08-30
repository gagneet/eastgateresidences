"""Guards the Strata Sync scraper launch chain.

The subprocess is spawned with Popen and its stdout redirected to a log file, so
every failure in this chain is SILENT from the API's point of view: Popen succeeds
as long as `xvfb-run` exists, the child dies immediately, and the job row sits at
status "starting" forever while the UI polls it. The portal login/PIN prompt simply
never appears.

That is exactly what happened after PR #597 merged the Strata Sync package back
in-repo: the router moved to backend/routers/ and run_scraper.py to backend/scripts/,
but the launcher kept resolving the script as a sibling of its own __file__.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[2] / "backend"


def test_scraper_script_exists_where_the_router_launches_it():
    """The path start_sync() builds must point at a real file."""
    script_path = BACKEND_DIR / "scripts" / "run_scraper.py"
    assert script_path.is_file(), (
        f"{script_path} is missing — start_sync() would spawn a subprocess that dies "
        "instantly and leave every sync job pinned at 'starting'."
    )


def test_router_resolves_the_scraper_via_backend_dir_not_its_own_directory():
    """run_scraper.py is NOT a sibling of the router; resolving it that way is the bug."""
    src = (BACKEND_DIR / "routers" / "strata_sync.py").read_text(encoding="utf-8")
    assert 'os.path.dirname(__file__), "run_scraper.py"' not in src, (
        "start_sync() is resolving run_scraper.py as a sibling of the router again. "
        "It lives in backend/scripts/ — resolve it from the backend dir instead."
    )
    assert '"scripts" / "run_scraper.py"' in src


def test_scraper_module_compiles():
    """A syntax error here surfaces only as a dead subprocess and a hung job.

    Compiled in-process from the source text rather than via `python -m py_compile`:
    that writes a .pyc into backend/scripts/__pycache__/, which is not writable in
    every environment, so the test failed with EACCES and reported it as "does not
    compile" — an environment fault dressed up as a syntax error.
    """
    script_path = BACKEND_DIR / "scripts" / "run_scraper.py"
    source = script_path.read_text(encoding="utf-8")
    try:
        compile(source, str(script_path), "exec")
    except SyntaxError as exc:
        pytest.fail(f"run_scraper.py does not compile: {exc}")


def test_status_vocabulary_matches_the_frontend():
    """StrataSyncPage.jsx only renders/stops polling on status == 'error'.

    A launcher that marks a dead job 'failed' leaves the page spinning forever —
    the same user-visible symptom as no error handling at all.
    """
    src = (BACKEND_DIR / "routers" / "strata_sync.py").read_text(encoding="utf-8")
    assert '"status": "failed"' not in src, (
        "strata_sync.py sets status 'failed'; the scraper and the frontend both use 'error'."
    )


def test_playwright_browser_matches_the_installed_version():
    """A Playwright upgrade without a matching `playwright install` leaves the old
    browser build in the cache and every launch fails with "Executable doesn't exist".
    That is a second, independent way the login screen never appears — it is how
    playwright 1.49.1 -> 1.62.0 (chromium build 1217 -> 1234) broke this tool.

    Run in a subprocess: sync_playwright() spins up its own driver loop, which
    collides with pytest-asyncio's auto mode and spews teardown errors into the run.
    """
    pytest.importorskip("playwright")
    probe = (
        "from playwright.sync_api import sync_playwright\n"
        "with sync_playwright() as pw: print(pw.chromium.executable_path)\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, f"could not resolve chromium path: {result.stderr}"
    exe = Path(result.stdout.strip())
    assert exe.exists(), (
        f"Playwright expects chromium at {exe}, which is not present — the installed "
        "playwright version and the downloaded browser build have drifted apart. "
        "Run: python -m playwright install chromium"
    )


# ── The dead-subprocess guard ────────────────────────────────────────────────
#
# These exist because the FIRST version of that guard used os.kill(pid, 0), which
# reports an unreaped child as alive forever. It would have detected nothing.

def _load_subprocess_state():
    """Exec just this one function, so the test does not have to import the router
    (which pulls in the app config, the Mongo client and every service it depends on)."""
    src = (BACKEND_DIR / "routers" / "strata_sync.py").read_text(encoding="utf-8")
    start = src.index("def _subprocess_state(")
    end = src.index("\ndef ", start + 1)
    ns = {}
    exec(compile(src[start:end], "strata_sync_excerpt", "exec"), ns)
    return ns["_subprocess_state"]


def test_dead_subprocess_is_detected_even_while_it_is_a_zombie():
    """The regression that makes this guard worth having.

    Nothing reaps the scraper child, so after it exits it lingers as a zombie:
    still in the process table, still signalable. os.kill(pid, 0) SUCCEEDS on it.
    A liveness check built on that never fires, and the job hangs at "starting"
    exactly as it did with no check at all.
    """
    subprocess_state = _load_subprocess_state()
    job_id = "zombie-probe-job"
    child = subprocess.Popen(["/bin/sh", "-c", f"# {job_id}\nexit 3"])
    try:
        deadline = time.time() + 10
        while time.time() < deadline:
            try:
                state = Path(f"/proc/{child.pid}/stat").read_text().rpartition(")")[2].split()[0]
            except (FileNotFoundError, IndexError):
                pytest.skip("child was reaped before the zombie window could be observed")
            if state == "Z":
                break
            time.sleep(0.05)
        else:
            pytest.skip("child never entered the zombie state on this platform")

        # Precondition: the naive check is fooled. If this ever stops holding the
        # test is no longer exercising the regression.
        os.kill(child.pid, 0)  # does NOT raise — the zombie looks alive

        assert subprocess_state(child.pid, job_id) == "dead"
    finally:
        child.wait()


def test_running_subprocess_for_this_job_is_reported_running():
    subprocess_state = _load_subprocess_state()
    job_id = "running-probe-job"
    child = subprocess.Popen(["/bin/sh", "-c", f"# {job_id}\nsleep 30"])
    try:
        assert subprocess_state(child.pid, job_id) == "running"
    finally:
        child.kill()
        child.wait()


def test_recycled_pid_belonging_to_another_process_is_not_our_job():
    """pids are reused. A live process that is not this job means our child is gone."""
    subprocess_state = _load_subprocess_state()
    child = subprocess.Popen(["/bin/sh", "-c", "sleep 30"])
    try:
        assert subprocess_state(child.pid, "some-other-job-id") == "dead"
    finally:
        child.kill()
        child.wait()


def test_absent_pid_is_dead_and_unreadable_proc_is_unknown():
    subprocess_state = _load_subprocess_state()
    # PID 1 exists but is not our job -> "dead" by the identity rule, never a crash.
    assert subprocess_state(1, "no-such-job") in {"dead", "unknown"}
    # A pid that cannot exist: /proc entry absent.
    assert subprocess_state(2 ** 22, "any-job") == "dead"
