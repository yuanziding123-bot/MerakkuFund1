"""Kernel core + selectable vertical packs — the mentor's on-demand-load model."""
from __future__ import annotations

from polyagents.kernel.packs import CORE, PACKS, kernel_capability_names, pack_capabilities


def test_core_is_always_on_and_packs_are_extra():
    assert "analyze_market" in CORE and "find_crypto_arb" not in CORE   # arb is a pack
    assert "find_crypto_arb" in PACKS["crypto-arb"]["capabilities"]


def test_none_loads_all_packs_backward_compatible():
    names = kernel_capability_names(None)
    assert "find_crypto_arb" in names and "backtest_strategies" in names   # every pack loaded
    assert set(CORE) <= set(names)


def test_empty_selection_is_core_only():
    names = kernel_capability_names([])
    assert set(names) == set(CORE)
    assert "find_crypto_arb" not in names


def test_selecting_a_pack_loads_just_it():
    names = kernel_capability_names(["crypto-arb"])
    assert "find_crypto_arb" in names
    assert "backtest_strategies" not in names             # other packs not loaded
    assert set(CORE) <= set(names)                        # core always present


def test_pack_capabilities_unknown_pack_ignored():
    assert pack_capabilities(["nope"]) == []
