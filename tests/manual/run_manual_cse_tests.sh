#!/usr/bin/env bash
# Manual test protocol for client-side encryption - see README.md.
# Thin wrapper: the protocol itself lives in run_manual_cse_tests.py.
set -euo pipefail

: "${JAVA_HOME:?set JAVA_HOME to a JDK 11 before running (ccm needs it)}"
export LOCAL_JMX=yes
export PYTHONUNBUFFERED=1
# botocore would otherwise prefer any ambient AWS credentials over the MinIO ones
unset AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY || true
ulimit -n 8192 || true

cd "$(dirname "$0")/../.."
exec poetry run python tests/manual/run_manual_cse_tests.py "$@"
