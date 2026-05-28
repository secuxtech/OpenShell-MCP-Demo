# OpenShell MCP PoC — Pitfalls & Solutions

This document records four issues encountered in sequence while connecting the Claude Code Agent to the MCP Signing Server, along with their resolutions.

---

## Issue 1: SSRF Protection (`ssrf_denied`)

**Symptom:** When the agent inside the sandbox calls an MCP tool, the OpenShell gateway log shows `ssrf_denied` and the connection is blocked.

**Root Cause:** `host.openshell.internal` resolves to the host machine's private IP (`172.17.0.1` or `192.168.x.x`). The OpenShell gateway has SSRF protection that by default disallows forwarding traffic to private IP ranges, preventing processes inside the sandbox from probing the internal network.

**Fix:** Add `allowed_ips` to the endpoint configuration in `policy.yaml` to explicitly tell the gateway "this hostname resolving to a private IP is expected — allow it":

```yaml
- host: host.openshell.internal
  port: 9001
  allowed_ips:
    - 172.16.0.0/12   # Docker bridge networks
    - 10.0.0.0/8      # Private class A
    - 192.168.0.0/16  # Private class C
```

> If the host machine's IP is not in the ranges above, run `getent hosts host.openshell.internal` inside the sandbox to get the actual IP, then add the corresponding CIDR.

---

## Issue 2: 404 — Wrong MCP Endpoint Path

**Symptom:** SSRF is resolved, but MCP calls return 404.

**Root Cause:** A combination issue between how FastMCP's `streamable_http_app()` is mounted and the path configuration.

- Mounting the entire FastMCP app at `/` overrides all other routes (the approval UI, `/pending`, and `/approve` all disappear).
- After moving the mount to `/mcp`, FastMCP's internal default `streamable_http_path` is incorrect, causing `/mcp/` to still return 404.

**Fix:** Both settings must be used together:

```python
# streamable_http_path="/" sets the MCP internal path to root
mcp = FastMCP("signing-server", streamable_http_path="/")

# Then mount it at /mcp in the outer app; final MCP endpoint is at /mcp/
app.mount("/mcp", mcp.streamable_http_app())
```

Corresponding `.mcp.json` configuration:

```json
{
  "mcpServers": {
    "signing": {
      "url": "http://host.openshell.internal:9001/mcp",
      "type": "http"
    }
  }
}
```

---

## Issue 3: 421 Invalid Host Header

**Symptom:** The path is correct, but MCP calls return 421 Invalid Host.

**Root Cause:** FastMCP has a built-in DNS rebinding protection — it checks the `Host` header of incoming HTTP requests and rejects anything that is not `localhost` or `127.0.0.1`, preventing external sites from attacking the local MCP server via DNS rebinding.

The request sent from inside the sandbox has a `Host` header of `host.openshell.internal`, which triggers this protection.

**Fix:** Disable DNS rebinding protection when creating the FastMCP instance:

```python
from mcp.server.transport_security import TransportSecuritySettings

mcp = FastMCP(
    "signing-server",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    streamable_http_path="/",
)
```

---

## Summary

| # | Issue | Root Cause | Fix |
|---|-------|------------|-----|
| 1 | `ssrf_denied` | OpenShell gateway blocks private IPs | Add `allowed_ips` to `policy.yaml` |
| 2 | 404 on `/mcp/` | FastMCP mount path misconfiguration | `streamable_http_path="/"` + `mount("/mcp", ...)` |
| 3 | 421 Invalid Host | FastMCP DNS rebinding protection | `enable_dns_rebinding_protection=False` |

All three issues fall into the category of "not documented anywhere — only discovered by actually running it." Each problem is straightforward on its own, but because they must be fixed in order to expose the next one, the overall debugging process requires peeling back the layers one at a time.
