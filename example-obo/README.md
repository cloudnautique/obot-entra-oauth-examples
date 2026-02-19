# Azure OAuth OBO Demo — FastMCP + Entra ID

A Python MCP server ([FastMCP](https://github.com/jlowin/fastmcp)) that receives Server App tokens from a gateway, then performs an **On-Behalf-Of (OBO)** token exchange to obtain a Microsoft Graph token and calls the Graph API. The gateway handles the OAuth 2.1 flow with Azure Entra ID — the MCP server holds its own credentials to perform the OBO exchange.

## Architecture

### App Registrations

Two Azure Entra ID app registrations control the OAuth flow:

| App | Purpose |
|-----|---------|
| **MCP OBO Demo Server** | Validates incoming tokens. Holds the Graph permissions (`User.Read`, `Mail.Read`) and a client secret used to perform OBO token exchange. Exposes an `access_as_user` scope that the Client App is pre-authorized against. |
| **MCP OBO Demo Client** | The OAuth client used by Obot. Requests the Server App's `access_as_user` scope. Has a client secret for the authorization code flow. |

**Why OBO?** The gateway authenticates the user and obtains a token scoped to the Server App (`api://{server_app_id}/access_as_user`). The MCP server then exchanges this token for a Microsoft Graph token using the OAuth 2.0 On-Behalf-Of flow — acting on behalf of the user without storing Graph tokens in the gateway. This is Azure's recommended pattern for delegated access in multi-tier applications.

### Token Validation

The MCP server receives **Server App tokens** (not Graph tokens) from the gateway. These are standard Azure AD access tokens with cryptographic signatures, so full JWT signature verification works.

| Claim | Check |
|-------|-------|
| `exp` | Token is not expired |
| `aud` | Must be `api://{server_app_id}` (the Server App's identifier URI) |
| `iss` | Must match `https://login.microsoftonline.com/{tenant_id}/v2.0` |
| `scp` | Must contain `access_as_user` |

The server uses FastMCP's `JWTVerifier` with JWKS fetched from Azure's OIDC metadata endpoint. Signature verification uses Azure's public keys.

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
   │                │ 13. {access_token (Server App scope),   │
   │                │      refresh_token}                     │
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

### Authenticated MCP Request with OBO Exchange

On each tool call, the MCP server validates the incoming Server App token, then exchanges it for a Graph token via OBO before calling the Graph API.

```
┌──────┐       ┌──────────┐       ┌────────────┐       ┌──────────────┐     ┌─────────────┐
│ User │       │   Obot   │       │ MCP Server │       │ Azure Entra  │     │  Microsoft  │
│      │       │          │       │ (FastMCP)  │       │     ID       │     │  Graph API  │
└──┬───┘       └────┬─────┘       └─────┬──────┘       └──────┬───────┘     └──────┬──────┘
   │                │                   │                     │                    │
   │ 1. Tool call   │                   │                     │                    │
   │───────────────>│                   │                     │                    │
   │                │                   │                     │                    │
   │                │ 2. POST /mcp      │                     │                    │
   │                │   Authorization:  │                     │                    │
   │                │   Bearer <server  │                     │                    │
   │                │   app token>      │                     │                    │
   │                │──────────────────>│                     │                    │
   │                │                   │                     │                    │
   │                │                   │ 3. Verify JWT sig   │                    │
   │                │                   │   + claims (exp,    │                    │
   │                │                   │   aud, iss, scp)    │                    │
   │                │                   │                     │                    │
   │                │                   │ 4. OBO exchange     │                    │
   │                │                   │   grant_type=       │                    │
   │                │                   │   urn:ietf:params:  │                    │
   │                │                   │   oauth:grant-type: │                    │
   │                │                   │   jwt-bearer        │                    │
   │                │                   │──────────────────-->│                    │
   │                │                   │                     │                    │
   │                │                   │ 5. Graph token      │                    │
   │                │                   │<────────────────────│                    │
   │                │                   │                     │                    │
   │                │                   │ 6. GET /v1.0/me     │                    │
   │                │                   │   Authorization:    │                    │
   │                │                   │   Bearer <graph     │                    │
   │                │                   │   token>            │                    │
   │                │                   │────────────────────────────────────────>│
   │                │                   │                     │                    │
   │                │                   │ 7. User data        │                    │
   │                │                   │<────────────────────────────────────────│
   │                │                   │                     │                    │
   │                │ 8. Tool result    │                     │                    │
   │                │<──────────────────│                     │                    │
   │                │                   │                     │                    │
   │ 9. Response    │                   │                     │                    │
   │<───────────────│                   │                     │                    │
```

## Comparison with example-no-obo

| | [example-no-obo](../example-no-obo) | example-obo (this) |
|---|---|---|
| Token the gateway sends | Microsoft Graph token | Server App token |
| Graph permissions on | Client App | Server App |
| MCP server credentials | None needed | Client ID + secret (for OBO) |
| Token validation | Claims only (no sig — Graph tokens use PoP nonce) | Full JWT signature verification |
| Graph token exchange | None (token passed through) | OBO exchange on every request |

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

This creates both app registrations, writes the server `.env` file (`AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`, `BASE_URL`), and prints gateway configuration values to stdout.

### 3. Grant admin consent

After the script runs, grant admin consent for the **Server App** (it holds the Graph permissions used in OBO). The script prints a direct link, or:

1. Go to **Azure Portal > App registrations > MCP OBO Demo Server > API permissions** — click "Grant admin consent"

The Client App doesn't need admin consent — it only requests the Server App's `access_as_user` scope, which is pre-authorized.

### 4. Configure the Obot gateway

In your Obot instance, setup a remote MCP server to point to the FastMCP instance. Under advanced settings configure a credential with the values from the setup script output:

| Field | Value |
|-------|-------|
| Client ID | Client App ID from setup output |
| Client Secret | Client Secret from setup output |

### 5. Add redirect URI

Add your Obot callback URL as a redirect URI on the Client App:

**Azure Portal > App registrations > MCP OBO Demo Client > Authentication > Add a platform > Web**

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
| `AZURE_TENANT_ID` | Your Azure Entra ID tenant ID |
| `AZURE_CLIENT_ID` | Server App's application ID — used as the token audience and for OBO exchange |
| `AZURE_CLIENT_SECRET` | Server App's client secret — used to authenticate the OBO exchange request |
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
