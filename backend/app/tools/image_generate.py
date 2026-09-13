"""image.generate tool — provider-routed image generation.

The tool calls the optional ModelProvider.generate_image() capability on its
bound provider: Ollama attempts its OpenAI-compatible images surface;
providers without the capability fail honestly. With no image model
configured (``ollama_image_model`` empty — the default), calls fail honest
instead of fabricating bytes.

Effect class: sandboxed (bounded compute producing an artifact). Client
delivery of the bytes waits for file-based artifacts (Q34): the PNG travels
as base64 in ToolResponse.data with a short textual output.

RIP port: no plugin system — direct Tool subclass (ADR-017); the provider
binds via constructor (tests, factory) instead of PluginContext.
"""

from __future__ import annotations

import base64
import logging
from typing import ClassVar

from app.providers.base import ModelProvider
from app.tools.base import Tool, ToolRequest, ToolResponse

logger = logging.getLogger("tools.image_generate")


class ImageGenerateTool(Tool):
    tool_id = "image.generate"
    name = "Image Generate"
    description = (
        "Generate one image from a text prompt using the bound provider's "
        "image model; returns base64 PNG bytes with its mime type."
    )
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {"message": {"type": "string"}},
        "required": ["message"],
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "mime": {"type": "string"},
            "image_b64": {"type": "string"},
            "byte_count": {"type": "integer"},
        },
    }
    effect_class = "sandboxed"  # type: ignore[assignment]
    cost_class = "high"

    def __init__(self, provider: ModelProvider | None = None) -> None:
        self._provider = provider

    def bind_provider(self, provider: ModelProvider) -> None:
        """Direct binding for tests and non-factory construction."""
        self._provider = provider

    def execute(self, request: ToolRequest) -> ToolResponse:
        prompt = request.input.get("message")
        if not prompt:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'message' is required in input",
            )
        if self._provider is None:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="image.generate has no provider bound",
            )
        try:
            mime, raw = self._provider.generate_image(str(prompt))
        except NotImplementedError as e:
            return ToolResponse(tool_id=self.tool_id, ok=False, output=None, error=str(e))
        except Exception as e:  # noqa: BLE001 - provider failure is a tool failure
            return ToolResponse(
                tool_id=self.tool_id, ok=False, output=None, error=f"image generation failed: {e}"
            )
        return ToolResponse(
            tool_id=self.tool_id,
            ok=True,
            output=f"image generated ({mime}, {len(raw)} bytes)",
            data={
                "mime": mime,
                "image_b64": base64.b64encode(raw).decode("ascii"),
                "byte_count": len(raw),
            },
        )
