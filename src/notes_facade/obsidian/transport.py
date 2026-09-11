"""HTTP transport client for Obsidian Local REST API."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)
_LOG_BODY_MAX_LENGTH = 500


class ObsidianTransportError(Exception):
    """Raised when a transport-level HTTP call cannot be completed."""


@dataclass(frozen=True)
class ObsidianHttpResponse:
    """Transport-level HTTP response envelope."""

    status_code: int
    text: str
    json_body: dict[str, Any] | list[Any] | None


class ObsidianHttpTransportClient:
    """Thin transport client that sends HTTP requests and logs packets."""

    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 20.0,
        write_timeout_seconds: float = 20.0,
        pool_timeout_seconds: float = 5.0,
    ) -> None:
        timeout = httpx.Timeout(
            connect=connect_timeout_seconds,
            read=read_timeout_seconds,
            write=write_timeout_seconds,
            pool=pool_timeout_seconds,
        )
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {bearer_token}",
            },
        )

    async def close(self) -> None:
        """Close underlying HTTP resources."""
        await self._client.aclose()

    async def get(
        self,
        endpoint_path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        """Send an HTTP GET request."""
        return await self._request(
            method="GET",
            endpoint_path=endpoint_path,
            params=params,
            headers=headers,
        )

    async def put(
        self,
        endpoint_path: str,
        *,
        json_body: Any | None = None,
        raw_body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        """Send an HTTP PUT request."""
        return await self._request(
            method="PUT",
            endpoint_path=endpoint_path,
            json_body=json_body,
            raw_body=raw_body,
            headers=headers,
        )

    async def patch(
        self,
        endpoint_path: str,
        *,
        json_body: Any | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        """Send an HTTP PATCH request."""
        return await self._request(
            method="PATCH",
            endpoint_path=endpoint_path,
            json_body=json_body,
            headers=headers,
        )

    async def delete(
        self,
        endpoint_path: str,
        *,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        """Send an HTTP DELETE request."""
        return await self._request(method="DELETE", endpoint_path=endpoint_path, headers=headers)

    async def post(
        self,
        endpoint_path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        raw_body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        """Send an HTTP POST request."""
        return await self._request(
            method="POST",
            endpoint_path=endpoint_path,
            params=params,
            json_body=json_body,
            raw_body=raw_body,
            headers=headers,
        )

    async def _request(
        self,
        method: str,
        endpoint_path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json_body: Any | None = None,
        raw_body: str | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        LOGGER.info(
            (
                "Outgoing Obsidian HTTP packet: method=%s path=%s params=%s headers=%s "
                "json=%s content=%s"
            ),
            method,
            endpoint_path,
            params,
            headers,
            json_body,
            self._truncate_text(raw_body or ""),
        )
        try:
            response = await self._client.request(
                method=method,
                url=endpoint_path,
                params=params,
                json=json_body,
                content=raw_body,
                headers=headers,
            )
        except httpx.HTTPError as error:
            raise ObsidianTransportError("Failed to execute Obsidian HTTP request") from error

        LOGGER.info(
            "Incoming Obsidian HTTP packet: method=%s path=%s status=%s body=%s",
            method,
            endpoint_path,
            response.status_code,
            self._truncate_text(response.text),
        )
        return ObsidianHttpResponse(
            status_code=response.status_code,
            text=response.text,
            json_body=self._extract_json_body(response=response),
        )

    def _extract_json_body(self, response: httpx.Response) -> dict[str, Any] | list[Any] | None:
        try:
            parsed_json = response.json()
        except ValueError:
            return None
        if isinstance(parsed_json, (dict, list)):
            return parsed_json
        return None

    def _truncate_text(self, text: str) -> str:
        if len(text) <= _LOG_BODY_MAX_LENGTH:
            return text
        return text[:_LOG_BODY_MAX_LENGTH] + "..."
