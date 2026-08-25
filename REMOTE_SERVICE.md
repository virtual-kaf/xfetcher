# xfetch Grok remote service

This standalone process exposes one stateless Grok polling endpoint for B. It
does not load QQ subscriptions, write deduplication state, push QQ messages, or
call the TwitterAPI fallback.

## A configuration

Add these values to `D:\Projects\kabubu\.env` (do not commit the real token):

```dotenv
KABUBU_XFETCH_REMOTE_ENABLED=true
KABUBU_XFETCH_REMOTE_HOST=100.98.44.83
KABUBU_XFETCH_REMOTE_PORT=8765
KABUBU_XFETCH_REMOTE_TOKEN=replace-with-a-long-random-token
KABUBU_GROK_API_KEY=replace-with-the-grok-token
# Optional: KABUBU_GROK_API_URL=http://127.0.0.1:8000/v1/chat/completions
```

The service refuses to bind when it is disabled, the token is empty, or the
host/port is invalid. The default host is A's Tailscale address and is not a
public wildcard listener.

## Start on A

Use the same Python environment as the Kabubu project:

```powershell
cd D:\Projects\kabubu\src\plugins
python -m nonebot_plugin_xfetch.remote_service
```

## Test from B

```bash
curl -X POST http://100.98.44.83:8765/api/xfetch/poll \
  -H "Authorization: Bearer $XF_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"members":["virtual_kaf","RIM_virtual"]}'
```

Successful responses, including a valid empty result, use HTTP 200:

```json
{"ok":true,"urls":[],"source":"grok","error":null}
```

Grok failures or invalid Grok results use HTTP 502 with `error=grok_failed`,
which is the signal for B to run its TwitterAPI fallback with the same member
list.

## Retiring the old A plugin

This change does not modify `scheduler.py` or the existing QQ plugin behavior.
After the complete NoneBot/QQ feature set has moved to B, remove the plugin
from A's NoneBot `plugin_dirs` (or otherwise stop loading it) and restart the A
bot. Keep the remote-service module and its Grok client importable by the Python
environment used to run the command above.

## Persistent data

QQ subscriptions, tweet deduplication history, live schedules, and archives
live under `nonebot_plugin_xfetch/data/`. Copy the complete plugin directory
when moving xfetch. Keep all API keys and Bearer tokens in environment
variables or `.env`, never in this directory.
