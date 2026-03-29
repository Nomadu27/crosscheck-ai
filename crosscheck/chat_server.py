"""
crosscheck.chat_server
----------------------
FastAPI + WebSocket server for the AI Dev Team Group Chat.

Provides real-time bidirectional communication between the user
and the AI team. Each agent's response streams to the client
as it arrives.

Launch:
    crosscheck chat --port 8080
    # or directly:
    uvicorn crosscheck.chat_server:app --port 8080

Endpoints:
    GET  /                  Serve the Group Chat UI
    WS   /ws/team           WebSocket for team chat
    GET  /api/team          Get default team info
    GET  /api/sessions      List past sessions (from HistoryStore)
    POST /api/sessions      Start a new team session
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Chat UI static files are in crosscheck/chat_ui/
_CHAT_UI_DIR = Path(__file__).parent / "chat_ui"


def create_chat_app(api_key: Optional[str] = None) -> "FastAPI":
    """Create the FastAPI chat application.

    Args:
        api_key: OpenRouter API key. Falls back to env var.

    Returns:
        FastAPI application instance.
    """
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError:
        raise ImportError(
            "FastAPI required for chat mode.\n"
            "Install: pip install 'crosscheck-ai[dashboard]'"
        )

    from crosscheck.team.roles import DEFAULT_TEAM, TeamRole, build_team
    from crosscheck.team.chat import SessionPhase

    app = FastAPI(title="crosscheck AI Dev Team", version="2.0.0")

    # Serve static files (JS, CSS)
    if _CHAT_UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_CHAT_UI_DIR)), name="static")

    # Track active sessions
    _sessions: dict[str, dict] = {}

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the Group Chat UI."""
        html_path = _CHAT_UI_DIR / "index.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        return HTMLResponse("<h1>crosscheck AI Dev Team</h1><p>chat_ui/ not found</p>")

    @app.get("/api/team")
    async def get_team():
        """Get default team configuration."""
        return [
            {
                "role": spec.role.value,
                "title": spec.title,
                "display_name": spec.display_name,
                "model": spec.default_model,
                "color": spec.color,
                "can_write_code": spec.can_write_code,
            }
            for spec in DEFAULT_TEAM
        ]

    @app.websocket("/ws/team")
    async def team_chat(websocket: WebSocket):
        """WebSocket endpoint for real-time team chat.

        Client sends:
            {"type": "start_task", "task": "...", "code": "..."}
            {"type": "user_message", "content": "...", "target": "@debugger"}

        Server sends:
            {"type": "agent_message", ...}
            {"type": "phase_change", "phase": "..."}
            {"type": "typing", "role": "...", "display_name": "..."}
            {"type": "error", "message": "..."}
            {"type": "done", "session_id": "..."}
        """
        await websocket.accept()
        session_id = str(uuid.uuid4())[:8]
        logger.info("WebSocket connected: %s", session_id)

        key = api_key or os.environ.get("CROSSCHECK_API_KEY") or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            await websocket.send_json({
                "type": "error",
                "message": "No API key. Set CROSSCHECK_API_KEY environment variable.",
            })
            await websocket.close()
            return

        try:
            from crosscheck.client import OpenRouterClient
            from crosscheck.team.session import TeamSession

            client = OpenRouterClient(api_key=key)
            team_session = TeamSession(client=client)
            _sessions[session_id] = {"session": team_session, "status": "idle"}

            async def stream_messages():
                """Stream agent messages to the WebSocket client."""
                async for msg in team_session.stream():
                    try:
                        await websocket.send_json({
                            "type": "agent_message",
                            "role": msg.role,
                            "model_id": msg.model_id,
                            "display_name": msg.display_name,
                            "content": msg.content,
                            "phase": msg.phase,
                            "round_num": msg.round_num,
                            "code_blocks": [
                                {"filename": cb.filename, "language": cb.language,
                                 "content": cb.content, "action": cb.action}
                                for cb in msg.code_blocks
                            ],
                            "is_code_output": msg.is_code_output,
                            "timestamp": msg.timestamp,
                            "session_id": session_id,
                        })
                    except Exception:
                        break

            # Listen for client messages
            while True:
                try:
                    data = await websocket.receive_json()
                except WebSocketDisconnect:
                    logger.info("WebSocket disconnected: %s", session_id)
                    break

                msg_type = data.get("type", "")

                if msg_type == "start_task":
                    task = data.get("task", "")
                    code = data.get("code", "")
                    if not task:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Task is required.",
                        })
                        continue

                    _sessions[session_id]["status"] = "running"

                    # Run session and stream in parallel
                    stream_task = asyncio.create_task(stream_messages())

                    try:
                        await websocket.send_json({
                            "type": "phase_change",
                            "phase": "planning",
                            "session_id": session_id,
                        })
                        history = await team_session.run(task=task, code=code)
                        await websocket.send_json({
                            "type": "done",
                            "session_id": session_id,
                            "message_count": len(history.messages),
                        })
                    except Exception as e:
                        logger.error("Session error: %s", e)
                        await websocket.send_json({
                            "type": "error",
                            "message": str(e),
                        })
                    finally:
                        _sessions[session_id]["status"] = "done"
                        stream_task.cancel()

                elif msg_type == "user_message":
                    content = data.get("content", "")
                    target = data.get("target")
                    if content:
                        try:
                            responses = await team_session.inject_human_message(
                                content=content, target=target,
                            )
                        except Exception as e:
                            await websocket.send_json({
                                "type": "error",
                                "message": str(e),
                            })

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("WebSocket error: %s", e)
        finally:
            _sessions.pop(session_id, None)
            logger.info("Session cleaned up: %s", session_id)

    return app


def _lazy_app():
    """Lazy app factory for uvicorn crosscheck.chat_server:app"""
    return create_chat_app()


# For `uvicorn crosscheck.chat_server:app` — only created when accessed
try:
    app = create_chat_app()
except ImportError:
    app = None  # FastAPI not installed — CLI will handle this
