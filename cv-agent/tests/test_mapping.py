"""Field mapping and rendering, exercised without touching the network or a browser."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cvagent.apply.forms import FormField  # noqa: E402
from cvagent.apply.mapper import deterministic_pass  # noqa: E402
from cvagent.apply.profile import build_profile  # noqa: E402
from cvagent.bank import load_bank  # noqa: E402
from cvagent.render import render_cv_page, render_cv_text  # noqa: E402

CV = {
    "summary": "Business development professional moving from FX into fintech sales.",
    "selected_roles": ["dojo", "leadsparker"],
    "roles": [
        {"id": "universal_partners", "bullets": ["Originated corporate FX mandates."]},
        {"id": "dojo", "bullets": ["Advised merchants on card acceptance."]},
        {"id": "leadsparker", "bullets": ["Closed £10k–£50k+ contracts quickly."]},
    ],
}


@pytest.fixture
def bank():
    return load_bank()


@pytest.fixture
def profile(bank):
    return build_profile(bank, CV)


def field(key, label, kind="text", **kwargs):
    return FormField(stamp=key, kind=kind, text=[label], **kwargs)


def test_standard_contact_fields_resolve_without_a_model_call(profile):
    fields = [
        field("f0", "First Name"),
        field("f1", "Last Name"),
        field("f2", "Email", kind="email"),
        field("f3", "Resume/CV", kind="file"),
    ]
    matched, remaining = deterministic_pass(fields, profile)
    assert remaining == []
    assert matched["0-f0"].value == "Zac"
    assert matched["0-f1"].value == "Devine"
    assert matched["0-f2"].value == "zacldevine@gmail.com"
    assert matched["0-f3"].action == "upload_cv"


def test_missing_profile_value_is_flagged_not_faked(profile):
    """A rule that matches but has no value behind it must flag, never fill blank."""
    stripped = {**profile, "website": ""}
    key = field("f0", "Personal website").key
    matched, _ = deterministic_pass([field("f0", "Personal website")], stripped)
    assert matched[key].action == "skip"
    assert matched[key].needs_review


def test_phone_now_fills_from_the_bank(profile):
    key = field("f0", "Phone", kind="tel").key
    matched, _ = deterministic_pass([field("f0", "Phone", kind="tel")], profile)
    assert matched[key].value == "07867860977"


def test_unfamiliar_and_option_fields_go_to_the_classifier(profile):
    fields = [
        field("f0", "Why do you want to work here?", kind="textarea"),
        FormField(stamp="f1", kind="select", text=["Country"],
                  options=[{"value": "uk", "label": "United Kingdom"}]),
        field("f2", "I accept the privacy policy", kind="checkbox"),
    ]
    matched, remaining = deterministic_pass(fields, profile)
    # The open question and the country dropdown need judgement; the privacy
    # consent is reserved for Zac and never reaches the classifier at all.
    assert {f.key for f in remaining} == {"0-f0", "0-f1"}
    assert matched["0-f2"].action == "skip"


def test_label_prefers_the_question_over_the_surrounding_block():
    noisy = FormField(
        stamp="f0", kind="textarea",
        text=["Why this role?", "Why this role? " + "Long surrounding container text. " * 20],
    )
    assert noisy.label == "Why this role?"


def test_prompt_view_truncates_context_and_lists_options():
    f = FormField(stamp="f0", kind="select", text=["Location"] ,
                  options=[{"value": "a", "label": "London"}, {"value": "b", "label": "Remote"}])
    view = f.prompt_view()
    assert view["options"] == ["London", "Remote"]


def test_profile_carries_tailored_not_generic_content(bank, profile):
    assert profile["tailored_summary"] == CV["summary"]
    assert "Advised merchants on card acceptance." in profile["cv_plain_text"]


def test_rendered_cv_only_contains_selected_roles(bank):
    page = render_cv_page(CV, bank)
    assert "Dojo" in page and "LeadSparker" in page
    assert "GRO.TEAM" not in page
    assert "University of the West of England" in page


def test_rendered_text_escapes_nothing_it_should_not(bank):
    text = render_cv_text(CV, bank)
    assert "£10k–£50k+" in text
