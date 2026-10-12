#!/usr/bin/env bash
# Start everything for the demo: memory-mcp (:8765), a public tunnel for ArmorIQ, and the demo UI (:8800).
#   ./start_demo.sh          start (or restart) and open the demo page
#   ./start_demo.sh stop     stop everything
set -euo pipefail
cd "$(dirname "$0")"
PY=.venv/bin/python
LOGS=data/logs
mkdir -p "$LOGS"

stop() {
  pkill -f "memory_mcp.server" 2>/dev/null || true
  pkill -f "demo.app" 2>/dev/null || true
}

if [[ "${1:-}" == "stop" ]]; then stop; pkill -f "ngrok http 8765" 2>/dev/null || true; echo "stopped"; exit 0; fi

stop
$PY -m scripts.seed
nohup $PY -m memory_mcp.server > "$LOGS/mcp.log" 2>&1 &
nohup $PY -m demo.app > "$LOGS/demo.log" 2>&1 &

# ArmorIQ reaches the MCP over public HTTPS (registration + scans). Enforcement is by MCP name,
# so a new tunnel URL does not break the demo; re-register only if you want the console to scan it.
if ! curl -s localhost:4040/api/tunnels >/dev/null 2>&1; then
  nohup ngrok http 8765 > "$LOGS/ngrok.log" 2>&1 &
  sleep 4
fi
TUNNEL=$(curl -s localhost:4040/api/tunnels | $PY -c "import sys,json; print(json.load(sys.stdin)['tunnels'][0]['public_url'])" 2>/dev/null || echo "")
REGISTERED=$(grep -oE 'https://[^ ]+/mcp' armoriq.yaml | sed 's#/mcp$##')

for _ in $(seq 1 20); do curl -s localhost:8800 >/dev/null 2>&1 && break; sleep 0.5; done
echo
echo "  memory-mcp   http://localhost:8765/mcp"
echo "  public URL   ${TUNNEL:-<ngrok not running>}"
[[ -n "$TUNNEL" && "$TUNNEL" != "$REGISTERED" ]] && echo "               (differs from armoriq.yaml; demo still works. To update the console: edit the url, then 'armoriq register')"
echo "  demo UI      http://localhost:8800"
echo "  logs         $LOGS/"
open http://localhost:8800 2>/dev/null || true
