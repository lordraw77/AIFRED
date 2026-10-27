"""A tiny MCP server over stdio for the tests: ``python fake_mcp_stdio.py [crash|hang|noinit]``."""

import json
import os
import sys
import time

MODE = sys.argv[1] if len(sys.argv) > 1 else ""
TOOLS = [
    {"name": "echo", "description": "Echo the text", "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "env", "description": "List environment variable names", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "cwd", "description": "Working directory", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "spawn", "description": "Start a long-lived grandchild and return its pid", "inputSchema": {"type": "object", "properties": {}}},
    {"name": "ask", "description": "Ask the client for sampling and return the error code it answers with", "inputSchema": {"type": "object", "properties": {}}},
]


def reply(request_id, result=None, error=None):
    message = {"jsonrpc": "2.0", "id": request_id}
    message.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    method, request_id = msg.get("method"), msg.get("id")
    if MODE == "noinit" and method == "initialize":
        continue
    if method == "initialize":
        sys.stderr.write("fake server starting\n")
        sys.stderr.flush()
        reply(request_id, {"protocolVersion": "2025-11-25", "capabilities": {"tools": {}}, "serverInfo": {"name": "fake-stdio", "version": "1"}, "instructions": "Stdio test server."})
    elif method == "tools/list":
        reply(request_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg["params"]
        if MODE == "crash":
            sys.exit(3)
        if MODE == "hang":
            time.sleep(60)
        if params["name"] == "echo":
            reply(request_id, {"content": [{"type": "text", "text": params["arguments"].get("text", "")}]})
        elif params["name"] == "env":
            reply(request_id, {"content": [{"type": "text", "text": ",".join(sorted(os.environ))}]})
        elif params["name"] == "spawn":
            import subprocess

            child = subprocess.Popen(["sleep", "60"])
            reply(request_id, {"content": [{"type": "text", "text": str(child.pid)}]})
        elif params["name"] == "ask":
            sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": 99, "method": "sampling/createMessage", "params": {}}) + "\n")
            sys.stdout.flush()
            answer = json.loads(sys.stdin.readline())
            reply(request_id, {"content": [{"type": "text", "text": str(answer.get("error", {}).get("code"))}]})
        elif params["name"] == "cwd":
            reply(request_id, {"content": [{"type": "text", "text": os.getcwd()}]})
        else:
            reply(request_id, error={"code": -32602, "message": "unknown tool"})
    elif request_id is not None:
        reply(request_id, error={"code": -32601, "message": "method not found"})
