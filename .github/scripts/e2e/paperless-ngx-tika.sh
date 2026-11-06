#!/usr/bin/env bash
# Consume office documents and e-mail through paperless-ngx with the Tika and Gotenberg the chart
# bundles. The test is Python, because it drives a REST API and translates a rendered release into
# containers; why it exists and what it covers are in `paperless_ngx_tika.py`.
#
# This wrapper only finds an interpreter, the same way `paperless-ngx.sh` does: CI runners ship
# `python3`, a Git Bash shell on Windows commonly has only `python`, and often a `python3` that is
# the Microsoft Store's install stub. So each candidate is executed rather than merely located.
#
# Usage: bash .github/scripts/e2e/paperless-ngx-tika.sh
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

for candidate in python3 python; do
  if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c "import yaml" >/dev/null 2>&1; then
    exec "${candidate}" "${here}/paperless_ngx_tika.py" "$@"
  fi
done
echo "no python interpreter with PyYAML found; install PyYAML" >&2
exit 1
