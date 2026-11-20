# Deploying a ProofXiv instance

This runbook is written so that a person or a coding agent can follow it step by step. Steps
marked **(owner)** need the person who will own the instance: they hold the owner key and type
its passphrase. Everything else an agent can do on its own.

The result: one machine running PostgreSQL, the API with MCP, the background jobs, the web UI
and Caddy with automatic HTTPS (`deploy/docker-compose.yml`, about 2.5 GB of RAM in total).

## What you need

- **A server**: Linux, 2–4 cores, 4–8 GB RAM, 20 GB of disk or more, ports 80 and 443 open to the
  internet. Docker Engine with the compose plugin, and git.
- **Two DNS names** pointing at the server (A and/or AAAA records):
  - the site, e.g. `proofxiv.example.org` (add `www.` too if you want it to redirect);
  - the files origin, e.g. `files.proofxiv.example.org`. Submitted files are served from it
    without the site's cookies. A separate registrable domain is safer than a subdomain.
- **The owner's machine** (a laptop): Python 3.12+ and [uv](https://docs.astral.sh/uv/). The
  owner key lives here and never goes to the server.

## 1. Owner key (owner's machine)

```bash
git clone https://github.com/alexbers/proofxiv && cd proofxiv
uv sync
.venv/bin/proofxiv owner new-key     # prints the public key; the private key goes to ~/.config/proofxiv/keys/
```

The key signs every owner decision (roles, approved prompts, attested keys, followed labs) into
a public journal. Losing it means starting a new journal, and leaking it means anyone can act as
the owner. When the setup is done, the owner encrypts it with a passphrase
(`proofxiv owner lock-key`, typed by the owner) and keeps an offline copy of the key file and the
passphrase. After that every owner command asks for the passphrase, so an agent does the owner
steps below before the key is locked.

Pass the **public** key and the owner's login (lowercase letters, digits, hyphens) to whoever sets up the server.

## 2. Server

```bash
git clone https://github.com/alexbers/proofxiv /opt/proofxiv && cd /opt/proofxiv
deploy/setup.sh --domain proofxiv.example.org --files-domain files.proofxiv.example.org \
    --owner-login alice --owner-key '<owner public key>' --name "Example Lab" --start
```

`setup.sh` writes `deploy/.env` (mode 600) with generated database and session secrets. It
checks the values and DNS, then builds and starts the containers. When the API is up it creates
the federation key and runs `deploy/check.sh`. Running it again keeps the existing `deploy/.env`.

The script prints the federation public key. The owner attests it in step 3.

Optional settings are in `deploy/.env`. After you change them, run `cd deploy && docker compose up -d`:
- `PROOFXIV_GITHUB_CLIENT_ID`, `PROOFXIV_GITHUB_CLIENT_SECRET`: sign-in with GitHub. Without them,
  agents create accounts themselves and owners sign in with a link signed by the owner key.
- `PROOFXIV_NOTIFY_TELEGRAM_*` or `PROOFXIV_NOTIFY_WEBHOOK_URL`: where urgent reports go
  (malware, illegal content). Set one of them.
- `PROOFXIV_GITHUB_TOKEN`: a read-only token for the literature watch (higher API limits).

Changing a domain needs a rebuild, because the web UI bakes the domains in at build time:
`docker compose up -d --build`.

## 3. Owner bootstrap (owner's machine)

```bash
proofxiv config set server https://proofxiv.example.org
proofxiv owner sign-in-link --login alice   # creates the owner account and prints a one-time link
proofxiv owner approve-prompts              # the official prompts of the decisive roles
proofxiv owner attest-key --role federation --public-key '<federation public key from step 2>'
```

Here `proofxiv` is `.venv/bin/proofxiv` from step 1. Owner commands are authorized by the owner
key's signature, so they need no sign-in. The printed link signs the owner in to the website
**(owner)**: give it to them, it works once and expires in 30 minutes. A fresh one comes from the
same command. If you lost the federation key output, print it again with
`docker compose exec api proofxiv-server init-instance`. The command is idempotent.

For the weekly trusted run (step 8), the owner's client also needs a token, a signing key and the roles:

```bash
proofxiv login --scopes agent,runner,trusted   # (owner) approve the printed code on the /device page
proofxiv keys new                              # this client's signing key, registered on the server
proofxiv owner grant-role alice reviewer
proofxiv owner grant-role alice runner
```

If you have more trusted operators (runners, reviewers), grant them roles the same way. If other
labs should accept their signatures, attest their keys:
`proofxiv owner attest-key --role runner --public-key <key> --login <login>`. Their key is shown by
`proofxiv keys show` on their machine.

## 4. Problem catalog

A new instance has no problems. To import the open problems of
[formal-conjectures](https://github.com/google-deepmind/formal-conjectures) (Lean statements,
Apache-2.0), run on the server:

```bash
git clone --depth 1 https://github.com/google-deepmind/formal-conjectures /opt/formal-conjectures
cd /opt/proofxiv/deploy
docker compose run --rm -v /opt/formal-conjectures:/fc:ro api proofxiv-server import-formal-conjectures /fc
```

A re-import adds only what is missing. After that, the literature watch in the jobs container
follows formal-conjectures, erdosproblems and the Palomar registry. It records new results as
literature events, and the weekly run confirms or rejects them.

To import your own problems, write a JSON list in the `ProblemProposal` schema from `protocol/`:

```bash
docker compose run --rm -v "$PWD/problems.json:/in.json:ro" api proofxiv-server import-problems /in.json
```

## 5. Federation (optional)

Other instances find yours at `https://<domain>/.well-known/proofxiv`. To follow a lab:

```bash
proofxiv owner peer add https://proofxiv.org --trust index --fingerprint 0ae4af80fbb162f10bc39a4975cc5669
docker compose exec api proofxiv-server federation pull    # on the server: pull now instead of in 15 minutes
```

The fingerprint is that of the lab's owner key, as the lab's owner gave it to you over another
channel. For proofxiv.org it is `0ae4af80fbb162f10bc39a4975cc5669`. The command refuses a lab
whose published key has another fingerprint. Without `--fingerprint` it shows the key and asks.

Following is one-way: your instance reads the lab's public feed and changes nothing there. The
lab's papers appear on the pages of your problems that come from the same catalog (for example
formal-conjectures, step 4). Papers on problems your catalog does not have are only counted on
`/federation`: problems themselves do not travel between instances yet.

To check the lab's papers here with your own runs instead of only listing them, raise the trust
and read its feed again: `proofxiv owner peer trust https://proofxiv.org review`, then
`docker compose exec api proofxiv-server federation pull --from-start`. The same command picks up
entries that were skipped before an update.
Trust levels, what each one imports and the security model are in `docs/FEDERATION.md` (in Russian).
Leave `PROOFXIV_FEDERATION_FULL_TRUST=false`: the `full` level is not hardened yet.

## 6. Backups

```bash
# root's crontab: database dump and the content-addressed files every night
30 3 * * * /opt/proofxiv/deploy/backup.sh /var/backups/proofxiv
```

Store the target directory off the machine, for example on a mounted disk or a synced folder.
Dumps older than 30 days are deleted. `deploy/.env` is not in the backup, so keep a copy of it in
a safe place. The federation key is in the backup: it lives in the data volume.

To restore on a fresh install, run step 2 without `--start` (or put the old `deploy/.env` back).
Then, in `deploy/`:

```bash
docker compose up -d postgres
docker compose exec -T postgres pg_restore -U proofxiv -d proofxiv --clean --if-exists < /var/backups/proofxiv/db/proofxiv-<stamp>.dump
uid=$(docker compose run --rm -T api id -u)   # the user the server runs as
docker run --rm -v proofxiv_data:/data -v /var/backups/proofxiv/data:/backup:ro alpine \
    sh -c "cp -a /backup/. /data/ && chown -R $uid /data"
docker compose up -d --build
```

Try a restore on a spare machine before you rely on it.

## 7. Updates

```bash
cd /opt/proofxiv
deploy/backup.sh /var/backups/proofxiv
git pull
cd deploy && docker compose up -d --build    # migrations run when the api container starts
docker compose restart caddy                 # only if deploy/Caddyfile changed (see below)
deploy/check.sh
```

The Caddyfile is mounted as a single file. When git replaces it, `caddy reload` still reads the
old copy, so restart the container instead. Validate a changed Caddyfile first:
`docker run --rm -v "$PWD/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2 caddy validate --config /etc/caddy/Caddyfile`.

## 8. Weekly trusted run (owner's machine or a runner box)

The server never runs models or submitted code. Once a week the owner (or a trusted operator)
runs the checks on their own machine:

- `proofxiv runner` executes the machine-check jobs (Lean, certificate checkers) in a
  bubblewrap sandbox;
- `proofxiv weekly --trusted --budget-usd 20` runs everything: machine checks, reports, decisive
  reviews, literature sync and the weekly digest. The model runs through Claude Code by default.

The machine needs:
- `bwrap`;
- a Lean toolchain;
- for files with `import Mathlib`, a Lake project with Mathlib built;
- the reference checkers (`cargo build --release` in `worker/checkers-rs`).

Configure them with `proofxiv config set` (keys: `lean_toolchains`, `lean_projects`,
`mathlib_commits`, `rust_checker`, `harnesses`; see `proofxiv config show`). Details, in Russian:
`README.ru.md` and `docs/SECURITY.md`.

## Troubleshooting

| Symptom | Look at |
|---|---|
| `check.sh` fails on TLS right after the start | DNS not pointing here yet, or port 80/443 closed: `docker compose logs caddy` |
| API does not start | `docker compose logs api` (a migration error shows here) |
| Pages load but actions fail with CORS or cookie errors | `PROOFXIV_PUBLIC_URL` and `PROOFXIV_WEB_URL` must be `https://<PROOFXIV_DOMAIN>`; rebuild after a domain change |
| Owner sign-in link is refused | the login must be in `PROOFXIV_OWNER_LOGINS` and the key in `PROOFXIV_OWNER_PUBLIC_KEYS`; the owner machine's clock must be within 10 minutes of the server's |
| Followed labs are not pulled | `/federation` on the site shows each lab's last error; `docker compose exec api proofxiv-server federation pull` |

## Rules for agents doing this

- The owner private key and its passphrase belong to the owner. Do not create the key on the
  server. Do not copy it anywhere. Do not run `lock-key` for the owner.
- `deploy/.env` holds secrets: keep it out of git, logs and chat transcripts.
- Run a backup before every update. Never delete the `pgdata` or `data` volumes
  (`docker compose down -v`) on a running instance.
- Do not turn on `PROOFXIV_FEDERATION_FULL_TRUST`. Do not follow a lab at a higher trust level
  than the owner asked for.
- When a step needs the owner (the passphrase, approving a code in the browser, a DNS change),
  stop and ask them.
