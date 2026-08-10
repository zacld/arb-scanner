"""Stage 2: drive a real application form with the Stage 1 output.

Browser automation rather than HTTP posting, because these are third-party sites
Zac doesn't control: they render with JS, sign their uploads, and validate on
blur. Driving Chromium is the only approach that keeps working when an employer
changes their form.

Nothing is ever submitted without an explicit, typed confirmation. The default
run fills the form, screenshots it and stops.

    python -m cvagent.apply.runner <job posting url>
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
from .forms import FormField, application_form_fields, extract_fields, read_values
from .listing import extract_listing_text, looks_usable
from .mapper import Assignment, map_form
from .profile import build_profile

SUBMIT_SELECTORS = [
    "input[type=submit]",
    "button[type=submit]",
    "button:has-text('Submit application')",
    "button:has-text('Submit')",
]


def write_cv_pdf(html: str, destination: Path, playwright: Any = None) -> Path:
    """Render the CV page to PDF with headless Chromium (same engine as the preview).

    Always its own headless browser, even mid-run: Chromium only generates PDFs
    headlessly, so the visible browser Zac is watching the form in cannot do it.
    Pass the active playwright instance when one exists — nesting a second
    sync_playwright context inside a live one raises.
    """
    from playwright.sync_api import sync_playwright

    with tempfile.NamedTemporaryFile("w", suffix=".html", delete=False) as handle:
        handle.write(html)
        source = Path(handle.name)

    def render(p: Any) -> None:
        browser = launch_chromium(p, headless=True)
        page = browser.new_page()
        page.goto(source.as_uri())
        page.pdf(path=str(destination), format="A4", print_background=True)
        browser.close()

    try:
        if playwright is not None:
            render(playwright)
        else:
            with sync_playwright() as p:
                render(p)
    finally:
        source.unlink(missing_ok=True)
    return destination


def _fill_combobox(frame: Any, locator: Any, value: str) -> str:
    """Type into a typeahead and commit a suggestion from its popup.

    A plain value write leaves these fields looking filled while the form still
    considers them empty, so the application fails validation on submit with no
    obvious cause. Type, wait for the popup, take the first option.
    """
    locator.click()
    locator.fill("")
    try:
        locator.press_sequentially(value, delay=40)
    except AttributeError:  # older Playwright
        locator.type(value, delay=40)
    frame.wait_for_timeout(900)

    for selector in ("[role=option]", "li[id*=option]", ".select__option"):
        options = frame.locator(selector)
        if options.count():
            options.first.click()
            return f"picked {value!r} from the suggestion list"

    locator.press("Enter")
    return f"typed {value!r} — NO SUGGESTION LIST, verify it registered"


def apply_assignment(page: Any, field: FormField, assignment: Assignment, cv_pdf: Path) -> str:
    """Put one value on the page. Returns a short status for the review summary."""
    frame = page.frames[field.frame_index]
    locator = frame.locator(field.selector).first
    try:
        if assignment.action == "skip":
            return "skipped"
        if assignment.action == "upload_cv":
            # Deliberately no visibility wait: the real file input is hidden
            # behind a styled "Attach" button on every modern ATS.
            locator.set_input_files(str(cv_pdf))
            return f"uploaded {cv_pdf.name}"
        if assignment.action == "select":
            if field.kind == "select":
                locator.select_option(label=assignment.value)
            else:  # radio group or checkbox rendered as an option
                locator.check()
            return f"selected {assignment.value!r}"
        if field.combobox:
            return _fill_combobox(frame, locator, assignment.value)
        locator.fill(assignment.value)
        return f"filled {len(assignment.value)} chars"
    except Exception as exc:
        return f"FAILED ({type(exc).__name__}: {exc})"


def verify_fills(
    fields: dict[str, FormField],
    assignments: dict[str, Assignment],
    values: dict[str, str],
) -> list[str]:
    """Compare what we meant to write against what the page actually holds."""
    problems = []
    for key, assignment in assignments.items():
        field = fields.get(key)
        if field is None:
            continue
        actual = values.get(key, "")
        if assignment.action == "upload_cv" and not actual:
            problems.append(f"{field.label[:48]}: CV upload did not attach")
        elif assignment.action == "fill" and assignment.value and not actual:
            problems.append(f"{field.label[:48]}: typed value did not stick (field reads empty)")
        elif assignment.action == "select" and assignment.value and not actual:
            problems.append(f"{field.label[:48]}: no option ended up selected")
        elif assignment.action == "skip" and field.required and not actual:
            problems.append(f"{field.label[:48]}: REQUIRED but left blank")
    return problems


CONFIRMATION_PHRASES = (
    "thank you", "thanks for applying", "application received", "we have received",
    "successfully submitted", "application submitted", "your application is",
)


def submission_landed(page: Any, before_url: str, before_field_count: int) -> bool:
    """Whether the click actually submitted anything.

    A submit click is not evidence of submission. If any required field is empty
    the browser blocks the submit and fires no event whatsoever — no error, no
    navigation, nothing to catch. Reporting success there would tell Zac an
    application went in when it did not, which is the worst thing this tool
    could do. Look for a real change instead: navigation, a confirmation
    message, or the form no longer being there.
    """
    if page.url != before_url:
        return True
    try:
        body = (page.inner_text("body") or "").lower()
    except Exception:
        body = ""
    if any(phrase in body for phrase in CONFIRMATION_PHRASES):
        return True
    # A single-page ATS often swaps the form out for a confirmation panel.
    return len(extract_fields(page)) < before_field_count / 2


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


def resolve_listing(page: Any, args: argparse.Namespace) -> str | None:
    """The advert text: read off the page, or from a file when one is given."""
    if args.listing:
        print(f"Reading the listing from {args.listing}")
        return Path(args.listing).read_text()

    text = extract_listing_text(page)
    if looks_usable(text):
        print(f"Read the job description off the page ({len(text.split())} words).")
        return text

    print("Could not find a job description on this page — only found "
          f"{len(text.split())} words.")
    print("Some sites (LinkedIn) block scraping. Paste the advert into a file and")
    print("re-run with --listing listing.txt.")
    return None


def run(args: argparse.Namespace) -> int:
    bank = load_bank()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = launch_chromium(p, headless=args.headless)
        page = browser.new_page()
        print(f"Opening {args.url}")
        page.goto(args.url, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)  # ATS forms hydrate after load

        listing = resolve_listing(page, args)
        if listing is None:
            browser.close()
            return 1

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

        cv_pdf = write_cv_pdf(render_cv_page(cv, bank), out_dir / "cv.pdf", playwright=p)
        print(f"Rendered {cv_pdf}")

        scraped = extract_fields(page)
        if not scraped:
            print("No form fields found. Is this the application page rather than the ad?")
            browser.close()
            return 1

        fields, off_form = application_form_fields(scraped)
        print(f"Found {len(scraped)} fields; {len(fields)} belong to the application form.")
        if off_form:
            print("  ignoring fields from other forms on the page "
                  f"({', '.join(sorted({f.label[:28] for f in off_form}))})")

        by_key = {f.key: f for f in fields}
        if args.dump_fields:
            (out_dir / "fields.json").write_text(
                json.dumps([{**asdict(f), "key": f.key} for f in fields], indent=2)
            )
            print(f"  wrote {out_dir / 'fields.json'}")

        profile = build_profile(bank, cv)
        assignments = map_form(fields, profile, cv, listing, use_llm=not args.no_llm)

        statuses = {
            key: apply_assignment(page, by_key[key], assignment, cv_pdf)
            for key, assignment in assignments.items()
            if key in by_key
        }

        page.wait_for_timeout(600)  # let any client-side validation settle
        problems = verify_fills(by_key, assignments, read_values(page))

        shot = out_dir / "filled_form.png"
        page.screenshot(path=str(shot), full_page=True)
        (out_dir / "assignments.json").write_text(
            json.dumps({k: asdict(v) for k, v in assignments.items()}, indent=2)
        )

        print("\nForm filled — review before anything is sent:\n")
        print("\n".join(summarise(by_key, assignments, statuses)))
        flagged = [a for a in assignments.values() if a.needs_review]
        # A typeahead that never offered a suggestion looks filled but usually
        # is not, so it counts as a failure for the submit gate.
        failed = [
            k for k, s in statuses.items()
            if s.startswith("FAILED") or "NO SUGGESTION" in s
        ]
        print(f"\nScreenshot: {shot}")
        print(f"{len(flagged)} field(s) flagged for review, {len(failed)} fill failure(s).")

        if problems:
            print("\nRead-back check — the page does not hold what we wrote:")
            for problem in problems:
                print(f"  ! {problem}")

        if not args.submit:
            print("\nNot submitting (default). The browser stays open — finish by hand,")
            print("or re-run with --submit once the review looks right.")
            if not args.headless:
                input("Press Enter to close the browser… ")
            browser.close()
            return 0

        if (flagged or problems or failed) and not args.force:
            print("\nRefusing to submit: fields are flagged, failed to fill, or did not read")
            print("back correctly. Fix them in the open browser, or pass --force.")
            browser.close()
            return 2

        print("\nAbout to SUBMIT this application to a real employer.")
        if input('Type "submit" to confirm: ').strip().lower() != "submit":
            print("Cancelled — nothing was sent.")
            browser.close()
            return 0

        for selector in SUBMIT_SELECTORS:
            button = page.locator(selector).first
            if not button.count():
                continue

            before_url = page.url
            before_fields = len(extract_fields(page))
            button.click()
            page.wait_for_timeout(4000)
            page.screenshot(path=str(out_dir / "after_submit.png"), full_page=True)

            if submission_landed(page, before_url, before_fields):
                print(f"Submitted. Confirmation screenshot: {out_dir / 'after_submit.png'}")
                browser.close()
                return 0

            # The commonest cause is HTML5 validation: a required field is empty,
            # so the browser blocks submission and fires no event at all. Saying
            # "submitted" here would be worse than saying nothing.
            print("Clicked submit, but the page shows no sign of a submission —")
            print("the form is probably blocking on a required field it considers empty.")
            print(f"Check the browser and {out_dir / 'after_submit.png'}. NOTHING WAS SENT,")
            print("as far as can be told from the page.")
            browser.close()
            return 4

        print("Could not find a submit button — submit manually in the open browser.")
        browser.close()
        return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Point this at a job posting URL; it tailors the CV and fills the form.",
        epilog="Example: python -m cvagent.apply.runner https://job-boards.greenhouse.io/acme/jobs/123")
    parser.add_argument("url", help="The job posting URL — that is all you need")
    parser.add_argument("--listing",
                        help="File with the advert text. Only needed when the page hides it "
                             "from scrapers (LinkedIn); otherwise it is read off the page.")
    parser.add_argument("--hint", default="auto", choices=["auto", "finance", "tech", "hybrid"])
    parser.add_argument("--cv", help="Reuse a previously generated CV JSON instead of tailoring again")
    parser.add_argument("--save-cv", default="tailored_cv.json")
    parser.add_argument("--out", default="application_run", help="Directory for PDF/screenshots")
    parser.add_argument("--headless", action="store_true", help="Run without a visible browser")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip the classifier: scrape and fill only the deterministic fields. "
                             "Use to debug a new employer's form with no API key and no spend.")
    parser.add_argument("--dump-fields", action="store_true",
                        help="Write the scraped field descriptors to fields.json for debugging")
    parser.add_argument("--submit", action="store_true", help="Allow submission after confirmation")
    parser.add_argument("--force", action="store_true", help="Submit even with flagged fields")
    return run(parser.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
