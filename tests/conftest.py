import pytest

import oracle_lab.spend as spend


@pytest.fixture(autouse=True)
def no_spend_lookups(monkeypatch):
    """Local tests must not reach OpenRouter; tests that need readings fake them."""

    def blocked(request, timeout):
        raise AssertionError("A test tried to reach OpenRouter")

    monkeypatch.setattr(spend.urllib.request, "urlopen", blocked)
