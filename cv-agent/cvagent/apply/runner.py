"""Stage 2: drive a real application form with the Stage 1 output.

Browser automation rather than HTTP posting, because these are third-party sites
Zac doesn't control: they render with JS, sign their uploads, and validate on
blur. Driving Chromium is the only approach that keeps working when an employer
changes their form.

Nothing is ever submitted without an explicit, typed confirmation. The default
run fills the form, screenshots it and stops.

    python -m cvagent.apply.runner --url <application url> --listing listing.txt
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ..bank import load_bank
from ..render import render_cv_page
from ..tailor import tailor_cv
from .browser import launch_chromium
from .forms import FormField, extract_fields
from .mapper import Assignment, map_form
from .profile import build_profile

SUBMIT_SELECTORS = [
    "input[type=submit]",
    "button[type=submit]",
    "button:has-text('Submit application')",
    "button:has-text('Submit')",
]


def write_cv_pdf(html: str, destination: Path) -> Path:
    """Render the CV page to PDF with headless Chromium (same engine as the preview)."""
    from playwright.sync_api import sync_playwright

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as handle:
        handle.write(html)
        source = Path(handle.name)
    try:
        with sync_playwright() as p:
            browser = launch_chromium(p)
            page = browser.new_page()
            page.goto(source.as_uri())
            page.pdf(path=str(destination), format="A4", print_background=True)
            browser.close()
    finally:
        source.unlink(missing_ok=True)
    return destination


def apply_assignment(page: Any, field: FormField, assignment: Assignment, cv_pdf: Path) -> str:
    """Put one value on the page. Returns a short status for the review summary."""
    frame = page.frames[field.frame_index]
    locator = frame.locator(field.selector).first
    try:
        if assignment.action == "skip":
            return "skipped"
        if assignment.action == "upload_cv":
            locator.set_input_files(str(cv_pdf))
            return f"uploaded {cv_pdf.name}"
        if assignment.action == "select":
            if field.kind == "select":
                locator.select_option(label=assignment.value)
            else:  # radio group or checkbox rendered as an option
                locator.check()
            return f"selected {assignment.value!r}"
        locator.fill(assignment.value)
        return f"filled {len(assignment.value)} chars"
    except Exception as exc:
        return f"FAILED ({type(exc).__name__}: {exc})"


def summarise(
    fields: dict[str, FormField], assignments: dict[str, Assignment], statuses: dict[str, str]
) -> list[str]:
    lines = []
    for key, assignment in assignments.items():
        field = fields[key]
        flag = "!" if assignment.needs_review else " "
        detail = statuses.get(key, "")
        lines.append(f" {flag} {field.label[:52]:<54} {detail}")
        if assignment.note:
            lines.append(f"     ↳ {assignment.note}")
    return lines


def run(args: argparse.Namespace) -> int:
    listing = Path(args.listing).read_text()
    bank = load_bank()

    if args.cv:
        cv = json.loads(Path(args.cv).read_text())
        print(f"Using cached CV from {args.cv}")
    else:
        print("Tailoring the CV for this listing…")
        result = tailor_cv(listing, args.hint, bank=bank)
        cv = result.cv
        check = result.report
        print(
            f"  roles: {', '.join(cv['selected_roles'])} | "
            f"rewrite verified: {check.ok} (longest shared run {check.max_run})"
        )
        if not check.ok:
            print("  warning: the rewrite check did not fully pass — review before submitting.")
        Path(args.save_cv).write_text(json.dumps(cv, indent=2))
        print(f"  saved CV JSON to {args.save_cv}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    cv_pdf = write_cv_pdf(render_cv_page(cv, bank), out_dir / "cv.pdf")
    print(f"Rendered {cv_pdf}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=args.headless)
        page = browser.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # ATS forms hydrate after load

        fields = extract_fields(page)
        if not fields:
            print("No form fields found. Is this the application page rather than the ad?")
            browser.close()
            return 1
        print(f"Found {len(fields)} fields; mapping…")

        profile = build_profile(bank, cv)
        assignments = map_form(fields, profile, cv, listing)

        by_key = {f.key: f for f in fields}
        statuses = {
            key: apply_assignment(page, by_key[key], assignment, cv_pdf)
            for key, assignment in assignments.items()
            if key in by_key
        }

        shot = out_dir / "filled_form.png"
        page.screenshot(path=str(shot), full_page=True)
        (out_dir / "assignments.json").write_text(
            json.dumps({k: asdict(v) for k, v in assignments.items()}, indent=2)
        )

        print("\nForm filled — review before anything is sent:\n")
        print("\n".join(summarise(by_key, assignments, statuses)))
        flagged = [a for a in assignments.values() if a.needs_review]
        failed = [k for k, s in statuses.items() if s.startswith("FAILED")]
        print(f"\nScreenshot: {shot}")
        print(f"{len(flagged)} field(s) flagged for review, {len(failed)} fill failure(s).")

        if not args.submit:
            print("\nNot submitting (default). The browser stays open — finish by hand,")
            print("or re-run with --submit once the review looks right.")
            if not args.headless:
                input("Press Enter to close the browser… ")
            browser.close()
            return 0

        if flagged and not args.force:
            print("\nRefusing to submit while fields are flagged. Fix them or pass --force.")
            browser.close()
            return 2

        print("\nAbout to SUBMIT this application to a real employer.")
        if input('Type "submit" to confirm: ').strip().lower() != "submit":
            print("Cancelled — nothing was sent.")
            browser.close()
            return 0

        for selector in SUBMIT_SELECTORS:
            button = page.locator(selector).first
            if button.count():
                button.click()
                page.wait_for_timeout(4000)
                page.screenshot(path=str(out_dir / "after_submit.png"), full_page=True)
                print(f"Submitted. Confirmation screenshot: {out_dir / 'after_submit.png'}")
                browser.close()
                return 0

        print("Could not find a submit button — submit manually in the open browser.")
        browser.close()
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fill a job application from the tailored CV.")
    parser.add_argument("--url", required=True, help="Application form URL (Greenhouse, Lever, …)")
    parser.add_argument("--listing", required=True, help="File containing the pasted job listing")
    parser.add_argument("--hint", default="auto", choices=["auto", "finance", "tech", "hybrid"])
    parser.add_argument("--cv", help="Reuse a previously generated CV JSON instead of tailoring again")
    parser.add_argument("--save-cv", default="tailored_cv.json")
    parser.add_argument("--out", default="application_run", help="Directory for PDF/screenshots")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser")
    parser.add_argument("--submit", action="store_true", help="Allow submission after confirmation")
    parser.add_argument("--force", action="store_true", help="Submit even with flagged fields")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
