"""
MCP Signing Server — Mock crypto wallet signing approval service.

MCP tool: request_signature
  - Blocks until user approves or rejects via the Web App UI at port 9000

Endpoints:
  GET  /health    Health check
  GET  /pending   List pending requests (polled by Web App)
  POST /approve/{id}
  POST /reject/{id}
  MCP SSE at /mcp/sse
"""

import asyncio
import contextlib
import uuid
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

# ---------- Shared state ----------

pending: dict[str, asyncio.Future] = {}
pending_details: dict[str, dict] = {}


# ---------- MCP server ----------
# FastMCP instance — handles MCP protocol and tool registration.
#   "signing-server" : server name advertised to Claude during handshake
#   enable_dns_rebinding_protection=False : allow requests from host.openshell.internal
#   streamable_http_path="/" : root of this FastMCP app; becomes /mcp/ after FastAPI mount
mcp = FastMCP(
    "signing-server",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    streamable_http_path="/",
)

# ---------- MCP tools ----------
# Each function decorated with @mcp.tool() is automatically registered as an MCP tool.
# When Claude Agent connects at startup, it receives the full tool list via handshake
# and can call any of them by name.
#
# To add a new tool, simply define a new async function here with @mcp.tool():
#   @mcp.tool()
#   async def your_new_tool(param: str) -> str:
#       """Tool description — shown to Claude so it knows when to use this tool."""
#       return "result"
@mcp.tool()
async def request_signature(transaction: str) -> str:
    """
    Request user approval to sign a crypto transaction.
    Blocks until the user approves or rejects via the approval UI.

    Args:
        transaction: Human-readable transaction description, e.g. 'Transfer 0.5 ETH to 0xABC123'
    """
    return await _do_request_signature(transaction)


# ---------- Core signing logic ----------

async def _do_request_signature(transaction: str) -> str:
    request_id = str(uuid.uuid4())[:8]
    future: asyncio.Future = asyncio.get_event_loop().create_future()

    pending[request_id] = future
    pending_details[request_id] = {
        "id": request_id,
        "transaction": transaction,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    print(f"[MCP] Awaiting approval — id={request_id}  tx={transaction!r}")

    try:
        result = await asyncio.wait_for(future, timeout=300) # 5 min timeout
    except asyncio.TimeoutError:
        result = "timeout"
    finally:
        pending.pop(request_id, None)
        pending_details.pop(request_id, None)

    if result == "approved":
        return f"APPROVED. Mock signature: 0xMOCKSIG_{request_id.upper()}"
    elif result == "rejected":
        return "REJECTED by user."
    else:
        return f"TIMEOUT: request {request_id} expired after 5 minutes."

# ---------- FastAPI app ----------

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    async with mcp.session_manager.run():
        yield

app = FastAPI(lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

# Allow any Host header (needed when accessed via host.openshell.internal from sandbox)
from starlette.middleware.trustedhost import TrustedHostMiddleware
app.add_middleware(TrustedHostMiddleware, allowed_hosts=["*"])
app.mount("/mcp", mcp.streamable_http_app())

@app.get("/health")
def health():
    return {"status": "ok", "service": "mcp-signing-server"}

@app.get("/pending")
def get_pending():
    return list(pending_details.values())

@app.post("/approve/{request_id}")
async def approve(request_id: str):
    if request_id not in pending:
        return JSONResponse(status_code=404, content={"error": "not found"})
    _resolve(pending[request_id], "approved")
    return {"status": "approved"}

@app.post("/reject/{request_id}")
async def reject(request_id: str):
    if request_id not in pending:
        return JSONResponse(status_code=404, content={"error": "not found"})
    _resolve(pending[request_id], "rejected")
    return {"status": "rejected"}

def _resolve(future: asyncio.Future, value: str):
    if not future.done():
        future.set_result(value)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=9001, forwarded_allow_ips="*", http="httptools")
