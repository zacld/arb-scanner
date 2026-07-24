"""Seasonal-demand provider: what UK shoppers buy at this time of year.

Replaces the live-weather idea with a structured, editable calendar
(``arbfinder/trends/data/uk_seasons.json``). Each category has an annual window
(start → peak_start → peak_end → end); ``seasonal_strength`` ramps up *before*
the peak (so you source ahead of demand) and decays after — it does not just
switch on during the season. One-off events (World Cup, Black Friday) are
deliberately out of scope here; they belong in a future events provider.
"""

from __future__ import annotations

import json
import logging
from datetime import date
from pathlib import Path

from ..base import TrendSignal, TrendSignalProvider

log = logging.getLogger(__name__)

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "uk_seasons.json"


def _yday(md: str) -> int:
    """Day-of-year for an 'MM-DD' string, using a fixed non-leap reference."""
    mm, dd = (int(x) for x in md.split("-"))
    return date(2001, mm, dd).timetuple().tm_yday


def _yday_of(d: date) -> int:
    return date(2001, d.month, min(d.day, 28) if d.month == 2 else d.day).timetuple().tm_yday


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


def seasonal_strength(today: date, start: str, peak_start: str,
                      peak_end: str, end: str) -> float:
    """0..1 position in a category's annual demand window (handles year wrap).

    Ramp 0.10→0.70 from start to peak_start, 1.0 across the peak, decay
    0.70→0.10 from peak_end to end, and 0.0 outside the window.
    """
    base = _yday(start)
    span = lambda md: (_yday(md) - base) % 365  # noqa: E731
    ps, pe, en = span(peak_start), span(peak_end), span(end)
    t = (_yday_of(today) - base) % 365
    if t > en:
        return 0.0
    if t <= ps:
        return _lerp(0.10, 0.70, t / ps) if ps else 0.70
    if t <= pe:
        return 1.0
    return _lerp(0.70, 0.10, (t - pe) / (en - pe)) if en > pe else 0.10


class SeasonalProvider(TrendSignalProvider):
    name = "seasonal"
    enabled_by_default = True
    needs_browser = False

    def __init__(self, today: date | None = None, threshold: float = 0.1,
                 path: Path | str | None = None):
        self.today = today or date.today()
        self.threshold = threshold
        self.path = Path(path) if path else DATA_PATH

    def fetch(self, session=None, browser=None) -> list[TrendSignal]:
        try:
            calendar = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:  # noqa: BLE001
            log.warning("Could not read seasonal calendar %s: %s", self.path, exc)
            return []
        out: list[TrendSignal] = []
        for category, w in calendar.items():
            s = seasonal_strength(self.today, w["start"], w["peak_start"],
                                  w["peak_end"], w["end"])
            if s >= self.threshold:
                out.append(TrendSignal(
                    source="seasonal",
                    original_title=f"{category} (in season)",
                    product_category=category,
                    keywords=w.get("keywords", []),
                    trend_strength=0.0,
                    seasonal_strength=round(s, 3),
                    market="UK",
                ))
        return out
