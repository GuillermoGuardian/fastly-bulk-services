from __future__ import annotations

import logging
import time
from typing import Callable
from urllib.parse import quote

import requests

BASE_URL = "https://api.fastly.com"
log = logging.getLogger(__name__)


class FastlyError(Exception):
    def __init__(self, status: int, body: str, method: str, path: str):
        self.status, self.body, self.method, self.path = status, body, method, path
        super().__init__(f"{method} {path} -> HTTP {status}: {body[:500]}")


class TokenMaskingFilter(logging.Filter):
    def __init__(self, token: str):
        super().__init__()
        self._token = token

    def filter(self, record: logging.LogRecord) -> bool:
        if self._token:
            record.msg = str(record.getMessage()).replace(self._token, "***")
            record.args = ()
        return True


class FastlyClient:
    def __init__(
        self,
        token: str,
        session: requests.Session | None = None,
        max_retries: int = 5,
        backoff_base: float = 1.0,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], float] = time.time,
    ):
        if not token:
            raise ValueError("Fastly API token is empty")
        self._token = token
        self._session = session or requests.Session()
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._sleep = sleep
        self._now = now
        log.addFilter(TokenMaskingFilter(token))

    def __repr__(self) -> str:
        return "FastlyClient(token=***)"

    def _delay(self, resp: requests.Response, attempt: int) -> float:
        reset = resp.headers.get("Fastly-RateLimit-Reset")
        if resp.status_code == 429 and reset and reset.isdigit():
            return max(0.0, min(float(reset) - self._now(), 60.0))
        return self._backoff_base * (2 ** attempt)

    def request(self, method: str, path: str, data: dict[str, str] | None = None) -> requests.Response:
        headers = {"Fastly-Key": self._token, "Accept": "application/json"}
        for attempt in range(self._max_retries + 1):
            resp = self._session.request(method, BASE_URL + path, headers=headers, data=data, timeout=30)
            retryable = resp.status_code == 429 or resp.status_code >= 500
            if retryable and attempt < self._max_retries:
                delay = self._delay(resp, attempt)
                log.warning("%s %s -> %s, retry %d in %.1fs", method, path, resp.status_code, attempt + 1, delay)
                self._sleep(delay)
                continue
            return resp
        raise AssertionError("unreachable")

    def _check(self, resp: requests.Response, method: str, path: str) -> requests.Response:
        if resp.status_code >= 400:
            raise FastlyError(resp.status_code, resp.text, method, path)
        return resp

    def find_service_id(self, name: str) -> str | None:
        path = f"/service/search?name={quote(name, safe='')}"
        resp = self.request("GET", path)
        if resp.status_code == 404:
            return None
        return self._check(resp, "GET", path).json().get("id")

    def post(self, path: str, data: dict[str, str]) -> dict:
        return self._check(self.request("POST", path, data), "POST", path).json()

    def get(self, path: str) -> requests.Response:
        return self.request("GET", path)

    def put(self, path: str, data: dict[str, str] | None = None) -> dict:
        return self._check(self.request("PUT", path, data), "PUT", path).json()

    def delete(self, path: str) -> requests.Response:
        return self.request("DELETE", path)
