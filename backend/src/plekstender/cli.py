"""Command-line entry point: `plekstender web|worker|migrate|healthcheck|openapi`."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import urllib.request

from plekstender.config import get_settings


def _setup_logging() -> None:
    from plekstender.logs import setup_logging

    setup_logging(get_settings().log_level)


def cmd_web(_: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    # Proxy headers are honoured for addresses in FORWARDED_ALLOW_IPS (uvicorn reads the env).
    uvicorn.run(
        "plekstender.api.app:create_app",
        factory=True,
        host="0.0.0.0",  # noqa: S104 - container entry point
        port=settings.port,
        proxy_headers=True,
        log_config=None,
        server_header=False,
    )
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    from plekstender.worker import app

    async def run() -> None:
        async with app.open_async():
            await app.run_worker_async(concurrency=args.concurrency, install_signal_handlers=True)

    asyncio.run(run())
    return 0


def cmd_migrate(_: argparse.Namespace) -> int:
    from plekstender.migrate import migrate

    migrate()
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    settings = get_settings()
    if args.target == "web":
        url = f"http://127.0.0.1:{settings.port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                return 0 if resp.status == 200 else 1
        except OSError:
            return 1

    from plekstender.api.routes.health import worker_is_healthy
    from plekstender.db import get_sessionmaker
    from plekstender.db.instance import get_worker_heartbeat

    async def check() -> bool:
        async with get_sessionmaker()() as db:
            return worker_is_healthy(await get_worker_heartbeat(db))

    return 0 if asyncio.run(check()) else 1


def cmd_openapi(_: argparse.Namespace) -> int:
    from plekstender.api.app import create_app

    json.dump(create_app().openapi(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="plekstender")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("web", help="Run the web app (API + frontend)").set_defaults(func=cmd_web)
    worker = sub.add_parser("worker", help="Run the background job worker")
    worker.add_argument("--concurrency", type=int, default=4)
    worker.set_defaults(func=cmd_worker)
    sub.add_parser("migrate", help="Apply database migrations").set_defaults(func=cmd_migrate)
    hc = sub.add_parser("healthcheck", help="Exit 0 if the given service is healthy")
    hc.add_argument("target", choices=["web", "worker"])
    hc.set_defaults(func=cmd_healthcheck)
    sub.add_parser("openapi", help="Print the OpenAPI schema").set_defaults(func=cmd_openapi)

    args = parser.parse_args(argv)
    if args.command != "openapi":
        _setup_logging()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
