"""Keep tests independent of the current command-chair seat environment."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def clean_musubi_environment(monkeypatch: pytest.MonkeyPatch) -> None:
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
    ):
        monkeypatch.delenv(name, raising=False)
