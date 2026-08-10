"""The repair loop, exercised with a stubbed client so it runs without an API key.

This is the fix for the original bug in one test: when the model hands back
lightly-paraphrased source text, the verifier rejects it and the agent sends it
back with the specific offending bullets rather than accepting the output.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cvagent.bank import load_bank  # noqa: E402
from cvagent.tailor import build_repair_message, tailor_cv  # noqa: E402
from cvagent.verify import check_cv  # noqa: E402

LAZY_BULLETS = [
    "Worked with business owners to improve payment systems, boosting efficiency and cash flow.",
    "Maintained 100+ outbound calls daily, consistently generating pipeline and driving deal flow.",
]
GOOD_BULLETS = [
    "Advised merchants on card acceptance, unlocking faster settlement and healthier working capital.",
    "Sustained 100+ prospecting dials a day, keeping the funnel stocked at every stage.",
]


def cv_payload(dojo_bullets):
    return {
        "category_read": "fintech sales",
        "selected_roles": ["dojo", "leadsparker"],
        "selection_reasoning": "Payments plus high-velocity B2B closing.",
        "listing_keywords": ["merchant", "settlement"],
        "summary": "Payments-focused business development.",
        "roles": [
            {"id": "universal_partners", "bullets": [
                "Originated corporate FX mandates across cross-border payment flows.",
                "Built market coverage from scratch, owning outbound pipeline end to end.",
            ]},
            {"id": "dojo", "bullets": dojo_bullets},
            {"id": "leadsparker", "bullets": [
                "Closed £10k–£50k+ contracts inside one or two conversations.",
                "Ran discovery with founders, holding control of every deal cycle.",
            ]},
        ],
        "self_check": {
            "max_source_run": 3, "distinct_openers": True,
            "no_invented_facts": True, "notes": "",
        },
    }


class _Block:
    type = "text"

    def __init__(self, text):
        self.text = text


class _Response:
    stop_reason = "end_turn"

    def __init__(self, payload):
        self.content = [_Block(json.dumps(payload))]


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
    """Returns lazy copy-paste first, then a genuine rewrite."""

    def __init__(self, payloads):
        self._payloads = list(payloads)
        self.calls = []

    @property
    def messages(self):
        return self

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _Stream(_Response(self._payloads.pop(0)))


@pytest.fixture
def bank():
    return load_bank()


def test_lazy_first_draft_is_rejected_and_repaired(bank):
    client = FakeClient([cv_payload(LAZY_BULLETS), cv_payload(GOOD_BULLETS)])
    result = tailor_cv("Fintech sales role, merchant settlement.", "auto", client=client, bank=bank)

    assert result.rounds == 2, "the paraphrased draft should have triggered a repair round"
    assert result.report.ok
    assert result.cv["roles"][1]["bullets"] == GOOD_BULLETS

    repair_prompt = client.calls[1]["messages"][-1]["content"]
    assert "improve payment systems" in repair_prompt, "repair must name the offending phrase"


def test_gives_up_cleanly_rather_than_shipping_a_copy(bank):
    client = FakeClient([cv_payload(LAZY_BULLETS)] * 3)
    result = tailor_cv("Fintech sales role.", "auto", client=client, bank=bank, max_repair_rounds=2)

    assert result.rounds == 3
    assert not result.report.ok, "a still-copied CV must be reported as failing, not passed off as clean"
    assert result.to_dict()["verification"]["passed"] is False


def test_clean_first_pass_costs_one_call(bank):
    client = FakeClient([cv_payload(GOOD_BULLETS)])
    result = tailor_cv("Fintech sales role.", "auto", client=client, bank=bank)
    assert result.rounds == 1 and result.report.ok


def test_repair_message_lists_every_violation(bank):
    report = check_cv(cv_payload(LAZY_BULLETS), bank)
    message = build_repair_message(report)
    assert message.count("offending bullet:") == len(report.violations)


def test_empty_listing_is_refused_before_any_api_call(bank):
    client = FakeClient([])
    with pytest.raises(ValueError):
        tailor_cv("   ", "auto", client=client, bank=bank)
    assert client.calls == []
