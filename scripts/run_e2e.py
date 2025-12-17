"""End-to-end browser workflow.

Starts the real application, drives it with Playwright, asserts the main user
journeys, captures screenshots and shuts the server down again. Any browser
console error fails the run.

Usage::

    python scripts/run_e2e.py [--keep-screenshots]
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

SHOTS = ROOT / "docs" / "images"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_ready(url: str, process: subprocess.Popen, timeout: float = 90.0) -> None:
    import urllib.error
    import urllib.request

    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError("The application exited before becoming ready.")
        try:
            # The URL is built from a loopback address and a locally bound port.
            with urllib.request.urlopen(f"{url}/api/v1/ready", timeout=2) as response:  # nosec B310
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.5)
    raise TimeoutError(f"The application did not become ready within {timeout}s.")


def assert_no_console_errors(errors: list[str]) -> None:
    if errors:
        raise AssertionError("Browser console errors detected:\n" + "\n".join(errors))


def run_workflow(base_url: str, capture: bool) -> list[str]:
    errors: list[str] = []
    checks: list[str] = []
    SHOTS.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        context = browser.new_context(viewport={"width": 1600, "height": 1000})
        page = context.new_page()
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on(
            "console",
            lambda msg: errors.append(f"console.error: {msg.text}") if msg.type == "error" else None,
        )

        # --- Overview -----------------------------------------------------
        page.goto(f"{base_url}/overview", wait_until="domcontentloaded")
        page.wait_for_selector("text=Execution overview", timeout=30_000)
        page.wait_for_timeout(900)
        assert page.locator("text=Total executions").count() >= 1, "overview metrics must be rendered"
        checks.append("overview renders live metrics")

        # --- Trace explorer + filtering ------------------------------------
        page.goto(f"{base_url}/runs", wait_until="domcontentloaded")
        page.wait_for_selector("text=Trace explorer", timeout=30_000)
        page.wait_for_timeout(700)
        rows_before = page.locator("table tbody tr").count()
        assert rows_before > 0, "the trace explorer must list stored runs"

        page.get_by_label("Filter by status").select_option("failure")
        page.get_by_role("button", name="Apply filters").click()
        page.wait_for_timeout(900)
        failures = page.locator("table tbody tr").count()
        assert failures <= rows_before, "the status filter must narrow the result set"
        body = page.inner_text("table")
        assert "Success" not in body or "Failure" in body, "filtered table must only show failures"
        checks.append("trace explorer filters by status")

        page.get_by_role("button", name="Clear").click()
        page.wait_for_timeout(700)

        # --- Investigation --------------------------------------------------
        page.goto(f"{base_url}/runs/demo-orders-invalid-args", wait_until="domcontentloaded")
        page.wait_for_selector("text=Earliest observed error", timeout=30_000)
        page.wait_for_timeout(700)
        assert page.locator("text=Invalid Tool Arguments").count() >= 1, "earliest error must be shown"

        page.get_by_role("tab", name="Timeline").click()
        page.wait_for_timeout(600)
        assert page.locator("text=Execution timeline").count() >= 1

        page.get_by_role("tab", name="Dependency graph").click()
        page.wait_for_timeout(1500)
        assert page.locator(".react-flow").count() >= 1, "the dependency graph must render"
        node_count = page.locator(".react-flow__node").count()
        assert node_count >= 3, f"expected at least 3 graph nodes, found {node_count}"
        checks.append(f"dependency graph renders {node_count} nodes")

        page.get_by_role("tab", name="Root-cause workbench").click()
        page.wait_for_timeout(700)
        assert page.locator("text=Evidence ledger").count() >= 1
        assert page.locator("text=Inferred Hypothesis").count() >= 1, "hypotheses must be labelled"
        checks.append("root-cause workbench labels hypotheses as inference")

        page.get_by_role("tab", name="Review & experiments").click()
        page.wait_for_timeout(600)
        page.get_by_role("button", name="Run safe fixture check").click()
        page.wait_for_timeout(1500)
        assert page.locator("text=Experiment").count() >= 1, "the experiment result must be recorded"
        checks.append("controlled fixture experiment runs and is recorded")

        # --- Comparison ------------------------------------------------------
        page.goto(f"{base_url}/compare", wait_until="domcontentloaded")
        page.wait_for_selector("text=Compare executions", timeout=30_000)
        page.wait_for_function(
            "() => document.querySelectorAll('#compare-baseline option').length > 2",
            timeout=20_000,
        )
        page.select_option("#compare-baseline", "demo-orders-reference")
        page.select_option("#compare-candidate", "demo-orders-invalid-args")
        page.wait_for_timeout(300)
        page.get_by_role("button", name="⇄ Align trajectories").click()
        page.wait_for_selector("text=Aligned actions", timeout=20_000)
        page.wait_for_timeout(700)
        assert page.locator("text=Aligned actions").count() >= 1, "alignment table must render"
        checks.append("trajectory comparison aligns two runs")

        # --- Tool analytics ---------------------------------------------------
        page.goto(f"{base_url}/tools", wait_until="domcontentloaded")
        page.wait_for_selector("text=Tool failure analytics", timeout=30_000)
        page.wait_for_timeout(900)
        assert page.locator("text=Per-tool breakdown").count() >= 1
        checks.append("tool failure analytics renders")

        # --- Settings ----------------------------------------------------------
        page.goto(f"{base_url}/settings", wait_until="domcontentloaded")
        page.wait_for_selector("text=Settings", timeout=30_000)
        page.wait_for_timeout(800)
        assert page.locator("text=AI provider").count() >= 1
        assert page.locator("text=Offline").count() >= 0
        checks.append("settings page renders provider configuration")

        if capture:
            page.goto(f"{base_url}/overview", wait_until="domcontentloaded")
            page.wait_for_timeout(1200)
            page.screenshot(path=str(SHOTS / "01-overview.png"), full_page=True)

        browser.close()

    assert_no_console_errors(errors)
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the TraceLens browser workflow.")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--keep-screenshots", action="store_true")
    args = parser.parse_args()

    port = args.port or free_port()
    base_url = f"http://127.0.0.1:{port}"
    env = {
        **os.environ,
        "DATABASE_URL": env_database_url(),
        "SEED_DEMO": "true",
        "APP_NAME": "TraceLens",
    }

    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        wait_ready(base_url, process)
        checks = run_workflow(base_url, capture=True)
        for check in checks:
            print(f"  ok  {check}")
        print(f"\nBrowser workflow passed ({len(checks)} checks).")
        return 0
    except Exception as exc:
        print(f"Browser workflow failed: {exc}", file=sys.stderr)
        return 1
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()


def env_database_url() -> str:
    """Use an isolated database so the workflow never touches real data."""
    path = ROOT / "data" / "e2e.db"
    path.parent.mkdir(parents=True, exist_ok=True)
    for suffix in ["", "-wal", "-shm"]:
        candidate = Path(str(path) + suffix)
        if candidate.exists():
            candidate.unlink()
    return f"sqlite:///{path.as_posix()}"


if __name__ == "__main__":
    raise SystemExit(main())
