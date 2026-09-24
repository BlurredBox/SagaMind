#!/usr/bin/env bash
# Generate Python gRPC stubs from proto/sagamind.proto into src/generated/.
#
# Requires the dev extras:  pip install -e ".[grpc]"
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="${ROOT}/src/generated"
PYTHON_BIN="${PYTHON_BIN:-python}"

mkdir -p "${OUT}"
touch "${OUT}/__init__.py"

"${PYTHON_BIN}" -m grpc_tools.protoc \
  --proto_path="${ROOT}/proto" \
  --python_out="${OUT}" \
  --grpc_python_out="${OUT}" \
  --pyi_out="${OUT}" \
  "${ROOT}/proto/sagamind.proto"

# Rewrite the absolute import emitted by protoc to a package-relative one.
if [[ "$(uname)" == "Darwin" ]]; then
  sed -i '' 's/^import sagamind_pb2/from src.generated import sagamind_pb2/' "${OUT}/sagamind_pb2_grpc.py"
else
  sed -i 's/^import sagamind_pb2/from src.generated import sagamind_pb2/' "${OUT}/sagamind_pb2_grpc.py"
fi

echo "Generated gRPC stubs into ${OUT}"
