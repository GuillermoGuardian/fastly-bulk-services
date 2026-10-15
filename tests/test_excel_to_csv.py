from __future__ import annotations

from fastly_bulk.excel_to_csv import (
    COL_ENV, COL_FQDN, COL_HOST_REWRITE, COL_LB, COL_ORIGIN, COL_PORT, COL_ROUTE, convert_rows,
)


def row(fqdn="a.example.com", origin="origin.example.com", port="443", rewrite="a.example.com"):
    return {
        COL_FQDN: fqdn, COL_ORIGIN: origin, COL_PORT: port, COL_HOST_REWRITE: rewrite,
        COL_ENV: "PRD", COL_LB: "lb1", COL_ROUTE: "1",
    }


def run(*records):
    return convert_rows(enumerate(records, start=2))


def test_strips_port_from_domain_and_lowercases():
    res = run(row(fqdn=" Ecommerce.Example.com.br:1002 "))
    assert res.rows[0].domain == "ecommerce.example.com.br"
    assert res.rows[0].service_name == res.rows[0].domain


def test_rejects_missing_fqdn_origin_and_port():
    res = run(row(fqdn=""), row(origin=""), row(port=""), row(port="70000"), row(port="abc"))
    assert not res.rows
    assert [r.reason for r in res.rejected] == [
        "empty fqdn_header_route", "empty origin_server", "empty origin_port",
        "invalid origin port: '70000'", "invalid origin port: 'abc'",
    ]


def test_rejects_invalid_domain_and_address():
    res = run(row(fqdn="https://bad.example.com"), row(origin="not a host!"))
    assert len(res.rejected) == 2 and not res.rows


def test_hostname_origin_lowercased_ip_kept():
    res = run(row(origin="MY-ELB.SA-EAST-1.ELB.AMAZONAWS.COM"), row(origin="10.0.0.5"))
    assert res.rows[0].origin_address == "my-elb.sa-east-1.elb.amazonaws.com"
    assert res.rows[1].origin_address == "10.0.0.5"


def test_port_accepts_numeric_float_text():
    assert run(row(port="443.0")).rows[0].origin_port == 443


def test_deduplicates_on_domain_origin_port():
    res = run(row(), row())
    assert len(res.rows) == 1 and res.duplicates_dropped == 1


def test_same_key_different_rewrite_keeps_both_and_warns():
    res = run(row(rewrite="a.example.com"), row(rewrite="b.example.com"))
    assert len(res.rows) == 2
    assert any("different host_rewrite" in w for w in res.warnings)


def test_different_rewrites_per_domain_warns():
    res = run(row(port="443", rewrite="a.example.com"), row(port="444", rewrite="b.example.com"))
    assert len(res.rows) == 2
    assert any(w.startswith("domain a.example.com has different host_rewrite") for w in res.warnings)


def test_warns_on_suspicious_host_rewrite_and_does_not_fix_it():
    res = run(row(fqdn="shop.example.com.br",
                  rewrite="shop.exampel.com.br"))
    assert res.rows[0].host_rewrite == "shop.exampel.com.br"
    assert any("possible typo" in w for w in res.warnings)


def test_same_registrable_domain_rewrite_does_not_warn():
    res = run(row(fqdn="x.example.com.br", rewrite="y.example.com.br"))
    assert not res.warnings


def test_empty_rewrite_kept_empty():
    res = run(row(rewrite=""))
    assert res.rows[0].host_rewrite == "" and not res.warnings
