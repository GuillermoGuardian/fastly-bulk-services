from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from .models import OriginRow
from .validation import is_ip

SERVICE_COMMENT = "Created by fastly-bulk-services from F5XC inventory"
VERSION = 1


@dataclass(frozen=True)
class ApiCall:
    method: str
    path: str
    payload: dict[str, str] | None = None


@dataclass(frozen=True)
class BackendPlan:
    name: str
    address: str
    port: int
    override_host: str
    ssl_hostname: str
    auto_loadbalance: bool

    def payload(self) -> dict[str, str]:
        data = {
            "name": self.name,
            "address": self.address,
            "port": str(self.port),
            "use_ssl": "1",
            "ssl_check_cert": "1",
            "ssl_cert_hostname": self.ssl_hostname,
            "ssl_sni_hostname": self.ssl_hostname,
            "override_host": self.override_host,
        }
        if self.auto_loadbalance:
            data["auto_loadbalance"] = "1"
        return data


@dataclass
class ServicePlan:
    domain: str
    backends: list[BackendPlan] = field(default_factory=list)

    def calls(self, service_id: str = "{service_id}", version: int = VERSION) -> list[ApiCall]:
        base = f"/service/{service_id}/version/{version}"
        calls = [
            ApiCall("POST", "/service", {"name": self.domain, "type": "vcl", "comment": SERVICE_COMMENT}),
            ApiCall("POST", f"{base}/domain", {"name": self.domain}),
        ]
        calls += [ApiCall("POST", f"{base}/backend", b.payload()) for b in self.backends]
        return calls


def sanitize_backend_name(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]", "-", raw)


def load_csv(path: Path) -> list[OriginRow]:
    with path.open(newline="", encoding="utf-8") as fh:
        return [
            OriginRow(
                service_name=r["service_name"], domain=r["domain"],
                origin_address=r["origin_address"], origin_port=int(r["origin_port"]),
                host_rewrite=r["host_rewrite"], environment=r["environment"],
                source_lb=r["source_lb"], source_route=r["source_route"],
            )
            for r in csv.DictReader(fh)
        ]


def build_plan(rows: list[OriginRow]) -> list[ServicePlan]:
    by_domain: dict[str, list[OriginRow]] = defaultdict(list)
    for row in rows:
        by_domain[row.domain].append(row)

    plans: list[ServicePlan] = []
    for domain, origins in by_domain.items():
        plan = ServicePlan(domain=domain)
        for n, o in enumerate(origins, start=1):
            effective_host = o.host_rewrite or domain
            plan.backends.append(BackendPlan(
                name=sanitize_backend_name(f"origin-{n}-{o.origin_address}-{o.origin_port}"),
                address=o.origin_address,
                port=o.origin_port,
                override_host=effective_host,
                ssl_hostname=effective_host if is_ip(o.origin_address) else o.origin_address,
                auto_loadbalance=len(origins) > 1,
            ))
        plans.append(plan)
    return plans
