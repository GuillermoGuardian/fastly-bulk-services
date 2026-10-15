from __future__ import annotations

import logging

import pytest
import responses

from fastly_bulk.fastly_client import BASE_URL, FastlyClient, FastlyError
from fastly_bulk.planner import build_plan
from fastly_bulk.runner import dry_run
from fastly_bulk.models import OriginRow

TOKEN = "SECRET-TOKEN-123"


def make_client(**kw):
    return FastlyClient(TOKEN, sleep=lambda s: None, **kw)


@responses.activate
def test_retries_on_429_then_succeeds():
    url = f"{BASE_URL}/service/search?name=a.example.com"
    responses.add(responses.GET, url, status=429, headers={"Fastly-RateLimit-Reset": "0"})
    responses.add(responses.GET, url, status=503)
    responses.add(responses.GET, url, json={"id": "SVC1"})
    assert make_client().find_service_id("a.example.com") == "SVC1"
    assert len(responses.calls) == 3


@responses.activate
def test_search_404_means_not_found():
    responses.add(responses.GET, f"{BASE_URL}/service/search?name=x.example.com", status=404)
    assert make_client().find_service_id("x.example.com") is None


@responses.activate
def test_other_4xx_fails_fast_with_body_and_no_token():
    responses.add(responses.POST, f"{BASE_URL}/service", status=400, body='{"msg":"Bad"}')
    with pytest.raises(FastlyError) as exc:
        make_client().post("/service", {"name": "x"})
    assert "Bad" in str(exc.value) and TOKEN not in str(exc.value)
    assert len(responses.calls) == 1


@responses.activate
def test_token_sent_as_header_but_never_logged_or_in_repr(caplog):
    responses.add(responses.GET, f"{BASE_URL}/service/search?name=a", status=503)
    responses.add(responses.GET, f"{BASE_URL}/service/search?name=a", json={"id": "S"})
    client = make_client()
    with caplog.at_level(logging.DEBUG):
        logging.getLogger("fastly_bulk.fastly_client").warning("debug token=%s", TOKEN)
        client.find_service_id("a")
    assert responses.calls[0].request.headers["Fastly-Key"] == TOKEN
    assert TOKEN not in caplog.text and TOKEN not in repr(client)


@responses.activate
def test_dry_run_makes_no_write_calls_and_marks_existing():
    rows = [OriginRow(d, d, "o.example.com", 443, "", "PRD", "lb", "1") for d in ("a.example.com", "b.example.com")]
    responses.add(responses.GET, f"{BASE_URL}/service/search?name=a.example.com", json={"id": "S1"})
    responses.add(responses.GET, f"{BASE_URL}/service/search?name=b.example.com", status=404)
    text, summary = dry_run(build_plan(rows), make_client())
    assert all(c.request.method == "GET" for c in responses.calls)
    assert summary.existing == ["a.example.com"] and summary.services == 1
    assert "skipped_exists" in text and TOKEN not in text


def test_dry_run_without_client_makes_no_network_calls():
    rows = [OriginRow("a.example.com", "a.example.com", "o.example.com", 443, "", "PRD", "lb", "1")]
    text, summary = dry_run(build_plan(rows))
    assert summary.services == 1 and "POST https://api.fastly.com/service" in text
