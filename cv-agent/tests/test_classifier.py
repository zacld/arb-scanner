"""The classifier path, exercised with a stubbed client.

This is the code that runs on the first live call, and until now nothing
executed it — a typo here would surface as a crash mid-way through a paid run
against a real posting. These tests cover the response handling: what happens
when the model returns a key that isn't on the page, drops a field, picks a
dropdown option that doesn't exist, writes past a character limit, or returns an
action the code doesn't understand.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cvagent.apply.forms import FormField  # noqa: E402
from cvagent.apply.mapper import classify_fields  # noqa: E402

CV = {"summary": "Payments-focused business development.", "roles": []}
PROFILE = {"full_name": "Zac Devine", "cv_plain_text": "x" * 900}


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    def __init__(self, payload, stop_reason="end_turn"):
        self.content = [_Block(json.dumps(payload))]
        self.stop_reason = stop_reason


class _Stream:
    def __init__(self, response):
        self._response = response

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self._response


class FakeClient:
    def __init__(self, payload, stop_reason="end_turn"):
        self._response = _Response(payload, stop_reason)
        self.calls = []

    @property
    def messages(self):
        return self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(self._response)


def assignments(*items):
    return {"assignments": list(items)}


def item(key, action="fill", value="", source="generated", confidence="high", note=""):
    return {
        "key": key, "action": action, "value": value,
        "source": source, "confidence": confidence, "note": note,
    }


WHY = FormField(stamp="f0", kind="textarea", labels=["Why do you want to work here?"],
                text=["Why do you want to work here?"], maxlength="200", frame_index=0)
DROPDOWN = FormField(stamp="f1", kind="select", labels=["How did you hear about us?"],
                     text=["How did you hear about us?"], frame_index=0,
                     options=[{"value": "1", "label": "LinkedIn"},
                              {"value": "2", "label": "Referral"}])


def test_free_text_answer_is_returned(monkeypatch):
    client = FakeClient(assignments(item(WHY.key, "fill", "I have sold payments for three years.")))
    result = classify_fields([WHY], PROFILE, CV, "listing", client=client)
    assert result[WHY.key].value == "I have sold payments for three years."
    assert result[WHY.key].needs_review is False


def test_request_carries_the_fields_and_the_schema():
    client = FakeClient(assignments(item(WHY.key, "fill", "short")))
    classify_fields([WHY], PROFILE, CV, "listing", client=client)
    call = client.calls[0]
    assert "Why do you want to work here?" in call["messages"][0]["content"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert call["system"].startswith("You map job-application form fields")


def test_over_long_answer_is_cut_and_flagged():
    """Programmatic writes bypass the browser's maxlength, so the form would
    reject the submission with no visible cause."""
    client = FakeClient(assignments(item(WHY.key, "fill", "word " * 100)))
    result = classify_fields([WHY], PROFILE, CV, "listing", client=client)
    assert len(result[WHY.key].value) <= 200
    assert result[WHY.key].needs_review
    assert "200-character limit" in result[WHY.key].note


def test_invented_dropdown_option_is_flagged():
    client = FakeClient(assignments(item(DROPDOWN.key, "select", "Careers fair")))
    result = classify_fields([DROPDOWN], PROFILE, CV, "listing", client=client)
    assert result[DROPDOWN.key].needs_review
    assert "not one of this dropdown" in result[DROPDOWN.key].note


def test_valid_dropdown_option_passes():
    client = FakeClient(assignments(item(DROPDOWN.key, "select", "LinkedIn")))
    result = classify_fields([DROPDOWN], PROFILE, CV, "listing", client=client)
    assert result[DROPDOWN.key].needs_review is False


def test_hallucinated_field_is_dropped():
    client = FakeClient(assignments(item("0-nonexistent", "fill", "junk"),
                                    item(WHY.key, "fill", "real")))
    result = classify_fields([WHY], PROFILE, CV, "listing", client=client)
    assert set(result) == {WHY.key}


def test_dropped_field_is_backfilled_as_a_flag_not_a_blank():
    client = FakeClient(assignments())
    result = classify_fields([WHY, DROPDOWN], PROFILE, CV, "listing", client=client)
    assert set(result) == {WHY.key, DROPDOWN.key}
    assert all(a.action == "skip" and a.needs_review for a in result.values())


def test_unusable_action_becomes_a_flag():
    client = FakeClient(assignments(item(WHY.key, "submit_the_form", "go")))
    result = classify_fields([WHY], PROFILE, CV, "listing", client=client)
    assert result[WHY.key].action == "skip"
    assert result[WHY.key].needs_review


def test_refusal_raises_rather_than_filling_blanks():
    client = FakeClient(assignments(), stop_reason="refusal")
    with pytest.raises(RuntimeError):
        classify_fields([WHY], PROFILE, CV, "listing", client=client)


def test_no_fields_means_no_api_call():
    client = FakeClient(assignments())
    assert classify_fields([], PROFILE, CV, "listing", client=client) == {}
    assert client.calls == []


def test_long_profile_values_are_truncated_in_the_prompt():
    """cv_plain_text is the whole CV; sending it uncut wastes context on every field."""
    client = FakeClient(assignments(item(WHY.key, "fill", "x")))
    classify_fields([WHY], PROFILE, CV, "listing", client=client)
    assert "x" * 900 not in client.calls[0]["messages"][0]["content"]
