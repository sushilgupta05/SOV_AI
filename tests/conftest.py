import pytest

from make_fixtures import build_all
from sov_agent import config


@pytest.fixture(scope="session")
def fx(tmp_path_factory):
    """Paths to every generated fixture, keyed by file name."""
    return build_all(tmp_path_factory.mktemp("fixtures"))


@pytest.fixture(autouse=True)
def no_network_llm(monkeypatch):
    """Tests never call a real LLM; agents fall back unless a mock is injected."""
    monkeypatch.setattr(config, "LLM_ENABLED", False)
