# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Python MCP server (FastMCP) protected by Azure Entra ID OAuth 2.1. Users authenticate via Entra ID, get tokens with `User.Read` and `Mail.Read` scopes, and the server passes those tokens directly to Microsoft Graph API (no OBO flow).

## Commands

- **Install deps:** `uv sync`
- **Add a dep:** `uv add <package>`
- **Run the server:** `uv run python server.py` (starts on port 8000)
- **Azure setup:** `az login && bash setup-azure.sh` (creates Entra ID app registrations)

## Architecture

- `server.py` — FastMCP server with HTTP transport, Azure OAuth auth, and two tools (`hello`, `list_junk_emails`) that call Microsoft Graph
- `setup-azure.sh` — Azure CLI script to create server API app + client app registrations
- `.env` — runtime config (tenant ID, app IDs, client secret); gitignored

Two Entra ID app registrations: a server API app (validates tokens) and a client app (pre-authorized on the server app, drives the OAuth flow).
