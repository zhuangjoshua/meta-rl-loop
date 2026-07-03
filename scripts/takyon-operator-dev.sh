#!/usr/bin/env bash
set -euo pipefail

# Dev-twin operator rail — the EXACT MIRROR of scripts/takyon-operator-prod.sh.
#
# This is a THIN wrapper: it sets TAKYON_OPERATOR_TARGET=dev and execs the prod rail, so EVERY
# command (sai | josh | console N --shells K | worker N | shell | seed | run | status ...) runs
# the SAME code as prod, differing only in the plane it connects to — the isolated dev twin
# (four-manifold-dev Supabase + local dev Safebox + Stripe TEST), never prod. `sai`/`josh` mean
# the same person in dev and prod (shared scripts/operator-users.sh).
#
#   scripts/takyon-operator-dev.sh josh                 # dev shell as josh
#   scripts/takyon-operator-dev.sh console 10 --user josh   # dev worker pool (10) + shell
#   scripts/takyon-operator-dev.sh worker 10 --user josh    # dev worker pool only

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
exec env TAKYON_OPERATOR_TARGET=dev "$ROOT/scripts/takyon-operator-prod.sh" "$@"
