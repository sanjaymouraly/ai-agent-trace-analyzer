"""Capture real screenshots of the running TraceLens application.

Usage::

    python scripts/capture_screenshots.py [--base-url http://127.0.0.1:8000] [--out docs/images]

The script drives the real application with Playwright: it navigates the
dashboard, filters the trace explorer, opens an investigation, interacts with
the dependency graph, compares two trajectories and visits the settings page.
Every PNG in ``docs/images`` is produced by this script from a live server.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from playwright.sync_api import Page, sync_playwright  # noqa: E402

RUN_ID = "demo-orders-invalid-args"
REFERENCE_RUN_ID = "demo-orders-reference"


def wait_for_app(page: Page) -> None:
    page.wait_for_selector("text=TraceLens", timeout=30_000)
    page.wait_for_load_state("networkidle")


def capture(page: Page, out: Path, name: str, full_page: bool = True) -> None:
    target = out / name
    page.screenshot(path=str(target), full_page=full_page)
    print(f"wrote {target.relative_to(ROOT)}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture TraceLens screenshots from a running server.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--out", default=str(ROOT / "docs" / "images"))
    parser.add_argument("--theme", default="dark", choices=["dark", "light"])
    args = parser.parse_args()

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    base = args.base_url.rstrip("/")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--force-color-profile=srgb"])
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, device_scale_factor=1)
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
        page.on("console", lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type == "error" else None)

        page.goto(f"{base}/overview", wait_until="domcontentloaded")
        wait_for_app(page)
        if args.theme == "light":
            page.evaluate("localStorage.setItem('tracelens.theme','light')")
            page.reload(wait_until="domcontentloaded")
            wait_for_app(page)
        page.wait_for_timeout(1200)
        capture(page, out, "01-overview.png")

        page.goto(f"{base}/runs", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_timeout(900)
        capture(page, out, "02-trace-explorer.png")

        page.goto(f"{base}/runs/{RUN_ID}", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_timeout(1200)
        capture(page, out, "03-run-overview.png")

        page.get_by_role("tab", name="Timeline").click()
        page.wait_for_timeout(700)
        capture(page, out, "04-execution-timeline.png")

        page.get_by_role("tab", name="Dependency graph").click()
        page.wait_for_timeout(1600)
        capture(page, out, "05-dependency-graph.png")

        page.get_by_role("tab", name="Root-cause workbench").click()
        page.wait_for_timeout(800)
        capture(page, out, "06-root-cause-workbench.png")

        page.get_by_role("tab", name="Findings").click()
        page.wait_for_timeout(700)
        capture(page, out, "07-findings.png")

        page.get_by_role("tab", name="Review & experiments").click()
        page.wait_for_timeout(700)
        capture(page, out, "08-review-experiments.png")

        page.goto(f"{base}/compare", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_function(
            "() => document.querySelectorAll('#compare-baseline option').length > 2",
            timeout=20_000,
        )
        # Compare the successful reference with its failing counterpart.
        page.select_option("#compare-baseline", REFERENCE_RUN_ID)
        page.select_option("#compare-candidate", RUN_ID)
        page.wait_for_timeout(400)
        page.get_by_role("button", name="⇄ Align trajectories").click()
        page.wait_for_timeout(1600)
        capture(page, out, "09-trajectory-comparison.png")

        page.goto(f"{base}/tools", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_timeout(1100)
        capture(page, out, "10-tool-failure-analytics.png")

        page.goto(f"{base}/assistant", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_timeout(900)
        capture(page, out, "11-ai-assistant.png")

        page.goto(f"{base}/settings", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_timeout(900)
        capture(page, out, "12-settings.png")

        page.goto(f"{base}/audit", wait_until="domcontentloaded")
        wait_for_app(page)
        page.wait_for_timeout(700)
        capture(page, out, "13-audit-log.png")

        # Mobile layout check.
        mobile = browser.new_context(viewport={"width": 420, "height": 900})
        mobile_page = mobile.new_page()
        mobile_page.goto(f"{base}/overview", wait_until="domcontentloaded")
        wait_for_app(mobile_page)
        mobile_page.wait_for_timeout(900)
        mobile_page.screenshot(path=str(out / "14-mobile-overview.png"), full_page=True)
        print(f"wrote {(out / '14-mobile-overview.png').relative_to(ROOT)}")
        mobile.close()

        browser.close()

    if errors:
        print("\nBrowser errors detected:", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print("\nNo browser console errors detected.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
