"""Loads Zac's master experience bank — the single source of truth for both stages."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
BANK_PATH = ROOT / "data" / "experience_bank.json"
PROMPT_PATH = ROOT / "prompts" / "tailor_system.md"

ALWAYS_ID = "universal_partners"


def load_bank(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or BANK_PATH).read_text())


def load_system_prompt(path: Path | None = None) -> str:
    return (path or PROMPT_PATH).read_text()


def all_roles(bank: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Every tailorable role keyed by id, including the always-included one."""
    roles = {bank["always_included"]["id"]: bank["always_included"]}
    for role in bank["pool"]:
        roles[role["id"]] = role
    return roles


def pool_ids(bank: dict[str, Any]) -> list[str]:
    return [r["id"] for r in bank["pool"]]


def format_bank_for_prompt(bank: dict[str, Any]) -> str:
    """Render the bank as the text block the model reads."""
    lines: list[str] = ["ALWAYS INCLUDED (slot 1, most recent):"]

    def block(role: dict[str, Any]) -> list[str]:
        head = f'id: {role["id"]} — {role["title"]}, {role["company"]}, {role["start"]}–{role["end"]}'
        if role.get("location"):
            head += f' ({role["location"]})'
        return [head] + [f"  - {b}" for b in role["bullets"]]

    lines += block(bank["always_included"])
    lines.append("")
    lines.append("POOL (select exactly TWO of these three):")
    for role in bank["pool"]:
        lines += block(role)
        lines.append("")

    lines.append("EDUCATION (verbatim, never tailored):")
    for edu in bank["education"]:
        lines.append(
            f'  - {edu["qualification"]}, {edu["institution"]}, '
            f'{edu["start"]}–{edu["end"]}, grade {edu["grade"]}'
            + (f'. {edu["detail"]}' if edu.get("detail") else "")
        )
    return "\n".join(lines)
