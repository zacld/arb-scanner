#!/usr/bin/env python3
"""Scrape a real posting and report where it diverges from the fixtures.

Costs nothing, sends nothing, needs no API key — it opens the page, reads the
form and prints what it found. Every check below corresponds to an assumption
baked into the fixtures; a FAIL means real Greenhouse does something the tests
do not cover yet.

    python scripts/scrape_report.py --url <posting> [--url <another>]
    python scripts/scrape_report.py --url <posting> --save-fixture acme.html

--save-fixture writes the live markup to tests/fixtures/ so the real shape can
be turned into an offline regression test.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cvagent.apply.browser import launch_chromium  # noqa: E402
from cvagent.apply.forms import (  # noqa: E402
    FormField, application_form_fields, extract_fields, group_by_form,
)
from cvagent.apply.mapper import _never_answer, deterministic_pass  # noqa: E402
from cvagent.apply.profile import build_profile  # noqa: E402
from cvagent.bank import load_bank  # noqa: E402

RULE = "=" * 78
CV = {"summary": "placeholder", "selected_roles": ["dojo", "leadsparker"], "roles": [
    {"id": "universal_partners", "bullets": ["placeholder"]},
    {"id": "dojo", "bullets": ["placeholder"]},
    {"id": "leadsparker", "bullets": ["placeholder"]},
]}


def check(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  [{'PASS' if passed else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    return passed


def report(url: str, fields: list[FormField], application: list[FormField],
           off_form: list[FormField], profile: dict) -> bool:
    print(f"\n{RULE}\n{url}\n{RULE}")
    print(f"{len(fields)} fields scraped across {len(group_by_form(fields))} form(s); "
          f"{len(application)} in the application form.")
    if off_form:
        print(f"  excluded from other forms: {sorted({f.label[:30] for f in off_form})}")

    frames = {f.frame_index for f in application}
    print(f"  frames in use: {sorted(frames)}"
          + (" (iframe-embedded)" if frames != {0} else " (top frame)"))

    ok = True
    print("\nAssumptions the fixtures encode:")

    uploads = [f for f in application if f.kind == "file"]
    ok &= check("a CV/resume upload field was found", bool(uploads),
                ", ".join(f.label[:30] for f in uploads) or "NONE — the CV cannot attach")

    unlabelled = [f for f in application if not f.labels]
    ok &= check("every field has a human-readable label", not unlabelled,
                f"{len(unlabelled)} without one: {[f.id or f.name for f in unlabelled][:5]}")

    matched, remaining = deterministic_pass(application, profile)
    core = {"first name", "last name", "email"}
    found_core = {f.label.lower().strip(" *") for f in application} & core
    ok &= check("core contact fields resolve without a model call",
                bool(found_core), f"resolved {len(matched)}, {len(remaining)} left for the classifier")

    reserved = [f for f in application if _never_answer(f)]
    print(f"  [INFO] {len(reserved)} field(s) reserved for Zac: "
          f"{[f.label[:34] for f in reserved][:6]}")

    combos = [f for f in application if f.combobox]
    print(f"  [INFO] {len(combos)} typeahead field(s): {[f.label[:30] for f in combos]}")

    required_unresolved = [
        f for f in remaining if f.required
    ]
    print(f"  [INFO] {len(required_unresolved)} required field(s) need the classifier: "
          f"{[f.label[:34] for f in required_unresolved][:6]}")

    long_labels = [f for f in application if len(f.label) > 90]
    ok &= check("labels are readable (not whole page blocks)", not long_labels,
                f"{len(long_labels)} over 90 chars — review output would be unreadable")

    print("\nEvery field:")
    for f in application:
        marks = "".join([
            "R" if f.required else "-",
            "C" if f.combobox else "-",
            "X" if _never_answer(f) else "-",
        ])
        print(f"  {marks} {f.kind:<9} {f.label[:58]}")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Scrape-only report for a real posting.")
    parser.add_argument("--url", action="append", required=True, help="repeatable")
    parser.add_argument("--save-fixture", help="write the live markup to tests/fixtures/<name>")
    parser.add_argument("--out", default="scrape_report", help="directory for fields.json")
    parser.add_argument("--headless", action="store_true", default=True)
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    profile = build_profile(load_bank(), CV)

    from playwright.sync_api import sync_playwright

    ok = True
    with sync_playwright() as p:
        browser = launch_chromium(p, headless=args.headless)
        for index, url in enumerate(args.url):
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(3000)  # ATS forms hydrate after load
                fields = extract_fields(page)
                if not fields:
                    print(f"\n{url}\n  no fields found — is this the ad rather than the form?")
                    ok = False
                    continue
                application, off_form = application_form_fields(fields)
                ok &= report(url, fields, application, off_form, profile)

                stem = f"{index}-" + "".join(c if c.isalnum() else "_" for c in url)[-40:]
                (out_dir / f"{stem}.json").write_text(json.dumps(
                    [{**f.__dict__, "key": f.key, "label": f.label} for f in fields], indent=2))
                if args.save_fixture:
                    target = ROOT / "tests" / "fixtures" / args.save_fixture
                    target.write_text(page.content())
                    print(f"\n  saved live markup to {target}")
            finally:
                page.close()
        browser.close()

    print(f"\n{RULE}")
    print("Scrape assumptions hold." if ok else
          "Divergence found — the FAIL lines above are what real Greenhouse does differently.")
    print(f"Field dumps in {out_dir}/")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
