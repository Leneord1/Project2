#!/usr/bin/env bash
set -euo pipefail
GATE="/c/C:/F/VT Bootcamp/Project 1"
OUT="/c/F/VT Bootcamp/Project 1/gate_exists.txt"
{
  echo "bash_test_d=$(test -d "$GATE" && echo yes || echo no)"
  echo "bash_test_f=$(test -f "$GATE/package.json" && echo yes || echo no)"
  if [[ -f "$GATE/package.json" ]]; then
    echo "PKG_BEGIN"
    cat "$GATE/package.json"
    echo "PKG_END"
  fi
  # Windows python via MSYS path
  /c/Python313/python.exe - <<'PY' || true
import os
candidates = [
    r"C:\C:\F\VT Bootcamp\Project 1",
    r"C:\C\F\VT Bootcamp\Project 1",
    r"C:\CCC2F~1\F\VT Bootcamp\Project 1",
]
for c in candidates:
    print("win", repr(c), os.path.exists(c), os.path.exists(os.path.join(c, "package.json")))
PY
} >"$OUT" 2>&1
