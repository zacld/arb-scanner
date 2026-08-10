# CV Tailoring Agent

Tailors Zac's CV to a specific job listing, then fills in the application form with it.

## Setup

```bash
pip install -r cv-agent/requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...        # stays server-side, never in the browser
```

For Stage 2 only: `playwright install chromium`.

## Stage 1 — tailor a CV

```bash
cd cv-agent && python -m cvagent.server        # http://127.0.0.1:8000
```

Paste the listing (LinkedIn blocks scraping, so it has to be text), optionally steer
the category, hit Generate. You get the CV preview, a print-to-PDF button, and a panel
showing which two roles were kept and why, which listing keywords were mirrored, and
the result of the rewrite check.

### The rewording fix

The old artifact handed back the LinkedIn bullets with a couple of words swapped. Two
changes address that:

1. **The prompt** (`prompts/tailor_system.md`) frames the job as extract → re-express →
   check rather than "reword", forbids reusing more than three consecutive ordinary
   words from a source bullet, and forbids reusing the source's sentence shape.
2. **A verifier that doesn't trust the model** (`cvagent/verify.py`). Every generated
   CV is checked in code: longest shared word-run against each source bullet (proper
   nouns and figures exempt, since those must survive verbatim), numbers that don't
   appear in the source, repeated opening verbs, and the structural rules. Failures go
   back to the model naming the offending phrase, up to two repair rounds. If it still
   fails, the UI says so rather than quietly shipping a copy.

To confirm the wording — not just the role selection — actually shifts between a
finance listing and a tech listing:

```bash
python scripts/compare_tailoring.py
```

## Stage 2 — fill an application form

```bash
python -m cvagent.apply.runner --url <application URL> --listing listing.txt
```

Tailors the CV, renders it to PDF, opens the form in Chromium, maps every field
(regex for the standard contact fields, Claude for everything else), fills it,
screenshots it, and **stops**. Nothing is submitted unless you pass `--submit` and
then type `submit` at the prompt — and flagged fields block submission entirely
unless you also pass `--force`.

Design notes and the build order for other ATS vendors: `docs/stage2-approach.md`.

## Layout

```
data/experience_bank.json   master profile — the one source of truth for both stages
prompts/tailor_system.md    the selection + rewrite rules
cvagent/tailor.py           Stage 1: generate → verify → repair
cvagent/verify.py           the rewrite check
cvagent/render.py           fixed CV layout (preview, PDF and plain text)
cvagent/server.py           local web app
cvagent/apply/              Stage 2: form reading, field mapping, browser runner
```

## Locked rules

Reverse-chronological always; Universal Partners always first; exactly two of Dojo /
LeadSparker / GRO.TEAM chosen per listing on genuine relevance; the third dropped
entirely; education untouched. `cvagent/verify.py` enforces all of these in code, so a
model that ignores them fails the check rather than reaching the PDF.

## Tests

```bash
python -m pytest tests -q     # runs offline, no API key needed
```
