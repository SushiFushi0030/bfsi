#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if [[ ! -x .venv/bin/python ]]; then
  printf '请先运行 ./scripts/setup.sh 安装依赖。\n' >&2
  exit 1
fi
export BFSI_PROJECT_ROOT="$PWD"
exec .venv/bin/python -B -m bfsi.server "$@"
