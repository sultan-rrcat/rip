from __future__ import annotations

from typing import ClassVar

from app.agents.base import Agent
from app.agents.provider_agent import ProviderAgent
from app.core.config import settings
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


class ReasoningAgent(ProviderAgent):
    agent_id = "reasoning"
    name = "Reasoning Agent"
    description = (
        "General conversational assistance, explanation, and brainstorming; "
        "fallback when no specialized capability fits."
    )
    input_schema: ClassVar[dict] = {"message": "str", "history": "optional list of {role, content}"}
    requires_permission = False
    side_effecting = False
    cost_class = "low"

    system_prompt = REASONING_SYSTEM_PROMPT
    default_budget = settings.default_max_tokens

    def __init__(self, provider: ModelProvider):
        super().__init__(provider)
