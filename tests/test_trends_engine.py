import pytest

from arbfinder.trends import cache
from arbfinder.trends.base import TrendSignal, TrendSignalProvider
from arbfinder.trends.engine import TrendEngine
from arbfinder.trends.normalize import make_rules_classifier


@pytest.fixture(autouse=True)
def isolate_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(cache, "CACHE_PATH", tmp_path / "trends.json")
    cache._MEMO.clear()
    yield
    cache._MEMO.clear()


class _FixedProvider(TrendSignalProvider):
    def __init__(self, name, signals):
        self.name = name
        self._signals = signals
        self.calls = 0

    def fetch(self, session=None, browser=None):
        self.calls += 1
        return list(self._signals)


def _sig(source, title, cat="", strength=0.6, seasonal=0.0):
    return TrendSignal(source=source, original_title=title, product_category=cat,
                       trend_strength=strength, seasonal_strength=seasonal)


def test_cross_source_agreement_outranks_single_source():
    # "air fryer" appears on amazon + manual; "lego" only on amazon.
    amazon = _FixedProvider("amazon_movers", [
        _sig("amazon_movers", "Ninja Air Fryer AF100UK"),
        _sig("amazon_movers", "LEGO Technic 42151"),
    ])
    manual = _FixedProvider("manual", [_sig("manual", "air fryer", strength=1.0)])
    targets = TrendEngine([amazon, manual]).discover(limit=10)
    terms = [t.term for t in targets]
    assert terms[0] == "air fryer"                 # multi-source wins
    air = next(t for t in targets if t.term == "air fryer")
    assert set(air.sources) == {"amazon_movers", "manual"}
    assert "Ninja Air Fryer AF100UK" in air.leads  # amazon model kept as a lead


def test_unmapped_social_signal_is_dropped_but_seasonal_kept():
    social = _FixedProvider("manual", [])  # empty
    # a preset-category seasonal signal survives; a nonsense one from a social
    # (non-keep) source would be dropped — emulate with a fake 'twitter' source.
    seasonal = _FixedProvider("seasonal", [_sig("seasonal", "bbq (in season)",
                                               cat="bbq", strength=0.0, seasonal=0.8)])
    noise = _FixedProvider("twitter", [_sig("twitter", "some politics hashtag")])
    targets = TrendEngine([social, seasonal, noise]).discover(limit=10)
    terms = [t.term for t in targets]
    assert "bbq" in terms
    assert not any("politics" in t for t in terms)


def test_cache_memoises_within_run():
    p = _FixedProvider("amazon_movers", [_sig("amazon_movers", "air fryer")])
    engine = TrendEngine([p])
    engine.discover(limit=5)
    engine.discover(limit=5)
    assert p.calls == 1  # second discover served from memo


def test_failing_provider_is_skipped():
    class _Boom(TrendSignalProvider):
        name = "boom"

        def fetch(self, session=None, browser=None):
            raise RuntimeError("nope")

    good = _FixedProvider("manual", [_sig("manual", "air fryer")])
    targets = TrendEngine([_Boom(), good]).discover(limit=5)
    assert [t.term for t in targets] == ["air fryer"]
