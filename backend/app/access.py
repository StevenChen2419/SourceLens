"""Public API boundary, bounded anonymous usage, and approved corpus adapter.

No authentication is implemented. These controls apply only to the single-corpus demo.
"""

import asyncio
import json
import logging
import time
from collections import deque
from collections.abc import Callable
from typing import TypeVar

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.config import Settings
from app.integrations.search import SearchError, SearchStore
from app.models import RetrievedChunk

DEMO_FILENAME = "employee-handbook.pdf"
SUGGESTED_QUESTIONS = [
    "How many vacation days do full-time employees receive per calendar year?",
    "How many days per week may employees work remotely, and whose approval is required?",
    "What is the capital of Japan?",
]
PUBLIC_ROUTES = {("GET", "/health"), ("GET", "/api/config"), ("POST", "/api/answers")}
T = TypeVar("T")
logger = logging.getLogger(__name__)


class DemoQuestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    question: str = Field(strict=True, min_length=1, max_length=1000)

    @field_validator("question")
    @classmethod
    def nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Question is empty")
        return value.strip()


class DemoSearchStore(SearchStore):
    """Server-owned scope; verify metadata before supplying any passage to GPT."""
    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        if settings.demo_document_id is None:
            raise SearchError("Approved demo document is not configured")
        self.document_id = settings.demo_document_id
        self.top_k = settings.demo_top_k

    def hybrid_search(self, question: str, vector: list[float], top_k: int) -> list[RetrievedChunk]:
        rows = self.document_chunks(self.document_id)
        expected = {}
        for row in rows:
            if (row.get("document_id") != str(self.document_id) or row.get("filename") != DEMO_FILENAME
                    or type(row.get("page_number")) is not int or type(row.get("chunk_index")) is not int
                    or not isinstance(row.get("chunk_id"), str) or not row["chunk_id"]):
                raise SearchError("Approved demo corpus metadata is invalid")
            expected[row["chunk_id"]] = (row["page_number"], row["chunk_index"])
        if (len(rows) != 6 or len(expected) != 6
                or set(expected.values()) != {(page, page - 1) for page in range(1, 7)}):
            raise SearchError("Approved six-page demo corpus is missing or incomplete")
        results = super().hybrid_search(question, vector, self.top_k,
                                       document_id=self.document_id, filename=DEMO_FILENAME)
        for chunk in results:
            if (chunk.document_id != str(self.document_id) or chunk.filename != DEMO_FILENAME
                    or expected.get(chunk.chunk_id) != (chunk.page_number, chunk.chunk_index)):
                raise SearchError("Search returned evidence outside the approved demo corpus")
        return results


class DemoRuntime:
    """One event loop/process, rolling windows, bounded visitor table and worker slots.

    A timeout does not cancel Azure work: the slot stays occupied until the worker
    really finishes. This prevents repeated timeouts from creating unlimited work.
    """
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic) -> None:
        self.settings = settings
        self.clock = clock
        self.visitors: dict[str, deque[float]] = {}
        self.minute: deque[float] = deque()
        self.day: deque[float] = deque()
        self.active = 0
        self.workers: set[asyncio.Task] = set()

    def admit(self, peer: str) -> bool:
        now = self.clock()
        for queue, window in [(self.minute, 60), (self.day, 86400)]:
            while queue and queue[0] <= now - window:
                queue.popleft()
        for key in list(self.visitors):
            queue = self.visitors[key]
            while queue and queue[0] <= now - 60:
                queue.popleft()
            if not queue:
                del self.visitors[key]
        visitor = self.visitors.get(peer)
        if (len(self.minute) >= self.settings.demo_global_requests_per_minute
                or len(self.day) >= self.settings.demo_global_requests_per_day
                or (visitor is not None and len(visitor) >= self.settings.demo_requests_per_minute)
                or (visitor is None and len(self.visitors) >= 2000)):
            return False
        visitor = self.visitors.setdefault(peer, deque())
        visitor.append(now)
        self.minute.append(now)
        self.day.append(now)
        return True

    async def execute(self, operation: Callable[[], T]) -> T:
        if self.active >= self.settings.demo_max_concurrency:
            raise HTTPException(429, "The demo is busy. Please try again later.", headers={"Retry-After": "60"})
        self.active += 1
        task = asyncio.create_task(asyncio.to_thread(operation))
        self.workers.add(task)
        detached = False
        def finished(worker: asyncio.Task) -> None:
            self.active -= 1
            self.workers.discard(worker)
            # Observe late failures without logging document text or SDK bodies.
            if not worker.cancelled():
                error = worker.exception()
                if error is not None and detached:
                    logger.warning("A public demo worker failed after its request ended.")
        task.add_done_callback(finished)
        try:
            return await asyncio.wait_for(asyncio.shield(task), self.settings.demo_request_timeout_seconds)
        except TimeoutError as exc:
            detached = True
            raise HTTPException(504, "The demo timed out. Please try again later.") from exc
        except asyncio.CancelledError:
            detached = True
            raise


class PublicAccessMiddleware:
    """Exact method/path allowlist and bounded body before FastAPI parsing."""
    def __init__(self, app: ASGIApp, settings: Settings, runtime: DemoRuntime) -> None:
        self.app, self.settings, self.runtime = app, settings, runtime

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.settings.app_mode != "public_demo" or scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        method, path = scope["method"], scope["path"]
        allowed = (method, path) in PUBLIC_ROUTES
        if method == "OPTIONS":
            requested = dict(scope["headers"]).get(b"access-control-request-method", b"").decode("ascii", errors="ignore")
            allowed = (requested, path) in PUBLIC_ROUTES
        async def reject(code: int, detail: str) -> None:
            headers = {"Retry-After": "60"} if code == 429 else {}
            origin = dict(scope["headers"]).get(b"origin", b"").decode("utf-8", errors="ignore")
            if origin in self.settings.cors_origins:
                headers.update({"Access-Control-Allow-Origin": origin, "Vary": "Origin"})
            await JSONResponse({"detail": detail}, status_code=code, headers=headers)(scope, receive, send)
        if not allowed:
            await reject(403, "This operation is unavailable in the public demo.")
            return
        if scope.get("query_string"):
            await reject(422, "The public demo does not accept configuration or corpus selectors.")
            return
        if method != "POST":
            await self.app(scope, receive, send)
            return
        # Use the actual socket peer only. Uvicorn must run with --no-proxy-headers.
        peer = scope.get("client")
        if not self.runtime.admit(str(peer[0]) if peer else "unknown"):
            await reject(429, "Demo request limit reached. Please try again later.")
            return
        headers = dict(scope["headers"])
        if headers.get(b"content-encoding", b"identity").lower() != b"identity":
            await reject(415, "Compressed request bodies are not supported.")
            return
        if headers.get(b"content-type", b"").split(b";", 1)[0].lower() != b"application/json":
            await reject(415, "Send a JSON question.")
            return
        try:
            if int(headers.get(b"content-length", b"0")) > self.settings.demo_max_body_bytes:
                await reject(413, "Question request is too large.")
                return
        except ValueError:
            await reject(400, "Invalid request size.")
            return
        body = bytearray()
        async def read_body() -> None:
            while True:
                message = await receive()
                if message["type"] == "http.disconnect":
                    raise ConnectionError("Client disconnected")
                part = message.get("body", b"")
                if len(body) + len(part) > self.settings.demo_max_body_bytes:
                    raise OverflowError("Body too large")
                body.extend(part)
                if not message.get("more_body", False):
                    return
        try:
            await asyncio.wait_for(read_body(), self.settings.demo_body_timeout_seconds)
            # Reject duplicate JSON keys as well as unknown fields/selectors.
            def unique_keys(pairs: list[tuple[str, object]]) -> dict:
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError("Duplicate JSON key")
                    result[key] = value
                return result
            decoded = json.loads(body, object_pairs_hook=unique_keys)
            question = DemoQuestion.model_validate(decoded)
            if len(decoded["question"]) > self.settings.demo_max_question_chars:
                raise ValueError("Question too long")
        except OverflowError:
            await reject(413, "Question request is too large.")
            return
        except TimeoutError:
            await reject(408, "Question upload timed out.")
            return
        except ConnectionError:
            return  # Explicit disconnect: no Azure work has started.
        except (ValueError, ValidationError, UnicodeDecodeError):
            await reject(422, "Send only a nonempty question within the demo length limit.")
            return
        payload = json.dumps({"question": question.question}).encode()
        sent = False
        async def replay() -> dict:
            nonlocal sent
            if not sent:
                sent = True
                return {"type": "http.request", "body": payload, "more_body": False}
            return await receive()
        await self.app(scope, replay, send)
