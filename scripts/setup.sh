#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v python3.12 >/dev/null 2>&1; then
  python3.12 -m venv .venv
else
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m pip install .
.venv/bin/python scripts/check_install.py
pnpm --dir web install --frozen-lockfile
pnpm --dir web run build
printf '\n安装完成。运行 ./scripts/start.sh 启动。\n'
