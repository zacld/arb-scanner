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

## Hardening pass — what real Greenhouse markup broke

Everything below was found by testing against fixtures modelled on Greenhouse's
actual DOM rather than a tidy mock form. Each one would have failed silently on a
live application.

| Failure | Why it happens on real forms | Fix |
|---|---|---|
| CV never attaches | Greenhouse hides the real `<input type=file>` behind a styled "Attach / Dropbox / Google Drive" row. A visibility filter drops it. | File inputs are exempt from every visibility rule; `set_input_files` works on hidden inputs. |
| Wrong value in the wrong box | The DOM stamp restarts at `f0` in every frame, so an embedded Greenhouse form and its host page both produce an `f0` and one field's assignment overwrites another's. | Field keys are frame-scoped (`{frame}-{stamp}`). |
| Honeypot filled → binned | Traps are hidden by `left:-9999px` as often as `display:none`; an off-screen element still has a client rect. | Off-viewport rects are excluded too. |
| Newsletter box treated as the applicant email | Careers pages carry their own forms, and a label matcher can't tell them apart. | Fields are grouped by `<form>`; the application is the group carrying the file upload. |
| Location silently empty on submit | The Location typeahead only counts as filled once a suggestion is committed; a plain value write leaves it invalid. | Combobox fields are typed into and a suggestion is clicked; "no suggestion list" blocks submission. |
| Unreviewable review step | Custom questions are `id="question_30538426"`, which is shorter than the real label, so a shortest-string label picker showed the id. | Human-authored labels are kept separate from machine identifiers. |
| A field looked filled but wasn't | React can revert a programmatic write, a select can keep its placeholder, an upload can be rejected — none of which raise. | Every field is read back from the DOM after filling and mismatches block submission. |

## Questions the agent will never answer

Matched before the classifier is called, so no model call is spent and no model
can decide to answer one: gender, ethnicity, veteran status, disability, any
voluntary self-identification, sexual orientation, age/DOB, salary expectations,
notice period, visa/sponsorship, criminal record, and legal consent checkboxes.
Greenhouse's EEO block often labels its dropdowns nothing more useful than
"Please select", so the match runs against the surrounding section copy, not just
the label.

Salary, notice period and visa status are in that list because they are
commitments only Zac can make, not because they are sensitive. They are the
answers most likely to be wrong in a way that costs him the role.

## Second ATS: what would change (not built yet)

Greenhouse is the one target that should be genuinely production-ready first.
Recording what the next one needs, based on how each differs structurally:

**Lever** — closest to Greenhouse and the cheapest next step. Same single-page
POST-at-the-end shape, so the runner needs no structural change. What differs:
fields are named `name`, `email`, `resume` without the `job_application[...]`
wrapper (the deterministic rules already match on label text, so this mostly
works as-is); the CV upload triggers a **resume parse that auto-populates work
history and education fields a few seconds after upload**, so the fill order has
to become upload-first, wait, re-scrape, then fill — otherwise the parser
overwrites what was already typed. That re-scrape is the one real change, and it
would benefit Greenhouse too.

**Workday** — a much bigger job, and not a mapping-layer change. It is a
multi-page wizard behind a mandatory account signup, so it needs: persisted
authenticated sessions (`storage_state`), per-step navigation with a
scrape-map-fill-verify loop per page rather than once, handling of repeating
"Add Another" work-history sections that don't exist as static fields, and
tolerance for its slow custom widgets. The field-mapping layer transfers; the
runner's single-page assumption does not. Treat Workday as its own project.

The reusable seam is that `forms.py` and `mapper.py` already assume nothing about
the vendor — the vendor-specific part is entirely in the runner's flow control
(when to scrape, how many pages, when to re-read).

## Live checklist for Zac

Nothing below can be run from the build environment — it has no outbound network
access and no API key — so these are the checks that have to happen on a real
machine against a real posting.

1. `export ANTHROPIC_API_KEY=...` and `playwright install chromium`.
2. Scrape-only, no spend, on a real Greenhouse posting:
   `python -m cvagent.apply.runner --url <posting> --listing listing.txt --cv tailored_cv.json --no-llm --dump-fields`
   Check `fields.json`: every question present, the resume input found, nothing
   from an unrelated form.
3. Full run with the classifier, still not submitting. Read the free-text answer
   it wrote before anything else.
4. Only then `--submit`, and read the summary before typing `submit`.

## Build order from here

1. **Greenhouse** — hardened against the shapes above and working end to end on
   both fixtures. What remains is the live checklist: real postings, real
   classifier output, one real submission with Zac watching.
2. **Lever**, via the upload-first re-scrape described above.
3. **Workday**, as its own project — the runner needs a multi-page loop.
4. **LinkedIn Easy Apply**, deliberately last: it sits behind an authenticated
   session and LinkedIn's automation terms, so it is a decision to take
   consciously rather than the default starting point. If it goes ahead it needs
   a persisted logged-in profile and much slower, human-paced interaction.
5. **A run log.** Once several applications have gone out, a record of which
   listing got which CV version matters more than any further automation.
