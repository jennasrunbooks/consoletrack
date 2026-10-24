from __future__ import annotations

import pytest

from demo_app.config import Config, load


def test_defaults() -> None:
    assert load({}) == Config()


def test_reads_every_setting() -> None:
    cfg = load(
        {
            "DEMO_MODE": "loadgen",
            "PORT": "9090",
            "DEMO_REPORT_MI": "4",
            "DEMO_REPORT_CACHE_MI": "64",
            "DEMO_BUCKET": "demo-app-data-use1-test",
            "DEMO_OBJECT_KEY": "other.txt",
            "AWS_REGION": "us-east-1",
            "LOADGEN_TARGET": "http://demo-app.demo:8080/",
            "LOADGEN_RPS": "2.5",
            "LOADGEN_TIMEOUT_SECONDS": "0.5",
            "LOADGEN_REPORT": "large",
        }
    )
    assert cfg == Config(
        mode="loadgen",
        port=9090,
        report_mi=4,
        report_cache_mi=64,
        bucket="demo-app-data-use1-test",
        object_key="other.txt",
        region="us-east-1",
        target="http://demo-app.demo:8080/",
        rps=2.5,
        timeout_seconds=0.5,
        report="large",
    )


def test_empty_values_mean_defaults() -> None:
    # The chart always sets DEMO_BUCKET and DEMO_OBJECT_KEY, empty on kind.
    assert load({"DEMO_BUCKET": "", "DEMO_OBJECT_KEY": "", "LOADGEN_REPORT": ""}) == Config()


@pytest.mark.parametrize(
    ("env", "message"),
    [
        ({"DEMO_MODE": "worker"}, "DEMO_MODE"),
        ({"PORT": "http"}, "PORT must be a int"),
        ({"DEMO_REPORT_MI": "0"}, "DEMO_REPORT_MI must be at least 1"),
        ({"DEMO_REPORT_CACHE_MI": "-1"}, "DEMO_REPORT_CACHE_MI must be at least 0"),
        ({"LOADGEN_REPORT": "huge"}, "LOADGEN_REPORT must be empty or large"),
        ({"LOADGEN_RPS": "0"}, "LOADGEN_RPS"),
        ({"LOADGEN_TIMEOUT_SECONDS": "soon"}, "LOADGEN_TIMEOUT_SECONDS"),
    ],
)
def test_rejects_bad_values(env: dict[str, str], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load(env)
