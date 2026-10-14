#!/usr/bin/env bash
# Redeploy the live demo on Render from the latest pushed commit and wait until it is live.
# The service was created from the public repo URL, so pushes do not auto-deploy.
#   RENDER_API_KEY and RENDER_SERVICE_ID come from .env
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
API="https://api.render.com/v1/services/${RENDER_SERVICE_ID:-srv-db7m1e7avr4c738ob9q0}/deploys"
AUTH="Authorization: Bearer $RENDER_API_KEY"
DEPLOY=$(curl -s -X POST -H "$AUTH" -H "Content-Type: application/json" "$API" -d '{"clearCache":"do_not_clear"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
echo "deploy $DEPLOY started"
while true; do
  STATUS=$(curl -s -H "$AUTH" "$API/$DEPLOY" | python3 -c "import sys,json; print(json.load(sys.stdin).get('status'))")
  case "$STATUS" in
    live) echo "live: https://proof-of-forgetting.onrender.com"; exit 0 ;;
    build_failed|update_failed|canceled|deactivated|pre_deploy_failed) echo "deploy $STATUS"; exit 1 ;;
  esac
  sleep 15
done
