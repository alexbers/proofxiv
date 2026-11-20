#!/usr/bin/env bash
# Local PostgreSQL cluster for development and tests, kept inside the repository (.dev/pg).
# Usage: scripts/dev_pg.sh [start|stop|status|psql|reset]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="$ROOT/.dev/pg"
PGDATA="$BASE/data"
SOCK="$BASE/sock"
PORT="${PROOFXIV_PG_PORT:-54329}"
export PGHOST="$SOCK" PGPORT="$PORT" PGUSER=proofxiv

init() {
    mkdir -p "$BASE" "$SOCK"
    initdb -D "$PGDATA" -U proofxiv --auth=trust --encoding=UTF8 --locale=C.UTF-8 >"$BASE/initdb.log"
}

start() {
    [ -d "$PGDATA" ] || init
    if pg_ctl -D "$PGDATA" status >/dev/null 2>&1; then
        echo "postgres already running on port $PORT"
    else
        pg_ctl -D "$PGDATA" -l "$BASE/server.log" -w \
            -o "-p $PORT -k $SOCK -c listen_addresses=127.0.0.1 -c fsync=off" start
    fi
    for db in proofxiv proofxiv_test; do
        psql -d postgres -tAc "SELECT 1 FROM pg_database WHERE datname='$db'" | grep -q 1 \
            || createdb "$db"
        psql -d "$db" -qc "CREATE EXTENSION IF NOT EXISTS pg_trgm"
    done
    echo "postgresql://proofxiv@127.0.0.1:$PORT/proofxiv"
}

case "${1:-start}" in
    start) start ;;
    stop) pg_ctl -D "$PGDATA" -m fast stop ;;
    status) pg_ctl -D "$PGDATA" status ;;
    psql) shift; exec psql -d "${1:-proofxiv}" ;;
    reset) pg_ctl -D "$PGDATA" -m fast stop || true; rm -rf "$BASE"; start ;;
    *) echo "usage: $0 [start|stop|status|psql|reset]" >&2; exit 2 ;;
esac
