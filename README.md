# Azure OAuth for MCP Servers

Examples demonstrating OAuth 2.1 authentication patterns for MCP servers with Azure Entra ID.

## Examples

### [example-no-obo](./example-no-obo)

A Python MCP server (FastMCP) that receives Microsoft Graph access tokens from a trusted gateway (like Obot) and forwards them directly to Microsoft Graph API. The gateway handles the full OAuth flow, the MCP server validates token claims and proxies requests to Graph.

**Use case:** When you have a trusted OAuth gateway that manages the authorization flow and token lifecycle, and your MCP server just needs to validate and use the tokens.

### [example-obo](./example-obo)

A Python MCP server (FastMCP) that receives Server App tokens from a gateway, then performs an **On-Behalf-Of (OBO)** token exchange to obtain a Microsoft Graph token. The MCP server holds its own credentials and exchanges the user's delegated token for a Graph token on each request.

**Use case:** When your MCP server needs to own the Graph permission grants and perform the OBO exchange itself — keeping Graph tokens off the gateway and following Azure's recommended multi-tier delegated access pattern.
