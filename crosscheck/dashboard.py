"""
crosscheck.dashboard
--------------------
Phase 2: Team dashboard — internal web UI for history, cost, anomaly trends.

FastAPI + Jinja2 + SQLite backend. Ships as a standalone server.

Launch:
    crosscheck dashboard --port 8080 --host 0.0.0.0

Features:
  - Session history with verdict / score / cost / duration
  - Per-model performance breakdown
  - Cost trends over time (daily/weekly)
  - Anomaly frequency charts (InsAIts)
  - Reviewer comparison (which models agree/disagree most)
  - Export to CSV / JSON
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI

DEFAULT_DB_PATH = Path.home() / ".crosscheck" / "history.db"


# ---------------------------------------------------------------------------
# History store
# ---------------------------------------------------------------------------

class HistoryStore:
    """Persists session results to SQLite for the dashboard."""

    def __init__(self, db_path: Path = DEFAULT_DB_PATH):
        self._path = db_path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self._path), timeout=10)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts            REAL,
                    verdict       TEXT,
                    final_score   REAL,
                    total_rounds  INTEGER,
                    issues_fixed  INTEGER,
                    anomaly_count INTEGER,
                    total_tokens  INTEGER,
                    cost_usd      REAL,
                    duration_sec  REAL,
                    review_type   TEXT,
                    mode          TEXT,
                    coder_model   TEXT,
                    supervisor_models TEXT,
                    analyzer_models   TEXT,
                    file_path     TEXT,
                    consensus     INTEGER
                );

                CREATE TABLE IF NOT EXISTS analyzer_runs (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER REFERENCES sessions(id),
                    round_num  INTEGER,
                    model_id   TEXT,
                    angle      TEXT,
                    score      REAL,
                    verdict    TEXT,
                    issue_count INTEGER
                );

                CREATE TABLE IF NOT EXISTS anomalies (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id INTEGER REFERENCES sessions(id),
                    round_num  INTEGER,
                    source     TEXT,
                    type       TEXT,
                    severity   TEXT,
                    description TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_sessions_ts ON sessions(ts);
                CREATE INDEX IF NOT EXISTS idx_ar_session  ON analyzer_runs(session_id);
            """)

    def record_session(
        self,
        result,            # SessionResult
        review_type: str  = "code",
        mode:        str  = "balanced",
        file_path:   str  = "",
        supervisors: list[str] | None = None,
        analyzers:   list[str] | None = None,
        coder:       str  = "",
    ) -> int:
        """Persist a SessionResult to history. Returns session_id."""
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO sessions (
                    ts, verdict, final_score, total_rounds, issues_fixed,
                    anomaly_count, total_tokens, cost_usd, duration_sec,
                    review_type, mode, coder_model, supervisor_models,
                    analyzer_models, file_path, consensus
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    time.time(),
                    result.verdict,
                    result.final_score,
                    result.total_rounds,
                    result.issues_fixed,
                    result.anomaly_count,
                    result.total_tokens,
                    result.cost_usd,
                    result.duration_sec,
                    review_type,
                    mode,
                    coder,
                    json.dumps(supervisors or []),
                    json.dumps(analyzers or []),
                    file_path,
                    int(result.supervisor_consensus),
                ),
            )
            session_id: int = cur.lastrowid  # type: ignore[assignment]  # always set after INSERT

            # Record analyzer runs
            for rnd in result.rounds:
                for r in rnd.analyzer_reports:
                    conn.execute(
                        """
                        INSERT INTO analyzer_runs
                          (session_id, round_num, model_id, angle, score, verdict, issue_count)
                        VALUES (?,?,?,?,?,?,?)
                        """,
                        (session_id, rnd.number, r.model_id, r.angle,
                         r.score, r.verdict, len(r.issues)),
                    )

                for a in rnd.anomalies:
                    conn.execute(
                        """
                        INSERT INTO anomalies
                          (session_id, round_num, source, type, severity, description)
                        VALUES (?,?,?,?,?,?)
                        """,
                        (session_id, rnd.number, a.source, a.anomaly_type,
                         a.severity, a.description),
                    )

        return session_id

    # ── Query helpers ─────────────────────────────────────────────────────

    def recent_sessions(self, limit: int = 50, days: int = 30) -> list[dict]:
        cutoff = time.time() - days * 86_400
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM sessions WHERE ts >= ? ORDER BY ts DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def cost_trend(self, days: int = 30) -> list[dict]:
        """Daily cost aggregation for the last N days."""
        cutoff = time.time() - days * 86_400
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT
                    date(ts, 'unixepoch') AS day,
                    COUNT(*)              AS sessions,
                    SUM(cost_usd)         AS total_cost,
                    AVG(final_score)      AS avg_score,
                    SUM(CASE WHEN verdict='APPROVED' THEN 1 ELSE 0 END) AS approved
                FROM sessions
                WHERE ts >= ?
                GROUP BY day
                ORDER BY day
            """, (cutoff,)).fetchall()
        return [dict(r) for r in rows]

    def model_performance(self) -> list[dict]:
        """Per-model average score and issue count."""
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT
                    model_id,
                    COUNT(*)      AS runs,
                    AVG(score)    AS avg_score,
                    SUM(issue_count) AS total_issues,
                    SUM(CASE WHEN verdict='PASS' THEN 1 ELSE 0 END) AS passes
                FROM analyzer_runs
                GROUP BY model_id
                ORDER BY avg_score DESC
            """).fetchall()
        return [dict(r) for r in rows]

    def anomaly_stats(self, days: int = 30) -> dict:
        cutoff = time.time() - days * 86_400
        with self._connect() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM anomalies a JOIN sessions s ON a.session_id=s.id WHERE s.ts>=?",
                (cutoff,)
            ).fetchone()[0]
            by_severity = conn.execute("""
                SELECT severity, COUNT(*) AS count
                FROM anomalies a JOIN sessions s ON a.session_id=s.id
                WHERE s.ts >= ?
                GROUP BY severity
            """, (cutoff,)).fetchall()
        return {
            "total":       total,
            "by_severity": [dict(r) for r in by_severity],
        }

    def export_csv(self, days: int = 90) -> str:
        """Export session history as CSV string."""
        import csv, io
        sessions = self.recent_sessions(limit=10_000, days=days)
        if not sessions:
            return ""
        out = io.StringIO()
        w   = csv.DictWriter(out, fieldnames=sessions[0].keys())
        w.writeheader()
        w.writerows(sessions)
        return out.getvalue()


# ---------------------------------------------------------------------------
# FastAPI dashboard app
# ---------------------------------------------------------------------------

def create_dashboard_app(db_path: Path = DEFAULT_DB_PATH) -> FastAPI:
    """
    Create the FastAPI dashboard application.
    
    Run with: uvicorn crosscheck.dashboard:app --port 8080
    """
    try:
        from fastapi import FastAPI
        from fastapi.responses import HTMLResponse, JSONResponse
    except ImportError:
        raise ImportError(
            "FastAPI required for dashboard mode.\n"
            "Install: pip install 'crosscheck-ai[dashboard]'"
        )

    app   = FastAPI(title="crosscheck-ai Dashboard", version="0.2.0")
    store = HistoryStore(db_path)

    @app.get("/", response_class=HTMLResponse)
    async def index():
        return _render_dashboard_html()

    @app.get("/api/sessions")
    async def api_sessions(days: int = 30, limit: int = 50):
        return store.recent_sessions(limit=limit, days=days)

    @app.get("/api/cost-trend")
    async def api_cost(days: int = 30):
        return store.cost_trend(days=days)

    @app.get("/api/model-performance")
    async def api_models():
        return store.model_performance()

    @app.get("/api/anomalies")
    async def api_anomalies(days: int = 30):
        return store.anomaly_stats(days=days)

    @app.get("/api/export/csv")
    async def api_export_csv(days: int = 90):
        from fastapi.responses import Response
        csv_data = store.export_csv(days=days)
        return Response(
            content    = csv_data,
            media_type = "text/csv",
            headers    = {"Content-Disposition": "attachment; filename=crosscheck_history.csv"},
        )

    return app


def _render_dashboard_html() -> str:
    """Minimal single-page dashboard — fetches data from /api/* endpoints."""
    return """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>crosscheck-ai Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4/dist/chart.umd.min.js"></script>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
         background: #0d1117; color: #e6edf3; }
  header { background: #161b22; padding: 16px 24px; border-bottom: 1px solid #30363d;
           display: flex; align-items: center; gap: 12px; }
  header h1 { font-size: 1.2rem; font-weight: 600; }
  .badge { background: #1f6feb; color: #fff; border-radius: 12px;
           padding: 2px 10px; font-size: 0.75rem; }
  main { max-width: 1200px; margin: 0 auto; padding: 24px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 16px; }
  .card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; }
  .card h2 { font-size: 0.85rem; color: #8b949e; margin-bottom: 8px; }
  .card .value { font-size: 2rem; font-weight: 700; }
  .card .sub { font-size: 0.8rem; color: #8b949e; margin-top: 4px; }
  .section { margin-top: 32px; }
  .section h2 { font-size: 1rem; margin-bottom: 12px; color: #8b949e; }
  table { width: 100%; border-collapse: collapse; }
  th { text-align: left; padding: 8px 12px; font-size: 0.8rem; color: #8b949e;
       border-bottom: 1px solid #30363d; }
  td { padding: 8px 12px; font-size: 0.85rem; border-bottom: 1px solid #21262d; }
  .approved { color: #3fb950; }
  .revise   { color: #f0883e; }
  canvas    { max-height: 220px; }
  .charts   { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }
  @media (max-width: 700px) { .charts { grid-template-columns: 1fr; } }
  .export-btn { background: #21262d; border: 1px solid #30363d; color: #e6edf3;
                padding: 6px 14px; border-radius: 6px; cursor: pointer; font-size: 0.85rem; }
</style>
</head>
<body>
<header>
  <div>✅</div>
  <h1>crosscheck-ai</h1>
  <span class="badge">Dashboard</span>
</header>
<main>
  <div class="grid" id="kpis">
    <div class="card"><h2>Sessions (30d)</h2><div class="value" id="kpi-sessions">—</div></div>
    <div class="card"><h2>Approval Rate</h2><div class="value" id="kpi-approval">—</div></div>
    <div class="card"><h2>Avg Score</h2><div class="value" id="kpi-score">—</div></div>
    <div class="card"><h2>Total Cost (30d)</h2><div class="value" id="kpi-cost">—</div></div>
  </div>

  <div class="section">
    <div class="charts">
      <div class="card"><h2>Daily Cost ($)</h2><canvas id="costChart"></canvas></div>
      <div class="card"><h2>Avg Score Trend</h2><canvas id="scoreChart"></canvas></div>
    </div>
  </div>

  <div class="section">
    <h2>Model Performance</h2>
    <div class="card">
      <table>
        <thead><tr><th>Model</th><th>Runs</th><th>Avg Score</th><th>Pass Rate</th></tr></thead>
        <tbody id="models-body"></tbody>
      </table>
    </div>
  </div>

  <div class="section">
    <div style="display:flex;justify-content:space-between;align-items:center">
      <h2>Recent Sessions</h2>
      <button class="export-btn" onclick="exportCSV()">Export CSV</button>
    </div>
    <div class="card" style="margin-top:12px;overflow-x:auto">
      <table>
        <thead><tr>
          <th>Time</th><th>Verdict</th><th>Score</th><th>Rounds</th>
          <th>Cost</th><th>Duration</th><th>File</th>
        </tr></thead>
        <tbody id="sessions-body"></tbody>
      </table>
    </div>
  </div>
</main>

<script>
async function load() {
  const [sessions, trend, models] = await Promise.all([
    fetch('/api/sessions?days=30').then(r=>r.json()),
    fetch('/api/cost-trend').then(r=>r.json()),
    fetch('/api/model-performance').then(r=>r.json()),
  ]);

  // KPIs
  const total    = sessions.length;
  const approved = sessions.filter(s=>s.verdict==='APPROVED').length;
  const avgScore = sessions.reduce((a,s)=>a+s.final_score,0)/Math.max(total,1);
  const totalCost= sessions.reduce((a,s)=>a+s.cost_usd,0);

  document.getElementById('kpi-sessions').textContent  = total;
  document.getElementById('kpi-approval').textContent  = total ? (approved/total*100).toFixed(0)+'%' : '—';
  document.getElementById('kpi-score').textContent     = total ? avgScore.toFixed(1)+'/10' : '—';
  document.getElementById('kpi-cost').textContent      = '$'+totalCost.toFixed(4);

  // Charts
  const days  = trend.map(r=>r.day);
  const costs = trend.map(r=>r.total_cost||0);
  const scores= trend.map(r=>r.avg_score||0);

  new Chart(document.getElementById('costChart'), {
    type: 'bar',
    data: { labels: days, datasets: [{ label: 'Cost $', data: costs,
      backgroundColor: '#1f6feb', borderRadius: 4 }] },
    options: { plugins:{legend:{display:false}}, scales:{x:{ticks:{color:'#8b949e'}},y:{ticks:{color:'#8b949e'}}} }
  });

  new Chart(document.getElementById('scoreChart'), {
    type: 'line',
    data: { labels: days, datasets: [{ label: 'Avg Score', data: scores,
      borderColor: '#3fb950', tension: 0.3, pointRadius: 3 }] },
    options: { plugins:{legend:{display:false}}, scales:{x:{ticks:{color:'#8b949e'}},y:{min:0,max:10,ticks:{color:'#8b949e'}}} }
  });

  // Model table
  const mb = document.getElementById('models-body');
  models.forEach(m => {
    mb.innerHTML += `<tr>
      <td>${m.model_id}</td>
      <td>${m.runs}</td>
      <td>${m.avg_score?.toFixed(1)||'—'}</td>
      <td>${m.runs ? ((m.passes/m.runs)*100).toFixed(0)+'%' : '—'}</td>
    </tr>`;
  });

  // Sessions table
  const sb = document.getElementById('sessions-body');
  sessions.slice(0,20).forEach(s => {
    const dt  = new Date(s.ts*1000).toLocaleString();
    const cls = s.verdict==='APPROVED' ? 'approved' : 'revise';
    sb.innerHTML += `<tr>
      <td>${dt}</td>
      <td class="${cls}">${s.verdict}</td>
      <td>${s.final_score?.toFixed(1)||'—'}</td>
      <td>${s.total_rounds}</td>
      <td>$${s.cost_usd?.toFixed(4)||'0'}</td>
      <td>${s.duration_sec?.toFixed(1)||'—'}s</td>
      <td>${s.file_path||'—'}</td>
    </tr>`;
  });
}

function exportCSV() {
  window.location.href = '/api/export/csv';
}

load();
</script>
</body>
</html>"""
