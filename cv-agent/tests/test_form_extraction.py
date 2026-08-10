"""Form scraping against a real browser and an ATS-shaped form.

This is the part of Stage 2 most likely to break silently on a live site, so it is
tested against actual Chromium rather than a parsed string. Skipped automatically
where Playwright or a browser isn't available.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

playwright_api = pytest.importorskip("playwright.sync_api")

from cvagent.apply.browser import launch_chromium
from cvagent.apply.forms import extract_fields  # noqa: E402
from cvagent.apply.mapper import deterministic_pass  # noqa: E402
from cvagent.apply.profile import build_profile  # noqa: E402
from cvagent.bank import load_bank  # noqa: E402

FORM = Path(__file__).parent / "fixtures" / "mock_greenhouse.html"

CV = {
    "summary": "Payments-focused business development.",
    "selected_roles": ["dojo", "groteam"],
    "roles": [
        {"id": "universal_partners", "bullets": ["Originated corporate FX mandates."]},
        {"id": "dojo", "bullets": ["Advised merchants on card acceptance."]},
        {"id": "groteam", "bullets": ["Scoped CRM rollouts for SME clients."]},
    ],
}


@pytest.fixture(scope="module")
def fields():
    with playwright_api.sync_playwright() as p:
        try:
            browser = launch_chromium(p)
        except Exception as exc:  # no browser binary in this environment
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        page.goto(FORM.as_uri())
        found = extract_fields(page)
        browser.close()
    return {f.id: f for f in found}


def test_finds_every_visible_control(fields):
    assert set(fields) == {
        "first_name", "last_name", "email", "phone", "resume",
        "linkedin", "q_why", "q_auth", "privacy",
    }


def test_ignores_hidden_and_display_none_controls(fields):
    assert "honeypot" not in fields, "a hidden honeypot field must never be filled"
    assert not any(f.name == "authenticity_token" for f in fields.values())


def test_reads_a_question_with_no_label_element(fields):
    """Several ATS put the question in a sibling div — the commonest scrape failure."""
    assert "legally authorised to work" in fields["q_auth"].label.lower() or any(
        "legally authorised to work" in t.lower() for t in fields["q_auth"].text
    )


def test_captures_select_options_and_maxlength(fields):
    labels = [o["label"] for o in fields["q_auth"].options]
    assert "Yes" in labels and "No" in labels
    assert fields["q_why"].maxlength == "1200"


def test_required_flag_survives(fields):
    assert fields["email"].required and not fields["linkedin"].required


def test_each_field_gets_a_unique_reusable_selector(fields):
    selectors = [f.selector for f in fields.values()]
    assert len(set(selectors)) == len(selectors)


def test_end_to_end_mapping_leaves_only_judgement_calls(fields):
    profile = build_profile(load_bank(), CV)
    matched, remaining = deterministic_pass(list(fields.values()), profile)

    assert matched[fields["first_name"].key].value == "Zac"
    assert matched[fields["email"].key].value == "zacldevine@gmail.com"
    assert matched[fields["resume"].key].action == "upload_cv"
    # Phone is empty in the bank: flagged, never faked.
    assert matched[fields["phone"].key].action == "skip"

    # Only the genuinely ambiguous fields should cost a model call.
    assert {f.id for f in remaining} == {"q_why", "q_auth", "privacy"}
