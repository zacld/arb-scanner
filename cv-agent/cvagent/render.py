"""Renders a tailored CV into the fixed HTML/CSS layout (print-to-PDF ready)."""

from __future__ import annotations

from html import escape
from typing import Any

from .bank import all_roles

CSS = """
@page { size: A4; margin: 14mm 15mm; }
* { box-sizing: border-box; }
body { margin: 0; background: #f2efe9; }
.cv {
  width: 210mm; min-height: 297mm; margin: 0 auto; padding: 16mm 15mm;
  background: #fff; color: #1d1d1b;
  font-family: "Iowan Old Style", "Palatino Linotype", Georgia, serif;
  font-size: 10.5pt; line-height: 1.45;
}
.cv h1 { font-size: 25pt; letter-spacing: .01em; margin: 0; font-weight: 600; }
.cv .headline { font-size: 10.5pt; letter-spacing: .16em; text-transform: uppercase;
  color: #8a6f4a; margin: 4px 0 10px; }
.cv .contact { font-size: 9.5pt; color: #4a4a46; border-bottom: 1.5px solid #d8d1c4; padding-bottom: 12px; }
.cv .contact span + span::before { content: "·"; margin: 0 8px; color: #b3a891; }
.cv h2 { font-size: 9.5pt; letter-spacing: .18em; text-transform: uppercase;
  color: #8a6f4a; margin: 20px 0 8px; font-weight: 600; }
.cv .summary { margin: 12px 0 0; }
.cv .role { margin-bottom: 14px; page-break-inside: avoid; }
.cv .role-head { display: flex; justify-content: space-between; align-items: baseline; gap: 12px; }
.cv .role-title { font-weight: 600; }
.cv .role-company { font-style: italic; color: #4a4a46; }
.cv .role-dates { font-size: 9pt; color: #6b6b66; white-space: nowrap; }
.cv ul { margin: 5px 0 0; padding-left: 16px; }
.cv li { margin-bottom: 3px; }
.cv .edu { margin-bottom: 10px; }
.cv .edu-detail { font-size: 9.5pt; color: #4a4a46; }
@media print { body { background: #fff; } .cv { width: auto; min-height: 0; margin: 0; padding: 0; } }
"""


def _role_html(role: dict[str, Any], bullets: list[str]) -> str:
    location = f' — {escape(role["location"])}' if role.get("location") else ""
    items = "".join(f"<li>{escape(b)}</li>" for b in bullets)
    return f"""
    <div class="role">
      <div class="role-head">
        <div>
          <span class="role-title">{escape(role["title"])}</span>,
          <span class="role-company">{escape(role["company"])}{location}</span>
        </div>
        <div class="role-dates">{escape(role["start"])} – {escape(role["end"])}</div>
      </div>
      <ul>{items}</ul>
    </div>"""


def render_cv_body(cv: dict[str, Any], bank: dict[str, Any]) -> str:
    """The CV markup itself, without the page wrapper — reusable in the live preview."""
    roles = all_roles(bank)
    identity = bank["identity"]
    contact = "".join(
        f"<span>{escape(v)}</span>"
        for v in (identity["location"], identity["email"])
    )
    role_blocks = "".join(
        _role_html(roles[r["id"]], r.get("bullets", []))
        for r in cv.get("roles", [])
        if r.get("id") in roles
    )
    edu_blocks = "".join(
        f"""<div class="edu">
          <div><span class="role-title">{escape(e["qualification"])}</span>,
            <span class="role-company">{escape(e["institution"])}</span>
            <span class="role-dates">({escape(e["start"])} – {escape(e["end"])}, {escape(e["grade"])})</span>
          </div>"""
        + (f'<div class="edu-detail">{escape(e["detail"])}</div>' if e.get("detail") else "")
        + "</div>"
        for e in bank["education"]
    )
    return f"""<div class="cv">
      <h1>{escape(identity["name"])}</h1>
      <div class="headline">{escape(identity["headline"])}</div>
      <div class="contact">{contact}</div>
      <p class="summary">{escape(cv.get("summary", ""))}</p>
      <h2>Experience</h2>
      {role_blocks}
      <h2>Education</h2>
      {edu_blocks}
    </div>"""


def render_cv_page(cv: dict[str, Any], bank: dict[str, Any]) -> str:
    """A standalone HTML document — open in a browser and print to PDF."""
    name = escape(bank["identity"]["name"])
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{name} — CV</title><style>{CSS}</style></head>"
        f"<body>{render_cv_body(cv, bank)}</body></html>"
    )


def render_cv_text(cv: dict[str, Any], bank: dict[str, Any]) -> str:
    """Plain-text CV — what Stage 2 pastes into ATS free-text work-history fields."""
    roles = all_roles(bank)
    identity = bank["identity"]
    lines = [identity["name"], identity["headline"], f'{identity["location"]} | {identity["email"]}', ""]
    lines += [cv.get("summary", ""), "", "EXPERIENCE"]
    for entry in cv.get("roles", []):
        role = roles.get(entry.get("id"))
        if not role:
            continue
        lines.append(f'{role["title"]} — {role["company"]} ({role["start"]}–{role["end"]})')
        lines += [f"  • {b}" for b in entry.get("bullets", [])]
        lines.append("")
    lines.append("EDUCATION")
    for edu in bank["education"]:
        lines.append(f'{edu["qualification"]} — {edu["institution"]} ({edu["start"]}–{edu["end"]}, {edu["grade"]})')
    return "\n".join(lines)
