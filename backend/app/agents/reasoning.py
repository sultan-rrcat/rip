from __future__ import annotations

from typing import ClassVar

from app.agents.provider_agent import ProviderAgent
from app.providers.base import ModelProvider

#: User-facing voice for every reasoning output (chat, grounded QA,
#: compare/summarize/quiz writers, ReAct synthesis). All of these render
#: directly in chat, so this single system prompt is the presentation lever.
REASONING_SYSTEM_PROMPT = (
    "You are RIP, a world-class research assistant. Answer the user "
    "directly in clear, friendly, well-formatted Markdown: lead with the "
    "answer, then supporting detail; use short headings, bullets, numbered "
    "steps, or a table when it helps readability; keep code spans for file "
    "and section names. Be concise but complete. Cite document sections by "
    "name when evidence was provided. Never expose internal machinery "
    "(step ids, placeholders like {{1}}, chunk ids, model or tool names). "
    "If the evidence does not cover something, say what is missing honestly."
)


REASONING_DESCRIPTION = (
    "General conversational assistance, explanation, and brainstorming; "
    "fallback when no specialized capability fits."
)


class ReasoningAgent(ProviderAgent):
    agent_id = "reasoning"
    name = "Reasoning Agent"
    input_schema: ClassVar[dict] = {"message": "str", "history": "optional list of {role, content}"}
    requires_permission = False
    side_effecting = False
    cost_class = "low"

    @property
    def description(self) -> str:
        # Live read like default_budget below: admin description edits
        # shape planner menus on the next run. The subclass check in
        # Agent.__init_subclass__ still passes (a property object is not
        # None at class level).
        from app.core.promptstore import get_prompt

        return get_prompt("agent.reasoning.description")

    @property
    def system_prompt(self) -> str:
        from app.core.promptstore import get_prompt

        return get_prompt("agent.reasoning.system_prompt")

    @property
    def default_budget(self) -> int:
        # Live read (never a class-level `= settings.…` capture): the admin
        # console mutates the singleton at runtime and the next step must
        # see the new budget without a backend restart.
        from app.core.config import settings

        return settings.default_max_tokens

    def __init__(self, provider: ModelProvider):
        super().__init__(provider)
