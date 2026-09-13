"""plot.chart tool — deterministic stdlib SVG charts.

Deliberately dependency-free: bars and lines render as inline SVG from the
standard library, so no new runtime dependency is introduced. File
persistence arrives with artifact delivery (Q34); today the SVG travels
inline in the tool response.

Effect class: sandboxed — bounded compute producing an artifact.

RIP port: no plugin system — direct Tool subclass (ADR-017).
"""

from __future__ import annotations

from html import escape
from typing import ClassVar

from app.tools.base import Tool, ToolRequest, ToolResponse

_MAX_POINTS = 50
_WIDTH, _HEIGHT = 640, 360
_PAD_LEFT, _PAD_RIGHT, _PAD_TOP, _PAD_BOTTOM = 56, 16, 36, 44


def _scale(values: list[float], height: float) -> list[float]:
    peak = max(values) if values else 0.0
    base = min(0.0, min(values) if values else 0.0)
    span = peak - base or 1.0
    return [(v - base) / span * height for v in values]


def render_svg(
    chart_type: str, labels: list[str], values: list[float], *, title: str = ""
) -> str:
    """Render a bar/line chart as an SVG document string."""
    plot_w = _WIDTH - _PAD_LEFT - _PAD_RIGHT
    plot_h = _HEIGHT - _PAD_TOP - _PAD_BOTTOM
    heights = _scale(values, plot_h)
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{_WIDTH}" height="{_HEIGHT}" role="img">',
    ]
    if title:
        parts.append(
            f"<text x='{_WIDTH // 2}' y='24' text-anchor='middle' "
            f"font-size='16' font-family='sans-serif'>{escape(title)}</text>"
        )
    parts.append(
        f"<rect x='{_PAD_LEFT}' y='{_PAD_TOP}' width='{plot_w}' "
        f"height='{plot_h}' fill='none' stroke='#888'/>"
    )
    n = len(values)
    if chart_type == "bar":
        gap = 6.0
        bar_w = (plot_w - gap * (n + 1)) / n if n else 0
        for i, (label, h) in enumerate(zip(labels, heights, strict=True)):
            x = _PAD_LEFT + gap + i * (bar_w + gap)
            y = _PAD_TOP + plot_h - h
            parts.append(
                f"<rect x='{x:.1f}' y='{y:.1f}' width='{bar_w:.1f}' "
                f"height='{h:.1f}' fill='#4a90d9'>"
                f"<title>{escape(label)}: {values[i]}</title></rect>"
            )
    else:  # line
        step = plot_w / (n - 1) if n > 1 else 0
        points = " ".join(
            f"{_PAD_LEFT + i * step:.1f},{_PAD_TOP + plot_h - h:.1f}"
            for i, h in enumerate(heights)
        )
        parts.append(f"<polyline points='{points}' fill='none' stroke='#4a90d9' stroke-width='2'/>")
        for i, (label, h) in enumerate(zip(labels, heights, strict=True)):
            x = _PAD_LEFT + i * step
            y = _PAD_TOP + plot_h - h
            parts.append(
                f"<circle cx='{x:.1f}' cy='{y:.1f}' r='3' fill='#4a90d9'>"
                f"<title>{escape(label)}: {values[i]}</title></circle>"
            )
    # x labels: first, middle, last (keeps small SVGs readable)
    for i in sorted({0, n // 2, n - 1}):
        x = _PAD_LEFT + (i + 0.5) * (plot_w / n) if chart_type == "bar" else _PAD_LEFT + i * step
        parts.append(
            f"<text x='{x:.1f}' y='{_HEIGHT - 12}' text-anchor='middle' "
            f"font-size='11' font-family='sans-serif'>{escape(labels[i])}</text>"
        )
    parts.append("</svg>")
    return "".join(parts)


class PlotChartTool(Tool):
    tool_id = "plot.chart"
    name = "Plot Chart"
    description = "Render a bar or line chart as inline SVG from labels + numeric values."
    input_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "chart_type": {"type": "string", "enum": ["bar", "line"]},
            "labels": {"type": "array", "items": {"type": "string"}},
            "values": {"type": "array", "items": {"type": "number"}},
            "title": {"type": "string"},
        },
        "required": ["chart_type", "labels", "values"],
    }
    output_schema: ClassVar[dict] = {
        "type": "object",
        "properties": {
            "svg": {"type": "string"},
            "chart_type": {"type": "string"},
            "point_count": {"type": "integer"},
        },
    }
    effect_class = "sandboxed"  # type: ignore[assignment]
    cost_class = "low"

    def execute(self, request: ToolRequest) -> ToolResponse:
        chart_type = request.input.get("chart_type")
        labels = request.input.get("labels")
        values = request.input.get("values")
        title = str(request.input.get("title", "") or "")
        if chart_type not in ("bar", "line"):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'chart_type' must be 'bar' or 'line'",
            )
        if not isinstance(labels, list) or not isinstance(values, list) or not labels:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'labels' and 'values' must be non-empty arrays",
            )
        if len(labels) != len(values):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'labels' and 'values' must have the same length",
            )
        if len(labels) > _MAX_POINTS:
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error=f"at most {_MAX_POINTS} points per chart",
            )
        try:
            numbers = [float(v) for v in values]
        except (TypeError, ValueError):
            return ToolResponse(
                tool_id=self.tool_id,
                ok=False,
                output=None,
                error="'values' must all be numbers",
            )
        str_labels = [str(label) for label in labels]
        svg = render_svg(str(chart_type), str_labels, numbers, title=title)
        return ToolResponse(
            tool_id=self.tool_id,
            ok=True,
            output=svg,
            data={"svg": svg, "chart_type": chart_type, "point_count": len(numbers)},
        )
