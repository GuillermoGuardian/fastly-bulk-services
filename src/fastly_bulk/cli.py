from __future__ import annotations

import argparse
import logging
from datetime import datetime, timezone
import os
import sys
from pathlib import Path

from . import excel_to_csv, planner, runner
from .fastly_client import FastlyClient


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fastly-bulk", description=__doc__)
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    conv = sub.add_parser("convert", help="Convert the Excel export into a clean CSV")
    conv.add_argument("--input", type=Path, required=True)
    conv.add_argument("--sheet", default="Todos")
    conv.add_argument("--output", type=Path, default=Path("out/services.csv"))
    conv.add_argument("--rejected", type=Path, default=Path("out/rejected_rows.csv"))

    create = sub.add_parser("create", help="Create Fastly services (dry-run unless --apply)")
    create.add_argument("--csv", type=Path, default=Path("out/services.csv"))
    create.add_argument("--domain", action="append", default=[], help="Only these domains (repeatable)")
    create.add_argument("--limit", type=int, help="Only the first N domains")
    create.add_argument("--check-exists", action="store_true",
                        help="Dry-run only: read-only GET /service/search per domain (needs FASTLY_API_TOKEN)")
    create.add_argument("--apply", action="store_true", help="Execute for real (default is dry-run)")
    create.add_argument("--yes", action="store_true", help="Skip the interactive 'yes' (CI only)")
    create.add_argument("--activate", action="store_true", help="Also activate version 1 (extra confirmation)")
    create.add_argument("--yes-activate", action="store_true", help="Skip the activation confirmation (CI only)")
    create.add_argument("--concurrency", type=int, default=1)
    create.add_argument("--runs-dir", type=Path, default=Path("runs"))

    rb = sub.add_parser("rollback", help="Delete draft services created by a previous run")
    rb.add_argument("--manifest", type=Path, required=True)
    rb.add_argument("--yes", action="store_true", help="Skip the interactive 'yes' (CI only)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    if args.command == "convert":
        result = excel_to_csv.run_convert(args.input, args.sheet, args.output, args.rejected)
        print(excel_to_csv.summary(result))
        print(f"\nCSV: {args.output}\nRejected: {args.rejected}")
        return 0
    if args.command == "create":
        return _create(args)
    if args.command == "rollback":
        return _rollback(args)
    return 2


def _token_client() -> FastlyClient | None:
    token = os.environ.get("FASTLY_API_TOKEN", "")
    if not token:
        print("FASTLY_API_TOKEN is not set.", file=sys.stderr)
        return None
    return FastlyClient(token)


def _confirm(prompt: str, expected: str = "yes") -> bool:
    return input(f"{prompt} Type '{expected}' to continue: ").strip() == expected


def _create(args: argparse.Namespace) -> int:
    if args.activate and not args.apply:
        print("--activate requires --apply.", file=sys.stderr)
        return 2
    plans = planner.build_plan(planner.load_csv(args.csv))
    if args.domain:
        wanted = {d.lower() for d in args.domain}
        plans = [p for p in plans if p.domain in wanted]
    if args.limit:
        plans = plans[: args.limit]
    client = None
    if args.check_exists or args.apply:
        client = _token_client()
        if client is None:
            return 2
    if args.apply:
        return _apply(args, plans, client)
    text, summary = runner.dry_run(plans, client)
    print(text)
    print("=== DRY-RUN: no write calls were made ===")
    print(f"services to create: {summary.services}, backends: {summary.backends}")
    if summary.existing:
        print(f"already exist (skipped): {', '.join(summary.existing)}")
    return 0


def _apply(args: argparse.Namespace, plans: list, client: FastlyClient) -> int:
    existing = runner.check_existing(client, plans)
    pending = [p for p in plans if p.domain not in existing]
    print(f"About to create {len(pending)} service(s), {len(pending)} domain(s), "
          f"{sum(len(p.backends) for p in pending)} backend(s) in the Fastly account of the current token.")
    if existing:
        print(f"Already exist (will be skipped): {', '.join(sorted(existing))}")
    if args.activate:
        print("WARNING: --activate will also ACTIVATE version 1 of every created service.")
    if not pending and not existing:
        return 0
    if not args.yes and not _confirm("Proceed?"):
        print("Aborted. Nothing was created.")
        return 1
    if args.activate and not args.yes_activate and not _confirm("Activation is high impact. Confirm again.", "activate"):
        print("Aborted. Nothing was created.")
        return 1

    run_dir = args.runs_dir / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    results = runner.apply_plans(client, plans, existing, run_dir, args.concurrency)
    if args.activate:
        runner.activate_results(client, results, run_dir)
    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(f"Done: {counts}\nManifest: {run_dir / 'manifest.json'}\nReport:   {run_dir / 'results.csv'}")
    failed = any(r.status.startswith(("failed", "activate_failed")) for r in results)
    return 1 if failed else 0


def _rollback(args: argparse.Namespace) -> int:
    client = _token_client()
    if client is None:
        return 2
    items = runner.plan_rollback(client, args.manifest)
    for i in items:
        print(f"  {i.action.upper():16} {i.domain} ({i.service_id}) {i.reason}")
    to_delete = [i for i in items if i.action == "delete"]
    if not to_delete:
        print("Nothing to delete.")
        return 0
    if not args.yes and not _confirm(f"Delete {len(to_delete)} draft service(s)?"):
        print("Aborted. Nothing was deleted.")
        return 1
    done = runner.execute_rollback(client, items, args.manifest.parent)
    print({a: sum(1 for i in done if i.action == a) for a in {i.action for i in done}})
    return 1 if any(i.action == "refuse" for i in done) else 0


if __name__ == "__main__":
    sys.exit(main())
