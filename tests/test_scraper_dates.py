"""Unit tests for parse_attachment_date (app/scraper.py).

Covers every title format seen on the live DOE pages (Sept 2026 redesign).
Pure function — no network, no database.
"""
from datetime import datetime, timezone

from app.scraper import parse_attachment_date

THIS_YEAR = datetime.now(timezone.utc).year


def dt(year, month, day):
    return datetime(year, month, day, tzinfo=timezone.utc)


def test_day_range_before_abbrev_month():
    assert parse_attachment_date("Prior Notice on LF Price Adjustments 15-21 Sep 2026") == dt(2026, 9, 15)


def test_day_range_before_full_month():
    assert parse_attachment_date("List of North Luzon Pump Prices 01-07 September 2026") == dt(2026, 9, 1)


def test_month_first_with_year():
    assert parse_attachment_date("Prior Notice on Price Adjustments as of August 25-31, 2026 [For Fuels]") == dt(2026, 8, 25)


def test_month_first_without_year_defaults_to_current_year():
    assert parse_attachment_date("Prior Notice on Price Adjustments Sep 8-14") == dt(THIS_YEAR, 9, 8)
    assert parse_attachment_date("September 15-21") == dt(THIS_YEAR, 9, 15)
    assert parse_attachment_date("September 1 to 7") == dt(THIS_YEAR, 9, 1)


def test_cross_month_range_uses_first_month():
    assert parse_attachment_date("Prior Notice on Price Adjustments as of July 28-Aug 03, 2026 [For Fuels]") == dt(2026, 7, 28)
    assert parse_attachment_date("July 28 to August 3") == dt(THIS_YEAR, 7, 28)


def test_dated_report_with_trailing_suffix():
    assert parse_attachment_date(
        "NORTHERN LUZON LIQUID FUELS PRICE MONITORING REPORT DATED SEPTEMBER 17 2026-2-12"
    ) == dt(2026, 9, 17)


def test_no_month_returns_none():
    assert parse_attachment_date("DOE Department Circular 2026-01") is None
    assert parse_attachment_date("") is None


def test_impossible_date_returns_none():
    assert parse_attachment_date("Price Adjustments Feb 30 2026") is None
