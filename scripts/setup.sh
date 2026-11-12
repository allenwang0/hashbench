#!/usr/bin/env bash
# Creates .venv with the pinned CMake, Ninja, clang-format and jsonschema.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m venv .venv
.venv/bin/pip install --quiet --requirement requirements-dev.txt
echo "Done. Run: source .venv/bin/activate"
