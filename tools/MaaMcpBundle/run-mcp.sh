#!/bin/sh
set -eu
root=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec "$root/python/bin/python3" -I -B "$root/run_mcp.py" "$@"
