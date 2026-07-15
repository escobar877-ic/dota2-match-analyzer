from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class DataClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class ClientResponse:
    ok: bool
    data: Any | None = None
    error: str | None = None


class BaseDotaDataClient(ABC):
    source_name: str
    enabled: bool = True

    @abstractmethod
    def get_teams(self) -> ClientResponse:
        raise NotImplementedError

    @abstractmethod
    def get_team(self, external_id: str) -> ClientResponse:
        raise NotImplementedError

    @abstractmethod
    def get_matches(self) -> ClientResponse:
        raise NotImplementedError

    @abstractmethod
    def get_match(self, external_id: str) -> ClientResponse:
        raise NotImplementedError

    @abstractmethod
    def get_recent_matches_for_team(self, team_id: str) -> ClientResponse:
        raise NotImplementedError

    @abstractmethod
    def get_upcoming_matches(self) -> ClientResponse:
        raise NotImplementedError


class JsonHttpClient:
    def __init__(self, base_url: str, timeout_seconds: int = 20) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
    ) -> ClientResponse:
        return self.request("GET", path, headers=headers, query=query)

    def post(
        self,
        path: str,
        body: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> ClientResponse:
        return self.request("POST", path, headers=headers, body=body)

    def request(
        self,
        method: str,
        path: str,
        headers: dict[str, str] | None = None,
        query: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> ClientResponse:
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urlencode(query)}"

        request_headers = {
            "Accept": "application/json",
            "User-Agent": "dota-analyzer-local/0.1",
        }
        if headers:
            request_headers.update(headers)

        payload = None
        if body is not None:
            payload = json.dumps(body).encode("utf-8")
            request_headers["Content-Type"] = "application/json"

        request = Request(url, data=payload, headers=request_headers, method=method)

        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode("utf-8")
                return ClientResponse(ok=True, data=json.loads(raw) if raw else None)
        except HTTPError as exc:
            return ClientResponse(
                ok=False,
                error=f"{method} {url} failed with HTTP {exc.code}",
            )
        except URLError as exc:
            return ClientResponse(ok=False, error=f"{method} {url} failed: {exc.reason}")
        except TimeoutError:
            return ClientResponse(ok=False, error=f"{method} {url} timed out")
        except json.JSONDecodeError as exc:
            return ClientResponse(ok=False, error=f"{method} {url} returned invalid JSON: {exc}")
