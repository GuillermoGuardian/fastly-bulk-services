from __future__ import annotations

import csv
import json
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path

import requests

from .fastly_client import FastlyClient, FastlyError
from .planner import ServicePlan

log = logging.getLogger(__name__)


@dataclass
class DryRunSummary:
    services: int
    backends: int
    existing: list[str]


def render_dry_run(plans: list[ServicePlan], existing: set[str] | None = None) -> str:
    existing = existing or set()
    lines: list[str] = []
    for plan in plans:
        header = f"=== {plan.domain} ({len(plan.backends)} backend(s))"
        if plan.domain in existing:
            lines.append(f"{header} -- ALREADY EXISTS, would be skipped (skipped_exists)")
            continue
        lines.append(header)
        for call in plan.calls():
            lines.append(f"  {call.method} https://api.fastly.com{call.path}")
            if call.payload:
                lines.append("    " + json.dumps(call.payload, ensure_ascii=False))
        lines.append("")
    return "\n".join(lines)


def check_existing(client: FastlyClient, plans: list[ServicePlan]) -> set[str]:
    """Read-only GET /service/search per domain."""
    return {p.domain for p in plans if client.find_service_id(p.domain)}


def dry_run(plans: list[ServicePlan], client: FastlyClient | None = None) -> tuple[str, DryRunSummary]:
    existing = check_existing(client, plans) if client else set()
    pending = [p for p in plans if p.domain not in existing]
    summary = DryRunSummary(
        services=len(pending),
        backends=sum(len(p.backends) for p in pending),
        existing=sorted(existing),
    )
    return render_dry_run(plans, existing), summary


# ---------------------------------------------------------------- apply

RESULT_COLUMNS = ["domain", "service_id", "status", "backends_created", "error"]


@dataclass
class ServiceResult:
    domain: str
    service_id: str = ""
    version: int = 1
    status: str = ""
    backends: list[str] = field(default_factory=list)
    error: str = ""


def _mask(text: str, client: FastlyClient) -> str:
    return text.replace(client._token, "***")


def _create_one(client: FastlyClient, plan: ServicePlan) -> ServiceResult:
    result = ServiceResult(plan.domain)
    try:
        first = plan.calls()[0]
        svc = client.post(first.path, first.payload or {})
        result.service_id = svc["id"]
        versions = svc.get("versions") or [{"number": 1}]
        result.version = int(versions[0].get("number", 1))
        for call in plan.calls(result.service_id, result.version)[1:]:
            client.post(call.path, call.payload or {})
            if call.path.endswith("/backend"):
                result.backends.append(call.payload["name"])
        result.status = "created"
    except FastlyError as exc:
        result.status = "failed_conflict" if exc.status == 409 else "failed"
        result.error = _mask(str(exc), client)
    except requests.RequestException as exc:
        result.status = "failed"
        result.error = _mask(f"{type(exc).__name__}: {exc}", client)
    return result


class RunWriter:
    """Writes manifest.json and results.csv incrementally so a crash keeps what was created."""

    def __init__(self, run_dir: Path):
        self.run_dir = run_dir
        run_dir.mkdir(parents=True, exist_ok=True)
        self.results: list[ServiceResult] = []
        self._lock = threading.Lock()

    def add(self, result: ServiceResult) -> None:
        with self._lock:
            self.results.append(result)
            self._flush()

    def _flush(self) -> None:
        manifest = {"services": [
            {"domain": r.domain, "service_id": r.service_id, "version": r.version,
             "status": r.status, "backends": r.backends}
            for r in self.results if r.service_id
        ]}
        (self.run_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
        with (self.run_dir / "results.csv").open("w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(RESULT_COLUMNS)
            for r in self.results:
                w.writerow([r.domain, r.service_id, r.status, len(r.backends), r.error])


def apply_plans(
    client: FastlyClient, plans: list[ServicePlan], existing: set[str], run_dir: Path, concurrency: int = 1,
) -> list[ServiceResult]:
    from concurrent.futures import ThreadPoolExecutor

    writer = RunWriter(run_dir)

    def work(plan: ServicePlan) -> None:
        if plan.domain in existing:
            writer.add(ServiceResult(plan.domain, status="skipped_exists"))
        else:
            writer.add(_create_one(client, plan))

    with ThreadPoolExecutor(max_workers=max(1, concurrency)) as pool:
        list(pool.map(work, plans))
    return writer.results


def activate_results(client: FastlyClient, results: list[ServiceResult], run_dir: Path) -> None:
    writer = RunWriter(run_dir)
    for r in results:
        if r.status == "created":
            try:
                client.put(f"/service/{r.service_id}/version/{r.version}/activate")
                r.status = "activated"
            except (FastlyError, requests.RequestException) as exc:
                r.status = "activate_failed"
                r.error = _mask(str(exc), client)
        writer.add(r)


# ---------------------------------------------------------------- rollback


@dataclass
class RollbackItem:
    domain: str
    service_id: str
    action: str  # "delete" | "refuse" | "already_deleted"
    reason: str = ""


def plan_rollback(client: FastlyClient, manifest_path: Path) -> list[RollbackItem]:
    """Read-only: decide, per manifest entry, whether it is safe to delete."""
    manifest = json.loads(manifest_path.read_text())
    items: list[RollbackItem] = []
    for svc in manifest["services"]:
        domain, sid = svc["domain"], svc["service_id"]
        resp = client.get(f"/service/{sid}/details")
        if resp.status_code == 404:
            items.append(RollbackItem(domain, sid, "already_deleted"))
            continue
        if resp.status_code >= 400:
            items.append(RollbackItem(domain, sid, "refuse", f"cannot inspect service: HTTP {resp.status_code}"))
            continue
        details = resp.json()
        if details.get("name") != domain:
            items.append(RollbackItem(domain, sid, "refuse", f"name mismatch: {details.get('name')!r}"))
        elif details.get("active_version") or any(v.get("active") for v in details.get("versions", [])):
            items.append(RollbackItem(domain, sid, "refuse", "service has an active version"))
        else:
            items.append(RollbackItem(domain, sid, "delete"))
    return items


def execute_rollback(client: FastlyClient, items: list[RollbackItem], run_dir: Path) -> list[RollbackItem]:
    done: list[RollbackItem] = []
    for item in items:
        if item.action == "delete":
            resp = client.delete(f"/service/{item.service_id}")
            if resp.status_code >= 400:
                item = RollbackItem(item.domain, item.service_id, "refuse", f"delete failed: HTTP {resp.status_code}")
            else:
                item = RollbackItem(item.domain, item.service_id, "deleted")
        done.append(item)
    with (run_dir / "rollback_results.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["domain", "service_id", "action", "reason"])
        w.writerows([[i.domain, i.service_id, i.action, i.reason] for i in done])
    return done
