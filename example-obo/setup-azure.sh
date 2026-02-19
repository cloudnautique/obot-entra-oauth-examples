#!/usr/bin/env bash
set -euo pipefail

# Azure Entra ID app registration script for MCP OBO Demo
# Prerequisites: az login
# Usage: bash setup-azure.sh [-i]   (-i for interactive/confirm-every-step mode)

INTERACTIVE=false
while getopts "i" opt; do
    case $opt in
        i) INTERACTIVE=true ;;
        *) echo "Usage: $0 [-i]"; exit 1 ;;
    esac
done

confirm() {
    if [ "$INTERACTIVE" = true ]; then
        echo ""
        read -r -p ">> $1 [Y/n] " response
        case "$response" in
            [nN][oO]|[nN]) echo "Aborted."; exit 1 ;;
        esac
    fi
}

TENANT_ID=$(az account show --query tenantId -o tsv)
echo "Tenant ID: $TENANT_ID"

# Graph permission IDs
GRAPH_APP_ID="00000003-0000-0000-c000-000000000000"
USER_READ_ID="e1fe6dd8-ba31-4d61-89e7-88639da4683d"
MAIL_READ_ID="570282fd-fa5c-430d-a7fd-fc8dc98a9dca"

# ── Step 1: Create Server API App ──────────────────────────────────────────────
confirm "Create Server API app registration?"

echo ""
echo "Creating Obot OBO Demo Server app..."
SERVER_APP_ID=$(az ad app create \
    --display-name "Obot OBO Demo Server" \
    --sign-in-audience AzureADMyOrg \
    --query appId -o tsv)
echo "Server App ID: $SERVER_APP_ID"

# Set identifier URI
az ad app update --id "$SERVER_APP_ID" \
    --identifier-uris "api://$SERVER_APP_ID"

# Add access_as_user scope
SCOPE_ID=$(uuidgen | tr '[:upper:]' '[:lower:]')
az ad app update --id "$SERVER_APP_ID" \
    --set api="{\"oauth2PermissionScopes\":[{\"adminConsentDescription\":\"Access MCP Demo as the signed-in user\",\"adminConsentDisplayName\":\"Access as user\",\"id\":\"$SCOPE_ID\",\"isEnabled\":true,\"type\":\"User\",\"userConsentDescription\":\"Access MCP Demo on your behalf\",\"userConsentDisplayName\":\"Access as user\",\"value\":\"access_as_user\"}]}"

# Add Microsoft Graph delegated permissions to Server App (needed for OBO exchange)
az ad app permission add --id "$SERVER_APP_ID" \
    --api "$GRAPH_APP_ID" \
    --api-permissions "$USER_READ_ID=Scope" "$MAIL_READ_ID=Scope"

# Create client secret for Server App (needed for OBO exchange)
SERVER_SECRET=$(az ad app credential reset \
    --id "$SERVER_APP_ID" \
    --display-name "OBO Secret" \
    --query password -o tsv)

# Create service principal for server app
az ad sp create --id "$SERVER_APP_ID" 2>/dev/null || echo "Server SP already exists"

# ── Step 2: Create Client App ──────────────────────────────────────────────────
confirm "Create Client app registration?"

echo ""
echo "Creating Obot OBO Demo Client app..."
CLIENT_APP_ID=$(az ad app create \
    --display-name "Obot OBO Demo Client" \
    --sign-in-audience AzureADMyOrg \
    --query appId -o tsv)
echo "Client App ID: $CLIENT_APP_ID"

# Create client secret
CLIENT_SECRET=$(az ad app credential reset \
    --id "$CLIENT_APP_ID" \
    --display-name "Client Secret" \
    --query password -o tsv)

# Add API permission for server app's access_as_user scope (only scope the client needs)
az ad app permission add --id "$CLIENT_APP_ID" \
    --api "$SERVER_APP_ID" \
    --api-permissions "$SCOPE_ID=Scope"

# Create service principal for client app
az ad sp create --id "$CLIENT_APP_ID" 2>/dev/null || echo "Client SP already exists"

# ── Step 3: Pre-authorize client on server app ─────────────────────────────────
confirm "Pre-authorize client app on server app?"

echo ""
echo "Pre-authorizing client on server app..."
az ad app update --id "$SERVER_APP_ID" \
    --set api="{\"oauth2PermissionScopes\":[{\"adminConsentDescription\":\"Access MCP Demo as the signed-in user\",\"adminConsentDisplayName\":\"Access as user\",\"id\":\"$SCOPE_ID\",\"isEnabled\":true,\"type\":\"User\",\"userConsentDescription\":\"Access MCP Demo on your behalf\",\"userConsentDisplayName\":\"Access as user\",\"value\":\"access_as_user\"}],\"preAuthorizedApplications\":[{\"appId\":\"$CLIENT_APP_ID\",\"delegatedPermissionIds\":[\"$SCOPE_ID\"]}]}"

# ── Step 4: Write server .env ─────────────────────────────────────────────────
confirm "Write server .env file?"

cat > .env <<EOF
AZURE_TENANT_ID=$TENANT_ID
AZURE_CLIENT_ID=$SERVER_APP_ID
AZURE_CLIENT_SECRET=$SERVER_SECRET
BASE_URL=http://localhost:8000
EOF

echo ""
echo "====================================="
echo "Setup complete!"
echo "====================================="
echo ""
echo "Server .env written with AZURE_TENANT_ID, AZURE_CLIENT_ID, AZURE_CLIENT_SECRET, and BASE_URL."
echo ""
echo "── Gateway / Obot Configuration ─────────────────────────────────────────────"
echo ""
echo "Configure your Obot gateway (or OAuth client) with these values:"
echo ""
echo "  Client App ID:     $CLIENT_APP_ID"
echo "  Client Secret:     $CLIENT_SECRET"
echo "  Server App ID:     $SERVER_APP_ID"
echo "  Tenant ID:         $TENANT_ID"
echo ""
echo "  Authorization URL: https://login.microsoftonline.com/$TENANT_ID/oauth2/v2.0/authorize"
echo "  Token URL:         https://login.microsoftonline.com/$TENANT_ID/oauth2/v2.0/token"
echo "  Scopes:            api://$SERVER_APP_ID/access_as_user offline_access"
echo ""
echo "── Redirect URI ─────────────────────────────────────────────────────────────"
echo ""
echo "Add your Obot callback URL as a redirect URI on the Client App:"
echo "  Azure Portal > App registrations > Obot OBO Demo Client > Authentication > Add a platform > Web"
echo "  Example: http://localhost:8080/oauth/mcp/callback"
echo ""
echo "── Admin Consent ────────────────────────────────────────────────────────────"
echo ""
echo "Grant admin consent for the Server App (it holds Graph permissions for OBO) in Azure Portal:"
echo "  https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationMenuBlade/~/CallAnAPI/appId/$SERVER_APP_ID"
echo ""
echo "Then start the server: uv run python server.py"
