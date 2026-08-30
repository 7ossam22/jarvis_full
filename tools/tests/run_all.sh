#!/bin/bash
# Runs the complete deterministic-autopilot test suite, fully offline:
#   1. rule-engine unit tests   (daemon's interpreter — needs playwright)
#   2. formflow router unit tests (stdlib python3)
#   3. end-to-end headless run against the mock Novatek app
# Usage: tools/tests/run_all.sh
set -e
cd "$(dirname "$0")/../.."

echo "== 1/3 rule-engine unit tests =="
.venv-browser/bin/python tools/tests/test_rules_unit.py

echo "== 2/3 formflow router unit tests =="
python3 tools/tests/test_formflow_unit.py

echo "== 3/3 offline end-to-end (headless daemon + mock Novatek) =="
python3 tools/tests/test_e2e_mock.py -v

echo "ALL TEST SUITES PASSED"
