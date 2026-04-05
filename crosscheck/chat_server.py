"""
crosscheck.chat_server
----------------------
Unified FastAPI server for the AI Dev Team Dashboard.

Serves the dashboard UI and provides all API endpoints:
    GET  /                      Serve the unified Dashboard UI
    WS   /ws/team               WebSocket for team chat
    GET  /api/team              Get default team info
    GET  /api/models            Full model registry for dashboard
    POST /api/test-key          Validate an OpenRouter API key
    POST /api/review            Run a code review from the browser
    GET  /api/sessions          List past sessions (from HistoryStore)
    GET  /api/model-performance Per-model stats from HistoryStore

Launch:
    crosscheck chat --port 8080
    # or directly:
    uvicorn crosscheck.chat_server:app --port 8080
"""

import asyncio
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = logging.getLogger(__name__)

# Chat UI static files are in crosscheck/chat_ui/
_CHAT_UI_DIR = Path(__file__).parent / "chat_ui"


def create_chat_app(api_key: str | None = None) -> "FastAPI":
    """Create the FastAPI dashboard + chat application.

    Args:
        api_key: OpenRouter API key. Falls back to env var.

    Returns:
        FastAPI application instance.
    """
    try:
        from fastapi import FastAPI, WebSocket, WebSocketDisconnect
        from fastapi.responses import HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as err:
        raise ImportError(
            "FastAPI required for chat mode.\n"
            "Install: pip install 'crosscheck-ai[dashboard]'"
        ) from err

    from crosscheck.models import REGISTRY
    from crosscheck.team.roles import DEFAULT_TEAM

    app = FastAPI(title="crosscheck AI Dev Team", version="2.0.0")

    # CRITICAL: WebSocket routes MUST be defined BEFORE the static mount.
    # In Starlette 1.0.0, app.mount() creates a catch-all Mount that can
    # interfere with WebSocket upgrade routing when the middleware stack
    # is built. Defining WS routes first ensures they take priority.

    @app.websocket("/ws/test")
    async def test_ws(websocket: WebSocket):
        """Minimal WS endpoint for connectivity checks."""
        await websocket.accept()
        await websocket.send_text("hello")
        await websocket.close()

    # Track active sessions
    _sessions: dict[str, dict] = {}

    # Serve static files (JS, CSS) — AFTER WebSocket routes
    if _CHAT_UI_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_CHAT_UI_DIR)), name="static")

    # ── Dashboard endpoints ───────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def index():
        """Serve the unified Dashboard UI."""
        html_path = _CHAT_UI_DIR / "dashboard.html"
        if html_path.exists():
            return HTMLResponse(html_path.read_text(encoding="utf-8"))
        # Fallback to old index.html
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

    @app.get("/api/models")
    async def get_models():
        """Return full model registry for the dashboard model selector."""
        return [
            {
                "model_id": m.model_id,
                "display_name": m.display_name,
                "provider": m.provider,
                "tiers": [t.value for t in m.tiers],
                "context_k": m.context_k,
                "origin": m.origin,
                "notes": m.notes,
            }
            for m in REGISTRY
        ]

    @app.post("/api/test-key")
    async def test_key(payload: dict):
        """Validate an OpenRouter API key by making a lightweight API call."""
        import httpx

        key = payload.get("api_key", "").strip()
        if not key:
            return JSONResponse(
                {"valid": False, "message": "No API key provided"},
                status_code=400,
            )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={
                        "Authorization": f"Bearer {key}",
                        "HTTP-Referer": "https://github.com/Nomadu27/crosscheck-ai",
                        "X-Title": "crosscheck-ai",
                    },
                )
                if resp.status_code == 200:
                    data = resp.json()
                    count = len(data.get("data", []))
                    return {"valid": True, "message": f"Access to {count} models"}
                if resp.status_code == 401:
                    return {"valid": False, "message": "Invalid or expired API key"}
                return {
                    "valid": False,
                    "message": f"OpenRouter returned status {resp.status_code}",
                }
        except httpx.TimeoutException:
            return {"valid": False, "message": "Request timed out"}
        except Exception as e:
            return {"valid": False, "message": str(e)}

    @app.post("/api/review")
    async def run_review(payload: dict):
        """Run a code review from the dashboard.

        Expects: {code, type, mode, max_rounds, api_key}
        Returns: {verdict, final_score, cost_usd, summary} or {error}
        """
        code = payload.get("code", "").strip()
        if not code:
            return JSONResponse({"error": "No code provided"}, status_code=400)

        key = payload.get("api_key") or api_key or os.environ.get(
            "CROSSCHECK_API_KEY"
        ) or os.environ.get("OPENROUTER_API_KEY")
        if not key:
            return JSONResponse(
                {"error": "No API key. Set one in Setup tab or CROSSCHECK_API_KEY env var."},
                status_code=400,
            )

        review_type = payload.get("type", "code")
        mode = payload.get("mode", "balanced")
        max_rounds = int(payload.get("max_rounds", 3))

        try:
            from crosscheck.client import OpenRouterClient
            from crosscheck.core import MultiAgentSession

            client = OpenRouterClient(api_key=key)
            session = MultiAgentSession(
                client=client,
                review_type=review_type,
                mode=mode,
                max_rounds=max_rounds,
            )
            result = await session.run(code)

            summary_lines = []
            for rnd in result.rounds:
                summary_lines.append(f"--- Round {rnd.number} ---")
                for report in rnd.analyzer_reports:
                    summary_lines.append(
                        f"[{report.model_id}] {report.verdict} "
                        f"({report.score:.1f}/10): {report.angle}"
                    )
                    for issue in report.issues[:3]:
                        summary_lines.append(f"  - {issue}")
                if rnd.supervisor_verdict:
                    summary_lines.append(
                        f"Supervisor: {rnd.supervisor_verdict.verdict} "
                        f"({rnd.supervisor_verdict.score:.1f}/10)"
                    )

            return {
                "verdict": result.verdict,
                "final_score": result.final_score,
                "cost_usd": result.cost_usd,
                "total_rounds": result.total_rounds,
                "summary": "\n".join(summary_lines),
            }
        except Exception as e:
            logger.error("Review failed: %s", e)
            return JSONResponse({"error": str(e)}, status_code=500)

    # ── History endpoints (from HistoryStore) ─────────────────────────────

    @app.get("/api/sessions")
    async def get_sessions(days: int = 30, limit: int = 50):
        """List past review sessions."""
        try:
            from crosscheck.dashboard import HistoryStore
            store = HistoryStore()
            return store.recent_sessions(limit=limit, days=days)
        except Exception:
            return []

    @app.get("/api/model-performance")
    async def get_model_performance():
        """Per-model average score and pass rate."""
        try:
            from crosscheck.dashboard import HistoryStore
            store = HistoryStore()
            return store.model_performance()
        except Exception:
            return []

    # ── WebSocket team chat ───────────────────────────────────────────────

    @app.websocket("/ws/team")
    async def team_chat(websocket: WebSocket):
        """WebSocket endpoint for real-time team chat."""
        # session_id MUST be set before accept() — Starlette 1.0.0 returns 403
        # if an exception occurs before the 101 upgrade completes, and the
        # finally block needs session_id for cleanup.
        session_id = str(uuid.uuid4())[:8]
        accepted = False

        await websocket.accept()
        accepted = True
        logger.info("WebSocket connected: %s", session_id)

        try:
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
                    # Accept API key from dashboard (sent via JS)
                    ws_key = (
                        data.get("api_key")
                        or api_key
                        or os.environ.get("CROSSCHECK_API_KEY")
                        or os.environ.get("OPENROUTER_API_KEY")
                    )

                    if not ws_key:
                        await websocket.send_json({
                            "type": "error",
                            "message": "No API key. Set one in the Setup tab.",
                        })
                        continue

                    if not task:
                        await websocket.send_json({
                            "type": "error",
                            "message": "Task is required.",
                        })
                        continue

                    try:
                        from crosscheck.client import OpenRouterClient
                        from crosscheck.team.session import TeamSession

                        client = OpenRouterClient(api_key=ws_key)

                        # Create workspace if project_root provided
                        workspace = None
                        project_root = data.get("project_root", "").strip()
                        if project_root:
                            try:
                                from crosscheck.team.workspace import ProjectWorkspace
                                workspace = ProjectWorkspace(root=project_root)
                                logger.info("Workspace: %s", workspace.root)
                            except Exception as e:
                                logger.warning("Workspace init failed: %s", e)
                                await websocket.send_json({
                                    "type": "error",
                                    "message": f"Invalid project root: {e}",
                                })

                        team_session = TeamSession(
                            client=client, workspace=workspace,
                        )
                        _sessions[session_id] = {
                            "session": team_session,
                            "status": "running",
                        }

                        async def stream_messages(ts: TeamSession, ws: WebSocket, sid: str):
                            """Stream agent messages to the WebSocket client."""
                            async for msg in ts.stream():
                                try:
                                    phase_val = msg.phase.value if hasattr(msg.phase, "value") else str(msg.phase)

                                    # Sanitize content for JSON safety (free models
                                    # may produce characters that break serialization)
                                    safe_content = (msg.content or "").encode(
                                        "utf-8", errors="replace"
                                    ).decode("utf-8")

                                    # Check if this is a proposal message
                                    if msg.role == "system" and msg.code_blocks and "proposal" in safe_content.lower():
                                        proposal = ts._current_proposal
                                        await ws.send_json({
                                            "type": "change_proposal",
                                            "proposal_id": proposal.id if proposal else "",
                                            "changes": [
                                                {
                                                    "filename": cb.filename,
                                                    "language": cb.language,
                                                    "content": cb.content,
                                                    "action": cb.action,
                                                }
                                                for cb in msg.code_blocks
                                            ],
                                            "diffs": [
                                                c.diff for c in proposal.changes
                                            ] if proposal else [],
                                            "coordinator_summary": proposal.coordinator_summary if proposal else "",
                                            "observer_findings": proposal.observer_findings if proposal else [],
                                            "session_id": sid,
                                        })
                                        continue

                                    # Check if this is a test result
                                    if msg.role == "system" and msg.display_name == "Safety Tools":
                                        await ws.send_json({
                                            "type": "test_results",
                                            "content": safe_content,
                                            "passed": "passed" in safe_content.lower() and "failed" not in safe_content.lower(),
                                            "session_id": sid,
                                        })
                                        continue

                                    # Phase change notification
                                    if msg.role == "system" and safe_content.startswith("Phase:"):
                                        await ws.send_json({
                                            "type": "phase_change",
                                            "phase": phase_val,
                                            "session_id": sid,
                                        })
                                        continue

                                    # Regular agent message
                                    await ws.send_json({
                                        "type": "agent_message",
                                        "role": msg.role,
                                        "model_id": msg.model_id or "",
                                        "display_name": msg.display_name or msg.role,
                                        "content": safe_content,
                                        "phase": phase_val,
                                        "round_num": msg.round_num,
                                        "code_blocks": [
                                            {
                                                "filename": cb.filename or "",
                                                "language": cb.language or "",
                                                "content": (cb.content or ""),
                                                "action": cb.action or "create",
                                            }
                                            for cb in (msg.code_blocks or [])
                                        ],
                                        "is_code_output": bool(msg.is_code_output),
                                        "timestamp": msg.timestamp or "",
                                        "session_id": sid,
                                    })
                                except WebSocketDisconnect:
                                    logger.info("Client disconnected during stream")
                                    break
                                except Exception as e:
                                    logger.error(
                                        "Stream error for %s: %s — skipping message",
                                        msg.role, e,
                                    )
                                    continue

                        stream_task = asyncio.create_task(
                            stream_messages(team_session, websocket, session_id)
                        )

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
                        if session_id in _sessions:
                            _sessions[session_id]["status"] = "done"
                        if "stream_task" in dir():
                            stream_task.cancel()

                elif msg_type == "user_message":
                    content = data.get("content", "")
                    target = data.get("target")
                    session_data = _sessions.get(session_id)
                    if content and session_data:
                        try:
                            await session_data["session"].inject_human_message(
                                content=content, target=target,
                            )
                        except Exception as e:
                            await websocket.send_json({
                                "type": "error",
                                "message": str(e),
                            })

                elif msg_type == "proposal_decision":
                    # Human clicked Accept/Reject/Edit on a proposal
                    decision = data.get("decision", "reject")
                    edits = data.get("edits")  # {filename: content}
                    session_data = _sessions.get(session_id)
                    if session_data:
                        session_data["session"].set_approval_decision(
                            decision=decision,
                            edits=edits,
                        )
                        logger.info(
                            "Proposal decision: %s (session %s)",
                            decision, session_id,
                        )

                elif msg_type == "ping":
                    await websocket.send_json({"type": "pong"})

        except WebSocketDisconnect:
            pass
        except Exception as e:
            logger.error("WebSocket error: %s", e)
        finally:
            if accepted:
                _sessions.pop(session_id, None)
                logger.info("Session cleaned up: %s", session_id)

    return app


# Module-level app is NOT created eagerly.
# Use `crosscheck chat` CLI (which calls create_chat_app()) or:
#   uvicorn crosscheck.chat_server:create_chat_app --factory
app = None
