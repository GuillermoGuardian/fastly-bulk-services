from __future__ import annotations

import csv
import json

import pytest
import responses

from fastly_bulk import cli
from fastly_bulk.fastly_client import BASE_URL

TOKEN = "SECRET-TOKEN-123"
CSV_HEADER = "service_name,domain,origin_address,origin_port,host_rewrite,environment,source_lb,source_route\n"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("FASTLY_API_TOKEN", TOKEN)
    monkeypatch.setattr("fastly_bulk.fastly_client.time.sleep", lambda s: None)
    rows = [
        "a.example.com,a.example.com,o1.example.com,443,,DUMMY,lb,1",
        "b.example.com,b.example.com,o1.example.com,443,,DUMMY,lb,2",
        "b.example.com,b.example.com,o2.example.com,8443,,DUMMY,lb,3",
    ]
    csv_path = tmp_path / "services.csv"
    csv_path.write_text(CSV_HEADER + "\n".join(rows) + "\n")
    return tmp_path, csv_path


def search(name, found):
    url = f"{BASE_URL}/service/search?name={name}"
    if found:
        responses.add(responses.GET, url, json={"id": f"EXISTING-{name}"})
    else:
        responses.add(responses.GET, url, status=404)


def run_apply(tmp_path, csv_path, *extra):
    return cli.main(["create", "--csv", str(csv_path), "--runs-dir", str(tmp_path / "runs"), "--apply", *extra])


def run_dirs(tmp_path):
    return sorted((tmp_path / "runs").iterdir())


@responses.activate
def test_apply_creates_services_and_writes_manifest_and_report(env):
    tmp_path, csv_path = env
    search("a.example.com", False)
    search("b.example.com", False)
    responses.add(responses.POST, f"{BASE_URL}/service", json={"id": "SVC_A", "versions": [{"number": 1}]})
    responses.add(responses.POST, f"{BASE_URL}/service", json={"id": "SVC_B", "versions": [{"number": 1}]})
    for sid in ("SVC_A", "SVC_B"):
        responses.add(responses.POST, f"{BASE_URL}/service/{sid}/version/1/domain", json={})
        responses.add(responses.POST, f"{BASE_URL}/service/{sid}/version/1/backend", json={})

    assert run_apply(tmp_path, csv_path, "--yes") == 0

    posts = [c.request.url.replace(BASE_URL, "") for c in responses.calls if c.request.method == "POST"]
    assert posts == ["/service", "/service/SVC_A/version/1/domain", "/service/SVC_A/version/1/backend",
                     "/service", "/service/SVC_B/version/1/domain",
                     "/service/SVC_B/version/1/backend", "/service/SVC_B/version/1/backend"]
    assert not any(c.request.method == "PUT" for c in responses.calls)  # never activated by default
    run = run_dirs(tmp_path)[0]
    manifest = json.loads((run / "manifest.json").read_text())
    assert [s["service_id"] for s in manifest["services"]] == ["SVC_A", "SVC_B"]
    rows = list(csv.DictReader((run / "results.csv").open()))
    assert [(r["domain"], r["status"], r["backends_created"]) for r in rows] == [
        ("a.example.com", "created", "1"), ("b.example.com", "created", "2")]
    assert all(TOKEN not in f.read_text() for f in run.iterdir())


@responses.activate
def test_apply_skips_existing_service_idempotently(env):
    tmp_path, csv_path = env
    search("a.example.com", True)
    search("b.example.com", True)
    assert run_apply(tmp_path, csv_path, "--yes") == 0
    assert not any(c.request.method == "POST" for c in responses.calls)
    rows = list(csv.DictReader((run_dirs(tmp_path)[0] / "results.csv").open()))
    assert {r["status"] for r in rows} == {"skipped_exists"}


@responses.activate
def test_conflict_on_one_domain_does_not_abort_others_and_exit_nonzero(env):
    tmp_path, csv_path = env
    search("a.example.com", False)
    search("b.example.com", False)
    responses.add(responses.POST, f"{BASE_URL}/service", json={"id": "SVC_A", "versions": [{"number": 1}]})
    responses.add(responses.POST, f"{BASE_URL}/service", json={"id": "SVC_B", "versions": [{"number": 1}]})
    responses.add(responses.POST, f"{BASE_URL}/service/SVC_A/version/1/domain", status=409, body="domain in use")
    responses.add(responses.POST, f"{BASE_URL}/service/SVC_B/version/1/domain", json={})
    responses.add(responses.POST, f"{BASE_URL}/service/SVC_B/version/1/backend", json={})

    assert run_apply(tmp_path, csv_path, "--yes") == 1
    rows = {r["domain"]: r for r in csv.DictReader((run_dirs(tmp_path)[0] / "results.csv").open())}
    assert rows["a.example.com"]["status"] == "failed_conflict" and "domain in use" in rows["a.example.com"]["error"]
    assert rows["b.example.com"]["status"] == "created"


@responses.activate
def test_declining_confirmation_makes_no_write_calls(env, monkeypatch):
    tmp_path, csv_path = env
    search("a.example.com", False)
    search("b.example.com", False)
    monkeypatch.setattr("builtins.input", lambda _: "no")
    assert run_apply(tmp_path, csv_path) == 1
    assert all(c.request.method == "GET" for c in responses.calls)
    assert not (tmp_path / "runs").exists()


@responses.activate
def test_activate_only_with_flag_and_second_confirmation(env, monkeypatch):
    tmp_path, csv_path = env
    search("a.example.com", False)
    search("b.example.com", False)
    answers = iter(["yes", "no"])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert run_apply(tmp_path, csv_path, "--activate") == 1
    assert not any(c.request.method in ("POST", "PUT") for c in responses.calls)


@responses.activate
def test_activate_calls_put_when_confirmed(env):
    tmp_path, csv_path = env
    search("a.example.com", False)
    search("b.example.com", False)
    for sid in ("SVC_A", "SVC_B"):
        responses.add(responses.POST, f"{BASE_URL}/service", json={"id": sid, "versions": [{"number": 1}]})
        responses.add(responses.POST, f"{BASE_URL}/service/{sid}/version/1/domain", json={})
        responses.add(responses.POST, f"{BASE_URL}/service/{sid}/version/1/backend", json={})
        responses.add(responses.PUT, f"{BASE_URL}/service/{sid}/version/1/activate", json={})
    assert run_apply(tmp_path, csv_path, "--yes", "--activate", "--yes-activate") == 0
    puts = [c.request.url for c in responses.calls if c.request.method == "PUT"]
    assert len(puts) == 2


def write_manifest(tmp_path):
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"services": [
        {"domain": "draft.example.com", "service_id": "S_DRAFT", "version": 1},
        {"domain": "active.example.com", "service_id": "S_ACTIVE", "version": 1},
        {"domain": "renamed.example.com", "service_id": "S_RENAMED", "version": 1},
        {"domain": "gone.example.com", "service_id": "S_GONE", "version": 1},
    ]}))
    return path


@responses.activate
def test_rollback_deletes_only_draft_services_in_manifest(env):
    tmp_path, _ = env
    manifest = write_manifest(tmp_path)
    responses.add(responses.GET, f"{BASE_URL}/service/S_DRAFT/details",
                  json={"name": "draft.example.com", "versions": [{"number": 1, "active": False}]})
    responses.add(responses.GET, f"{BASE_URL}/service/S_ACTIVE/details",
                  json={"name": "active.example.com", "active_version": {"number": 1}, "versions": [{"active": True}]})
    responses.add(responses.GET, f"{BASE_URL}/service/S_RENAMED/details", json={"name": "other.example.com", "versions": []})
    responses.add(responses.GET, f"{BASE_URL}/service/S_GONE/details", status=404)
    responses.add(responses.DELETE, f"{BASE_URL}/service/S_DRAFT", json={"status": "ok"})

    assert cli.main(["rollback", "--manifest", str(manifest), "--yes"]) == 1  # refusals -> non-zero
    deletes = [c.request.url for c in responses.calls if c.request.method == "DELETE"]
    assert deletes == [f"{BASE_URL}/service/S_DRAFT"]
    report = {r["domain"]: r["action"] for r in csv.DictReader((tmp_path / "rollback_results.csv").open())}
    assert report == {"draft.example.com": "deleted", "active.example.com": "refuse",
                      "renamed.example.com": "refuse", "gone.example.com": "already_deleted"}


@responses.activate
def test_rollback_declined_deletes_nothing(env, monkeypatch):
    tmp_path, _ = env
    manifest = write_manifest(tmp_path)
    responses.add(responses.GET, f"{BASE_URL}/service/S_DRAFT/details",
                  json={"name": "draft.example.com", "versions": [{"active": False}]})
    for sid in ("S_ACTIVE", "S_RENAMED", "S_GONE"):
        responses.add(responses.GET, f"{BASE_URL}/service/{sid}/details", status=404)
    monkeypatch.setattr("builtins.input", lambda _: "n")
    assert cli.main(["rollback", "--manifest", str(manifest)]) == 1
    assert not any(c.request.method == "DELETE" for c in responses.calls)
