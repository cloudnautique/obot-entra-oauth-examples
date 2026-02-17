# Azure OAuth Demo — FastMCP + Entra ID

A Python MCP server ([FastMCP](https://github.com/jlowin/fastmcp)) that calls Microsoft Graph API using Bearer tokens provided by an Obot gateway. The gateway handles the full OAuth 2.1 flow with Azure Entra ID — the MCP server receives a Graph token and forwards it to the Graph API.

## Architecture

### App Registrations

Two Azure Entra ID app registrations control the OAuth flow:

| App | Purpose |
|-----|---------|
| **MCP Demo Server** | Defines the consent boundary. Exposes an `access_as_user` delegated scope that the Client App is pre-authorized against. Has no Graph permissions itself. |
| **MCP Demo Client** | The OAuth client used by Obot. Has delegated Graph permissions (`User.Read`, `Mail.Read`) and a client secret for the authorization code flow. |

**Is the Server App strictly required?** No. The MCP server validates incoming tokens against Microsoft Graph's audience (`00000003-0000-0000-c000-000000000000`), not the Server App's ID. The Client App alone could request `User.Read Mail.Read offline_access` directly and everything would work. The Server App exists to provide a clean consent boundary — grouping Graph permissions under a single `access_as_user` scope tied to this application — which is Azure's recommended pattern for multi-tier apps.

### Token Validation

The MCP server receives **Microsoft Graph access tokens** from the gateway. These tokens have a specific property: Microsoft includes a `nonce` field in the JWT header for proof-of-possession, which makes cryptographic signature verification fail with all third-party JWT libraries (authlib, PyJWT, python-jose). [Microsoft's own docs](https://learn.microsoft.com/en-us/entra/identity-platform/access-tokens) state that access tokens for Microsoft APIs should be treated as opaque by clients.

Because of this, the server uses a `GraphTokenVerifier` that decodes the JWT **without signature verification** and validates claims:

| Claim | Check |
|-------|-------|
| `exp` | Token is not expired |
| `aud` | Must be `00000003-0000-0000-c000-000000000000` (Microsoft Graph's app ID) |
| `iss` | Must match `https://sts.windows.net/{tenant_id}/` |
| `scp` | Must contain `User.Read` AND `Mail.Read` |

This prevents expired tokens, tokens from other tenants, tokens meant for other APIs, and tokens without the required scopes. Graph itself performs full cryptographic validation when the token is forwarded.

The server also serves **OAuth Protected Resource Metadata** ([RFC 9728](https://www.rfc-editor.org/rfc/rfc9728)) via `RemoteAuthProvider`, so MCP clients can discover the authorization server automatically.

### OAuth Login Flow

When a user first connects, Obot discovers the MCP server's auth requirements, drives the Azure login, and stores the resulting token.

```
┌──────┐       ┌──────────┐       ┌────────────┐       ┌──────────────┐
│ User │       │   Obot   │       │ MCP Server │       │ Azure Entra  │
│      │       │          │       │ (FastMCP)  │       │     ID       │
└──┬───┘       └────┬─────┘       └─────┬──────┘       └──────┬───────┘
   │                │                   │                     │
   │ 1. Connect     │                   │                     │
   │───────────────>│                   │                     │
   │                │                   │                     │
   │                │ 2. POST /mcp      │                     │
   │                │   (initialize)    │                     │
   │                │──────────────────>│                     │
   │                │                   │                     │
   │                │ 3. 401            │                     │
   │                │   WWW-Authenticate│                     │
   │                │   resource_meta   │                     │
   │                │<──────────────────│                     │
   │                │                   │                     │
   │                │ 4. GET /.well-known/oauth-protected-    │
   │                │    resource/mcp   │                     │
   │                │──────────────────>│                     │
   │                │                   │                     │
   │                │ 5. {resource,     │                     │
   │                │  authorization_   │                     │
   │                │  servers, scopes} │                     │
   │                │<──────────────────│                     │
   │                │                   │                     │
   │                │ 6. GET /.well-known/openid-configuration
   │                │──────────────────────────────────────-->│
   │                │                   │                     │
   │                │ 7. {authorize_endpoint, token_endpoint} │
   │                │<────────────────────────────────────────│
   │                │                   │                     │
   │                │ 8. Build auth URL │                     │
   │                │   (client_id,     │                     │
   │                │    redirect_uri,  │                     │
   │                │    PKCE, scopes)  │                     │
   │                │                   │                     │
   │ 9. Auth URL    │                   │                     │
   │  (popup/       │                   │                     │
   │   redirect)    │                   │                     │
   │<───────────────│                   │                     │
   │                │                   │                     │
   │ 10. User logs in + consents        │                     │
   │──────────────────────────────────────────────────────-->│
   │                │                   │                     │
   │                │ 11. Callback with auth code             │
   │                │  (redirect to     │                     │
   │                │   Obot callback)  │                     │
   │<─────────────────────────────────────────────────────────│
   │───────────────>│                   │                     │
   │                │                   │                     │
   │                │ 12. Exchange code  │                     │
   │                │   + PKCE verifier │                     │
   │                │   + client secret │                     │
   │                │──────────────────────────────────────-->│
   │                │                   │                     │
   │                │ 13. {access_token, refresh_token}       │
   │                │<────────────────────────────────────────│
   │                │                   │                     │
   │                │ ┌───────────────┐ │                     │
   │                │ │ Store token   │ │                     │
   │                │ │ in DB/vault   │ │                     │
   │                │ └───────────────┘ │                     │
   │                │                   │                     │
   │ 14. Auth       │                   │                     │
   │   complete     │                   │                     │
   │<───────────────│                   │                     │
```

### Authenticated MCP Request

On subsequent requests, Obot loads the stored token and forwards it to the MCP server as a Bearer token.

```
┌──────┐       ┌──────────┐       ┌────────────┐       ┌─────────────┐
│ User │       │   Obot   │       │ MCP Server │       │  Microsoft  │
│      │       │          │       │ (FastMCP)  │       │  Graph API  │
└──┬───┘       └────┬─────┘       └─────┬──────┘       └──────┬──────┘
   │                │                   │                     │
   │ 1. Tool call   │                   │                     │
   │───────────────>│                   │                     │
   │                │                   │                     │
   │                │ ┌───────────────┐ │                     │
   │                │ │ Load token    │ │                     │
   │                │ │ from DB/vault │ │                     │
   │                │ └───────────────┘ │                     │
   │                │                   │                     │
   │                │ 2. POST /mcp      │                     │
   │                │   Authorization:  │                     │
   │                │   Bearer <token>  │                     │
   │                │──────────────────>│                     │
   │                │                   │                     │
   │                │                   │ 3. Verify claims    │
   │                │                   │   (exp, aud, iss,   │
   │                │                   │    scp — no sig)    │
   │                │                   │                     │
   │                │                   │ 4. GET /v1.0/me     │
   │                │                   │   Authorization:    │
   │                │                   │   Bearer <token>    │
   │                │                   │────────────────────>│
   │                │                   │                     │
   │                │                   │ 5. User data        │
   │                │                   │<────────────────────│
   │                │                   │                     │
   │                │ 6. Tool result    │                     │
   │                │<──────────────────│                     │
   │                │                   │                     │
   │ 7. Response    │                   │                     │
   │<───────────────│                   │                     │
```

## Assumptions

- The **gateway is trusted** to perform the OAuth flow correctly and provide legitimate Graph tokens. The MCP server does not perform OBO (On-Behalf-Of) token exchange.
- **Signature verification is intentionally skipped** because Microsoft Graph tokens include a `nonce` header for proof-of-possession that prevents third-party signature validation. Microsoft Graph itself validates the signature when the token is forwarded.
- The MCP server validates claims locally as a **defense-in-depth** measure — rejecting expired, wrong-tenant, wrong-audience, or wrong-scope tokens before they reach Graph.

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- [Azure CLI](https://learn.microsoft.com/en-us/cli/azure/install-azure-cli) (`az`)
- An Azure Entra ID tenant with admin access

## Setup

### 1. Install dependencies

```bash
uv sync
```

### 2. Create Azure app registrations

```bash
az login
bash setup-azure.sh        # non-interactive
bash setup-azure.sh -i     # interactive (confirm each step)
```

This creates both app registrations and writes the server `.env` file (`AZURE_TENANT_ID`, `BASE_URL`). Gateway configuration values (client ID, secret, URLs, scopes) are printed to stdout.

### 3. Grant admin consent

After the script runs, grant admin consent for the **Client App** (it has the Graph permissions). The script prints a direct link, or:

1. Go to **Azure Portal > App registrations > MCP Demo Client > API permissions** — click "Grant admin consent"

The Server App doesn't need admin consent — it only exposes a scope and has no API permissions.

### 4. Configure the Obot gateway

In your Obot instance, setup a remote MCP server to point to the FastMCP instance. Under advanced settings configure a credential with the values from the setup script output:

| Field | Value |
|-------|-------|
| Client ID | Client App ID from setup output |
| Client Secret | Client Secret from setup output |

### 5. Add redirect URI

Add your Obot callback URL as a redirect URI on the Client App:

**Azure Portal > App registrations > MCP Demo Client > Authentication > Add a platform > Web**

Example: `http://localhost:8080/oauth/mcp/callback`

### 6. Start the server

```bash
uv run python server.py
```

The server starts on `http://0.0.0.0:8000` with streamable HTTP transport.

## Configuration Reference

### MCP Server `.env` (written by `setup-azure.sh`)

| Variable | Description |
|----------|-------------|
| `AZURE_TENANT_ID` | Your Azure Entra ID tenant ID — used in the authorization server URL for metadata and for issuer validation |
| `BASE_URL` | The server's public URL (default: `http://localhost:8000`) — used for metadata endpoints |

### Gateway / Obot

| Value | Description |
|-------|-------------|
| Client App ID | The client app registration's application ID |
| Client Secret | The client app's secret (for authorization code exchange) |

## Tools

| Tool | Description |
|------|-------------|
| `hello` | Returns a greeting using the authenticated user's display name from Microsoft Graph |
| `list_junk_emails` | Lists the 5 most recent junk emails from the user's mailbox |
