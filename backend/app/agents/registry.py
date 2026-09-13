from __future__ import annotations

from app.agents.base import Agent
from app.providers.base import ModelProvider


class AgentRegistry:
    """In-memory registry of agents.

    Job: hold agent instances by agent_id, and expose read-only manifest
    metadata for the Planner (PM-2). Postgres-backed version comes later;
    this interface is the swap boundary.
    """

    def __init__(self) -> None:
        # self._agents = an empty dict[str, Agent]
        self._agents: dict[str, Agent] = {}

    def register(self, agent: Agent) -> None:
        if agent.agent_id in self._agents:
            raise ValueError(f"Agent already registered: {agent.agent_id}")

        self._agents[agent.agent_id] = agent

    def get(self, agent_id: str) -> Agent:
        if agent_id not in self._agents:
            raise KeyError(f"Unknown agent: {agent_id}")
        return self._agents[agent_id]

    def __len__(self) -> int:
        return len(self._agents)

    def manifest(self) -> list[dict]:
        result : list[dict] = []
        for agent in self._agents.values():
            result.append({
                "agent_id": agent.agent_id,
                "name": agent.name,
                "description": agent.description,
                "input_schema": agent.input_schema,
                "requires_permission": agent.requires_permission,
                "side_effecting": agent.side_effecting,
                "cost_class": agent.cost_class,
            })
        return result


def get_default_agent_registry(provider: ModelProvider) -> AgentRegistry:
    """Build the fixed RIP agent set (no plugin system — static composition).

    Registers reasoning, coding, and vision agents sharing `provider`.
    """
    # Local imports: keeps `app.agents.registry` importable without pulling
    # agent modules (and their provider/config deps) until the factory runs.
    from app.agents.coding import CodingAgent
    from app.agents.reasoning import ReasoningAgent
    from app.agents.vision import VisionAgent

    registry = AgentRegistry()
    registry.register(ReasoningAgent(provider))
    registry.register(CodingAgent(provider))
    registry.register(VisionAgent(provider))
    return registry
