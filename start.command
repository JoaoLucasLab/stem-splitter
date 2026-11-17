#!/bin/bash
cd "$(dirname "$0")"
export STEMS_VOCAL_OVERLAP="${STEMS_VOCAL_OVERLAP:-2}"   # 2 = ~2x faster vocals; delete this line for full quality (4)
exec arch -arm64 "/Library/Frameworks/Python.framework/Versions/3.11/bin/python3" run.py
