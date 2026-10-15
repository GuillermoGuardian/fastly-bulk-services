from __future__ import annotations

from fastly_bulk.models import OriginRow
from fastly_bulk.planner import build_plan


def origin(domain="a.example.com", addr="o.example.com", port=443, rewrite=""):
    return OriginRow(domain, domain, addr, port, rewrite, "PRD", "lb", "1")


def test_default_override_host_is_domain_when_rewrite_empty():
    backend = build_plan([origin(rewrite="")])[0].backends[0]
    assert backend.override_host == "a.example.com"


def test_tls_hostnames_use_origin_hostname_when_origin_is_a_name():
    payload = build_plan([origin(addr="o.example.com", rewrite="r.example.com")])[0].backends[0].payload()
    assert payload["ssl_cert_hostname"] == payload["ssl_sni_hostname"] == "o.example.com"
    assert payload["override_host"] == "r.example.com"


def test_tls_hostnames_use_effective_host_when_origin_is_ip():
    payload = build_plan([origin(addr="10.0.0.5", rewrite="")])[0].backends[0].payload()
    assert payload["ssl_cert_hostname"] == payload["ssl_sni_hostname"] == "a.example.com"


def test_rewrite_used_when_present():
    backend = build_plan([origin(rewrite="r.example.com")])[0].backends[0]
    assert backend.override_host == "r.example.com"


def test_multiple_origins_become_backends_in_one_service():
    plans = build_plan([origin(port=443), origin(port=1002), origin(domain="b.example.com")])
    assert [len(p.backends) for p in plans] == [2, 1]
    first = plans[0].backends
    assert len({b.name for b in first}) == 2
    assert all(b.payload().get("auto_loadbalance") == "1" for b in first)
    assert "auto_loadbalance" not in plans[1].backends[0].payload()


def test_backend_payload_enables_tls_and_cert_check():
    payload = build_plan([origin(addr="10.0.0.5")])[0].backends[0].payload()
    assert payload["use_ssl"] == "1" and payload["ssl_check_cert"] == "1"
    assert payload["name"] == "origin-1-10-0-0-5-443"


def test_calls_order_and_service_is_vcl():
    calls = build_plan([origin()])[0].calls()
    assert [c.path.rsplit("/", 1)[-1] for c in calls] == ["service", "domain", "backend"]
    assert calls[0].payload["type"] == "vcl"
