from datetime import date

from arbfinder.trends.providers.seasons import SeasonalProvider, seasonal_strength


def test_strength_zero_out_of_window():
    # Tower fan window ~ Apr–Sep; a January date is out.
    s = seasonal_strength(date(2026, 1, 10), "04-15", "06-01", "08-15", "09-15")
    assert s == 0.0


def test_strength_peaks_in_season():
    s = seasonal_strength(date(2026, 7, 1), "04-15", "06-01", "08-15", "09-15")
    assert s == 1.0


def test_strength_ramps_before_peak():
    early = seasonal_strength(date(2026, 4, 20), "04-15", "06-01", "08-15", "09-15")
    mid = seasonal_strength(date(2026, 5, 20), "04-15", "06-01", "08-15", "09-15")
    assert 0.10 <= early < mid < 1.0  # ramping up, not yet peak


def test_strength_decays_after_peak():
    s = seasonal_strength(date(2026, 9, 1), "04-15", "06-01", "08-15", "09-15")
    assert 0.10 <= s < 1.0


def test_year_wrapping_window_for_winter_category():
    # Dehumidifier window wraps the new year (Oct → Feb). Both sides are in-season.
    dec = seasonal_strength(date(2026, 12, 15), "09-15", "10-15", "01-31", "03-01")
    jan = seasonal_strength(date(2026, 1, 15), "09-15", "10-15", "01-31", "03-01")
    jul = seasonal_strength(date(2026, 7, 15), "09-15", "10-15", "01-31", "03-01")
    assert dec == 1.0 and jan == 1.0 and jul == 0.0


def test_provider_emits_in_season_categories_only():
    # High summer: fans/BBQ in; heaters/air-fryer(Christmas) out.
    signals = SeasonalProvider(today=date(2026, 7, 20)).fetch()
    cats = {s.product_category for s in signals}
    assert "tower fan" in cats
    assert "bbq" in cats
    assert "electric heater" not in cats
    # every emitted signal carries a real seasonal strength and preset category
    assert all(s.seasonal_strength >= 0.1 and s.product_category for s in signals)
