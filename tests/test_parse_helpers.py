"""Unit tests for parse_price_range (app/scraper.py). Pure function."""
from app.scraper import parse_price_range


def test_paired_prices():
    assert parse_price_range("53.58 53.58") == [53.58, 53.58]


def test_range_with_dash():
    assert parse_price_range("71.70-80.60") == [71.70, 80.60]


def test_empty_markers_return_none():
    for val in ("", "-", "0.00", "0.00 - 0.00", "#N/A", None):
        assert parse_price_range(val) is None


def test_out_of_range_values_ignored():
    assert parse_price_range("5.00") is None
    assert parse_price_range("200.00") is None
    assert parse_price_range("abc") is None
