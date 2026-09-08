"""Einfacher In-Process-Event-Broker für Server-Sent Events (SSE)."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from app.core.logging import get_logger

log = get_logger("netprobe.events")


class EventBroker:
    def __init__(self, max_queue: int = 100) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()
        self._max_queue = max_queue

    async def publish(self, event: str, data: dict) -> None:
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        for q in list(self._subscribers):
            try:
                q.put_nowait(payload)
            except asyncio.QueueFull:
                # Langsamer Consumer -> ältestes Element verwerfen
                try:
                    q.get_nowait()
                    q.put_nowait(payload)
                except asyncio.QueueEmpty:
                    pass

    async def subscribe(self) -> AsyncIterator[str]:
        q: asyncio.Queue[str] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)


ping_broker = EventBroker()
