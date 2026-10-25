#!/bin/bash
set -euo pipefail
cd "$(dirname "$0")"
./scripts/start.sh || { printf '启动失败，请查看上方信息。按回车关闭。'; read -r _; }
