#!/usr/bin/env bash
set -euo pipefail

python -m uvicorn threads_poster.main:app --reload
