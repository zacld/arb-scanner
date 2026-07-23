import pytest

flask = pytest.importorskip("flask")

from arbfinder import config, dashboard
from arbfinder.demo import DemoEbayClient, demo_products
from arbfinder.pipeline import compare_products
from arbfinder.report import sort_comparisons


@pytest.fixture(autouse=True)
def isolate_credentials(tmp_path, monkeypatch):
    # Never touch the real ~/.arbfinder-credentials.json during tests.
    monkeypatch.setattr(config, "CONFIG_PATH", tmp_path / "creds.json")
    monkeypatch.delenv("EBAY_CLIENT_ID", raising=False)
    monkeypatch.delenv("EBAY_CLIENT_SECRET", raising=False)


@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def test_index_renders_form(client):
    body = client.get("/").get_data(as_text=True)
    assert "retail-arbitrage-finder" in body
    assert 'name="url"' in body
    assert 'type="password"' in body  # secret field is masked input


def test_results_table_from_real_comparisons():
    comps = sort_comparisons(compare_products(demo_products(), DemoEbayClient()),
                             fees_pct=13, postage=0)
    out = dashboard._results_table(comps, "ebay", 13.0, 0.0)
    assert out.count("<tr>") == len(comps) + 1  # + header row
    assert "Tommee Tippee" in out
    assert 'class="pos"' in out and 'class="neg"' in out  # gains and losses coloured


def test_secret_is_never_echoed_back():
    page = dashboard._render(
        results="", form={"ebay_secret": "TOPSECRET123", "comparator": "ebay", "url": "u"}
    )
    assert "TOPSECRET123" not in page


def test_saved_credentials_prefill_id_not_secret():
    config.save_credentials("MY-APP-ID", "MY-CERT-SECRET")
    page = dashboard._render()
    assert "MY-APP-ID" in page          # Client ID pre-filled for convenience
    assert "MY-CERT-SECRET" not in page  # secret NEVER rendered into the page
    assert "saved secret will be used" in page


def test_remember_saves_and_forget_clears(client):
    # A blank-secret ebay scan with saved creds resolves them (no "needs both").
    config.save_credentials("APPID", "SECRET")
    out = dashboard._run_scan(
        {"url": "https://www.argos.co.uk/search/x/", "comparator": "ebay",
         "ebay_id": "", "ebay_secret": ""}
    )
    assert "eBay needs both" not in out

    # /forget removes them.
    client.get("/forget")
    assert not config.has_saved_secret()


def test_remember_checkbox_persists_typed_credentials():
    dashboard._run_scan(
        {"url": "https://www.argos.co.uk/search/x/", "comparator": "ebay",
         "ebay_id": "TYPED_ID", "ebay_secret": "TYPED_SECRET", "remember": "1"}
    )
    assert config.load_credentials() == {
        "ebay_client_id": "TYPED_ID", "ebay_client_secret": "TYPED_SECRET"
    }


def test_scan_validates_no_input():
    out = dashboard._run_scan({"url": "", "comparator": "google"}, {})
    assert "Upload a saved search page" in out


class _FakeUpload:
    filename = "argos.html"

    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


def test_uploaded_page_is_parsed(monkeypatch, tmp_path):
    from pathlib import Path
    # Stub the comparison so no network/browser is needed; we only test that
    # the uploaded page is read and parsed into products.
    seen = {}
    def fake_compare(products, client, **k):
        seen["n"] = len(products)
        return []
    monkeypatch.setattr(dashboard, "compare_products", fake_compare)
    argos = Path("tests/fixtures/argos_flight_search.html").read_bytes()
    out = dashboard._run_scan(
        {"source": "argos", "comparator": "google", "max_products": "5"},
        {"page": _FakeUpload(argos)},
    )
    assert seen["n"] == 3  # 3 products parsed from the fixture and handed to compare
    assert "Parsed 3 products from the uploaded Argos page" in out


def test_dropdowns_render():
    body = dashboard._render()
    assert '<select name="source">' in body
    assert '<select name="comparator">' in body
    assert '<select name="ebay_env">' in body
    assert 'type="file"' in body  # the upload control
    assert "John Lewis (beta)" in body


def test_scan_ebay_requires_credentials():
    out = dashboard._run_scan({"url": "https://www.argos.co.uk/search/x/",
                               "comparator": "ebay", "ebay_id": "", "ebay_secret": ""})
    assert "eBay needs both" in out


def test_empty_results_message():
    assert "No comparisons produced" in dashboard._results_table([], "google", 13.0, 0.0)
