#!/usr/bin/env bash
set -euo pipefail
repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -n "${TBOT_PYTHON:-}" ]]; then
  classroom_python="${TBOT_PYTHON}"
elif [[ -x "${repo_root}/.venv-tbot/bin/python" ]]; then
  classroom_python="${repo_root}/.venv-tbot/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  classroom_python="$(command -v python3)"
else
  echo 'Python 3 is required. Activate the project environment or set TBOT_PYTHON.' >&2
  exit 1
fi
exec "${classroom_python}" "${repo_root}/scripts/tbot-classroom.py" "$@"
