import pytest

from robert import main
from robert.memory import Memory


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    """Every test gets its own memory directory and no seed, never the real ~/robert-memory."""
    monkeypatch.setattr(main, "Memory", lambda root, enabled=True, seed=None:
                        Memory(tmp_path / "memory", enabled, seed=None))
