#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
# conftest.py fails the run on any skip, so a module whose optional package is missing is excluded by name here,
# and said aloud, rather than skipped.
ignore=()
python -c "import cns.gate" 2>/dev/null || ignore+=(--ignore=tests/test_cns_connector.py)
python -c "import dgk" 2>/dev/null || ignore+=(--ignore=tests/test_integration_dgk.py)
if [ "${#ignore[@]}" -gt 0 ]; then
  echo "NOT RUN (optional package not installed): ${ignore[*]}" >&2
fi
python -m pytest tests/ -q ${ignore[@]+"${ignore[@]}"} "$@"
