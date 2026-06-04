#!/usr/bin/env python3
"""Upload a transcript Markdown to CANN Wiki via the HTTP MCP endpoint.

Bypasses the model output-token cap. Calling `wiki_submit_trajectory` as a
regular `tool_use` forces the entire `content` string through the model's
response budget; on long sessions (>~13KB rendered) the call gets silently
truncated mid-string. This script is invoked from Bash, so the payload travels
over a local HTTP socket — verified byte-identical at 250KB.

Usage:
    python3 mcp_upload.py --file /tmp/session_output.md --session-id <id> [--agent claude-code|opencode]

The optional `--agent` flag namespaces the uploaded filename by platform so
Claude Code and OpenCode trajectories don't collide and stay visually grouped:
`claude-code` -> `claudecode-<id>.md`, `opencode` -> `opencode-<id>.md`. The
prefix is applied idempotently (re-running on an already-prefixed id is a no-op).

URL resolution order:
    --url flag  >  $CANN_WIKI_MCP_URL  >  agent MCP config (cann-wiki entry's
    `url` in `.mcp.json` / `.opencode/opencode.json` walking up from cwd, or
    `~/.claude.json`)  >  http://113.46.4.206:8767/mcp

This means setting a non-default port via `/setup-cann-wiki` is enough: the
agent config that setup wrote is what this script reads when uploading.
"""
import argparse
import datetime
import json
import os
import re
import sys
import urllib.request
from urllib.error import HTTPError, URLError

# Per-platform filename prefixes. Keys match SKILL.md's `$AGENT` values.
_AGENT_PREFIXES = {
    "claude-code": "claudecode-",
    "opencode": "opencode-",
}

# Upload-date subdir format; server archives at <date>/<session_id>.md.
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def resolve_upload_date(date):
    """Return a YYYY-MM-DD date-dir string: the given `date` if valid, else today.

    The server re-validates and falls back to its own day on anything invalid,
    so this only picks a sensible default (the client's upload day).
    """
    if date and _DATE_RE.match(date):
        return date
    return datetime.date.today().isoformat()


def apply_agent_prefix(session_id, agent):
    """Prepend the platform prefix to session_id, idempotently.

    Unknown/empty agent -> session_id unchanged. Already-prefixed -> unchanged,
    so re-uploading the same session never stacks `claudecode-claudecode-...`.
    """
    prefix = _AGENT_PREFIXES.get(agent)
    if not prefix or session_id.startswith(prefix):
        return session_id
    return prefix + session_id


def short_display_path(path):
    """Reduce the server's absolute path to `<parent-dir>/<filename>`.

    The server returns an absolute path under uploaded_dir; we surface only the
    last two segments so the success line never leaks the full machine path
    (e.g. `/data2/.../uploaded/claudecode-x.md` -> `uploaded/claudecode-x.md`).
    """
    if not path:
        return ""
    base = os.path.basename(path)
    parent = os.path.basename(os.path.dirname(path))
    return f"{parent}/{base}" if parent else base


def _post(url, headers, body):
    data = body.encode("utf-8") if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req) as resp:
        return resp.headers, resp.read().decode("utf-8")


def _parse_sse(body):
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload:
            continue
        try:
            yield json.loads(payload)
        except json.JSONDecodeError:
            continue


def _scan_config(path):
    """Return the cann-wiki entry's `url` from one MCP config file, or None."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    # `mcpServers` is the Claude Code / Desktop shape; `mcp` is OpenCode's.
    for top_key in ("mcpServers", "mcp"):
        servers = data.get(top_key)
        if isinstance(servers, dict):
            entry = servers.get("cann-wiki")
            if isinstance(entry, dict):
                url = entry.get("url")
                if isinstance(url, str) and url:
                    return url
    return None


def _find_url_in_agent_configs():
    cur = os.path.abspath(os.getcwd())
    while True:
        for rel in (".mcp.json", os.path.join(".opencode", "opencode.json")):
            url = _scan_config(os.path.join(cur, rel))
            if url:
                return url
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return _scan_config(os.path.expanduser("~/.claude.json"))


def _resolve_url(cli_url):
    if cli_url:
        return cli_url
    env_url = os.environ.get("CANN_WIKI_MCP_URL")
    if env_url:
        return env_url
    found = _find_url_in_agent_configs()
    if found:
        return found
    return "http://113.46.4.206:8767/mcp"


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--file", required=True, help="Path to rendered Markdown transcript")
    p.add_argument("--session-id", required=True, help="Session id used as <session_id>.md filename")
    p.add_argument(
        "--agent",
        choices=sorted(_AGENT_PREFIXES),
        default=None,
        help="Platform that produced the trajectory; prefixes the uploaded "
             "filename (claude-code -> claudecode-, opencode -> opencode-)",
    )
    p.add_argument(
        "--url",
        default=None,
        help="MCP HTTP endpoint (default: $CANN_WIKI_MCP_URL > agent MCP config > http://113.46.4.206:8767/mcp)",
    )
    p.add_argument(
        "--date",
        default=None,
        help="Upload-date subdir YYYY-MM-DD; server archives at <date>/<session_id>.md "
             "(default: today)",
    )
    args = p.parse_args()
    args.url = _resolve_url(args.url)
    args.session_id = apply_agent_prefix(args.session_id, args.agent)
    args.date = resolve_upload_date(args.date)

    with open(args.file, encoding="utf-8") as f:
        content = f.read()

    common = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    init_req = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "session-upload", "version": "1.0"},
        },
    }
    try:
        headers, _ = _post(args.url, common, json.dumps(init_req))
    except (URLError, HTTPError) as e:
        sys.exit(f"MCP init failed against {args.url}: {e}")

    # Stateful servers return an mcp-session-id header that must echo on every
    # later request; stateless servers (stateless_http=True) return none and
    # accept tools/call directly. Support both: use the header when present,
    # otherwise fall through to a sessionless call.
    sid = headers.get("mcp-session-id") or headers.get("Mcp-Session-Id")
    sess = dict(common)
    if sid:
        sess["mcp-session-id"] = sid
        # Required handshake step before tools/call (stateful only).
        _post(args.url, sess, json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}))

    call_req = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {
            "name": "wiki_submit_trajectory",
            "arguments": {"session_id": args.session_id, "content": content, "date": args.date},
        },
    }
    try:
        _, body = _post(args.url, sess, json.dumps(call_req, ensure_ascii=False))
    except (URLError, HTTPError) as e:
        sys.exit(f"wiki_submit_trajectory call failed: {e}")

    response = next((o for o in _parse_sse(body) if o.get("id") == 2), None)
    if response is None:
        sys.exit(f"No JSON-RPC response in MCP body:\n{body[:500]}")
    if "error" in response:
        sys.exit(f"MCP error: {json.dumps(response['error'], ensure_ascii=False)}")

    inner = response.get("result", {})
    structured = inner.get("structuredContent") or {}
    if structured.get("status") == "ok":
        print(f"OK {short_display_path(structured.get('path', ''))}")
        return 0

    # Surface server's own error/status verbatim — don't paraphrase.
    print(json.dumps(inner, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    sys.exit(main() or 0)
