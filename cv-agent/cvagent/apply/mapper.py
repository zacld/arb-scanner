"""Decides what goes in each form field.

Two tiers, deliberately. Standard contact fields are matched with regexes — they
are the bulk of every form, they never change, and spending a model call on
"First Name" would be silly and non-deterministic. Everything left over goes to
Claude in a single classification call: unfamiliar labels, dropdowns whose option
wording varies per employer, and the free-text questions ("why do you want to
work here?") that need a written answer rather than a lookup.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

import anthropic

from .forms import FormField
from .profile import DETERMINISTIC_RULES, NEVER_ANSWER_PATTERNS

MODEL = os.environ.get("CV_AGENT_MODEL", "claude-opus-5")

ACTIONS = ["fill", "select", "upload_cv", "skip"]

ASSIGNMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "assignments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string"},
                    "action": {"type": "string", "enum": ACTIONS},
                    "value": {"type": "string"},
                    "source": {"type": "string"},
                    "confidence": {"type": "string", "enum": ["high", "low"]},
                    "note": {"type": "string"},
                },
                "required": ["key", "action", "value", "source", "confidence", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["assignments"],
    "additionalProperties": False,
}

CLASSIFIER_SYSTEM = """\
You map job-application form fields to a candidate's data, and write the free-text
answers a form asks for. You are filling in Zac Devine's application; everything you
write is submitted to a real employer under his name.

For each field, choose one action:
  fill      — type `value` into it (text, textarea, email, tel, url, number, date)
  select    — pick an option; `value` must match one of the field's listed options exactly
  upload_cv — the field wants a CV/resume file upload
  skip      — you cannot answer it honestly, or it needs a human (salary, visa specifics,
              demographic/EEO questions, anything you would have to invent)

Set `source` to `profile:<key>` when the value is copied from the profile, `generated`
when you wrote it, or `none` when skipping.

Rules that matter more than filling every box:
- Never invent a fact. No employers, dates, grades, salary figures, notice periods,
  visa statuses or qualifications that are not in the profile. If it is not there, skip.
- Free-text questions ("why this company?", "tell us about yourself") are written fresh
  from the tailored CV and this specific listing: name what the company actually does,
  and use only real experience from the CV. Two short paragraphs at most, first person,
  plain and specific — no flattery, no filler, no invented enthusiasm for products he
  hasn't used. Respect `maxlength` when present.
- Demographic, diversity, disability and veteran-status questions are always `skip` —
  they are Zac's to answer, not yours.
- Mark `confidence` low whenever a human should look before this is submitted, and say
  why in `note`. A low-confidence guess is fine; a confident wrong answer is not.
"""


@dataclass
class Assignment:
    key: str
    action: str
    value: str = ""
    source: str = ""
    confidence: str = "high"
    note: str = ""

    @property
    def needs_review(self) -> bool:
        return self.confidence == "low" or self.action == "skip"


def _haystack(field: FormField) -> str:
    return " ".join([field.label, field.name, field.id]).lower()


def _never_answer(field: FormField) -> str | None:
    """Whether this field is one only Zac may answer. Checks the full question text.

    Matched against every descriptive string, not just the shortest label: a
    Greenhouse EEO block often carries the disclosure in the surrounding copy and
    labels the control something bland like "Please select".
    """
    haystack = " ".join([*field.text, field.name, field.id]).lower()
    for pattern, description in NEVER_ANSWER_PATTERNS:
        if re.search(pattern, haystack):
            return description
    return None


def _is_cv_upload(field: FormField) -> bool:
    if field.kind != "file":
        return False
    return bool(re.search(r"resume|cv\b|curriculum", _haystack(field)))


def deterministic_pass(
    fields: list[FormField], profile: dict[str, str]
) -> tuple[dict[str, Assignment], list[FormField]]:
    """Match the standard contact fields; return what matched and what is left."""
    matched: dict[str, Assignment] = {}
    remaining: list[FormField] = []

    for field in fields:
        # Checked first, ahead of everything: no model call, no chance of an
        # answer. A CV upload is exempt — "disability" in an employer's boilerplate
        # must not stop the resume attaching.
        if not _is_cv_upload(field):
            reserved = _never_answer(field)
            if reserved:
                matched[field.key] = Assignment(
                    field.key, "skip", "", "none", "low",
                    f"Left for Zac: {reserved}.",
                )
                continue

        if _is_cv_upload(field):
            matched[field.key] = Assignment(field.key, "upload_cv", source="generated_pdf")
            continue
        if field.kind in {"file", "checkbox", "radio"} or field.options:
            # Uploads we don't recognise, consent boxes and any option-based field
            # carry employer-specific wording — let the classifier read them.
            remaining.append(field)
            continue

        haystack = _haystack(field)
        for pattern, profile_key in DETERMINISTIC_RULES:
            if not re.search(pattern, haystack):
                continue
            value = profile.get(profile_key, "")
            if value:
                matched[field.key] = Assignment(
                    field.key, "fill", value, f"profile:{profile_key}"
                )
            else:
                matched[field.key] = Assignment(
                    field.key, "skip", "", "none", "low",
                    f"Profile has no {profile_key} — add it to experience_bank.json.",
                )
            break
        else:
            remaining.append(field)

    return matched, remaining


def classify_fields(
    fields: list[FormField],
    profile: dict[str, str],
    cv: dict[str, Any],
    listing: str,
    *,
    client: anthropic.Anthropic | None = None,
    model: str = MODEL,
) -> dict[str, Assignment]:
    """Ask Claude to handle the fields the regexes could not."""
    if not fields:
        return {}

    client = client or anthropic.Anthropic()
    catalogue = {
        key: (value if len(value) < 300 else value[:300] + " …")
        for key, value in profile.items()
    }
    user = (
        "JOB LISTING:\n<<<LISTING\n"
        f"{listing.strip()[:8000]}\nLISTING\n\n"
        f"TAILORED CV (already written for this listing):\n{json.dumps(cv, indent=2)}\n\n"
        f"PROFILE VALUES AVAILABLE:\n{json.dumps(catalogue, indent=2)}\n\n"
        "FORM FIELDS TO RESOLVE:\n"
        f"{json.dumps([f.prompt_view() for f in fields], indent=2)}\n\n"
        "Return one assignment per field key above — no more, no fewer."
    )

    with client.messages.stream(
        model=model,
        max_tokens=32000,
        system=CLASSIFIER_SYSTEM,
        messages=[{"role": "user", "content": user}],
        output_config={
            "effort": "high",
            "format": {"type": "json_schema", "schema": ASSIGNMENT_SCHEMA},
        },
    ) as stream:
        response = stream.get_final_message()

    if response.stop_reason == "refusal":
        raise RuntimeError("The model declined to classify this form.")

    text = next((b.text for b in response.content if b.type == "text"), "{}")
    payload = json.loads(text)

    valid_keys = {f.key for f in fields}
    by_key = {f.key: f for f in fields}
    assignments: dict[str, Assignment] = {}
    for item in payload.get("assignments", []):
        assignment = Assignment(**item)
        if assignment.key not in valid_keys:
            continue  # model hallucinated a field that isn't on the page
        field = by_key[assignment.key]

        if assignment.action not in ACTIONS:
            assignment = Assignment(
                assignment.key, "skip", "", "none", "low",
                f"Classifier returned an unusable action {assignment.action!r}.",
            )

        if assignment.action == "select" and field.options:
            labels = {o["label"] for o in field.options}
            if assignment.value not in labels:
                assignment.confidence = "low"
                assignment.note = (
                    f'"{assignment.value}" is not one of this dropdown\'s options. '
                    + assignment.note
                )

        # Setting a value programmatically bypasses the browser's own maxlength
        # enforcement, so an over-long answer sails into the field and the form
        # rejects it on submit. Cut it here and say so rather than discovering it
        # at the submit step.
        limit = int(field.maxlength) if field.maxlength.isdigit() else 0
        if limit and len(assignment.value) > limit:
            assignment.value = assignment.value[:limit].rstrip()
            assignment.confidence = "low"
            assignment.note = (
                f"Answer exceeded the field's {limit}-character limit and was cut — "
                "read it before submitting. " + assignment.note
            )

        assignments[assignment.key] = assignment

    for field in fields:  # anything the model silently dropped
        assignments.setdefault(
            field.key,
            Assignment(field.key, "skip", "", "none", "low", "Classifier returned no answer."),
        )
    return assignments


def map_form(
    fields: list[FormField],
    profile: dict[str, str],
    cv: dict[str, Any],
    listing: str,
    *,
    client: anthropic.Anthropic | None = None,
    use_llm: bool = True,
) -> dict[str, Assignment]:
    """Map every field. With use_llm=False the judgement fields are flagged rather
    than answered — lets the scraper be exercised against a real posting with no
    API key and no spend, which is how you debug a new employer's form."""
    matched, remaining = deterministic_pass(fields, profile)
    if use_llm:
        matched.update(classify_fields(remaining, profile, cv, listing, client=client))
    else:
        for field in remaining:
            matched.setdefault(field.key, Assignment(
                field.key, "skip", "", "none", "low",
                "Needs the classifier (running with --no-llm).",
            ))
    return matched
