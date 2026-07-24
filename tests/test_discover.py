from pathlib import Path

from arbfinder.discover import Idea, discover_bestsellers, parse_bestsellers, _short_term


def _fixture() -> str:
    return Path("tests/fixtures/amazon_bestsellers.html").read_text(encoding="utf-8")


def test_parse_extracts_terms_and_prices():
    ideas = parse_bestsellers(_fixture())
    terms = {i.term for i in ideas}
    assert "Ninja Air Fryer AF100UK" in terms          # trimmed at the comma
    assert "Bush Black Desk Fan - 12 Inch" in terms
    ninja = next(i for i in ideas if i.term.startswith("Ninja"))
    assert ninja.amazon_price == 99.99
    assert ninja.url == "https://www.amazon.co.uk/dp/B08AIRFRY1"


def test_priceless_item_still_becomes_an_idea():
    ideas = parse_bestsellers(_fixture())
    lego = next((i for i in ideas if "LEGO" in i.term), None)
    assert lego is not None           # a term with no price is still huntable
    assert lego.amazon_price is None


def test_short_term_trims_long_titles():
    assert _short_term("Brand Model X, 5L, Black (2024)") == "Brand Model X"
    assert len(_short_term("word " * 40)) <= 60


class _StubBrowser:
    def __init__(self, html):
        self._html = html
        self.calls = 0

    def fetch(self, url):
        self.calls += 1
        return self._html


def test_discover_dedupes_across_sources_and_respects_limit():
    b = _StubBrowser(_fixture())
    ideas = discover_bestsellers(
        b, sources=[("A", "http://a"), ("B", "http://b")], limit=10)
    # Same fixture from two sources -> deduped by term (3 unique items).
    terms = [i.term for i in ideas]
    assert len(terms) == len(set(terms)) == 3

    capped = discover_bestsellers(_StubBrowser(_fixture()),
                                  sources=[("A", "http://a")], limit=2)
    assert len(capped) == 2


def test_idea_is_a_dataclass():
    idea = Idea(term="thing", reason="test")
    assert idea.term == "thing" and idea.amazon_price is None
