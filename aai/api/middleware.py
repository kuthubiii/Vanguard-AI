"""Request-size guard for multipart video uploads."""

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import JSONResponse

ASGIReceive = Callable[[], Awaitable[dict[str, Any]]]
ASGISend = Callable[[dict[str, Any]], Awaitable[None]]


class _RequestBodyTooLarge(Exception):
    pass


class UploadRequestSizeLimit:
    """Limit multipart request bodies before Starlette spools them to disk."""

    def __init__(self, app: Any, *, max_request_bytes: int, route_path: str = "/api/videos") -> None:
        self.app = app
        self.max_request_bytes = max_request_bytes
        self.route_path = route_path

    async def __call__(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        if scope.get("type") != "http" or scope.get("method") != "POST" or scope.get("path") != self.route_path:
            await self.app(scope, receive, send)
            return

        content_length = next(
            (value for name, value in scope.get("headers", []) if name.lower() == b"content-length"),
            None,
        )
        if content_length is not None:
            try:
                if int(content_length) > self.max_request_bytes:
                    await self._reject(scope, receive, send)
                    return
            except ValueError:
                pass

        total_bytes = 0

        async def limited_receive() -> dict[str, Any]:
            nonlocal total_bytes
            message = await receive()
            if message.get("type") == "http.request":
                total_bytes += len(message.get("body", b""))
                if total_bytes > self.max_request_bytes:
                    raise _RequestBodyTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except _RequestBodyTooLarge:
            await self._reject(scope, receive, send)

    async def _reject(self, scope: dict[str, Any], receive: ASGIReceive, send: ASGISend) -> None:
        response = JSONResponse(
            status_code=413,
            content={"detail": {"code": "request_too_large", "message": "The maximum video upload size is 2 GB."}},
        )
        await response(scope, receive, send)
