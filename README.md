# fastly-bulk-services

Bulk-create Fastly CDN (VCL) services from an Excel export of an F5 Distributed Cloud load balancer inventory, using the Fastly REST API directly.

One Fastly service is created per domain. Each origin row of that domain becomes a backend in the same service. Services are left in **draft** (version 1, not activated) unless `--activate` is passed.

## Setup

Requires Python 3.11+.

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pytest            # all tests use mocked HTTP, no network
```

## API token

The token is read **only** from the `FASTLY_API_TOKEN` environment variable. It is never accepted as an argument, never written to disk, and masked in logs and reports.

```bash
read -rs FASTLY_API_TOKEN && export FASTLY_API_TOKEN   # paste the token, nothing is echoed
```

`.env.example` documents the variable name only. `.env` is git-ignored.

## Usage

```bash
# 1. Excel -> clean CSV (+ rejected_rows.csv). Read-only, no network.
.venv/bin/fastly-bulk convert --input examples/dummy-5-services.xlsx \
    --output out/dummy/services.csv --rejected out/dummy/rejected_rows.csv

# 2. Dry-run (default): prints the exact API calls and payloads, makes zero write calls.
.venv/bin/fastly-bulk create --csv out/dummy/services.csv
#    Optional read-only existence check (GET /service/search), needs the token:
.venv/bin/fastly-bulk create --csv out/dummy/services.csv --check-exists
#    Restrict to some domains:  --domain a.example.com --domain b.example.com   |   --limit 3

# 3. Real execution. Shows a summary and requires typing 'yes'.
.venv/bin/fastly-bulk create --csv out/dummy/services.csv --apply
#    Optional: --concurrency N (default 1), --runs-dir runs
#    --yes skips the prompt (CI only). --activate also activates v1 and asks a second confirmation
#    (--yes-activate skips it, CI only).

# 4. Undo a run: deletes only DRAFT services listed in that run's manifest.
.venv/bin/fastly-bulk rollback --manifest runs/<timestamp>/manifest.json
```

Each `--apply` run writes `runs/<timestamp>/manifest.json` and `results.csv` (`domain, service_id, status, backends_created, error`). Statuses: `created`, `skipped_exists`, `failed`, `failed_conflict`, `activated`, `activate_failed`. The exit code is non-zero if any domain failed. The run is idempotent: existing services (matched by name) are skipped.

`rollback` refuses to delete a service that has an active version, whose name differs from the manifest, or that is not in the manifest. It writes `rollback_results.csv` next to the manifest.

## Field mapping

| Excel column (`Todos`)      | CSV column       | Fastly API field                                                    |
| --------------------------- | ---------------- | ------------------------------------------------------------------- |
| `fqdn_header_route`         | `domain`         | `POST /service` `name`, `POST .../domain` `name` (`:port` removed)  |
| `fqdn_header_route`         | `service_name`   | service `name` (same as domain)                                     |
| `origin_server`             | `origin_address` | backend `address`                                                   |
| `origin_port outbound f5`   | `origin_port`    | backend `port`                                                      |
| `host_rewrite`              | `host_rewrite`   | backend `override_host` (domain if empty)                           |
| `ambiente F5`               | `environment`    | none (traceability only)                                            |
| `load_balancer f5`          | `source_lb`      | none (traceability only)                                            |
| `route_num`                 | `source_route`   | none (traceability only)                                            |

Ignored columns: `porta_match inbound`, `path`, `http_method`, `name origin_pool`, `namespace F5`, `Novo Namespace Signal Sciences`.

Backend fields set by the tool: `use_ssl=1`, `ssl_check_cert=1`, `ssl_cert_hostname` and `ssl_sni_hostname` (the origin hostname when the origin is a name, the effective Host when the origin is an IP), `auto_loadbalance=1` when the domain has more than one backend. Booleans are sent as `1` (form-encoded), as the Fastly API expects.

## Decisions recorded

- Services are left in **draft**; nothing is activated unless `--activate` is passed and confirmed twice.
- **TLS on every backend**, regardless of port, with certificate verification.
- **Default Host**: when `host_rewrite` is empty, `override_host` is the service domain.
- **Multi-backend**: several origins for one domain become several backends in one service; no directors.
- **TLS names**: SNI and certificate hostname use the origin hostname; for IP origins they use the effective Host.
- `convert` never fixes data silently: suspicious `host_rewrite` values (different registrable domain) only produce warnings. Invalid rows go to `rejected_rows.csv`.

## Out of scope

DNS cutover, TLS certificates for the customer domains (for example through Fastly TLS) and WAF/security configuration are **not** handled by this tool and must be done separately. Domains added to a service are not served until the version is activated and DNS points to Fastly.
