"""Ask module — backend unit tests (model selector). UI behaviours are covered
by the acceptance cases in docs/product/ask-module-tests.md."""
from __future__ import annotations

from polyagents.default_config import DEFAULT_CONFIG
from polyagents.web.agent import ASK_MODELS, WRITE_TOOLS, build_tools, resolve_model


def test_ask_is_readonly_no_write_tools():
    names = {t.name for t in build_tools(readonly=True)}
    assert not (names & WRITE_TOOLS), f"Ask must not bind write tools: {names & WRITE_TOOLS}"
    # the read-only surface still has the things Ask needs
    for read in ("scan_markets", "market_snapshot", "evaluation_report", "find_similar_markets"):
        assert read in names


def test_write_tools_present_only_in_full_surface():
    full = {t.name for t in build_tools(readonly=False)}
    assert WRITE_TOOLS <= full                       # full host surface keeps them
    assert "paper_execute" in full and "size_position" in full


def test_propose_hypothesis_is_readonly_and_available_in_ask():
    from polyagents.web.agent import propose_hypothesis

    names = {t.name for t in build_tools(readonly=True)}
    assert "propose_hypothesis" in names             # Ask can surface ideas
    assert "propose_hypothesis" not in WRITE_TOOLS    # but it only proposes
    out = propose_hypothesis("crypto news beats market", category="crypto",
                             feature_set="news_event")
    assert isinstance(out, str) and "crypto news beats market" in out


def test_known_model_is_passed_through():
    assert resolve_model("claude-opus-4-8") == "claude-opus-4-8"
    assert resolve_model("claude-haiku-4-5-20251001") == "claude-haiku-4-5-20251001"


def test_unknown_or_missing_model_falls_back_to_default():
    default = DEFAULT_CONFIG["anthropic_model"]
    assert resolve_model("totally-made-up") == default
    assert resolve_model("") == default
    assert resolve_model(None) == default


def test_ask_models_are_well_formed():
    assert ASK_MODELS, "the composer must offer at least one model"
    for key, mid in ASK_MODELS.items():
        assert mid.startswith("claude-")          # real Anthropic ids only
        assert resolve_model(key) == mid           # every offered option resolves


def test_chat_endpoint_accepts_a_model_field():
    # the route exists and the handler reads body.model (no LLM call here)
    import inspect

    from polyagents.web import server
    src = inspect.getsource(server.chat)
    assert 'body.get("model")' in src
