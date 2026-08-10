"""Pulls the job description off the application page itself.

An ATS posting carries the advert and the form on the same page, so asking Zac
to paste the listing into a file when he has already handed over the URL is
pointless friction. (LinkedIn still needs a paste — it blocks robots — which is
what --listing is for.)
"""

from __future__ import annotations

from typing import Any

MAX_CHARS = 12000

# Tried in order. Falls back to the body with the form stripped out.
_DESCRIPTION_JS = r"""
() => {
  const clean = (text) => (text || '').replace(/\n{3,}/g, '\n\n').trim();

  // The advert without the application form. The form's own labels and option
  // lists are not part of the job description, and feeding them to the tailoring
  // prompt as if they were skews which keywords it thinks the employer cares about.
  const withoutForm = (node) => {
    const clone = node.cloneNode(true);
    for (const junk of clone.querySelectorAll('form, nav, footer, header, script, style')) {
      junk.remove();
    }
    return clean(clone.innerText);
  };

  const selectors = [
    '.job__description', '[class*="job-description"]', '[class*="jobDescription"]',
    '[data-testid*="description"]', '#content', 'article', 'main', '#main',
  ];
  for (const selector of selectors) {
    const node = document.querySelector(selector);
    if (node) {
      const text = withoutForm(node);
      // A container that is basically just the form is the form, not the advert.
      if (text.length > 400) return text;
    }
  }
  return withoutForm(document.body);
}
"""


def extract_listing_text(page: Any) -> str:
    """Best-effort job description from the posting page, across frames.

    Greenhouse embeds put the form in an iframe while the advert stays on the
    host page, so the longest candidate across all frames wins rather than the
    first one found.
    """
    best = ""
    for frame in page.frames:
        try:
            text = frame.evaluate(_DESCRIPTION_JS)
        except Exception:
            continue
        if text and len(text) > len(best):
            best = text
    return best[:MAX_CHARS]


def looks_usable(text: str) -> bool:
    """Whether there is enough here to tailor against.

    A thin scrape produces a CV tailored to nothing, which is worse than being
    told to paste the advert in.
    """
    return len(text.split()) >= 80
