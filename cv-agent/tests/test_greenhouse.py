"""Greenhouse end-to-end, against real browser behaviour.

Three fixtures modelled on the shapes Greenhouse actually ships: the modern
job-boards React form, the classic board embedded in an iframe on a company
careers page, and the honeypot/hidden-input patterns both use. Everything here
runs offline — the classifier is stubbed, so the deterministic mapping, the
never-answer rules, uploading, typeahead handling and read-back verification are
all exercised without an API key.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

playwright_api = pytest.importorskip("playwright.sync_api")

from cvagent.apply.browser import launch_chromium  # noqa: E402
from cvagent.apply.forms import application_form_fields, extract_fields, read_values  # noqa: E402
from cvagent.apply.mapper import Assignment, deterministic_pass, map_form  # noqa: E402
from cvagent.apply.profile import build_profile  # noqa: E402
from cvagent.apply.runner import apply_assignment, verify_fills, write_cv_pdf  # noqa: E402
from cvagent.bank import load_bank  # noqa: E402
from cvagent.render import render_cv_page  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"

CV = {
    "summary": "Business development moving from FX into fintech sales.",
    "selected_roles": ["dojo", "leadsparker"],
    "roles": [
        {"id": "universal_partners", "bullets": ["Originated corporate FX mandates."]},
        {"id": "dojo", "bullets": ["Advised merchants on card acceptance."]},
        {"id": "leadsparker", "bullets": ["Closed £10k–£50k+ contracts quickly."]},
    ],
}


@pytest.fixture(scope="session")
def bank():
    return load_bank()


@pytest.fixture(scope="session")
def profile(bank):
    return build_profile(bank, CV)


@pytest.fixture(scope="session")
def cv_pdf(bank, tmp_path_factory):
    path = tmp_path_factory.mktemp("cv") / "cv.pdf"
    return write_cv_pdf(render_cv_page(CV, bank), path)


@pytest.fixture
def browser():
    with playwright_api.sync_playwright() as p:
        try:
            instance = launch_chromium(p)
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        yield instance
        instance.close()


def open_form(browser, filename):
    page = browser.new_page()
    page.goto((FIXTURES / filename).as_uri())
    page.wait_for_timeout(300)
    return page


# --------------------------------------------------------------------------
# Modern job-boards.greenhouse.io shape
# --------------------------------------------------------------------------

@pytest.fixture
def modern(browser):
    page = open_form(browser, "greenhouse_modern.html")
    return page, {f.id: f for f in extract_fields(page)}


def test_hidden_file_input_is_still_found(modern):
    """The regression that matters most: Greenhouse hides the real file input
    behind an Attach button, so a visibility filter drops the CV upload."""
    _, fields = modern
    assert "resume" in fields
    assert fields["resume"].kind == "file"


def test_honeypot_is_still_excluded(modern):
    """...while an off-screen text trap stays excluded."""
    _, fields = modern
    assert "hp_email" not in fields


def test_typeahead_location_is_detected(modern):
    _, fields = modern
    assert fields["candidate-location"].combobox is True
    assert fields["first_name"].combobox is False


def test_eeo_questions_never_reach_the_classifier(modern, profile):
    page, fields = modern
    matched, remaining = deterministic_pass(list(fields.values()), profile)

    for field_id in ("gender", "demo_ethnicity", "veteran_status", "disability_status"):
        assert matched[fields[field_id].key].action == "skip", f"{field_id} must not be answered"
    remaining_ids = {f.id for f in remaining}
    assert not remaining_ids & {"gender", "demo_ethnicity", "veteran_status", "disability_status"}


def test_bland_labelled_eeo_field_is_caught_by_section_copy(modern, profile):
    """The ethnicity dropdown is labelled only "Please select" — the disclosure
    is in the surrounding section text, which is where the match has to come from."""
    _, fields = modern
    matched, _ = deterministic_pass([fields["demo_ethnicity"]], profile)
    assert matched[fields["demo_ethnicity"].key].action == "skip"
    assert "ethnicity" in matched[fields["demo_ethnicity"].key].note.lower()


def test_sensitive_non_eeo_questions_are_also_left_alone(modern, profile):
    _, fields = modern
    matched, _ = deterministic_pass(
        [fields["question_30538428"], fields["question_30538429"], fields["gdpr_consent"]], profile
    )
    for field_id in ("question_30538428", "question_30538429", "gdpr_consent"):
        assignment = matched[fields[field_id].key]
        assert assignment.action == "skip" and assignment.needs_review


def test_contact_fields_fill_from_the_bank(modern, profile):
    _, fields = modern
    matched, _ = deterministic_pass(list(fields.values()), profile)
    assert matched[fields["first_name"].key].value == "Zac"
    assert matched[fields["phone"].key].value == "07867860977"
    assert matched[fields["question_30538425"].key].value.startswith("https://www.linkedin.com/in/")


def test_only_judgement_fields_are_left_for_the_model(modern, profile):
    _, fields = modern
    _, remaining = deterministic_pass(list(fields.values()), profile)
    # The open question and the work-authorisation dropdown genuinely need reading;
    # the cover-letter upload is ambiguous. Nothing else should cost a model call.
    # Location matches a deterministic rule and is filled via the typeahead path;
    # only the open question, the work-authorisation dropdown and the ambiguous
    # cover-letter upload should cost a model call.
    assert {f.id for f in remaining} == {
        "question_30538426", "question_30538427", "cover_letter",
    }


def test_no_llm_mode_flags_instead_of_guessing(modern, profile):
    _, fields = modern
    assignments = map_form(list(fields.values()), profile, CV, "listing", use_llm=False)
    unresolved = [a for a in assignments.values() if a.action == "skip"]
    assert unresolved and all(a.needs_review for a in unresolved)


def test_upload_attaches_to_the_hidden_input(modern, cv_pdf):
    page, fields = modern
    status = apply_assignment(page, fields["resume"], Assignment("k", "upload_cv"), cv_pdf)
    assert "uploaded" in status
    assert read_values(page)[fields["resume"].key] == "cv.pdf"


def test_typeahead_commits_a_suggestion(modern, cv_pdf):
    page, fields = modern
    status = apply_assignment(
        page, fields["candidate-location"], Assignment("k", "fill", "London"), cv_pdf
    )
    assert "picked" in status
    assert page.locator("#candidate-location").get_attribute("data-committed") == "true"
    assert read_values(page)[fields["candidate-location"].key] == "London, UK"


def test_readback_catches_a_required_field_left_blank(modern, cv_pdf):
    page, fields = modern
    by_key = {f.key: f for f in fields.values()}
    assignments = {
        fields["first_name"].key: Assignment(fields["first_name"].key, "fill", "Zac"),
        fields["phone"].key: Assignment(fields["phone"].key, "skip", "", "none", "low", "no phone"),
    }
    for key, assignment in assignments.items():
        apply_assignment(page, by_key[key], assignment, cv_pdf)

    problems = verify_fills(by_key, assignments, read_values(page))
    assert any("REQUIRED but left blank" in p for p in problems)
    assert not any("First Name" in p for p in problems)


def test_readback_catches_an_upload_that_never_attached(modern):
    _, fields = modern
    by_key = {f.key: f for f in fields.values()}
    key = fields["resume"].key
    problems = verify_fills(by_key, {key: Assignment(key, "upload_cv")}, {key: ""})
    assert any("did not attach" in p for p in problems)


# --------------------------------------------------------------------------
# Classic board embedded in an iframe on a company careers page
# --------------------------------------------------------------------------

@pytest.fixture
def embedded(browser):
    page = open_form(browser, "greenhouse_embed.html")
    page.wait_for_timeout(500)
    return page, {f.id: f for f in extract_fields(page)}


def test_fields_are_found_inside_the_embed_iframe(embedded):
    _, fields = embedded
    assert {"first_name", "last_name", "email", "resume"} <= set(fields)
    assert fields["first_name"].frame_index != 0, "form lives in the iframe, not the host page"


def test_host_page_newsletter_form_is_excluded_from_the_application(embedded, profile):
    """A careers page carries its own forms. The newsletter signup is scraped but
    must not be treated as part of the application — its email box looks exactly
    like the applicant email box to a label matcher."""
    page, fields = embedded
    assert "nl_email" in fields, "the newsletter input is on the page and should be scraped"

    application, off_form = application_form_fields(list(fields.values()))
    application_ids = {f.id for f in application}
    assert "nl_email" not in application_ids
    assert "nl_email" in {f.id for f in off_form}
    assert {"first_name", "email", "resume"} <= application_ids


def test_visibility_hidden_upload_in_the_classic_form_is_found(embedded):
    _, fields = embedded
    assert fields["resume"].kind == "file"


def test_question_in_a_sibling_div_is_read(embedded):
    _, fields = embedded
    field = fields["job_application_answers_attributes_1_text_value"]
    assert "deal you closed" in " ".join(field.text).lower()
    assert field.required, "aria-required must count as required"


def test_sibling_question_becomes_the_label_not_the_whole_block(embedded):
    """Classic Greenhouse renders custom questions as a div, not a <label>. Falling
    back to the surrounding block splices the question together with the option
    list, which is what the reviewer then has to read."""
    _, fields = embedded
    dropdown = fields["job_application_answers_attributes_2_answer_selected"]
    assert dropdown.label == "How did you hear about this role?"
    assert "LinkedIn" not in dropdown.label, "options must not leak into the label"

    textarea = fields["job_application_answers_attributes_1_text_value"]
    assert textarea.label.startswith("Tell us about a deal you closed")


def test_upload_label_excludes_the_attach_button_text(embedded):
    _, fields = embedded
    assert fields["resume"].label.strip().rstrip("*").strip() == "Resume/CV"


def test_filling_across_frames_actually_lands(embedded, profile, cv_pdf):
    page, fields = embedded
    by_key = {f.key: f for f in fields.values()}
    matched, _ = deterministic_pass(list(fields.values()), profile)
    for key, assignment in matched.items():
        apply_assignment(page, by_key[key], assignment, cv_pdf)

    values = read_values(page)
    assert values[fields["first_name"].key] == "Zac"
    assert values[fields["email"].key] == "zacldevine@gmail.com"
    assert not verify_fills(by_key, matched, values)


def test_scraped_fields_serialise_for_debugging(modern):
    """--dump-fields has to survive json.dumps for a real posting to be debuggable."""
    from dataclasses import asdict

    _, fields = modern
    assert json.dumps([asdict(f) for f in fields.values()])


# --------------------------------------------------------------------------
# Submit-button discovery — the least-exercised line in the codebase
# --------------------------------------------------------------------------

def _click_submit(page):
    from cvagent.apply.runner import SUBMIT_SELECTORS

    for selector in SUBMIT_SELECTORS:
        button = page.locator(selector).first
        if button.count():
            button.click()
            page.wait_for_timeout(400)
            return True
    return False


def test_blocked_submit_is_not_reported_as_sent(browser):
    """A required field left empty makes the browser block submission and fire no
    event at all. Reporting success here would tell Zac an application went in
    when it did not — the worst failure this tool could have."""
    from cvagent.apply.runner import submission_landed

    page = open_form(browser, "greenhouse_submit_rehearsal.html")
    before_url, before_count = page.url, len(extract_fields(page))

    assert _click_submit(page), "no submit control matched"
    assert page.locator("#rehearsal-received").count() == 0, "form should not have submitted"
    assert submission_landed(page, before_url, before_count) is False


def test_submit_lands_once_the_required_fields_are_filled(browser, cv_pdf):
    """Rehearses the real click path offline. The live submit sends an application
    to a real employer, so this is the only place it can be exercised safely."""
    from cvagent.apply.runner import submission_landed

    page = open_form(browser, "greenhouse_submit_rehearsal.html")
    for field in extract_fields(page):
        if not field.required:
            continue
        locator = page.locator(field.selector).first
        if field.kind == "file":
            locator.set_input_files(str(cv_pdf))
        elif field.kind == "select":
            locator.select_option(index=1)
        elif field.kind == "checkbox":
            locator.check()
        else:
            # Type-appropriate values: HTML5 validation rejects a malformed email
            # or url just as firmly as an empty one, and blocks submission the
            # same silent way.
            locator.fill({
                "email": "zac@example.com",
                "url": "https://example.com",
                "tel": "07867860977",
            }.get(field.kind, "London, UK" if field.combobox else "filled"))

    before_url, before_count = page.url, len(extract_fields(page))
    assert _click_submit(page)
    assert page.locator("#rehearsal-received").count() == 1
    assert submission_landed(page, before_url, before_count) is True


def test_submit_button_is_found_on_the_classic_input_style_form(browser):
    """The classic board uses <input type=submit value="Submit Application">, not
    a <button> — a selector list that only looks for buttons silently finds nothing."""
    from cvagent.apply.runner import SUBMIT_SELECTORS

    page = open_form(browser, "greenhouse_embed_inner.html")
    assert any(page.locator(s).first.count() for s in SUBMIT_SELECTORS)
