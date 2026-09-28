"""Unit tests for the SSRF guard (app/security.py).

The allowlist rejections need no network (they fail before DNS). The two
positive tests do real DNS lookups, proving allowlist + resolver agree.
"""
from app.security import ALLOWED_DOMAINS, is_internal_ip, validate_and_resolve_url


def test_internal_ips_rejected():
    assert is_internal_ip("127.0.0.1")
    assert is_internal_ip("10.0.0.5")
    assert is_internal_ip("192.168.1.1")
    assert is_internal_ip("169.254.169.254")  # cloud metadata endpoint


def test_public_ip_accepted():
    assert not is_internal_ip("8.8.8.8")


def test_invalid_input_treated_as_internal():
    assert is_internal_ip("not-an-ip")


def test_scheme_must_be_https():
    assert not validate_and_resolve_url("http://doe.gov.ph/data-and-prices/")
    assert not validate_and_resolve_url("ftp://doe.gov.ph/file.pdf")


def test_unknown_domain_rejected_without_dns():
    # Rejected by the allowlist before any DNS lookup happens.
    assert not validate_and_resolve_url("https://evil.example/file.pdf")
    assert not validate_and_resolve_url("https://doe.gov.ph.evil.example/")
    assert not validate_and_resolve_url("not a url")
    assert not validate_and_resolve_url("")


def test_cdn_domain_allowlisted():
    assert "d24qbtp4vooyzi.cloudfront.net" in ALLOWED_DOMAINS


def test_live_allowed_urls_pass():
    assert validate_and_resolve_url(
        "https://doe.gov.ph/data-and-prices/liquid-fuels/retail-pump-prices/price-adjustments"
    )
    assert validate_and_resolve_url(
        "https://d24qbtp4vooyzi.cloudfront.net/api/media/file/Prior%20Notice%20on%20LF%20Price%20Adjustment%2015-21%20Sep%202026.pdf?prefix=dev%2Fmedia"
    )
