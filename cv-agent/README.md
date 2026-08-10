# CV Tailoring Agent

Send it a job link. It reads the advert off the page, tailors the CV to it, fills
in the application form, and stops so you can look before anything is sent.

```bash
./apply https://job-boards.greenhouse.io/acme/jobs/4012345
```

That is the whole thing. No pasting the advert, no flags. It opens a browser you
can watch, fills every field it can answer honestly, attaches a freshly tailored
CV as a PDF, and leaves the questions only you should answer — salary, notice
period, visa status, anything demographic — flagged and blank.

Add `--submit` when you have read it and want it sent; it still asks you to type
`submit` to confirm, and refuses outright if any field is flagged or did not fill
cleanly.

One-time setup:

```bash
pip install -r cv-agent/requirements.txt
playwright install chromium
export ANTHROPIC_API_KEY=sk-ant-...     # stays on your machine, never in the browser
```

LinkedIn is the exception: it blocks scrapers, so paste the advert into a file
and pass `--listing listing.txt` alongside the URL.

## Stage 1 — tailor a CV by hand

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
finance listing and a tech listing: `python scripts/live_check.py --step tailoring`.

## Stage 2 — fill an application form

```bash
./apply <job posting URL>
```

Reads the advert off the page, tailors the CV, renders it to PDF, opens the form
in Chromium, maps every field
(regex for the standard contact fields, Claude for everything else), fills it,
reads every value back out of the page, screenshots it, and **stops**. Nothing is
submitted unless you pass `--submit` and then type `submit` at the prompt — and a
flagged field, a fill failure or a read-back mismatch blocks submission entirely
unless you also pass `--force`.

Useful flags when a new employer's form misbehaves:

| Flag | Why |
|---|---|
| `--no-llm` | Scrape and fill only the deterministic fields. No API key, no spend — the fastest way to see whether a new form scrapes correctly. |
| `--dump-fields` | Write every scraped field descriptor to `fields.json`. |
| `--cv tailored_cv.json` | Reuse a CV you already generated instead of tailoring again. |

The agent will never answer for you: gender, ethnicity, veteran status,
disability, self-identification, age, salary expectations, notice period,
visa/sponsorship, criminal-record questions, or legal consent checkboxes. Those
are matched before the classifier runs, so no model ever sees a chance to fill
one in.

Design notes, the Greenhouse hardening log, and what a second ATS would need:
`docs/stage2-approach.md`.

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

## Live validation

The test suite runs entirely offline, so a few things can only be confirmed on a
machine with a key and network access. In ascending order of risk:

```bash
python scripts/live_check.py --step classifier   # real model, local form, no employer
python scripts/scrape_report.py --url <posting>  # real posting, no key, sends nothing
python scripts/live_check.py --step tailoring    # Stage 1 wording check
```

The classifier step prints every generated answer verbatim before anything
downstream happens. `scrape_report.py` checks a real posting against every
assumption the fixtures encode and prints PASS/FAIL per assumption.

Only after those are clean is a real submission worth attempting — see
`docs/stage2-approach.md` for why the first one should be an application Zac
actually wants rather than a throwaway test.

## Tests

```bash
python -m pytest tests -q     # 70 tests, all offline, no API key needed
```
