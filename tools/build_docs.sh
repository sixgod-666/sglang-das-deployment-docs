#!/usr/bin/env bash
set -euo pipefail

DOCS_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -n "${READTHEDOCS_OUTPUT:-}" ]]; then
    SITE_DIR="${READTHEDOCS_OUTPUT}/html"
else
    SITE_DIR="${DOCS_ROOT}/site"
fi

cd "${DOCS_ROOT}"
uv sync --locked
uv run mkdocs build --strict --site-dir "${SITE_DIR}"
