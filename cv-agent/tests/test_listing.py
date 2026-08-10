"""Reading the job advert off the posting page.

The whole point is that a URL is enough: an ATS posting carries the advert and
the form on the same page, so making Zac paste the advert into a file is
friction with no purpose.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

playwright_api = pytest.importorskip("playwright.sync_api")

from cvagent.apply.browser import launch_chromium  # noqa: E402
from cvagent.apply.forms import application_form_fields, extract_fields  # noqa: E402
from cvagent.apply.listing import extract_listing_text, looks_usable  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def scraped():
    with playwright_api.sync_playwright() as p:
        try:
            browser = launch_chromium(p)
        except Exception as exc:
            pytest.skip(f"chromium unavailable: {exc}")
        page = browser.new_page()
        results = {}
        for name in ("greenhouse_full_posting.html", "greenhouse_modern.html"):
            page.goto((FIXTURES / name).as_uri())
            page.wait_for_timeout(300)
            fields, _ = application_form_fields(extract_fields(page))
            results[name] = (extract_listing_text(page), fields)
        browser.close()
    return results


def test_advert_is_read_from_the_posting_page(scraped):
    text, _ = scraped["greenhouse_full_posting.html"]
    assert looks_usable(text)
    assert "workflow automation platform" in text
    assert "integration scoping" in text


def test_form_text_does_not_leak_into_the_advert(scraped):
    """Option lists and field labels are not part of the job description; feeding
    them to the tailoring prompt skews which keywords it thinks matter."""
    text, _ = scraped["greenhouse_full_posting.html"]
    assert "Please select" not in text
    assert "Voluntary Self-Identification" not in text


def test_scraping_the_advert_does_not_disturb_the_form(scraped):
    _, fields = scraped["greenhouse_full_posting.html"]
    ids = {f.id for f in fields}
    assert {"first_name", "email", "resume", "question_30538426"} <= ids


def test_a_form_only_page_is_reported_as_unusable(scraped):
    """A bare form with no advert must say so rather than tailor against nothing."""
    text, _ = scraped["greenhouse_modern.html"]
    assert not looks_usable(text), f"expected too little text, got {len(text.split())} words"


def test_usable_threshold_rejects_a_stub():
    assert looks_usable("Account Executive. London. Apply now.") is False
    assert looks_usable(" ".join(["word"] * 200)) is True
