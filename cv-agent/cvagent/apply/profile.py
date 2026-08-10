"""Flattens the experience bank + a tailored CV into the value catalogue a form draws from."""

from __future__ import annotations

from typing import Any

from ..bank import all_roles
from ..render import render_cv_text


def build_profile(bank: dict[str, Any], cv: dict[str, Any]) -> dict[str, str]:
    """Every value a form might ask for, keyed by a name the classifier can pick."""
    identity = bank["identity"]
    first, _, last = identity["name"].partition(" ")
    roles = all_roles(bank)
    current = bank["always_included"]
    selected = [roles[r["id"]] for r in cv.get("roles", []) if r.get("id") in roles]
    education = bank["education"][0]

    profile: dict[str, str] = {
        "full_name": identity["name"],
        "first_name": first,
        "last_name": last,
        "email": identity["email"],
        "phone": identity.get("phone", ""),
        "linkedin": identity.get("linkedin", ""),
        "website": identity.get("website", ""),
        "location": identity["location"],
        "city": identity.get("city", ""),
        "country": identity.get("country", ""),
        "work_authorisation": identity.get("work_authorisation", ""),
        "current_company": current["company"],
        "current_title": current["title"],
        "tailored_summary": cv.get("summary", ""),
        "cv_plain_text": render_cv_text(cv, bank),
        "school": education["institution"],
        "degree": education["qualification"],
        "degree_grade": education["grade"],
        "education_start_year": education["start"].split()[-1],
        "education_end_year": education["end"].split()[-1],
    }
    for index, role in enumerate(selected, start=1):
        profile[f"role{index}_title"] = role["title"]
        profile[f"role{index}_company"] = role["company"]
        profile[f"role{index}_start"] = role["start"]
        profile[f"role{index}_end"] = role["end"]
    return profile


# Deterministic patterns for the fields every ATS asks in roughly the same words.
# Order matters: the first match wins, so put the specific ones above the generic.
DETERMINISTIC_RULES: list[tuple[str, str]] = [
    (r"\bfirst[\s_-]*name\b|\bgiven[\s_-]*name\b|\bforename\b", "first_name"),
    (r"\blast[\s_-]*name\b|\bsurname\b|\bfamily[\s_-]*name\b", "last_name"),
    (r"\bfull[\s_-]*name\b|^name$", "full_name"),
    (r"\be[\s_-]*mail\b", "email"),
    (r"\bphone\b|\bmobile\b|\btelephone\b", "phone"),
    (r"linked[\s_-]*in", "linkedin"),
    (r"\bwebsite\b|\bportfolio\b|personal\s+site", "website"),
    (r"\bcity\b|\btown\b", "city"),
    (r"\bcountry\b", "country"),
    (r"\blocation\b|\baddress\b|where.*based", "location"),
    (r"current\s+(employer|company)", "current_company"),
    (r"current\s+(job\s+)?title|current\s+role", "current_title"),
    (r"\bschool\b|\buniversity\b|\bcollege\b", "school"),
    (r"\bdegree\b|\bqualification\b", "degree"),
]

# Questions the agent must never answer on Zac's behalf, whatever the wording.
# These are matched before the classifier ever sees the field, so no model call
# is spent on them and no model can decide to fill one in.
NEVER_ANSWER_PATTERNS: list[tuple[str, str]] = [
    (r"\bgender\b|\bsex\b|\bpronoun", "a gender/identity question"),
    (r"\brace\b|\bethnic|\bhispanic\b|\blatino\b", "an ethnicity question"),
    (r"\bveteran\b|\bmilitary\b", "a veteran-status question"),
    (r"\bdisab|\bimpairment\b", "a disability question"),
    (r"self[\s-]*identif", "a voluntary self-identification question"),
    (r"sexual\s+orientation|\btransgender\b|\blgbt", "an orientation question"),
    (r"\bage\b|date\s+of\s+birth|\bdob\b", "an age question"),
    (r"\bsalary\b|compensation\s+expect|expected\s+pay|desired\s+pay", "a pay-expectation question"),
    (r"notice\s+period|when\s+can\s+you\s+start|availability\s+to\s+start", "a notice-period question"),
    (r"\bsponsorship\b|\bvisa\b|work\s+permit|immigration", "a visa/sponsorship question"),
    (r"\bcriminal\b|\bconvict|background\s+check", "a criminal-record question"),
    (r"privacy\s+(notice|policy)|\bconsent\b|terms\s+and\s+conditions|\bgdpr\b",
     "a legal consent — Zac's to give, not the agent's"),
]
