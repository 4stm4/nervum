"""FastAPI adapter exposing the agent's HTTP API."""

from netos_agent.adapters.http_api.app import create_app

__all__ = ["create_app"]
