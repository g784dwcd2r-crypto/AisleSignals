#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  printf '%s\n' 'First run: python3 scripts/setup_prototype.py'
  exit 1
fi
exec .venv/bin/python scripts/run_prototype.py
