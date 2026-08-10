"""Programmatic check that the model actually rewrote the bullets.

The Stage 1 bug was the model handing back the source LinkedIn text with a couple
of words swapped. The system prompt asks for a genuine rewrite and a self-check,
but a model grading its own homework is not evidence — so every generated CV is
re-checked here, and violations are sent back for a repair round.

The rule: a candidate bullet may share at most MAX_RUN consecutive *ordinary*
words with any source bullet from the same role. Proper nouns, product names and
numeric facts are exempt, because those must be preserved exactly and would
otherwise register as false violations.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

MAX_RUN = 3

# Words that must survive a rewrite verbatim, so they don't count toward a run.
EXEMPT_WORDS = {
    "fx", "hubspot", "salesforce", "crm", "dojo", "leadsparker", "gro", "team",
    "universal", "partners", "b2b", "smes", "sme", "it", "uk", "tier",
}

_WORD_RE = re.compile(r"[a-z0-9£%+\-–]+")


def tokenize(text: str) -> list[str]:
    return _WORD_RE.findall(text.lower())


def _is_exempt(token: str) -> bool:
    return token in EXEMPT_WORDS or any(ch.isdigit() for ch in token)


def _score(run: Iterable[str]) -> int:
    """Length of a shared run, counting only non-exempt words."""
    return sum(0 if _is_exempt(t) else 1 for t in run)


def longest_shared_run(candidate: str, source: str) -> tuple[int, str]:
    """Longest common consecutive token run, scored by non-exempt word count."""
    a, b = tokenize(candidate), tokenize(source)
    best_score, best_run = 0, []
    # Classic longest-common-substring DP over token sequences; the bullets are
    # one line each, so the quadratic table is trivially small.
    lengths = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            if a[i - 1] != b[j - 1]:
                continue
            lengths[i][j] = lengths[i - 1][j - 1] + 1
            run = a[i - lengths[i][j]: i]
            score = _score(run)
            if score > best_score:
                best_score, best_run = score, run
    return best_score, " ".join(best_run)


@dataclass
class Violation:
    kind: str
    role_id: str
    bullet: str
    detail: str

    def as_instruction(self) -> str:
        return f"[{self.role_id}] {self.detail}\n    offending bullet: {self.bullet}"


@dataclass
class Report:
    violations: list[Violation] = field(default_factory=list)
    max_run: int = 0

    @property
    def ok(self) -> bool:
        return not self.violations


def _numbers(text: str) -> set[str]:
    return {t for t in tokenize(text) if any(ch.isdigit() for ch in t)}


def check_cv(result: dict[str, Any], bank: dict[str, Any]) -> Report:
    """Verify a tailored CV against the experience bank.

    Catches the copy-paste regression (shared runs), fabricated numbers,
    repeated opening verbs, and structural rule breaks.
    """
    from .bank import ALWAYS_ID, all_roles, pool_ids

    report = Report()
    roles = all_roles(bank)
    generated = result.get("roles", [])
    ids = [r.get("id") for r in generated]

    if not ids or ids[0] != ALWAYS_ID:
        report.violations.append(Violation(
            "structure", ids[0] if ids else "?", "",
            f"Universal Partners must be the first role; got {ids!r}.",
        ))
    if len(ids) != 3:
        report.violations.append(Violation(
            "structure", "-", "",
            f"CV must contain exactly 3 roles (Universal Partners + 2 pool roles); got {len(ids)}.",
        ))
    selected_pool = [i for i in ids if i in pool_ids(bank)]
    if len(set(selected_pool)) != 2:
        report.violations.append(Violation(
            "structure", "-", "",
            f"Select exactly two of {pool_ids(bank)}; got {selected_pool!r}.",
        ))

    openers: dict[str, str] = {}
    for role in generated:
        role_id = role.get("id", "?")
        source = roles.get(role_id)
        if source is None:
            report.violations.append(Violation(
                "structure", role_id, "", f"Unknown role id {role_id!r}."))
            continue

        source_numbers = _numbers(" ".join(source["bullets"]))
        allowed = len(source["bullets"]) + 1
        bullets = role.get("bullets", [])
        if len(bullets) > allowed:
            report.violations.append(Violation(
                "structure", role_id, "",
                f"{len(bullets)} bullets for a role with {len(source['bullets'])} source bullets — do not pad.",
            ))

        for bullet in bullets:
            worst_score, worst_phrase = 0, ""
            for src in source["bullets"]:
                score, phrase = longest_shared_run(bullet, src)
                if score > worst_score:
                    worst_score, worst_phrase = score, phrase
            report.max_run = max(report.max_run, worst_score)
            if worst_score > MAX_RUN:
                report.violations.append(Violation(
                    "copied", role_id, bullet,
                    f'Reuses {worst_score} consecutive words from the source ("{worst_phrase}"). '
                    "Rebuild this bullet from the underlying fact instead of paraphrasing.",
                ))

            invented = _numbers(bullet) - source_numbers
            if invented:
                report.violations.append(Violation(
                    "invented", role_id, bullet,
                    f"Contains figures not present in the source bullets: {sorted(invented)}.",
                ))

            tokens = tokenize(bullet)
            if tokens:
                first = tokens[0]
                if first in openers:
                    report.violations.append(Violation(
                        "opener", role_id, bullet,
                        f'Opens with "{first}", already used by: {openers[first]}',
                    ))
                else:
                    openers[first] = bullet

    return report
