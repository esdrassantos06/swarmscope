import random
from collections.abc import Callable

MAX_WAIT_S = 30.0


def should_retry(status: int) -> bool:
    """Retry rate limits (429), server errors (5xx) and network failures (Locust reports these as 0)."""
    return status == 0 or status == 429 or status >= 500


def retry_delay(
    attempt: int,
    retry_after: str | None,
    base: float,
    rand: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before retry number `attempt + 1`.

    Uses the server's numeric Retry-After when present, otherwise exponential backoff
    with ±50% jitter so users do not retry in lockstep. Never exceeds MAX_WAIT_S.
    """
    try:
        return min(max(float(retry_after), 0.0), MAX_WAIT_S)
    except (TypeError, ValueError):
        return min(base * 2**attempt * (0.5 + rand()), MAX_WAIT_S)


def parse_paths(text: str) -> list[str]:
    """Split a comma or newline separated list of paths, defaulting to the site root."""
    parts = [p.strip() for p in text.replace("\n", ",").split(",")]
    return ["/" + p.lstrip("/") for p in parts if p] or ["/"]


def fetch_with_retry(
    client, path: str, max_retries: int, base: float, sleep: Callable[[float], None]
) -> int:
    """GET `path` through a Locust client, retrying retryable responses.

    Each retried attempt is reported to Locust as a failure tagged with the retry count.
    The final attempt keeps Locust's default pass/fail rule. Returns the final status code.
    """
    max_retries = max(max_retries, 0)
    attempt = 0
    while True:
        with client.get(path, catch_response=True, name=path) as response:
            status = response.status_code
            if not should_retry(status) or attempt == max_retries:
                return status
            attempt += 1
            response.failure(f"HTTP {status}, retry {attempt}/{max_retries}")
            delay = retry_delay(
                attempt - 1, (response.headers or {}).get("retry-after"), base
            )
        sleep(delay)
