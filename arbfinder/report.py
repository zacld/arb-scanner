"""CSV + console output of comparison results."""

from __future__ import annotations

import csv
from pathlib import Path

from .models import Comparison

CSV_COLUMNS = [
    "product_name", "source", "source_price", "market", "market_price",
    "diff_gbp", "diff_pct", "n_listings", "matched_by", "source_url", "market_url",
]


def sort_comparisons(comparisons: list[Comparison], by: str = "abs") -> list[Comparison]:
    key = (lambda c: c.diff_pct) if by == "pct" else (lambda c: c.diff_abs)
    return sorted(comparisons, key=key, reverse=True)


def write_csv(comparisons: list[Comparison], path: str | Path) -> Path:
    path = Path(path)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(CSV_COLUMNS)
        for c in comparisons:
            writer.writerow([
                c.product.name, c.product.source, f"{c.product.price:.2f}",
                c.market, f"{c.market_price:.2f}", f"{c.diff_abs:.2f}",
                f"{c.diff_pct:.1f}", c.n_listings, c.matched_by,
                c.product.url, c.market_url,
            ])
    return path


def _trunc(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def format_table(comparisons: list[Comparison], name_width: int = 44) -> str:
    header = (
        f"{'Product':<{name_width}}  {'Argos £':>8}  {'eBay £':>8}  "
        f"{'Diff £':>8}  {'Diff %':>7}  {'N':>3}  {'Match':<5}"
    )
    lines = [header, "-" * len(header)]
    for c in comparisons:
        lines.append(
            f"{_trunc(c.product.name, name_width):<{name_width}}  "
            f"{c.product.price:>8.2f}  {c.market_price:>8.2f}  "
            f"{c.diff_abs:>+8.2f}  {c.diff_pct:>+6.1f}%  {c.n_listings:>3}  {c.matched_by:<5}"
        )
    return "\n".join(lines)
