# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Repository containing examples for Azure Entra ID OAuth 2.1 authentication with MCP servers.

## Structure

- `example-no-obo/` — FastMCP server that receives Graph tokens from a trusted gateway (no OBO flow)
- `example-obo/` — FastMCP server that receives Server App tokens and performs OBO exchange to get Graph tokens

## Working with example-no-obo

- **Install deps:** `cd example-no-obo && uv sync`
- **Add a dep:** `cd example-no-obo && uv add <package>`
- **Run the server:** `cd example-no-obo && uv run python server.py` (starts on port 8000)
- **Azure setup:** `cd example-no-obo && az login && bash setup-azure.sh` (creates Entra ID app registrations)

### example-no-obo Architecture

- `server.py` — FastMCP server with HTTP transport, Azure OAuth auth, and two tools (`hello`, `list_junk_emails`) that call Microsoft Graph
- `setup-azure.sh` — Azure CLI script to create server API app + client app registrations
- `.env` — runtime config (tenant ID, app IDs, client secret); gitignored

Two Entra ID app registrations: a server API app (validates tokens) and a client app (pre-authorized on the server app, drives the OAuth flow).

## Working with example-obo

- **Install deps:** `cd example-obo && uv sync`
- **Add a dep:** `cd example-obo && uv add <package>`
- **Run the server:** `cd example-obo && uv run python server.py` (starts on port 8000)
- **Azure setup:** `cd example-obo && az login && bash setup-azure.sh` (creates Entra ID app registrations)

### example-obo Architecture

- `server.py` — FastMCP server that validates Server App tokens (full JWT sig), performs OBO exchange for Graph tokens, and calls Microsoft Graph
- `setup-azure.sh` — Azure CLI script to create server app (with Graph permissions + OBO secret) + client app registrations
- `.env` — runtime config (tenant ID, server app client ID + secret); gitignored

Two Entra ID app registrations: a server app (holds Graph permissions, performs OBO) and a client app (drives the OAuth flow, requests only the server app scope).
