"Settings for every test."

import pytest


@pytest.fixture(autouse=True)
def no_commands(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> None:
    "Run no do$ line unless a test asks, and keep each test's cache of runs to itself, out of the user's."
    monkeypatch.setenv("TIMEWALK_NO_RUN", "1")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path_factory.mktemp("cache")))
