"""The local web dashboard: a stdlib HTTP server and the React bundle it serves."""

from __future__ import annotations

from fath.server.app import Server, load_or_create_token, serve

__all__ = ["Server", "load_or_create_token", "serve"]
