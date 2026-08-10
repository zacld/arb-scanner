# Stage 2 — autonomous application form fill

Stage 2 is a skin on Stage 1, not a rebuild. The tailored CV that Stage 1 already
produces is the only source of application content; Stage 2's job is deciding which
piece of it goes in which box on a stranger's website, and then not submitting until
a human has looked.

## Why a browser, not HTTP

Posting directly to an ATS endpoint is tempting and wrong. Greenhouse, Lever and
Workday forms render client-side, sign their file uploads with short-lived tokens,
validate on blur, and change markup per customer. A hand-built POST breaks the first
time an employer reorders a field, and it breaks silently — the worst failure mode
when the output is a real job application.

So: **Playwright driving Chromium** (`cvagent/apply/runner.py`). It is already a
dependency pattern in this repo, Chromium is preinstalled here, and the same headless
Chromium renders the CV PDF, so the PDF Zac attaches is pixel-identical to the preview
he approved. Headed by default — Zac watches it work and can take the wheel.

## Field mapping: deterministic first, model second

A single LLM pass over every field would be slower, costlier and less predictable than
it needs to be. "First Name" does not require judgement.

**Tier 1 — regex** (`apply/profile.py`, `DETERMINISTIC_RULES`): name, email, phone,
location, LinkedIn, website, current employer/title, school, degree. This is the
majority of fields on every form, resolved with no model call and no variance. A rule
that matches a field whose profile value is *empty* produces an explicit skip flagged
for review — never a blank or a guess.

**Tier 2 — Claude classifier** (`apply/mapper.py`): everything else in one call —
unfamiliar labels, every dropdown (option wording is employer-specific), checkboxes,
and the free-text questions. It receives the trimmed field descriptors, the full
profile catalogue, the tailored CV and the listing, and returns a structured
assignment per field via `output_config.format`, so there is no JSON parsing to fail.

Field discovery itself (`apply/forms.py`) assumes nothing about the vendor: it walks
every visible `input`/`select`/`textarea`, harvests label, `aria-label`, placeholder,
name and the surrounding text block, and stamps each control with a unique attribute
so it can be relocated after re-render. It scans every frame, because Greenhouse and
Lever are usually embedded in the company's own site via iframe — a top-frame-only
scan finds nothing on exactly the pages that matter.

## File upload

`page.pdf()` on the rendered CV page produces `cv.pdf` in the run directory, and the
upload field gets it via `set_input_files`. Because the CV layout is fixed HTML/CSS
(`cvagent/render.py`), the same layout serves the browser preview, the print-to-PDF
button and the ATS attachment — one layout to maintain, matching the plan to rebuild
the Canva design in HTML rather than automate Canva.

## Free-text questions

"Why do you want to work here?" is generated in the same classifier call, from the
tailored CV plus the listing — same discipline as the bullets: no invented enthusiasm,
no claimed familiarity with products Zac hasn't used, only real experience, and
`maxlength` respected. Demographic, diversity, disability and veteran-status questions
are always skipped; those are Zac's to answer, not the agent's.

## Safety defaults

- **Nothing is submitted by default.** A plain run fills the form, screenshots it,
  writes `assignments.json`, prints a review table and stops with the browser open.
- `--submit` still requires typing the word `submit` at the prompt.
- Any field flagged low-confidence or skipped blocks submission entirely unless
  `--force` is passed.
- Every fill failure is reported in the summary rather than swallowed, so a form that
  half-filled is obvious before anything is sent.

## Build order from here

1. **Greenhouse** — the first target, and what the current prototype is aimed at:
   consistent structure across employers, so it is the cheapest way to prove the loop
   end to end. Run it against a real posting and iterate on the classifier prompt.
2. **Lever, then Workday.** Lever is structurally similar. Workday is the hard one —
   multi-page wizards with a login wall — and will need step-through navigation plus
   session reuse (`storage_state`), not just a single-page fill.
3. **LinkedIn Easy Apply.** Listed first in the brief as the simplest form, and it is —
   but it sits behind an authenticated session and LinkedIn's automation terms, so it
   is worth treating as a deliberate later decision rather than the default starting
   point. If it goes ahead, it needs a persisted logged-in profile and much slower,
   human-paced interaction.
4. **A run log.** Once several applications have gone out, a small record of which
   listing got which CV version matters more than any further automation.
