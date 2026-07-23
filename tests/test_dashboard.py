import pytest

flask = pytest.importorskip("flask")

from arbfinder import dashboard
from arbfinder.demo import DemoEbayClient, demo_products
from arbfinder.pipeline import compare_products
from arbfinder.report import sort_comparisons


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
    assert "entered secret not shown" in page  # placeholder confirms it was received


def test_scan_validates_missing_url():
    out = dashboard._run_scan({"url": "", "comparator": "google"})
    assert "Please enter an Argos URL" in out


def test_scan_ebay_requires_credentials():
    out = dashboard._run_scan({"url": "https://www.argos.co.uk/search/x/",
                               "comparator": "ebay", "ebay_id": "", "ebay_secret": ""})
    assert "eBay needs both" in out


def test_empty_results_message():
    assert "No comparisons produced" in dashboard._results_table([], "google", 13.0, 0.0)
