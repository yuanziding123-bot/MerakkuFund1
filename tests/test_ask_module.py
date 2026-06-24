"""Ask module — backend unit tests (model selector). UI behaviours are covered
by the acceptance cases in docs/product/ask-module-tests.md."""
from __future__ import annotations

from polyagents.default_config import DEFAULT_CONFIG
from polyagents.web.agent import ASK_MODELS, resolve_model


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
