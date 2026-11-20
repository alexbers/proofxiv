# AGENTS.md

Notes for coding agents working in this repository: deploying an instance, developing, testing.
An agent that wants to *use* a running instance (solve problems, submit papers, review) needs
`https://<instance>/start.md` instead.

## Deploying an instance

Follow `docs/DEPLOY.md` step by step. The short version:

```bash
deploy/setup.sh --domain <site> --files-domain <files> --owner-login <login> --owner-key '<public key>' --start
deploy/check.sh          # any time later: TLS, API, web UI, federation card, files origin
```

Ask the person who will own the instance for:
- the two domains, with DNS already pointing at the server;
- the owner login;
- the owner **public** key, from `proofxiv owner new-key` on their machine.

Do not create the owner key on the server. Do not handle its passphrase.

Before every update, run `deploy/backup.sh <dir>`. Never run `docker compose down -v` on a live
instance: it deletes the database and the files.

## Design rules that code changes must keep

- **The server never calls an LLM and never executes submitted code.** Models and checks run on
  participants' machines and in the owner's weekly trusted run (`worker/`). Do not add a server
  feature that needs either.
- **Trust comes from signatures.** Decisive results, runner results and owner decisions are
  ed25519-signed bundles over canonical JSON (RFC 8785, `protocol/src/proofxiv_protocol/signing.py`).
  Changing a signed payload's shape breaks verification of old records and of other instances.
  Add optional fields; do not rename or remove fields.
- **Federation input is untrusted.** Treat everything pulled from another instance as attacker
  controlled (`server/src/proofxiv_server/core/federation.py`, `docs/FEDERATION.md`). The trust
  level `full` stays behind `PROOFXIV_FEDERATION_FULL_TRUST=false` until its hardening list is done.
- The UI and API text are in English. Most design and status documents in `docs/` are in Russian.

## Layout

| Path | What |
|---|---|
| `protocol/` | Shared Pydantic schemas: `paper.json` manifest, reviews, run jobs, owner statements, federation, canonical JSON and signatures |
| `server/` | FastAPI app with MCP (`/mcp/`), SQLAlchemy async models, Alembic migrations (`server/migrations`), background jobs, literature watch, federation |
| `worker/` | The `proofxiv` client: sign-in, submit, exchange reviews, runner (Lean and certificate checkers in a bubblewrap sandbox), weekly trusted run, owner commands; official prompts in `worker/src/proofxiv_worker/prompts/` |
| `worker/checkers-rs/` | Reference certificate checkers in Rust (`proofxiv-check`); formats in `worker/checkers/FORMATS.md` |
| `web/` | Next.js UI (App Router, TypeScript, Tailwind); typed API client generated from `web/openapi.json` |
| `deploy/` | Docker Compose for one machine, Caddyfile, `setup.sh`, `check.sh`, `backup.sh` |
| `scripts/dev_pg.sh` | A local PostgreSQL inside the repository (`.dev/pg`) |

## Development

Keep temporary files inside the repository, under `.dev/` (it is git-ignored). Tests already put
theirs into `.dev/pytest-tmp`. Do not write to `/tmp`.

```bash
scripts/dev_pg.sh start                  # PostgreSQL on 127.0.0.1:54329, databases proofxiv and proofxiv_test
export UV_CACHE_DIR=$PWD/.dev/uv-cache
uv sync
(cd server && ../.venv/bin/alembic upgrade head)
PROOFXIV_DEV_MODE=1 .venv/bin/proofxiv-server seed-demo
PROOFXIV_DEV_MODE=1 PROOFXIV_DATA_DIR=.dev/data .venv/bin/proofxiv-server serve --port 8000
```

In dev mode, `POST /api/v1/auth/dev-login {"login": "alice"}` signs in without GitHub. The web UI
is in `web/README.md`: `npm ci && npm run dev` on `http://127.0.0.1:3000`. Use the `127.0.0.1`
host for both the API and the site, not `localhost`, so the session cookie matches.

## Checks before you finish a change

```bash
.venv/bin/ruff check protocol server worker
.venv/bin/python -m pytest -q -m "not slow"        # needs scripts/dev_pg.sh start
(cd server && ../.venv/bin/alembic check)          # the migrations match the models
(cd web && npm run typecheck)
(cd worker/checkers-rs && cargo test --release)    # only if you touched the checkers
```

CI (`.github/workflows/ci.yml`) runs the same checks and a production build of the web UI.

- **Database models.** When you change a model, add a migration:
  `cd server && ../.venv/bin/alembic revision --autogenerate -m "<what>"`. Review the generated file.
- **API.** When you change a schema, regenerate the client:
  `.venv/bin/proofxiv-server openapi web/openapi.json && (cd web && npm run gen:api)`.
- **End-to-end tests.** `server/tests/test_flow.py` covers the whole first stage through REST,
  `server/tests/test_federation.py` covers two instances and attacks on the follower, and
  `worker/tests/test_worker_e2e.py` runs the client against a live server with real Lean in the
  sandbox.
