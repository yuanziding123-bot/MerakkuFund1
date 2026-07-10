"""plot_market — visualize market data as an inline SVG chart (core, always-on).

Covers the capability routing, CORE membership, and the SVG renderer for every
chart type (well-formed XML, no network)."""
from __future__ import annotations

import xml.dom.minidom as minidom

from polyagents.kernel.controller import KernelController
from polyagents.kernel.capabilities import plot_market_capability
from polyagents.kernel.packs import CORE, kernel_capability_names
from polyagents.web import server


class FakeLLM:
    def __init__(self, *replies):
        self.replies = list(replies)

    def invoke(self, messages):
        text = self.replies.pop(0) if self.replies else '{"action":"final","answer":"(end)"}'
        return type("R", (), {"content": text})()


def test_plot_market_is_core_always_on():
    assert "plot_market" in CORE
    assert "plot_market" in kernel_capability_names([])


def test_plot_market_routes_and_captures_spec():
    def fn(query):
        return {"type": "line", "query": query, "title": "Will Norway win?",
                "series": [{"label": "Norway", "points": [["2026-06-01T00:00:00", 0.02],
                                                          ["2026-06-15T00:00:00", 0.03]]}]}

    llm = FakeLLM('{"action":"call","capability":"plot_market"}',
                  '{"action":"final","answer":"图已生成"}')
    res = KernelController([plot_market_capability(fn)], llm).run("把挪威夺冠的价格走势画出来")
    assert [s.capability for s in res.trace] == ["plot_market"]
    assert res.facts["chart"]["type"] == "line"


def test_svg_renderer_wellformed_for_every_type():
    specs = [
        {"type": "line", "title": "T", "series": [{"label": "A",
            "points": [["2026-06-01", 0.02], ["2026-06-10", 0.05], ["2026-06-20", 0.03]]}]},
        {"type": "area", "title": "T", "series": [{"label": "A",
            "points": [["2026-06-01", 0.6], ["2026-06-10", 0.4]]}]},
        {"type": "multi", "title": "T", "series": [
            {"label": "A", "points": [["2026-06-01", 0.2], ["2026-06-10", 0.3]]},
            {"label": "B", "points": [["2026-06-01", 0.5], ["2026-06-10", 0.4]]}]},
        {"type": "bar", "title": "T", "bars": [{"label": "A", "value": 0.2},
                                               {"label": "B", "value": 0.5}]},
    ]
    for spec in specs:
        svg = server._render_svg_chart(spec)
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")
        minidom.parseString(svg)                    # raises if not well-formed


def test_svg_renderer_handles_empty_gracefully():
    svg = server._render_svg_chart({"type": "line", "series": [], "error": "无价格历史"})
    minidom.parseString(svg)
    assert "无价格历史" in svg
