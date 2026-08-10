"""Stage 1: turn a pasted job listing into a tailored CV."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any

import anthropic

from .bank import ALWAYS_ID, format_bank_for_prompt, load_bank, load_system_prompt, pool_ids
from .verify import MAX_RUN, Report, check_cv

DEFAULT_MODEL = os.environ.get("CV_AGENT_MODEL", "claude-opus-5")
MAX_REPAIR_ROUNDS = 2

CV_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "category_read": {"type": "string"},
        "selected_roles": {"type": "array", "items": {"type": "string"}},
        "selection_reasoning": {"type": "string"},
        "listing_keywords": {"type": "array", "items": {"type": "string"}},
        "summary": {"type": "string"},
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "bullets": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "bullets"],
                "additionalProperties": False,
            },
        },
        "self_check": {
            "type": "object",
            "properties": {
                "max_source_run": {"type": "integer"},
                "distinct_openers": {"type": "boolean"},
                "no_invented_facts": {"type": "boolean"},
                "notes": {"type": "string"},
            },
            "required": ["max_source_run", "distinct_openers", "no_invented_facts", "notes"],
            "additionalProperties": False,
        },
    },
    "required": [
        "category_read", "selected_roles", "selection_reasoning",
        "listing_keywords", "summary", "roles", "self_check",
    ],
    "additionalProperties": False,
}

HINT_TEXT = {
    "auto": "No steer given — read the listing yourself and pick the two most relevant pool roles.",
    "finance": "Zac leans finance for this one, but still justify the two roles from the listing itself.",
    "tech": "Zac leans tech for this one, but still justify the two roles from the listing itself.",
    "hybrid": "Zac reads this as a blended finance/tech role; weigh both sides.",
}


@dataclass
class TailorResult:
    cv: dict[str, Any]
    report: Report
    rounds: int
    model: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "cv": self.cv,
            "rounds": self.rounds,
            "model": self.model,
            "verification": {
                "passed": self.report.ok,
                "max_source_run": self.report.max_run,
                "limit": MAX_RUN,
                "violations": [
                    {"kind": v.kind, "role": v.role_id, "bullet": v.bullet, "detail": v.detail}
                    for v in self.report.violations
                ],
            },
        }


def build_user_message(listing: str, hint: str, bank: dict[str, Any]) -> str:
    return (
        "JOB LISTING (pasted verbatim by Zac):\n"
        "<<<LISTING\n"
        f"{listing.strip()}\n"
        "LISTING\n\n"
        f"CATEGORY HINT: {HINT_TEXT.get(hint, HINT_TEXT['auto'])}\n\n"
        "MASTER EXPERIENCE BANK:\n"
        f"{format_bank_for_prompt(bank)}\n\n"
        f"Always include {ALWAYS_ID} first, then exactly two of {pool_ids(bank)}. "
        "Rewrite every bullet from the underlying fact in this listing's vocabulary."
    )


def build_repair_message(report: Report) -> str:
    items = "\n".join(f"  {i + 1}. {v.as_instruction()}" for i, v in enumerate(report.violations))
    return (
        "An automated verifier checked your CV against the source bank and rejected it. "
        "Your self_check did not catch these:\n\n"
        f"{items}\n\n"
        f"Rebuild every offending bullet from scratch: state the fact to yourself, discard the "
        f"source sentence and its shape entirely, then write a new sentence in the listing's "
        f"vocabulary sharing no more than {MAX_RUN} consecutive ordinary words with the source. "
        "Keep everything that passed unchanged, and return the complete CV JSON again."
    )


def _extract_json(content: list[Any]) -> dict[str, Any]:
    text = next((b.text for b in content if b.type == "text"), None)
    if text is None:
        raise RuntimeError("Model returned no text block — cannot read the CV JSON.")
    return json.loads(text)


def tailor_cv(
    listing: str,
    hint: str = "auto",
    *,
    client: anthropic.Anthropic | None = None,
    bank: dict[str, Any] | None = None,
    model: str = DEFAULT_MODEL,
    max_repair_rounds: int = MAX_REPAIR_ROUNDS,
) -> TailorResult:
    """Generate a tailored CV, then verify and repair it until the rewrite is genuine."""
    if not listing.strip():
        raise ValueError("Paste the job listing text — LinkedIn blocks URL scraping.")

    bank = bank or load_bank()
    client = client or anthropic.Anthropic()
    system = load_system_prompt()

    messages: list[dict[str, Any]] = [
        {"role": "user", "content": build_user_message(listing, hint, bank)}
    ]

    result: dict[str, Any] = {}
    report = Report()
    for round_index in range(max_repair_rounds + 1):
        with client.messages.stream(
            model=model,
            max_tokens=32000,
            system=system,
            messages=messages,
            output_config={
                "effort": "high",
                "format": {"type": "json_schema", "schema": CV_SCHEMA},
            },
        ) as stream:
            response = stream.get_final_message()

        if response.stop_reason == "refusal":
            raise RuntimeError("The model declined this request; check the pasted listing text.")

        result = _extract_json(response.content)
        report = check_cv(result, bank)
        if report.ok or round_index == max_repair_rounds:
            return TailorResult(result, report, round_index + 1, model)

        messages.append({"role": "assistant", "content": response.content})
        messages.append({"role": "user", "content": build_repair_message(report)})

    return TailorResult(result, report, max_repair_rounds + 1, model)
