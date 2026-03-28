"""
crosscheck.cache
----------------
Phase 2: Smart caching + delta analysis.

Caches analyzer results so unchanged code sections are not re-analyzed.
Computes content hashes and stores results in a local SQLite database.

Features:
  - Content-addressed cache (SHA-256 of content + model + task)
  - Delta analysis: only re-analyze changed functions/classes
  - TTL-based expiry (default: 7 days)
  - Per-model cache keys (different models → different cache entries)
  - CLI: crosscheck cache stats / clear

Delta analysis:
  Uses Python AST (or line-based for other languages) to identify which
  functions/classes changed between versions. Only those chunks are
  re-analyzed; unchanged chunks use the cached result.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ---------------------------------------------------------------------------
# Cache store
# ---------------------------------------------------------------------------

DEFAULT_CACHE_DIR  = Path.home() / ".crosscheck" / "cache"
DEFAULT_TTL_DAYS   = 7
DEFAULT_MAX_ENTRIES = 10_000


@dataclass
class CacheEntry:
    key:         str
    model_id:    str
    content_hash: str
    result:      dict    # serialized AnalyzerReport or SynthesisResult
    created_at:  float
    ttl_sec:     float

    @property
    def is_expired(self) -> bool:
        return time.time() > self.created_at + self.ttl_sec


class ResultCache:
    """
    SQLite-backed cache for analyzer and synthesis results.
    Thread-safe for concurrent agents via WAL mode.
    """

    def __init__(
        self,
        cache_dir:   Path = DEFAULT_CACHE_DIR,
        ttl_days:    int  = DEFAULT_TTL_DAYS,
        max_entries: int  = DEFAULT_MAX_ENTRIES,
        enabled:     bool = True,
    ):
        self.enabled     = enabled
        self.ttl_sec     = ttl_days * 86_400
        self.max_entries = max_entries
        self._db_path    = cache_dir / "results.db"

        if self.enabled:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._init_db()

    def _init_db(self) -> None:
        with self._connect() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS cache (
                    key          TEXT PRIMARY KEY,
                    model_id     TEXT,
                    content_hash TEXT,
                    result_json  TEXT,
                    created_at   REAL,
                    ttl_sec      REAL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_created ON cache(created_at)")
            conn.execute("PRAGMA journal_mode=WAL")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(str(self._db_path), timeout=10)

    # ── Cache key ────────────────────────────────────────────────────────

    @staticmethod
    def make_key(model_id: str, content: str, task: str, angle: str = "") -> str:
        """Deterministic cache key from model + content + task + angle."""
        raw = f"{model_id}|{task}|{angle}|{content}"
        return hashlib.sha256(raw.encode()).hexdigest()

    # ── CRUD ─────────────────────────────────────────────────────────────

    def get(self, key: str) -> Optional[dict]:
        if not self.enabled:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT result_json, created_at, ttl_sec FROM cache WHERE key=?",
                (key,)
            ).fetchone()
        if not row:
            return None
        result_json, created_at, ttl_sec = row
        if time.time() > created_at + ttl_sec:
            self.delete(key)
            return None
        return json.loads(result_json)

    def set(self, key: str, model_id: str, content_hash: str, result: dict) -> None:
        if not self.enabled:
            return
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache
                  (key, model_id, content_hash, result_json, created_at, ttl_sec)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (key, model_id, content_hash, json.dumps(result), time.time(), self.ttl_sec),
            )
        self._evict_if_needed()

    def delete(self, key: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM cache WHERE key=?", (key,))

    def clear(self) -> int:
        """Delete all entries. Returns count deleted."""
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            conn.execute("DELETE FROM cache")
        return count

    def clear_expired(self) -> int:
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE created_at + ttl_sec < ?",
                (time.time(),)
            ).fetchone()[0]
            conn.execute(
                "DELETE FROM cache WHERE created_at + ttl_sec < ?",
                (time.time(),)
            )
        return count

    def stats(self) -> dict:
        with self._connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            expired = conn.execute(
                "SELECT COUNT(*) FROM cache WHERE created_at + ttl_sec < ?",
                (time.time(),)
            ).fetchone()[0]
            size_bytes = self._db_path.stat().st_size if self._db_path.exists() else 0
        return {
            "total_entries":   total,
            "expired_entries": expired,
            "db_size_mb":      round(size_bytes / 1_048_576, 2),
            "db_path":         str(self._db_path),
            "ttl_days":        self.ttl_sec / 86_400,
        }

    def _evict_if_needed(self) -> None:
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) FROM cache").fetchone()[0]
            if count > self.max_entries:
                # Evict oldest 10%
                evict_n = max(1, self.max_entries // 10)
                conn.execute("""
                    DELETE FROM cache WHERE key IN (
                        SELECT key FROM cache ORDER BY created_at ASC LIMIT ?
                    )
                """, (evict_n,))


# ---------------------------------------------------------------------------
# Delta analyzer — identify changed code chunks
# ---------------------------------------------------------------------------

@dataclass
class CodeChunk:
    name:      str           # function/class name
    kind:      str           # "function" | "class" | "block"
    start_line: int
    end_line:  int
    content:   str
    hash:      str = field(init=False)

    def __post_init__(self):
        self.hash = hashlib.sha256(self.content.encode()).hexdigest()[:16]


class DeltaAnalyzer:
    """
    Identifies which code chunks changed between two versions.
    Only changed chunks need re-analysis; stable chunks can use cache.
    """

    @staticmethod
    def extract_chunks(source: str, language: str = "python") -> list[CodeChunk]:
        """Extract top-level functions and classes as chunks."""
        if language == "python":
            return DeltaAnalyzer._python_chunks(source)
        else:
            return DeltaAnalyzer._line_chunks(source)

    @staticmethod
    def _python_chunks(source: str) -> list[CodeChunk]:
        """Use AST to extract Python functions and classes."""
        chunks: list[CodeChunk] = []
        lines  = source.splitlines()

        try:
            tree = ast.parse(source)
        except SyntaxError:
            return DeltaAnalyzer._line_chunks(source)

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            # Only top-level definitions
            if not isinstance(getattr(node, "parent", None) or node, ast.Module):
                # crude check: line col == 0
                if node.col_offset != 0:
                    continue

            start = node.lineno - 1
            end   = node.end_lineno - 1
            chunk_lines = lines[start:end+1]
            chunks.append(CodeChunk(
                name       = node.name,
                kind       = "class" if isinstance(node, ast.ClassDef) else "function",
                start_line = start + 1,
                end_line   = end + 1,
                content    = "\n".join(chunk_lines),
            ))

        return chunks or DeltaAnalyzer._line_chunks(source)

    @staticmethod
    def _line_chunks(source: str, chunk_size: int = 50) -> list[CodeChunk]:
        """Fallback: split into fixed-size line chunks."""
        lines  = source.splitlines()
        chunks = []
        for i, start in enumerate(range(0, len(lines), chunk_size)):
            chunk_lines = lines[start:start+chunk_size]
            chunks.append(CodeChunk(
                name       = f"block_{i+1}",
                kind       = "block",
                start_line = start + 1,
                end_line   = min(start + chunk_size, len(lines)),
                content    = "\n".join(chunk_lines),
            ))
        return chunks

    @staticmethod
    def changed_chunks(
        before_chunks: list[CodeChunk],
        after_chunks:  list[CodeChunk],
    ) -> list[CodeChunk]:
        """
        Return chunks from `after` that are new or changed relative to `before`.
        Compares by name+hash (renamed = changed).
        """
        before_map = {c.name: c.hash for c in before_chunks}
        changed    = []
        for chunk in after_chunks:
            prev_hash = before_map.get(chunk.name)
            if prev_hash is None or prev_hash != chunk.hash:
                changed.append(chunk)
        return changed


# ---------------------------------------------------------------------------
# Cached analyzer pool wrapper
# ---------------------------------------------------------------------------

class CachedAnalyzerPool:
    """
    Wraps AnalyzerPool with cache-aware logic.
    Unchanged content sections skip the model call and use cached results.
    """

    def __init__(
        self,
        pool:  "AnalyzerPool",  # type: ignore[name-defined]
        cache: ResultCache,
        task:  str = "code",
    ):
        self.pool  = pool
        self.cache = cache
        self.task  = task
        self._hits  = 0
        self._misses = 0

    @property
    def cache_hit_rate(self) -> float:
        total = self._hits + self._misses
        return self._hits / total if total else 0.0

    async def analyze(self, content: str, tasks: list[dict], round_num: int, **kwargs):
        """
        For each model/task pair:
          1. Check cache
          2. If hit → use cached result (mark as cached)
          3. If miss → call model, store in cache
        """
        from crosscheck.agents.analyzer import AnalyzerReport

        content_hash = hashlib.sha256(content.encode()).hexdigest()
        results      = []

        for i, model in enumerate(self.pool.models):
            task_def = tasks[i % len(tasks)] if tasks else {"angle": "General", "instruction": ""}
            angle    = task_def.get("angle", "General")
            key      = self.cache.make_key(model, content, self.task, angle)

            cached = self.cache.get(key)
            if cached:
                self._hits += 1
                # Reconstruct AnalyzerReport from cache
                report = AnalyzerReport(
                    model_id          = model,
                    angle             = cached.get("angle", angle),
                    score             = cached.get("score", 0.0),
                    verdict           = cached.get("verdict", "PASS"),
                    issues            = cached.get("issues", []),
                    positive_findings = cached.get("positive_findings", []),
                    summary           = cached.get("summary", "") + " [cached]",
                    raw               = cached,
                )
                results.append(report)
            else:
                self._misses += 1
                # Run actual analysis
                rpts = await self.pool.analyze(
                    content, [task_def], round_num,
                    **{k: v for k, v in kwargs.items()}
                )
                report = rpts[0] if rpts else AnalyzerReport(
                    model_id=model, angle=angle, score=0, verdict="ERROR",
                    issues=[], positive_findings=[], summary="Analysis failed",
                )
                # Cache the result
                self.cache.set(key, model, content_hash, report.raw or {
                    "angle": report.angle, "score": report.score,
                    "verdict": report.verdict, "issues": report.issues,
                    "positive_findings": report.positive_findings,
                    "summary": report.summary,
                })
                results.append(report)

        return results
