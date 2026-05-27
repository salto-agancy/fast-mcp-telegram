#!/usr/bin/env bash
# Minimal ACL smoke test: call get_messages as each ACL profile via curl.
# Requires http-auth server with ACL_ENABLED=true and acl.dev.yaml (see CONTRIBUTING.md).
#
# Token resolution (in order):
#   1. ACL_CONFIG_PATH (default: acl.dev.yaml) — profile keys from tokens: block
#   2. BEARER_TOKEN_FOR_TESTING — overrides readonly profile when set
#   3. Example placeholders from acl.dev.yaml.example when config file is missing
set -euo pipefail

BASE_URL="${ACL_SMOKE_URL:-http://127.0.0.1:8765/v1/mcp}"
ACL_CONFIG_PATH="${ACL_CONFIG_PATH:-acl.dev.yaml}"

mapfile -t ACL_TOKENS < <(
  python3 - "$ACL_CONFIG_PATH" <<'PY'
import os
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    print("error: PyYAML required (pip install pyyaml)", file=sys.stderr)
    sys.exit(1)

DEFAULTS = {
    "readonly": "dev_acl_readonly_abcdefghijklmnopqrstuvwxz0",
    "empty_lane": "dev_acl_empty_lane_abcdefghijklmnopqrstuvwx",
    "team": "dev_acl_team_lane__abcdefghijklmnopqrstuvwx",
}

def pick_by_prefix(tokens: dict, prefix: str, fallback: str) -> str:
    for key in tokens:
        if key.startswith(prefix):
            return key
    return fallback

def pick_by_shape(tokens: dict) -> dict[str, str]:
    profiles = dict(DEFAULTS)
    readonly_env = os.environ.get("BEARER_TOKEN_FOR_TESTING", "").strip()
    if readonly_env:
        profiles["readonly"] = readonly_env

    for key, cfg in tokens.items():
        if not isinstance(cfg, dict):
            continue
        chats = cfg.get("chats") or []
        read_only = bool(cfg.get("read_only"))
        if read_only and "me" in chats:
            profiles["readonly"] = key
        elif chats == []:
            profiles["empty_lane"] = key
        elif not read_only and chats:
            profiles["team"] = key

    profiles["readonly"] = pick_by_prefix(tokens, "dev_acl_readonly", profiles["readonly"])
    profiles["empty_lane"] = pick_by_prefix(tokens, "dev_acl_empty_lane", profiles["empty_lane"])
    profiles["team"] = pick_by_prefix(tokens, "dev_acl_team_lane", profiles["team"])
    if readonly_env:
        profiles["readonly"] = readonly_env
    return profiles

config_path = Path(sys.argv[1])
if config_path.is_file():
    data = yaml.safe_load(config_path.read_text()) or {}
    tokens = data.get("tokens") or {}
    if not isinstance(tokens, dict):
        tokens = {}
    profiles = pick_by_shape(tokens)
else:
    profiles = dict(DEFAULTS)
    readonly_env = os.environ.get("BEARER_TOKEN_FOR_TESTING", "").strip()
    if readonly_env:
        profiles["readonly"] = readonly_env
    print(
        f"note: {config_path} not found; using example tokens "
        f"(copy acl.dev.yaml.example → acl.dev.yaml for local profiles)",
        file=sys.stderr,
    )

for name in ("readonly", "empty_lane", "team"):
    print(profiles[name])
PY
)

READONLY_TOKEN="${ACL_TOKENS[0]}"
EMPTY_LANE_TOKEN="${ACL_TOKENS[1]}"
TEAM_TOKEN="${ACL_TOKENS[2]}"

call_tool() {
  local token="$1"
  local label="$2"
  echo "--- ${label} (${token:0:24}...) ---"
  curl -sS --connect-timeout 5 --max-time 15 -X POST "${BASE_URL}" \
    -H "Authorization: Bearer ${token}" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json, text/event-stream" \
    -d '{
      "jsonrpc": "2.0",
      "id": 1,
      "method": "tools/call",
      "params": {
        "name": "get_messages",
        "arguments": {"chat_id": "me", "limit": 1}
      }
    }'
  echo
}

if ! curl -sS --connect-timeout 2 -o /dev/null "${BASE_URL}" 2>/dev/null; then
  echo "skip: no server at ${BASE_URL} (start http-auth ACL server on port 8765 first)"
  exit 0
fi

call_tool "${READONLY_TOKEN}" "readonly"
call_tool "${EMPTY_LANE_TOKEN}" "empty-lane"
call_tool "${TEAM_TOKEN}" "team"
