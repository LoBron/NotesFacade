"""Integration client for Obsidian Local REST API."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from notes_facade.obsidian.errors import (
    ObsidianAuthError,
    ObsidianClientError,
    ObsidianNotFoundError,
    ObsidianUnavailableError,
)
from notes_facade.obsidian.models import (
    ObsidianStatusResponse,
    SearchJsonLogicRequest,
    SearchResponse,
    SearchResultItem,
    SearchSimpleRequest,
    VaultMutationResponse,
    VaultNoteResponse,
    VaultPatchRequest,
    VaultPathRequest,
    VaultPutRequest,
)
from notes_facade.obsidian.transport import (
    ObsidianHttpResponse,
    ObsidianHttpTransportClient,
    ObsidianTransportError,
)

CONTENT_TYPE_JSON = "application/json"
CONTENT_TYPE_JSONLOGIC = "application/vnd.olrapi.jsonlogic+json"
CONTENT_TYPE_MARKDOWN = "text/markdown; charset=utf-8"


class ObsidianRestApiHttpClient:
    """Orchestrates Obsidian REST API calls and maps failures to client-specific errors."""

    def __init__(
        self,
        api_url: str,
        api_key: str,
        connect_timeout_seconds: float = 5.0,
        read_timeout_seconds: float = 20.0,
        write_timeout_seconds: float = 20.0,
        pool_timeout_seconds: float = 5.0,
        transport: ObsidianHttpTransportClient | None = None,
    ) -> None:
        self._transport = transport or ObsidianHttpTransportClient(
            base_url=api_url,
            bearer_token=api_key,
            connect_timeout_seconds=connect_timeout_seconds,
            read_timeout_seconds=read_timeout_seconds,
            write_timeout_seconds=write_timeout_seconds,
            pool_timeout_seconds=pool_timeout_seconds,
        )

    async def close(self) -> None:
        """Close allocated transport resources."""
        await self._transport.close()

    async def get_note(self, request: VaultPathRequest) -> VaultNoteResponse:
        """Read a note by path via GET /vault/{path}."""
        endpoint_path = self._vault_endpoint(path=request.path)
        response = await self._safe_get(endpoint_path=endpoint_path)
        self._raise_for_status(response=response, operation="get_note")
        return VaultNoteResponse(path=request.path, content=response.text)

    async def put_note(self, request: VaultPutRequest) -> VaultMutationResponse:
        """Write note content via PUT /vault/{path}."""
        endpoint_path = self._vault_endpoint(path=request.path)
        response = await self._safe_put(
            endpoint_path=endpoint_path,
            content=request.content,
            headers={"Content-Type": CONTENT_TYPE_MARKDOWN},
        )
        self._raise_for_status(response=response, operation="put_note")
        return self._build_mutation_response(path=request.path, response=response)

    async def patch_note(self, request: VaultPatchRequest) -> VaultMutationResponse:
        """Patch a note via PATCH /vault/{path} with typed targetType payload."""
        endpoint_path = self._vault_endpoint(path=request.path)
        payload = request.body.model_dump(by_alias=True, exclude_none=True)
        response = await self._safe_patch(
            endpoint_path=endpoint_path,
            json_body=payload,
            headers={"Content-Type": CONTENT_TYPE_JSON},
        )
        self._raise_for_status(response=response, operation="patch_note")
        return self._build_mutation_response(path=request.path, response=response)

    async def delete_note(self, request: VaultPathRequest) -> VaultMutationResponse:
        """Delete a note via DELETE /vault/{path}."""
        endpoint_path = self._vault_endpoint(path=request.path)
        response = await self._safe_delete(endpoint_path=endpoint_path)
        self._raise_for_status(response=response, operation="delete_note")
        return self._build_mutation_response(path=request.path, response=response)

    async def search_simple(self, request: SearchSimpleRequest) -> SearchResponse:
        """Perform full-text search via POST /search/simple/."""
        params = request.model_dump(by_alias=True, exclude_none=True)
        response = await self._safe_post(endpoint_path="/search/simple/", params=params)
        self._raise_for_status(response=response, operation="search_simple")
        return self._build_search_response(response=response)

    async def search_jsonlogic(self, request: SearchJsonLogicRequest) -> SearchResponse:
        """Perform JsonLogic search via POST /search/."""
        response = await self._safe_post(
            endpoint_path="/search/",
            json_body=request.query,
            headers={"Content-Type": CONTENT_TYPE_JSONLOGIC},
        )
        self._raise_for_status(response=response, operation="search_jsonlogic")
        return self._build_search_response(response=response)

    async def status(self) -> ObsidianStatusResponse:
        """Get Obsidian API status from GET /."""
        response = await self._safe_get(endpoint_path="/")
        self._raise_for_status(response=response, operation="status")
        if isinstance(response.json_body, dict):
            body = response.json_body
        else:
            body = {}
        return ObsidianStatusResponse(status_code=response.status_code, body=body)

    async def _safe_get(self, endpoint_path: str) -> ObsidianHttpResponse:
        try:
            return await self._transport.get(endpoint_path=endpoint_path)
        except ObsidianTransportError as error:
            raise ObsidianUnavailableError("Obsidian API request failed during GET") from error

    async def _safe_put(
        self,
        endpoint_path: str,
        content: str,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        try:
            return await self._transport.put(
                endpoint_path=endpoint_path,
                raw_body=content,
                headers=headers,
            )
        except ObsidianTransportError as error:
            raise ObsidianUnavailableError("Obsidian API request failed during PUT") from error

    async def _safe_patch(
        self,
        endpoint_path: str,
        json_body: dict[str, Any],
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        try:
            return await self._transport.patch(
                endpoint_path=endpoint_path,
                json_body=json_body,
                headers=headers,
            )
        except ObsidianTransportError as error:
            raise ObsidianUnavailableError("Obsidian API request failed during PATCH") from error

    async def _safe_delete(self, endpoint_path: str) -> ObsidianHttpResponse:
        try:
            return await self._transport.delete(endpoint_path=endpoint_path)
        except ObsidianTransportError as error:
            raise ObsidianUnavailableError("Obsidian API request failed during DELETE") from error

    async def _safe_post(
        self,
        endpoint_path: str,
        params: dict[str, Any] | None = None,
        json_body: Any | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> ObsidianHttpResponse:
        try:
            return await self._transport.post(
                endpoint_path=endpoint_path,
                params=params,
                json_body=json_body,
                headers=headers,
            )
        except ObsidianTransportError as error:
            raise ObsidianUnavailableError("Obsidian API request failed during POST") from error

    def _raise_for_status(self, response: ObsidianHttpResponse, operation: str) -> None:
        status_code = response.status_code
        if status_code in {401, 403}:
            raise ObsidianAuthError("Obsidian API authentication failed")
        if status_code == 404:
            raise ObsidianNotFoundError("Obsidian API resource was not found")
        if status_code >= 500:
            raise ObsidianUnavailableError("Obsidian API is unavailable")
        if status_code >= 400:
            raise ObsidianClientError(
                f"Obsidian API returned unexpected status for {operation}: {status_code}"
            )

    def _vault_endpoint(self, path: str) -> str:
        normalized_path = path.lstrip("/")
        return "/vault/" + quote(normalized_path, safe="/")

    def _build_search_response(self, response: ObsidianHttpResponse) -> SearchResponse:
        raw_items = self._extract_search_items(response=response)
        parsed_items = [SearchResultItem.model_validate(item) for item in raw_items]
        return SearchResponse(results=parsed_items)

    def _extract_search_items(self, response: ObsidianHttpResponse) -> list[dict[str, Any]]:
        if isinstance(response.json_body, list):
            list_items = response.json_body
        elif isinstance(response.json_body, dict):
            result_items = response.json_body.get("results")
            list_items = result_items if isinstance(result_items, list) else []
        else:
            list_items = []

        normalized_items: list[dict[str, Any]] = []
        for item in list_items:
            if isinstance(item, dict):
                normalized_items.append(item)
        return normalized_items

    def _build_mutation_response(
        self,
        path: str,
        response: ObsidianHttpResponse,
    ) -> VaultMutationResponse:
        message = None
        if isinstance(response.json_body, dict):
            raw_message = response.json_body.get("message")
            if isinstance(raw_message, str):
                message = raw_message
        if message is None and response.text:
            message = response.text
        return VaultMutationResponse(path=path, status_code=response.status_code, message=message)
