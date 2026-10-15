from __future__ import annotations

from dataclasses import dataclass, field

CSV_COLUMNS = [
    "service_name",
    "domain",
    "origin_address",
    "origin_port",
    "host_rewrite",
    "environment",
    "source_lb",
    "source_route",
]


@dataclass(frozen=True)
class OriginRow:
    service_name: str
    domain: str
    origin_address: str
    origin_port: int
    host_rewrite: str
    environment: str
    source_lb: str
    source_route: str

    def as_csv_dict(self) -> dict[str, str | int]:
        return {c: getattr(self, c) for c in CSV_COLUMNS}


@dataclass(frozen=True)
class RejectedRow:
    excel_row: int
    reason: str
    raw_fqdn: str
    raw_origin_server: str
    raw_origin_port: str
    environment: str
    source_lb: str
    source_route: str


@dataclass
class ConvertResult:
    rows_read: int = 0
    rows: list[OriginRow] = field(default_factory=list)
    rejected: list[RejectedRow] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    duplicates_dropped: int = 0

    @property
    def unique_domains(self) -> int:
        return len({r.domain for r in self.rows})

    @property
    def unique_origins(self) -> int:
        return len({(r.origin_address, r.origin_port) for r in self.rows})
