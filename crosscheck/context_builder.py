"""
crosscheck.context_builder
---------------------------
Phase 1 Feature 1: Multi-file / repo context injection.

Gathers relevant source files and dependency graphs, then injects them
into the supervisor prompt so agents see the full picture — not just
the file under review.

Usage:
    builder = RepoContextBuilder(root=".", max_files=20, max_tokens=60_000)
    ctx = builder.build(target_file="src/auth.py")
    # ctx.context_block → inject into supervisor prompt
    # ctx.file_tree     → textual tree for display
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

# ---------------------------------------------------------------------------
# Language-specific import parsers
# ---------------------------------------------------------------------------

def _python_imports(source: str, file_path: Path) -> list[str]:
    """Extract local module references from Python source."""
    refs: list[str] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return refs
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                refs.append(alias.name.replace(".", "/"))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                refs.append(node.module.replace(".", "/"))
    return refs


def _ts_imports(source: str, file_path: Path) -> list[str]:
    """Extract relative imports from TypeScript/JavaScript source."""
    pattern = re.compile(r"""(?:import|from)\s+['"](\.[^'"]+)['"]""")
    refs: list[str] = []
    for m in pattern.finditer(source):
        raw = m.group(1)
        resolved = (file_path.parent / raw).resolve()
        refs.append(str(resolved))
    return refs


def _go_imports(source: str, _: Path) -> list[str]:
    pattern = re.compile(r'"([^"]+)"')
    refs = []
    in_import = False
    for line in source.splitlines():
        stripped = line.strip()
        if stripped.startswith("import ("):
            in_import = True
        elif in_import and stripped == ")":
            in_import = False
        elif in_import or stripped.startswith("import "):
            for m in pattern.finditer(stripped):
                refs.append(m.group(1))
    return refs


_PARSERS = {
    ".py":   _python_imports,
    ".ts":   _ts_imports,
    ".tsx":  _ts_imports,
    ".js":   _ts_imports,
    ".jsx":  _ts_imports,
    ".go":   _go_imports,
}

# File extensions treated as source code
_SOURCE_EXTS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs",
    ".kt", ".swift", ".java", ".cpp", ".c", ".cs",
    ".rb", ".php", ".scala", ".r",
}

# Directories/files to always skip
_SKIP_DIRS  = {
    ".git", ".hg", "node_modules", "__pycache__", ".venv",
    "venv", "env", "dist", "build", ".next", ".nuxt",
    "coverage", ".pytest_cache", ".mypy_cache",
}
_SKIP_FILES = {".DS_Store", "Thumbs.db"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileEntry:
    path:     Path
    rel_path: str
    language: str
    size:     int
    content:  str


@dataclass
class RepoContext:
    target_file:   str
    file_tree:     str          # printable ASCII tree
    context_block: str          # injected into supervisor prompt
    files_included: list[str]   # relative paths
    total_chars:   int
    truncated:     bool = False


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class RepoContextBuilder:
    """
    Collects repo context for a target file.

    Strategy:
      1. Parse imports of the target file → priority queue of referenced files.
      2. BFS outward through imports until max_files or max_tokens reached.
      3. Also include sibling files in the same directory (heuristic).
      4. Render a file tree and a context block for injection.
    """

    def __init__(
        self,
        root:       str | Path = ".",
        max_files:  int         = 20,
        max_chars:  int         = 80_000,   # ~20k tokens
        include_tests: bool     = False,
    ):
        self.root          = Path(root).resolve()
        self.max_files     = max_files
        self.max_chars     = max_chars
        self.include_tests = include_tests

    # ── Public ──────────────────────────────────────────────────────────

    def build(self, target_file: str | Path) -> RepoContext:
        target = Path(target_file).resolve()
        rel    = self._rel(target)

        visited: dict[str, FileEntry] = {}
        queue   = [target]

        # BFS through dependency graph
        while queue and len(visited) < self.max_files:
            current = queue.pop(0)
            key     = str(current)
            if key in visited or not current.exists():
                continue
            if not self._should_include(current):
                continue

            entry = self._read(current)
            if entry is None:
                continue
            visited[key] = entry

            # Parse imports → enqueue referenced files
            refs = self._parse_imports(entry)
            for ref in refs:
                if str(ref) not in visited:
                    queue.append(ref)

        # Also include siblings of the target
        for sibling in target.parent.iterdir():
            if len(visited) >= self.max_files:
                break
            key = str(sibling)
            if key not in visited and sibling.is_file() and self._should_include(sibling):
                entry = self._read(sibling)
                if entry:
                    visited[key] = entry

        # Sort: target first, then by path
        entries = sorted(
            visited.values(),
            key=lambda e: (e.path != target, e.rel_path),
        )

        return self._render(target, rel, entries)

    # ── Private ──────────────────────────────────────────────────────────

    def _rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)

    def _should_include(self, p: Path) -> bool:
        if not p.is_file():
            return False
        if p.suffix.lower() not in _SOURCE_EXTS:
            return False
        if p.name in _SKIP_FILES:
            return False
        parts = set(p.relative_to(self.root).parts) if p.is_relative_to(self.root) else set()
        if parts & _SKIP_DIRS:
            return False
        if not self.include_tests:
            name = p.name.lower()
            if name.startswith("test_") or name.endswith("_test.py") or "/tests/" in str(p):
                return False
        return True

    def _read(self, p: Path) -> FileEntry | None:
        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        lang = _LANG_MAP.get(p.suffix.lower(), p.suffix.lstrip("."))
        return FileEntry(
            path     = p,
            rel_path = self._rel(p),
            language = lang,
            size     = len(content),
            content  = content,
        )

    def _parse_imports(self, entry: FileEntry) -> list[Path]:
        parser = _PARSERS.get(entry.path.suffix.lower())
        if not parser:
            return []
        refs = parser(entry.content, entry.path)
        resolved: list[Path] = []
        for ref in refs:
            # Try relative resolution first
            candidates = [
                entry.path.parent / ref,
                entry.path.parent / (ref + entry.path.suffix),
                self.root / ref,
                self.root / (ref + entry.path.suffix),
            ]
            for c in candidates:
                c = c.resolve()
                if c.exists() and c.is_file():
                    resolved.append(c)
                    break
        return resolved

    def _render(self, target: Path, rel_target: str, entries: list[FileEntry]) -> RepoContext:
        # Build file tree string
        tree_lines = [f"📁 {self.root.name}/"]
        _dirs_seen: set[str] = set()
        for e in entries:
            parts = Path(e.rel_path).parts
            indent = "  " * (len(parts) - 1)
            icon   = "🎯" if e.path == target else "📄"
            tree_lines.append(f"{indent}{icon} {parts[-1]}")

        file_tree = "\n".join(tree_lines)

        # Build context block with budget awareness
        sections: list[str] = []
        total_chars = 0
        truncated   = False
        included    = []

        for e in entries:
            header  = f"\n=== FILE: {e.rel_path} ({e.language}) ===\n"
            content = e.content
            # Truncate very large individual files
            if len(content) > 15_000:
                content = content[:15_000] + f"\n... [truncated — {len(e.content) - 15_000} chars omitted]"

            block = header + content + "\n"

            if total_chars + len(block) > self.max_chars and sections:
                truncated = True
                break

            sections.append(block)
            included.append(e.rel_path)
            total_chars += len(block)

        context_block = (
            f"=== REPOSITORY CONTEXT ({len(included)} files) ===\n"
            f"Root: {self.root.name}/\n"
            f"Target file: {rel_target}\n"
            + ("".join(sections))
            + ("=== [Additional files omitted — budget limit reached] ===\n" if truncated else "")
        )

        return RepoContext(
            target_file    = rel_target,
            file_tree      = file_tree,
            context_block  = context_block,
            files_included = included,
            total_chars    = total_chars,
            truncated      = truncated,
        )


_LANG_MAP = {
    ".py":    "Python",
    ".ts":    "TypeScript",
    ".tsx":   "TypeScript/React",
    ".js":    "JavaScript",
    ".jsx":   "JavaScript/React",
    ".go":    "Go",
    ".rs":    "Rust",
    ".kt":    "Kotlin",
    ".swift": "Swift",
    ".java":  "Java",
    ".cpp":   "C++",
    ".c":     "C",
    ".cs":    "C#",
    ".rb":    "Ruby",
    ".php":   "PHP",
    ".scala": "Scala",
    ".r":     "R",
}
