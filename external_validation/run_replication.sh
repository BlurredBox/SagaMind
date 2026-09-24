#!/bin/sh
set -eu

PYTHON_BIN="${PYTHON_BIN:-python3.11}"
ENV_DIR="${SAGAMIND_REPLICATION_ENV:-.replication-venv}"

"$PYTHON_BIN" -m venv "$ENV_DIR"
if [ -f external_validation/SOURCE_MANIFEST.sha256 ]; then
    shasum -a 256 -c external_validation/SOURCE_MANIFEST.sha256
fi
"$ENV_DIR/bin/python" -m pip install --disable-pip-version-check --require-hashes -r external_validation/requirements.lock
"$ENV_DIR/bin/python" -m pip check
"$ENV_DIR/bin/python" -m external_validation.compare
"$ENV_DIR/bin/python" -m external_validation.verify_results
"$ENV_DIR/bin/python" -m external_validation.render_report
