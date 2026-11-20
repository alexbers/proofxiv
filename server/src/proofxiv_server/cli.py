"""`proofxiv-server` command line: serve, jobs, schema, OpenAPI export, seeding."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from proofxiv_protocol.enums import ProblemStatus


def main() -> None:
    parser = argparse.ArgumentParser(prog="proofxiv-server")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the API (uvicorn)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--workers", type=int, default=1)
    serve.add_argument("--reload", action="store_true")
    serve.add_argument("--access-log", action="store_true", help="log every request (Caddy logs anyway)")
    jobs = sub.add_parser("jobs", help="run the maintenance loop")
    jobs.add_argument("--interval", type=float, default=60.0)
    jobs.add_argument("--once", action="store_true")
    schema = sub.add_parser("create-schema", help="create tables directly (dev); prod uses alembic")
    schema.add_argument("--drop", action="store_true")
    openapi = sub.add_parser("openapi", help="write the OpenAPI spec as JSON")
    openapi.add_argument("output", type=Path)
    imp = sub.add_parser("import-problems", help="import a JSON list of problem proposals")
    imp.add_argument("path", type=Path)
    imp.add_argument("--status", default="open", choices=[s.value for s in ProblemStatus])
    imp.add_argument("--origin", default=None, help="where the problems come from (default: the file name)")
    sub.add_parser("seed-demo", help="create demo operators and problems")
    fc = sub.add_parser(
        "import-formal-conjectures",
        help="import open problems from a clone of google-deepmind/formal-conjectures",
    )
    fc.add_argument("repo", type=Path)
    fc.add_argument("--limit", type=int, default=None)
    fc.add_argument("--domain", action="append", default=[], help="only problems in this area (repeat for several)")
    fc.add_argument("--dry-run", action="store_true", help="only count the problems of each area")
    wat = sub.add_parser("watch", help="run the automatic literature watch now (all sources or some)")
    wat.add_argument("sources", nargs="*", help="formal-conjectures, erdosproblems, palomar, formal-conjectures-pr, github")
    wat.add_argument("--reset", action="store_true", help="forget the sources' cursors first (re-read 45 days of PRs)")
    sub.add_parser("init-instance", help="create the federation key (signs the outbox) and print what the owner attests")
    fed = sub.add_parser("federation", help="federation: pull the labs this instance follows, or publish old papers")
    fed.add_argument("action", choices=["pull", "backfill"])
    fed.add_argument(
        "--from-start",
        action="store_true",
        help="pull: read the feeds again from the first entry (after an update, or after raising a lab to review)",
    )
    args = parser.parse_args()

    if args.cmd == "serve":
        import uvicorn

        uvicorn.run(
            "proofxiv_server.main:create_app",
            factory=True,
            host=args.host,
            port=args.port,
            workers=args.workers,
            reload=args.reload,
            proxy_headers=True,
            access_log=args.access_log,
        )
    elif args.cmd == "watch":
        from .core.watch import run_due

        async def watch_now() -> None:
            print(json.dumps(await run_due(set(args.sources) or None, force=True, reset=args.reset), indent=2, default=str))

        asyncio.run(watch_now())
    elif args.cmd == "init-instance":
        from proofxiv_protocol.signing import key_fingerprint

        from .core.federation import init_key, instance_info, key_path

        public = init_key()
        info = instance_info()
        print(f"instance:        {info.name} at {info.base_url}")
        print(f"federation key:  {key_path()} (keep it private)")
        print(f"public key:      {public}")
        print(f"fingerprint:     {key_fingerprint(public)}")
        print("Attest it as the owner, so that other labs accept the outbox:")
        print(f"  proofxiv owner attest-key --role federation --public-key {public}")
    elif args.cmd == "federation":
        from .db.session import init_engine, sessionmaker

        async def federation_now() -> None:
            from .core import federation

            init_engine()
            if args.action == "pull":
                if args.from_start:
                    async with sessionmaker()() as session:
                        print(f"reading {await federation.rewind(session)} feed(s) from the start")
                        await session.commit()
                print(json.dumps(await federation.pull_due(force=True), indent=2, default=str))
                return
            async with sessionmaker()() as session:
                count = await federation.backfill(session)
                await session.commit()
            print(f"published {count} outbox entries")

        asyncio.run(federation_now())
    elif args.cmd == "jobs":
        from .jobs import run_forever, tick

        asyncio.run(tick() if args.once else run_forever(args.interval))
    elif args.cmd == "create-schema":
        from .db.session import create_schema, init_engine

        async def go() -> None:
            engine = init_engine()
            await create_schema(engine, drop=args.drop)
            await engine.dispose()

        asyncio.run(go())
    elif args.cmd == "openapi":
        from .main import create_app

        args.output.write_text(json.dumps(create_app().openapi(), indent=2))
    elif args.cmd == "import-problems":
        from .seed import import_problems

        created = asyncio.run(import_problems(args.path, ProblemStatus(args.status), args.origin))
        print(created, "imported")
    elif args.cmd == "seed-demo":
        from .seed import seed_demo

        asyncio.run(seed_demo())
        print("demo data created")
    elif args.cmd == "import-formal-conjectures":
        from collections import Counter

        from .importers.formal_conjectures import collect, in_domains
        from .seed import import_proposals

        proposals = collect(args.repo, args.limit)
        if args.domain:
            proposals = in_domains(proposals, args.domain)
        if args.dry_run:
            for domain, n in Counter(p.domain for p in proposals).most_common():
                print(f"{n:6}  {domain}")
            print(f"{len(proposals)} open problems")
        else:
            created = asyncio.run(
                import_proposals(proposals, ProblemStatus.OPEN, origin="formal-conjectures")
            )
            print(f"{created} imported ({len(proposals)} open problems found)")


if __name__ == "__main__":
    main()
