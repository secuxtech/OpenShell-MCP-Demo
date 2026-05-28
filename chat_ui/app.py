"""
Web App — browser-based UI for the Claude Code Agent.

Send message : openshell sandbox exec -- claude -p "<msg>" --output-format stream-json
Receive reply: parse stream-json, stream to browser via SSE

"""

import json
import os
import subprocess

from flask import Flask, Response, render_template, request, stream_with_context

app = Flask(__name__)

SANDBOX_NAME = os.environ.get("SANDBOX_NAME", "wallet-agent")


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message", "").strip()
    if not user_message:
        return {"error": "empty message"}, 400

    # --dangerously-skip-permissions bypasses Claude Code's interactive file/command
    # confirmations (required here since the sandbox has no TTY).
    # Transaction signing confirmation is handled separately via MCP approve/reject.
    # Unsafe operations are prevented by the sandbox policy, not by interactive prompts.
    def generate():
        cmd = [
            "openshell", "sandbox", "exec", "--name", SANDBOX_NAME,
            "--",
            "claude",
            "--mcp-config", "/sandbox/.mcp.json",
            "--dangerously-skip-permissions",
            "--system-prompt", (
                "你是一個加密錢包簽名助手。"
                "當使用者提出交易請求時，使用 request_signature 工具提交以待審核。"
                "請預設以繁體中文回應，除非使用者指定其他語言。"
            ),
            "-p", user_message,
            "--output-format", "stream-json",
            "--verbose",
        ]

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            # Final result
            if event.get("type") == "result":
                text = event.get("result", "")
                if text:
                    yield f"data: {json.dumps({'type': 'text', 'content': text})}\n\n"

        proc.wait()
        yield f"data: {json.dumps({'type': 'done'})}\n\n"

    return Response(
        stream_with_context(generate()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=9000, debug=False, threaded=True)
