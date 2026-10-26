"""Keep tests independent of the current command-chair seat environment."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_musubi_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from musubi_codex.runtime import runtime

    for field in (
        "_project",
        "_bound_context",
        "_context_error",
        "_mcp_bound_root",
        "_mcp_bound_identity",
    ):
        monkeypatch.setattr(runtime, field, None)
    monkeypatch.setattr(runtime, "_mcp_locator_mode", False)
    for name in (
        "MUSUBI_ACTOR",
        "MUSUBI_PRESENCE",
        "MUSUBI_ZONE",
        "MUSUBI_DELIVERY_MODE",
        "MUSUBI_API_URL",
        "MUSUBI_TOKEN",
        "MUSUBI_PROMPT_RECALL",
        "MUSUBI_HARNESS_BIN",
        "MUSUBI_MEMORY_DATA_BIN",
        "MUSUBI_CONFIG_FILE",
    ):
        monkeypatch.delenv(name, raising=False)
