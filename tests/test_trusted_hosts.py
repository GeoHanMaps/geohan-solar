"""Sprint 9 #5a — TrustedHost / CORS decoupling.

Regression guard for issue_trusted_host_cors_coupling: the healthcheck
returned 400 because TrustedHostMiddleware's allowed_hosts was *derived
from* CORS_ORIGINS. Now trusted_hosts is an independent setting and the
internal (healthcheck / SSL terminator / TestClient) hosts are always
whitelisted, so that coupling can't recur.
"""
from app.main import _ALWAYS_TRUSTED_HOSTS, compute_trusted_hosts


def test_wildcard_cors_skips_middleware():
    assert compute_trusted_hosts(["*"], []) is None


def test_internal_hosts_always_present_even_with_restrictive_cors():
    # No localhost in CORS, no explicit trusted_hosts → the old code would
    # have 400'd the healthcheck. Now internal hosts are guaranteed.
    hosts = compute_trusted_hosts(["https://geohanmaps.com"], [])
    assert "geohanmaps.com" in hosts
    assert _ALWAYS_TRUSTED_HOSTS.issubset(set(hosts))


def test_trusted_hosts_independent_of_cors():
    hosts = compute_trusted_hosts(
        ["https://app.example.com"],          # CORS origin
        ["internal.lb", "geohanmaps.com"],    # explicit trusted_hosts
    )
    # Derived-from-CORS host is NOT used when trusted_hosts is set.
    assert "app.example.com" not in hosts
    assert "internal.lb" in hosts
    assert "geohanmaps.com" in hosts
    assert _ALWAYS_TRUSTED_HOSTS.issubset(set(hosts))


def test_result_is_sorted_and_deduped():
    hosts = compute_trusted_hosts(["https://geohanmaps.com"], ["localhost"])
    assert hosts == sorted(set(hosts))
