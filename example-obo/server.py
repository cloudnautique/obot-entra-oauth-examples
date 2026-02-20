import asyncio
import logging
from pathlib import Path
from typing import Any

import httpx
import msal
from fastmcp import FastMCP
from fastmcp.server.auth.auth import RemoteAuthProvider
from fastmcp.server.auth.providers.jwt import JWTVerifier
from fastmcp.server.context import Context
from fastmcp.server.dependencies import get_access_token
from pydantic import AnyHttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

_ENV_FILE = Path(__file__).resolve().parent / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_ENV_FILE, env_file_encoding="utf-8")

    azure_tenant_id: str
    azure_client_id: str
    azure_client_secret: str
    base_url: AnyHttpUrl = "http://localhost:8000"
    server_host: str = "0.0.0.0"
    server_port: int = 8000


settings = Settings()

identifier_uri = f"api://{settings.azure_client_id}"


def _load_oidc_metadata(tenant_id: str) -> dict[str, Any]:
    url = (
        "https://login.microsoftonline.com/"
        f"{tenant_id}/v2.0/.well-known/openid-configuration"
    )
    resp = httpx.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


oidc_metadata = _load_oidc_metadata(settings.azure_tenant_id)

token_cache = msal.TokenCache()
msal_app = msal.ConfidentialClientApplication(
    client_id=settings.azure_client_id,
    client_credential=settings.azure_client_secret,
    authority=f"https://login.microsoftonline.com/{settings.azure_tenant_id}",
    token_cache=token_cache,
)

_graph_client: httpx.AsyncClient | None = None


def _get_graph_client() -> httpx.AsyncClient:
    global _graph_client
    if _graph_client is None:
        _graph_client = httpx.AsyncClient(timeout=30)
    return _graph_client


def _close_graph_client() -> None:
    global _graph_client
    if _graph_client is None:
        return
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = None
    if loop and loop.is_running():
        loop.create_task(_graph_client.aclose())
    else:
        asyncio.run(_graph_client.aclose())
    _graph_client = None


class AzureJWTVerifier(JWTVerifier):
    """JWTVerifier that bridges Azure's scope naming mismatch.

    Metadata advertises fully-qualified scopes (api://{id}/access_as_user)
    so the gateway sends the right scopes to Azure. But Azure puts just
    the short name (access_as_user) in the token's scp claim. This
    subclass prefixes extracted scopes so they match required_scopes.
    """

    def __init__(self, *, scope_prefix: str = "", **kwargs: Any):
        super().__init__(**kwargs)
        self._scope_prefix = scope_prefix

    def _extract_scopes(self, claims: dict[str, Any]) -> list[str]:
        scopes = super()._extract_scopes(claims)
        if self._scope_prefix:
            return [
                s if "/" in s else f"{self._scope_prefix}/{s}" for s in scopes
            ]
        return scopes

    async def load_access_token(self, token: str):
        import base64
        import json

        # Decode claims without verification for debug logging
        try:
            parts = token.split(".")
            payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
            claims = json.loads(base64.urlsafe_b64decode(payload_b64))
            logger.debug(
                "Token claims: iss=%s aud=%s scp=%s exp=%s",
                claims.get("iss"),
                claims.get("aud"),
                claims.get("scp"),
                claims.get("exp"),
            )
            logger.debug(
                "Expected: iss=%s aud=%s required_scopes=%s",
                self.issuer,
                self.audience,
                self.required_scopes,
            )
        except Exception as e:
            logger.debug("Failed to decode token for logging: %s", e)

        result = await super().load_access_token(token)
        if result is None:
            logger.warning("Token verification FAILED — check claims above against expected values")
        else:
            logger.info("Token verification succeeded")
        return result


token_verifier = AzureJWTVerifier(
    jwks_uri=oidc_metadata["jwks_uri"],
    issuer=oidc_metadata["issuer"],
    audience=settings.azure_client_id,
    algorithm="RS256",
    required_scopes=[f"{identifier_uri}/access_as_user"],
    scope_prefix=identifier_uri,
)

auth = RemoteAuthProvider(
    token_verifier=token_verifier,
    authorization_servers=[
        AnyHttpUrl(f"https://login.microsoftonline.com/{settings.azure_tenant_id}/v2.0"),
    ],
    base_url=settings.base_url,
    resource_name="Azure OAuth OBO Demo",
)

mcp = FastMCP("Azure OAuth OBO Demo", auth=auth)


async def _obo_exchange(assertion: str) -> str:
    """Exchange an incoming Server App token for a Graph token via OBO."""
    logger.info("Performing OBO token exchange...")
    result = msal_app.acquire_token_on_behalf_of(
        user_assertion=assertion,
        scopes=["https://graph.microsoft.com/.default"],
    )
    if "access_token" not in result:
        error = result.get("error")
        error_desc = result.get("error_description")
        logger.error("OBO exchange failed: %s %s", error, error_desc)
        raise RuntimeError(f"OBO token exchange failed: {error} {error_desc}")
    return result["access_token"]


async def _graph_get(path: str) -> dict:
    """Call Microsoft Graph using an OBO-exchanged token."""
    token = get_access_token()
    if token is None:
        raise RuntimeError("No access token available")

    graph_token = await _obo_exchange(token.token)

    client = _get_graph_client()
    resp = await client.get(
        f"{GRAPH_BASE}{path}",
        headers={"Authorization": f"Bearer {graph_token}"},
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
    try:
        mcp.run(
            transport="streamable-http",
            host=settings.server_host,
            port=settings.server_port,
        )
    finally:
        _close_graph_client()
