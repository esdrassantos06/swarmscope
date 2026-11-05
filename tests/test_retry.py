from contextlib import contextmanager

import pytest

from retry import fetch_with_retry, parse_paths, retry_delay, should_retry


class FakeResponse:
    def __init__(self, status_code, headers=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.failed_with = None

    def failure(self, message):
        self.failed_with = message


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.handed_out = []

    @contextmanager
    def get(self, path, catch_response, name):
        assert catch_response is True
        self.calls.append((path, name))
        response = self.responses.pop(0)
        self.handed_out.append(response)
        yield response


def make_sleep():
    waits = []
    return waits, waits.append


@pytest.mark.parametrize(
    "status,expected",
    [
        (200, False),
        (301, False),
        (404, False),
        (429, True),
        (500, True),
        (503, True),
        (0, True),
    ],
)
def test_should_retry_only_rate_limits_server_errors_and_network_failures(
    status, expected
):
    assert should_retry(status) is expected


def test_retry_delay_grows_exponentially_with_jitter():
    assert retry_delay(0, None, base=1, rand=lambda: 0.0) == 0.5
    assert retry_delay(0, None, base=1, rand=lambda: 1.0) == 1.5
    assert retry_delay(2, None, base=1, rand=lambda: 0.5) == 4.0


def test_retry_delay_honors_retry_after_seconds():
    assert retry_delay(0, "7", base=1, rand=lambda: 0.5) == 7.0


def test_retry_delay_caps_long_waits():
    assert retry_delay(0, "3600", base=1, rand=lambda: 0.5) == 30.0
    assert retry_delay(20, None, base=1, rand=lambda: 0.5) == 30.0


def test_retry_delay_falls_back_to_backoff_on_http_date_retry_after():
    assert (
        retry_delay(1, "Wed, 21 Oct 2026 07:28:00 GMT", base=1, rand=lambda: 0.5) == 2.0
    )


def test_parse_paths_splits_commas_and_newlines_and_adds_leading_slash():
    assert parse_paths(" /, docs ,\n/docs/installation\n\n") == [
        "/",
        "/docs",
        "/docs/installation",
    ]


def test_parse_paths_defaults_to_root():
    assert parse_paths("  ") == ["/"]


def test_fetch_returns_first_success_without_retrying():
    client = FakeClient([FakeResponse(200)])
    waits, sleep = make_sleep()

    assert fetch_with_retry(client, "/docs", max_retries=3, base=1, sleep=sleep) == 200
    assert client.calls == [("/docs", "/docs")]
    assert waits == []


def test_fetch_retries_rate_limit_then_succeeds():
    client = FakeClient(
        [FakeResponse(429, {"retry-after": "2"}), FakeResponse(429), FakeResponse(200)]
    )
    waits, sleep = make_sleep()

    assert fetch_with_retry(client, "/", max_retries=3, base=1, sleep=sleep) == 200
    assert len(client.calls) == 3
    assert waits[0] == 2.0
    assert client.handed_out[0].failed_with == "HTTP 429, retry 1/3"
    assert client.handed_out[2].failed_with is None


def test_fetch_gives_up_after_max_retries():
    client = FakeClient([FakeResponse(503) for _ in range(3)])
    waits, sleep = make_sleep()

    assert fetch_with_retry(client, "/", max_retries=2, base=1, sleep=sleep) == 503
    assert len(client.calls) == 3
    assert len(waits) == 2
    assert client.handed_out[-1].failed_with is None


def test_fetch_treats_negative_max_retries_as_zero():
    client = FakeClient([FakeResponse(503), FakeResponse(200)])
    _, sleep = make_sleep()

    assert fetch_with_retry(client, "/", max_retries=-1, base=1, sleep=sleep) == 503
    assert len(client.calls) == 1


def test_fetch_does_not_retry_client_errors():
    client = FakeClient([FakeResponse(404)])
    waits, sleep = make_sleep()

    assert (
        fetch_with_retry(client, "/missing", max_retries=3, base=1, sleep=sleep) == 404
    )
    assert len(client.calls) == 1
    assert waits == []
