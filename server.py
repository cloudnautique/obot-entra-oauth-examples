import logging
import os

import httpx
import jwt as pyjwt
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.server.auth.auth import AccessToken, RemoteAuthProvider, TokenVerifier
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

tenant_id = os.environ["AZURE_TENANT_ID"]
base_url = os.environ.get("BASE_URL", "http://localhost:8000")


class GraphTokenVerifier(TokenVerifier):
    """Verify Microsoft Graph access tokens via JWT claim checks.

    Microsoft Graph tokens include a `nonce` in the JWT header for
    proof-of-possession, which prevents signature verification with
    any third-party library. Microsoft's own docs say these tokens
    should be treated as opaque by clients.

    This verifier decodes without signature verification and validates:
      - exp: not expired
      - aud: matches Graph's application ID
      - iss: matches the configured tenant
      - scp: contains all required scopes (AND logic)
    """

    def __init__(
        self,
        *,
        audience: str,
        issuer: str | list[str],
        required_scopes: list[str],
    ):
        super().__init__(required_scopes=required_scopes)
        self.audience = audience
        self.issuers = [issuer] if isinstance(issuer, str) else issuer

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None

        try:
            claims = pyjwt.decode(
                token,
                key=None,
                algorithms=["RS256"],
                audience=self.audience,
                issuer=self.issuers,
                options={
                    "verify_signature": False,
                    "verify_exp": True,
                    "verify_aud": True,
                    "verify_iss": True,
                    "require": ["exp", "aud", "iss"],
                },
            )
        except pyjwt.PyJWTError as e:
            logger.warning("Token validation failed: %s", e)
            return None

        # Scope check (AND logic)
        token_scopes = set(claims.get("scp", "").split())
        missing = set(self.required_scopes or []) - token_scopes
        if missing:
            logger.warning("Missing required scopes: %s", missing)
            return None

        return AccessToken(
            token=token,
            client_id=claims.get("appid") or claims.get("azp") or "unknown",
            scopes=list(token_scopes),
            expires_at=claims["exp"],
            claims=claims,
        )


token_verifier = GraphTokenVerifier(
    audience="00000003-0000-0000-c000-000000000000",
    issuer=f"https://sts.windows.net/{tenant_id}/",
    required_scopes=["User.Read", "Mail.Read"],
)

auth = RemoteAuthProvider(
    token_verifier=token_verifier,
    authorization_servers=[
        AnyHttpUrl(f"https://login.microsoftonline.com/{tenant_id}/v2.0"),
    ],
    base_url=base_url,
    resource_name="Azure OAuth Demo",
)

mcp = FastMCP("Azure OAuth Demo", auth=auth)


async def _graph_get(path: str) -> dict:
    """Call Microsoft Graph with the current user's token."""
    token = get_access_token()
    if token is None:
        raise RuntimeError("No access token available")
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{GRAPH_BASE}{path}",
            headers={"Authorization": f"Bearer {token.token}"},
        )
        resp.raise_for_status()
        return resp.json()


@mcp.tool()
async def hello(ctx: Context) -> str:
    """Say hello using your Microsoft profile name."""
    data = await _graph_get("/me")
    return f"Hello, {data['displayName']}!"


@mcp.tool()
async def list_junk_emails(ctx: Context) -> str:
    """List your 5 most recent junk emails."""
    data = await _graph_get(
        "/me/mailFolders/junkemail/messages?$top=5&$select=subject,from,receivedDateTime"
    )
    messages = data.get("value", [])
    if not messages:
        return "No junk emails found."
    lines = []
    for msg in messages:
        sender = msg.get("from", {}).get("emailAddress", {}).get("address", "unknown")
        lines.append(f"- {msg['subject']} (from: {sender}, {msg['receivedDateTime']})")
    return "\n".join(lines)


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
