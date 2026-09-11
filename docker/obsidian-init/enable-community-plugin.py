"""Enable the pinned community plugin through local Electron DevTools."""

from __future__ import annotations

import argparse
import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any

import websockets

DEVTOOLS_URL = "http://127.0.0.1:9222/json/list"


def _page_websocket_url() -> str | None:
    try:
        with urllib.request.urlopen(DEVTOOLS_URL, timeout=2) as response:
            pages: list[dict[str, Any]] = json.load(response)
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return None
    for page in pages:
        if page.get("type") == "page" and page.get("url") == "app://obsidian.md/index.html":
            value = page.get("webSocketDebuggerUrl")
            if isinstance(value, str):
                return value
    return None


async def _evaluate(websocket_url: str, expression: str) -> Any:
    async with websockets.connect(websocket_url, open_timeout=5, close_timeout=2) as socket:
        await socket.send(
            json.dumps(
                {
                    "id": 1,
                    "method": "Runtime.evaluate",
                    "params": {
                        "expression": expression,
                        "returnByValue": True,
                        "awaitPromise": True,
                    },
                }
            )
        )
        while True:
            message = json.loads(await asyncio.wait_for(socket.recv(), timeout=10))
            if message.get("id") != 1:
                continue
            result = message.get("result", {}).get("result", {})
            if result.get("subtype") == "error":
                raise RuntimeError("Electron rejected the plugin activation expression")
            return result.get("value")


async def enable_plugin(plugin_id: str, timeout_seconds: int) -> None:
    deadline = time.monotonic() + timeout_seconds
    websocket_url: str | None = None
    while time.monotonic() < deadline:
        websocket_url = _page_websocket_url()
        if websocket_url is not None:
            break
        await asyncio.sleep(1)
    if websocket_url is None:
        raise TimeoutError("Obsidian Electron DevTools endpoint did not become ready")

    quoted_plugin_id = json.dumps(plugin_id)
    is_loaded = await _evaluate(
        websocket_url,
        f"Boolean(app?.plugins?.plugins?.[{quoted_plugin_id}])",
    )
    if is_loaded:
        return

    activated = await _evaluate(
        websocket_url,
        (
            "(() => {"
            "if (!globalThis.app?.appId) return false;"
            "localStorage.setItem('enable-plugin-' + app.appId, 'true');"
            "location.reload();"
            "return true;"
            "})()"
        ),
    )
    if activated is not True:
        raise RuntimeError("Obsidian vault was not ready for plugin activation")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--timeout", type=int, default=120)
    arguments = parser.parse_args()
    asyncio.run(enable_plugin(arguments.plugin_id, arguments.timeout))


if __name__ == "__main__":
    main()
