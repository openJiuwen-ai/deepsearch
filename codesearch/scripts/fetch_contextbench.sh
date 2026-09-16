#!/usr/bin/env bash
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# Opt-in clone of ContextBench. CI / product wheels do not run this.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="${CONTEXTBENCH_DIR:-$ROOT/third_party/contextbench}"
URL="${CONTEXTBENCH_URL:-https://github.com/EuniAI/ContextBench}"

if [[ -f "$DEST/contextbench/__init__.py" ]]; then
  echo "ContextBench already present at $DEST"
  exit 0
fi

mkdir -p "$(dirname "$DEST")"
if [[ -e "$DEST" ]] && [[ -n "$(ls -A "$DEST" 2>/dev/null || true)" ]]; then
  echo "Refusing to clone into non-empty path: $DEST" >&2
  echo "Set CONTEXTBENCH_DIR to another directory, or empty this path." >&2
  exit 1
fi

echo "Cloning $URL -> $DEST"
git clone "$URL" "$DEST"

if [[ -n "${CONTEXTBENCH_PIN:-}" ]]; then
  git -C "$DEST" checkout "$CONTEXTBENCH_PIN"
fi

echo "ContextBench ready at $DEST"
echo "Put dataset parquet files under $DEST/data/ if they are not already there."
