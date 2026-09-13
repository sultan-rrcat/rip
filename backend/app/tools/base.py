"""Tool ABC — Tier 2 utility capabilities.

An agent is a capability container (model + reasoning); a TOOL is a single-
purpose deterministic function (RAG query, chart rendering, file generation).
Tools never call an LLM directly — they do one thing honestly or fail
explicitly.

Metadata mirrors the Agent pattern so the Planner menu renders from one
shape: tool_id / name / description / input_schema, plus the effect class:

- "read-only": free to run within budget (rag.query).
- "sandboxed": bounded compute producing data/artifacts (plot.chart,
  doc.generate, code.sandbox, image.generate).
- "side-effecting": outbound or mutating. RIP is local single-user, so
  tools execute directly with no approval gate (the executor runs every
  registered tool unconditionally); the class is kept for manifest honesty.

RIP port: no plugin system — inherit directly from Tool (ADR-017).
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, Field

from app.core.classutils import is_abstract

ToolEffect = Literal["read-only", "sandboxed", "side-effecting"]

EFFECT_READ_ONLY: ToolEffect = "read-only"
EFFECT_SANDBOXED: ToolEffect = "sandboxed"
EFFECT_SIDE_EFFECTING: ToolEffect = "side-effecting"

EFFECT_CLASSES: tuple[str, ...] = ("read-only", "sandboxed", "side-effecting")


class ToolRequest(BaseModel):
    tool_id: str
    step_id: str = ""
    trace_id: str = ""
    input: dict[str, Any] = Field(default_factory=dict)
    timeout_ms: int = 30000


class ToolResponse(BaseModel):
    tool_id: str
    ok: bool = True
    output: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)
    error: str | None = None


class Tool(ABC):
    """Deterministic single-purpose capability (Tier 2).

    Concrete tools set class-level metadata; __init_subclass__ enforces the
    required fields — the same pattern as Agent.
    """

    tool_id: str
    name: str
    description: str
    input_schema: ClassVar[dict] = {}
    output_schema: ClassVar[dict] = {}
    effect_class: ToolEffect = EFFECT_READ_ONLY  # type: ignore[assignment]
    requires_approval: bool = False
    cost_class: str = "low"

    _REQUIRED_METADATA: ClassVar[tuple[str, ...]] = ("tool_id", "name", "description")

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if is_abstract(cls):
            return
        missing = [attr for attr in Tool._REQUIRED_METADATA if getattr(cls, attr, None) is None]
        if missing:
            raise TypeError(f"{cls.__name__} must define class attributes: {', '.join(missing)}")
        effect = getattr(cls, "effect_class", None)
        if effect not in EFFECT_CLASSES:
            raise TypeError(
                f"{cls.__name__} effect_class {effect!r} must be one of {', '.join(EFFECT_CLASSES)}"
            )

    @abstractmethod
    def execute(self, request: ToolRequest) -> ToolResponse:
        """Run the tool deterministically; never raise — return ok=False."""
