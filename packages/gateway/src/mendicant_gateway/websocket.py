"""
mendicant_gateway.websocket
===========================
WebSocket event pipeline for the Brain Dashboard.

Provides real-time event broadcasting from the hook pipeline to connected
browser clients via WebSocket at /ws/brain.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Event model
# ---------------------------------------------------------------------------

VALID_EVENT_TYPES = frozenset({
    "session_start",
    "classification",
    "rule_match",
    "verification",
    "tool_use",
    "heartbeat",
    "state_snapshot",
})


class BrainEvent(BaseModel):
    """A single event in the brain event stream."""

    type: str = Field(..., description="Event type")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )
    session_id: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Event broadcaster
# ---------------------------------------------------------------------------


class EventBroadcaster:
    """Manages WebSocket clients and broadcasts events to all of them."""

    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()
        self._heartbeat_task: asyncio.Task | None = None

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        logger.info("[Brain WS] Client connected (%d total)", len(self._clients))

    async def disconnect(self, ws: WebSocket) -> None:
        self._clients.discard(ws)
        logger.info("[Brain WS] Client disconnected (%d total)", len(self._clients))

    async def broadcast(self, event: BrainEvent) -> None:
        if not self._clients:
            return

        data = event.model_dump_json()
        dead: list[WebSocket] = []

        for ws in self._clients:
            try:
                await ws.send_text(data)
            except (WebSocketDisconnect, RuntimeError, Exception):
                dead.append(ws)

        for ws in dead:
            self._clients.discard(ws)

    async def start_heartbeat(self) -> None:
        """Start the heartbeat loop as a background task."""
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

    async def stop_heartbeat(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self) -> None:
        while True:
            await asyncio.sleep(5)
            await self.broadcast(BrainEvent(
                type="heartbeat",
                payload={"uptime": time.monotonic(), "clients": len(self._clients)},
            ))


# Module singleton
broadcaster = EventBroadcaster()


# ---------------------------------------------------------------------------
# WebSocket endpoint handler
# ---------------------------------------------------------------------------


async def brain_websocket(ws: WebSocket) -> None:
    """WebSocket endpoint for /ws/brain. Accepts and holds connection."""
    await broadcaster.connect(ws)
    try:
        while True:
            # Keep connection alive by listening for client messages
            # (e.g., snapshot requests). Mainly used for disconnect detection.
            data = await ws.receive_text()
            # Could handle client requests here in the future
            if data:
                logger.debug("[Brain WS] Received from client: %s", data[:100])
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("[Brain WS] Connection error: %s", exc)
    finally:
        await broadcaster.disconnect(ws)


# ---------------------------------------------------------------------------
# Internal POST endpoint handler
# ---------------------------------------------------------------------------


async def receive_brain_event(event: BrainEvent) -> dict[str, str]:
    """POST /internal/brain-event — receives events from hook_handler and broadcasts."""
    await broadcaster.broadcast(event)
    return {"status": "broadcast", "clients": str(len(broadcaster._clients))}
