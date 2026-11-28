#!/usr/bin/env sh
set -eu
backend=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -n "${BACKUP_PYTHON:-}" ]; then
    python=$BACKUP_PYTHON
elif [ -x "$backend/../.venv/bin/python" ]; then
    python="$backend/../.venv/bin/python"
else
    python=python3
fi
cd "$backend"
exec "$python" -m scripts.backup_db "$@"
