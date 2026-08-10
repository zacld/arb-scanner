#!/usr/bin/env python3
"""Live validation — the checks that need a real model.

Neither step here touches an employer's website: the classifier is exercised
against the local Greenhouse fixture, so the first live run costs a few pennies
and cannot send anything to anyone. Requires ANTHROPIC_API_KEY.

    python scripts/live_check.py                 # both steps
    python scripts/live_check.py --step classifier
    python scripts/live_check.py --step tailoring

Step 1 prints every generated free-text answer verbatim before anything else
happens, because an answer you have not read is not one you can trust.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cvagent.apply.browser import launch_chromium  # noqa: E402
from cvagent.apply.forms import application_form_fields, extract_fields  # noqa: E402
from cvagent.apply.mapper import classify_fields, deterministic_pass  # noqa: E402
from cvagent.apply.profile import build_profile  # noqa: E402
from cvagent.bank import all_roles, load_bank  # noqa: E402
from cvagent.tailor import tailor_cv  # noqa: E402
from cvagent.verify import longest_shared_run  # noqa: E402

FIXTURE = ROOT / "tests" / "fixtures" / "greenhouse_modern.html"
LISTINGS = {
    "FINANCE": ROOT / "tests" / "fixtures" / "listing_finance.txt",
    "TECH": ROOT / "tests" / "fixtures" / "listing_tech.txt",
}
RULE = "=" * 78


def wrap(text: str, indent: str = "      ") -> str:
    return textwrap.fill(text, width=76, initial_indent=indent, subsequent_indent=indent)


def step_classifier(bank: dict) -> bool:
    """First live exercise of the classifier: real model, local form, no employer."""
    print(f"\n{RULE}\nSTEP 1 — CLASSIFIER (live model, local fixture form)\n{RULE}")
    listing = LISTINGS["TECH"].read_text()

    print("Tailoring a CV to have something real to answer from…")
    result = tailor_cv(listing, "auto", bank=bank)
    print(f"  roles: {', '.join(result.cv['selected_roles'])} | "
          f"rewrite verified: {result.report.ok}")

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = launch_chromium(p)
        page = browser.new_page()
        page.goto(FIXTURE.as_uri())
        page.wait_for_timeout(300)
        fields, _ = application_form_fields(extract_fields(page))
        browser.close()

    profile = build_profile(bank, result.cv)
    _, remaining = deterministic_pass(fields, profile)
    print(f"{len(remaining)} field(s) need the classifier; calling the model…\n")

    answers = classify_fields(remaining, profile, result.cv, listing)

    by_key = {f.key: f for f in remaining}
    free_text = []
    for key, assignment in answers.items():
        field = by_key[key]
        print(f"  Q: {field.label}")
        print(f"     action={assignment.action} source={assignment.source} "
              f"confidence={assignment.confidence}")
        if assignment.value:
            print(wrap(assignment.value))
        if assignment.note:
            print(f"      note: {assignment.note}")
        print()
        if field.kind == "textarea" and assignment.value:
            free_text.append((field.label, assignment.value))

    print(RULE)
    if not free_text:
        print("NO free-text answer was generated. That is the thing this step exists to")
        print("check, so treat this as a FAIL and look at the actions above.")
        return False

    print("Read the answers above before trusting this on a real application.")
    print("Specifically check: no invented facts, no claimed familiarity with")
    print("products Zac has not used, and nothing that reads as flattery.")
    return True


def step_tailoring(bank: dict) -> bool:
    """Confirm the wording shifts between listings, not just the role selection."""
    print(f"\n{RULE}\nSTEP 3 — STAGE 1 WORDING CHECK\n{RULE}")
    roles = all_roles(bank)
    results = {}
    for label, path in LISTINGS.items():
        print(f"Tailoring against the {label} listing…")
        results[label] = tailor_cv(path.read_text(), "auto", bank=bank)

    ok = True
    for label, result in results.items():
        cv, check = result.cv, result.report
        print(f"\n{label}: {cv['category_read']} — roles: {', '.join(cv['selected_roles'])}")
        print(f"  rounds={result.rounds} verified={check.ok} longest_shared_run={check.max_run}")
        if not check.ok:
            ok = False
            for violation in check.violations:
                print(f"  ! {violation.detail}")
        for entry in cv["roles"]:
            source = roles[entry["id"]]
            print(f"  {source['company']}:")
            for bullet in entry["bullets"]:
                worst = max((longest_shared_run(bullet, s)[0] for s in source["bullets"]), default=0)
                print(f"    · [{worst}] {bullet}")

    shared = set(results["FINANCE"].cv["selected_roles"]) & set(results["TECH"].cv["selected_roles"])
    print(f"\n{RULE}")
    print(f"Role selection differs: "
          f"{results['FINANCE'].cv['selected_roles'] != results['TECH'].cv['selected_roles']}")
    for role_id in shared:
        fin = next(r for r in results["FINANCE"].cv["roles"] if r["id"] == role_id)
        tech = next(r for r in results["TECH"].cv["roles"] if r["id"] == role_id)
        identical = sum(1 for b in fin["bullets"] if b in tech["bullets"])
        verdict = "PASS" if identical == 0 else "FAIL"
        print(f"{verdict} {role_id}: {identical}/{len(fin['bullets'])} bullets identical "
              "across both versions (want 0 — same role, different listing)")
        if identical:
            ok = False
    if not shared:
        print("No role appears in both versions, so wording cannot be compared "
              "directly — rerun with listings that share a role.")
    return ok


def main() -> int:
    parser = argparse.ArgumentParser(description="Live checks that need a real model.")
    parser.add_argument("--step", choices=["classifier", "tailoring", "all"], default="all")
    args = parser.parse_args()

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set — these steps need a real model.")
        return 1

    bank = load_bank()
    ok = True
    if args.step in ("classifier", "all"):
        ok &= step_classifier(bank)
    if args.step in ("tailoring", "all"):
        ok &= step_tailoring(bank)

    print(f"\n{RULE}\n{'ALL CHECKS PASSED' if ok else 'SOMETHING NEEDS A LOOK — see above'}\n{RULE}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
