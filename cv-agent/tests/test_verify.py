"""The rewrite verifier is the guard against the Stage 1 copy-paste bug, so it gets
tested against the exact failure it exists to catch."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cvagent.bank import load_bank  # noqa: E402
from cvagent.verify import check_cv, longest_shared_run  # noqa: E402

SOURCE = "Partnered with business owners to optimise payment systems, improving efficiency and cash flow."


@pytest.fixture
def bank():
    return load_bank()


def base_cv(dojo_bullets, leadsparker_bullets=None):
    return {
        "roles": [
            {"id": "universal_partners", "bullets": [
                "Originated corporate FX mandates across cross-border payment flows.",
                "Built market coverage from scratch, owning outbound pipeline end to end.",
            ]},
            {"id": "dojo", "bullets": dojo_bullets},
            {"id": "leadsparker", "bullets": leadsparker_bullets or [
                "Closed £10k–£50k+ contracts inside one or two conversations.",
                "Ran discovery with founders, holding control of every deal cycle.",
            ]},
        ]
    }


def test_verbatim_copy_is_caught(bank):
    report = check_cv(base_cv([SOURCE]), bank)
    assert not report.ok
    assert any(v.kind == "copied" for v in report.violations)


def test_light_paraphrase_is_caught(bank):
    """The actual bug: a couple of words swapped, sentence shape untouched."""
    paraphrase = "Worked with business owners to improve payment systems, boosting efficiency and cash flow."
    report = check_cv(base_cv([paraphrase]), bank)
    assert not report.ok
    assert any(v.kind == "copied" for v in report.violations)


def test_genuine_rewrite_passes(bank):
    rewrite = "Advised merchants on card acceptance, unlocking faster settlement and healthier working capital."
    report = check_cv(base_cv([rewrite]), bank)
    assert report.ok, [v.detail for v in report.violations]


def test_preserved_proper_nouns_do_not_trigger(bank):
    """Tool names and figures must survive verbatim without registering as copying."""
    score, _ = longest_shared_run(
        "Rolled out HubSpot and Salesforce for SME clients chasing better lead capture.",
        "Partnered with B2B SMEs to assess digital needs and design comprehensive solutions, "
        "including CRM rollouts (HubSpot, Salesforce) and website redesigns.",
    )
    assert score <= 3


def test_invented_numbers_are_caught(bank):
    report = check_cv(base_cv(["Lifted merchant conversion by 42% across the payments book."]), bank)
    assert any(v.kind == "invented" for v in report.violations)


def test_repeated_openers_are_caught(bank):
    report = check_cv(base_cv(
        ["Advised merchants on card acceptance, unlocking faster settlement."],
        ["Advised founders on pricing, shortening every negotiation."],
    ), bank)
    assert any(v.kind == "opener" for v in report.violations)


def test_wrong_role_count_is_caught(bank):
    cv = base_cv(["Advised merchants on card acceptance, unlocking faster settlement."])
    cv["roles"] = cv["roles"][:2]
    report = check_cv(cv, bank)
    assert any(v.kind == "structure" for v in report.violations)


def test_universal_partners_must_lead(bank):
    cv = base_cv(["Advised merchants on card acceptance, unlocking faster settlement."])
    cv["roles"].reverse()
    report = check_cv(cv, bank)
    assert any(v.kind == "structure" for v in report.violations)


def test_all_three_pool_roles_is_rejected(bank):
    cv = base_cv(["Advised merchants on card acceptance, unlocking faster settlement."])
    cv["roles"].append({"id": "groteam", "bullets": ["Scoped CRM rollouts for SME clients."]})
    report = check_cv(cv, bank)
    assert any(v.kind == "structure" for v in report.violations)
