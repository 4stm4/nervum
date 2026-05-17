"""Controller-side adapter that drives ``netos_agent`` over HTTP.

The adapter is the *only* place in the controller that knows the agent's
wire format. Use cases consume the abstract ``AgentPort`` Protocol; the
contract test in ``tests/integration/test_http_agent_client.py`` exercises
this adapter against a real ``netos_agent`` FastAPI app so both packages stay
schema-compatible.
"""

from sdn_controller.adapters.netos_agent.client import (
    AgentEndpoints,
    HttpAgentClient,
)

__all__ = ["AgentEndpoints", "HttpAgentClient"]
