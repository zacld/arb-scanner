#!/usr/bin/env python3
"""Live check that the rewording bug is actually fixed.

Runs the same experience bank against a finance-heavy and a tech-heavy listing and
prints both versions side by side with the source bullets, so it is obvious whether
the *wording* moved or only the role selection did. Needs ANTHROPIC_API_KEY.

    python scripts/compare_tailoring.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from cvagent.bank import all_roles, load_bank  # noqa: E402
from cvagent.tailor import tailor_cv  # noqa: E402
from cvagent.verify import longest_shared_run  # noqa: E402

LISTINGS = {
    "FINANCE": ROOT / "tests" / "fixtures" / "listing_finance.txt",
    "TECH": ROOT / "tests" / "fixtures" / "listing_tech.txt",
}


def worst_run(bullet: str, sources: list[str]) -> int:
    return max((longest_shared_run(bullet, s)[0] for s in sources), default=0)


def main() -> int:
    bank = load_bank()
    roles = all_roles(bank)
    results = {}

    for label, path in LISTINGS.items():
        print(f"Tailoring against the {label} listing…")
        results[label] = tailor_cv(path.read_text(), "auto", bank=bank)

    for label, result in results.items():
        cv = result.cv
        check = result.report
        print(f"\n{'=' * 78}\n{label}: {cv['category_read']} — roles kept: {', '.join(cv['selected_roles'])}")
        print(f"rounds: {result.rounds} | verified: {check.ok} | longest shared run: {check.max_run}")
        print(f"keywords mirrored: {', '.join(cv['listing_keywords'])}\n")
        for entry in cv["roles"]:
            source = roles[entry["id"]]
            print(f"  {source['title']} — {source['company']}")
            for bullet in entry["bullets"]:
                print(f"    · {bullet}   [shared run {worst_run(bullet, source['bullets'])}]")
            print()

    shared = set(results["FINANCE"].cv["selected_roles"]) & set(results["TECH"].cv["selected_roles"])
    print("=" * 78)
    print(f"Role selection differs: {results['FINANCE'].cv['selected_roles'] != results['TECH'].cv['selected_roles']}")
    for role_id in shared:
        fin = next(r for r in results["FINANCE"].cv["roles"] if r["id"] == role_id)
        tech = next(r for r in results["TECH"].cv["roles"] if r["id"] == role_id)
        identical = sum(1 for b in fin["bullets"] if b in tech["bullets"])
        print(f"{role_id}: {identical}/{len(fin['bullets'])} bullets identical across both versions "
              f"(want 0 — same role, different listing, different wording)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
