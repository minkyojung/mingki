"""Spring /internal/** 호출. 응답은 고치지 않고 그대로 돌려준다 (업무 규칙은 Spring 한 곳에만)."""

import os

import httpx


class BackendError(Exception):
    """Agent에게 그대로 보여줄 수 있는 백엔드 오류 ({error, message} 응답을 한 줄로)."""


class Backend:
    def __init__(
        self,
        base_url: str | None = None,
        token: str | None = None,
        caller: str = "code-mcp",
        transport: httpx.BaseTransport | None = None,
    ):
        self.base_url = base_url or os.environ.get("BACKEND_BASE_URL", "http://localhost:8080")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={
                "X-Internal-Token": token or os.environ.get("INTERNAL_TOKEN", "rr-internal-dev-token"),
                "X-Internal-Caller": caller,
            },
            timeout=10.0,
            transport=transport,
        )

    def get(self, path: str, params: dict | None = None):
        return self._send("GET", path, params={k: v for k, v in (params or {}).items() if v is not None})

    def post(self, path: str, body: dict):
        return self._send("POST", path, json=body)

    def _send(self, method: str, path: str, **kwargs):
        try:
            res = self._client.request(method, path, **kwargs)
        except httpx.TransportError as e:
            raise BackendError(f"백엔드에 연결할 수 없습니다 ({self.base_url}): {e}") from e
        if res.is_error:
            try:
                body = res.json()
                detail = f"{body.get('error', res.reason_phrase)}: {body.get('message', '')}"
            except ValueError:
                detail = res.text[:200] or res.reason_phrase
            raise BackendError(f"백엔드 {res.status_code} {detail}")
        return res.json()
