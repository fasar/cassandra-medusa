#!/usr/bin/env bash
# Upgrade compatibility protocol - see UPGRADE.md.
# Needs a checkout of master with its own poetry venv next to this repository:
#   git worktree add ../cassandra-medusa-master master && (cd ../cassandra-medusa-master && poetry install)
set -euo pipefail

: "${JAVA_HOME:?set JAVA_HOME to a JDK 11 before running (ccm needs it)}"
export LOCAL_JMX=yes
export PYTHONUNBUFFERED=1
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY || true
ulimit -n 8192 || true

cd "$(dirname "$0")/../.."
exec poetry run python tests/manual/run_upgrade_compat_tests.py "$@"
