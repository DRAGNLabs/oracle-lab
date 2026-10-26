import io
import json
import urllib.error

import pytest

import oracle_lab.harnesses.spend as spend
from oracle_lab.config import HarnessConfig
from oracle_lab.io import read_json

KEY = "credential-fixture"


def openrouter(harness="codex"):
    return HarnessConfig.model_validate(
        {"model": "model", "harness": harness, "provider": "openrouter"}
    )


def fake_clock():
    """A clock that moves one poll interval per sleep."""
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    return (lambda: now[0]), sleep


def readings(monkeypatch, values):
    values = iter(values)

    def next_reading(key):
        value = next(values)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(spend, "key_spend", next_reading)


def test_key_spend_reads_usage_and_redacts_the_key_from_errors(monkeypatch):
    def urlopen(request, timeout):
        assert request.get_header("Authorization") == f"Bearer {KEY}"
        return io.BytesIO(json.dumps({"data": {"usage": 9.25}}).encode())

    monkeypatch.setattr(spend.urllib.request, "urlopen", urlopen)
    assert spend.key_spend(KEY) == 9.25

    def failing(request, timeout):
        raise urllib.error.URLError(f"refused for {KEY}")

    monkeypatch.setattr(spend.urllib.request, "urlopen", failing)
    with pytest.raises(spend.SpendUnavailable) as error:
        spend.key_spend(KEY)
    assert KEY not in str(error.value)


def test_settle_waits_for_steady_readings_above_the_start(monkeypatch):
    # The total lags, then briefly flips back to the old value before settling.
    readings(monkeypatch, [1.0] * 10 + [1.5, 1.0, 1.5, 1.5, 1.5])
    clock, sleep = fake_clock()

    cost = spend.settle(KEY, 1.0, clock, sleep)

    assert cost.status == "measured"
    assert cost.cost_usd == 0.5
    assert cost.waited_seconds == 75


def test_settle_gives_up_as_unsettled(monkeypatch):
    readings(monkeypatch, [1.0] * 100)
    clock, sleep = fake_clock()

    cost = spend.settle(KEY, 1.0, clock, sleep)

    assert cost.status == "unsettled"
    assert cost.cost_usd == 0
    assert cost.waited_seconds == spend.MAX_WAIT_SECONDS


def test_settle_skips_failed_readings_and_reports_when_all_fail(monkeypatch):
    readings(monkeypatch, [spend.SpendUnavailable("HTTP Error 503")] * 100)
    clock, sleep = fake_clock()

    cost = spend.settle(KEY, 1.0, clock, sleep)

    assert cost.status == "unavailable"
    assert cost.error == "HTTP Error 503"


@pytest.fixture
def instant(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", KEY)
    monkeypatch.setattr(spend, "POLL_SECONDS", 0)
    monkeypatch.setattr(spend, "MIN_WAIT_SECONDS", 0)


def test_measure_spend_saves_cost_even_when_the_block_fails(tmp_path, monkeypatch, instant):
    readings(monkeypatch, [2.0, 2.25, 2.25, 2.25])
    waits = []

    with (
        pytest.raises(RuntimeError),
        spend.measure_spend(
            [openrouter(), openrouter("claude")], tmp_path / "cost.json", lambda: waits.append(1)
        ),
    ):
        raise RuntimeError("evaluation failed")

    assert read_json(tmp_path / "cost.json") | {"waited_seconds": None} == {
        "status": "measured",
        "cost_usd": 0.25,
        "before": 2.0,
        "after": 2.25,
        "waited_seconds": None,
        "error": None,
    }
    assert waits == [1]


def test_measure_spend_skips_an_interrupted_block(tmp_path, monkeypatch, instant):
    readings(monkeypatch, [2.0])

    with (
        pytest.raises(KeyboardInterrupt),
        spend.measure_spend([openrouter()], tmp_path / "cost.json"),
    ):
        raise KeyboardInterrupt

    assert not (tmp_path / "cost.json").exists()


def test_measure_spend_needs_every_stage_on_openrouter(tmp_path, monkeypatch, instant):
    readings(monkeypatch, [])
    direct = HarnessConfig(model="model", harness="claude", provider="anthropic")

    with spend.measure_spend([openrouter(), direct], tmp_path / "cost.json"):
        pass

    assert read_json(tmp_path / "cost.json")["status"] == "not_measured"


def test_measure_spend_records_a_failed_first_reading(tmp_path, monkeypatch, instant):
    readings(monkeypatch, [spend.SpendUnavailable("HTTP Error 401: Unauthorized")])
    ran = []

    with spend.measure_spend([openrouter()], tmp_path / "cost.json"):
        ran.append(1)

    assert ran == [1]
    assert read_json(tmp_path / "cost.json")["error"] == "HTTP Error 401: Unauthorized"
