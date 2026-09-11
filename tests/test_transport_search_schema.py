import json

import pytest
import respx
from httpx import Response

from notes_facade.obsidian.client import ObsidianRestApiHttpClient
from notes_facade.obsidian.models import SearchJsonLogicRequest, SearchSimpleRequest
from notes_facade.obsidian.transport import ObsidianHttpTransportClient


@pytest.mark.asyncio
async def test_search_simple_uses_query_params_and_parses_results():
    transport = ObsidianHttpTransportClient(
        base_url="http://obsidian.local",
        bearer_token="token",
    )
    client = ObsidianRestApiHttpClient(
        api_url="http://obsidian.local",
        api_key="token",
        transport=transport,
    )

    try:
        with respx.mock(assert_all_called=True) as mock_router:
            route = mock_router.post("http://obsidian.local/search/simple/").mock(
                return_value=Response(
                    status_code=200,
                    json=[
                        {
                            "filename": "/vault/personal/10 Notes/N.md",
                            "score": 0.92,
                            "matches": [
                                {
                                    "context": "prefix match suffix",
                                    "match": {
                                        "start": 7,
                                        "end": 12,
                                        "source": "content",
                                    },
                                }
                            ],
                        }
                    ],
                )
            )

            response = await client.search_simple(SearchSimpleRequest(query="match"))

            assert route.called
            assert route.calls.last.request.url.params["query"] == "match"
            item = response.results[0]
            assert item.filename == "/vault/personal/10 Notes/N.md"
            assert item.score == pytest.approx(0.92)
            assert item.matches[0].context == "prefix match suffix"
            assert item.matches[0].match is not None
            assert item.matches[0].match.start == 7
            assert item.matches[0].match.end == 12
            assert item.matches[0].match.source == "content"
    finally:
        await client.close()


@pytest.mark.asyncio
async def test_search_jsonlogic_sends_root_json_body_and_parses_result_items():
    transport = ObsidianHttpTransportClient(
        base_url="http://obsidian.local",
        bearer_token="token",
    )
    client = ObsidianRestApiHttpClient(
        api_url="http://obsidian.local",
        api_key="token",
        transport=transport,
    )

    expression = {"and": [{"==": [{"var": "frontmatter.status"}, "active"]}]}

    try:
        with respx.mock(assert_all_called=True) as mock_router:
            route = mock_router.post("http://obsidian.local/search/").mock(
                return_value=Response(
                    status_code=200,
                    json=[
                        {
                            "filename": "/vault/personal/10 Notes/N.md",
                            "result": {"frontmatter": {"status": "active"}},
                        }
                    ],
                )
            )

            response = await client.search_jsonlogic(SearchJsonLogicRequest(query=expression))

            assert route.called
            request = route.calls.last.request
            assert request.headers["Content-Type"] == "application/vnd.olrapi.jsonlogic+json"
            assert json.loads(request.content.decode("utf-8")) == expression
            assert response.results[0].filename == "/vault/personal/10 Notes/N.md"
            assert response.results[0].result == {"frontmatter": {"status": "active"}}
    finally:
        await client.close()
