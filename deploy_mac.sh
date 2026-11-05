#!/usr/bin/env bash
# Wrapper redirecting to unified server.sh
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$SCRIPT_DIR/server.sh" start "$@"
