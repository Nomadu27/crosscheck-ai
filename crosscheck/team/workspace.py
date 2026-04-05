"""
crosscheck.team.workspace
--------------------------
Project workspace with safe filesystem access, change proposals,
and tool execution for the AI Dev Team.

Safety-first design:
  - ALL paths validated against project root (no traversal)
  - ALL writes go through propose_changes() → ChangeProposal
  - Nothing written to disk until human clicks Accept
  - Every applied change recorded in undo_log for rollback
  - After apply, auto-run safety tools via ExecutionSandbox
"""

from __future__ import annotations

import fnmatch
import logging
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Max file size to read (prevent reading huge binaries)
_MAX_READ_SIZE = 512_000  # 512 KB
# Extensions to skip when listing files
_SKIP_DIRS = {
    ".git", "__pycache__", "node_modules", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", "dist",
    "build", ".egg-info", ".tox", ".nox",
}
_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".zip", ".tar", ".gz", ".bz2", ".7z",
    ".db", ".sqlite", ".sqlite3",
}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FileChange:
    """A single file change within a proposal."""
    filename: str           # Relative path from project root
    action: str             # "create" | "edit" | "delete"
    content: str = ""       # New content (for create/edit)
    original: str = ""      # Original content (for edit/delete — enables rollback)
    diff: str = ""          # Unified diff string
    language: str = ""      # Programming language

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "action": self.action,
            "content": self.content,
            "diff": self.diff,
            "language": self.language,
        }


@dataclass
class ChangeProposal:
    """A set of proposed file changes awaiting human approval."""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    changes: list[FileChange] = field(default_factory=list)
    coordinator_summary: str = ""
    observer_findings: list[str] = field(default_factory=list)
    status: str = "pending"  # "pending" | "accepted" | "rejected" | "rolled_back"
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "proposal_id": self.id,
            "changes": [c.to_dict() for c in self.changes],
            "coordinator_summary": self.coordinator_summary,
            "observer_findings": self.observer_findings,
            "status": self.status,
        }


@dataclass
class UndoEntry:
    """Record of an applied change for rollback."""
    proposal_id: str
    filename: str
    action: str             # "create" | "edit" | "delete"
    original_content: str   # Content before change (empty for create)
    existed: bool           # Whether file existed before change


# ---------------------------------------------------------------------------
# ProjectWorkspace
# ---------------------------------------------------------------------------

class ProjectWorkspace:
    """Safe filesystem workspace for the AI Dev Team.

    All file operations are validated against the project root.
    Writes are never direct — they go through ChangeProposal.
    """

    def __init__(self, root: str | Path = "."):
        self.root = Path(root).resolve()
        if not self.root.is_dir():
            raise ValueError(f"Project root does not exist: {self.root}")
        self.proposals: dict[str, ChangeProposal] = {}
        self.undo_log: list[UndoEntry] = []

    # ── Path validation ───────────────────────────────────────────────────

    def _validate_path(self, path: str) -> Path:
        """Validate and resolve a path, ensuring it's within project root.

        Raises ValueError on path traversal attempts.
        """
        # Normalize and resolve
        resolved = (self.root / path).resolve()

        # Check it's within project root
        try:
            resolved.relative_to(self.root)
        except ValueError:
            raise ValueError(
                f"Path traversal blocked: '{path}' resolves outside project root"
            ) from None

        return resolved

    # ── Reading ───────────────────────────────────────────────────────────

    def read_file(self, path: str) -> str:
        """Read a file safely within the project root.

        Args:
            path: Relative path from project root.

        Returns:
            File content as string.

        Raises:
            ValueError: If path is outside project root.
            FileNotFoundError: If file doesn't exist.
        """
        resolved = self._validate_path(path)
        if not resolved.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        if resolved.stat().st_size > _MAX_READ_SIZE:
            raise ValueError(f"File too large to read: {path} ({resolved.stat().st_size} bytes)")
        return resolved.read_text(encoding="utf-8", errors="replace")

    def list_files(self, pattern: str = "*") -> list[str]:
        """List project files matching a glob pattern.

        Args:
            pattern: Glob pattern (e.g., "*.py", "src/**/*.ts").

        Returns:
            List of relative paths from project root.
        """
        results = []
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            # Skip hidden dirs and known non-source dirs
            parts = p.relative_to(self.root).parts
            if any(part in _SKIP_DIRS or part.startswith(".") for part in parts):
                continue
            # Skip binary extensions
            if p.suffix.lower() in _SKIP_EXTENSIONS:
                continue
            # Apply glob filter
            rel = str(p.relative_to(self.root)).replace("\\", "/")
            if fnmatch.fnmatch(rel, pattern):
                results.append(rel)

        return sorted(results)

    def file_tree(self, max_depth: int = 4) -> str:
        """Generate an ASCII file tree of the project.

        Args:
            max_depth: Maximum directory depth to show.

        Returns:
            ASCII tree string.
        """
        lines = [f"{self.root.name}/"]
        self._tree_recurse(self.root, "", 0, max_depth, lines)
        return "\n".join(lines)

    def _tree_recurse(
        self, dir_path: Path, prefix: str, depth: int, max_depth: int, lines: list[str]
    ) -> None:
        if depth >= max_depth:
            return
        entries = sorted(dir_path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        # Filter out skip dirs
        entries = [
            e for e in entries
            if e.name not in _SKIP_DIRS and not e.name.startswith(".")
        ]
        for i, entry in enumerate(entries):
            is_last = i == len(entries) - 1
            connector = "└── " if is_last else "├── "
            if entry.is_dir():
                lines.append(f"{prefix}{connector}{entry.name}/")
                ext_prefix = prefix + ("    " if is_last else "│   ")
                self._tree_recurse(entry, ext_prefix, depth + 1, max_depth, lines)
            elif entry.suffix.lower() not in _SKIP_EXTENSIONS:
                lines.append(f"{prefix}{connector}{entry.name}")

    def scan(self, target_file: str | None = None) -> dict:
        """Scan the project and build context for agents.

        Args:
            target_file: Optional file to focus on (builds dependency graph).

        Returns:
            Dict with tree, files, and optionally focused context.
        """
        result: dict = {
            "tree": self.file_tree(),
            "files": self.list_files("*.py") + self.list_files("*.js") + self.list_files("*.ts"),
        }

        if target_file:
            try:
                from crosscheck.context_builder import RepoContextBuilder
                builder = RepoContextBuilder(root=str(self.root), max_files=15, max_chars=60_000)
                ctx = builder.build(target_file)
                result["context"] = ctx.context_block
                result["dependencies"] = ctx.files_included
            except Exception as e:
                logger.warning("Context builder failed: %s", e)

        return result

    # ── Writing (proposals) ───────────────────────────────────────────────

    def propose_changes(self, code_blocks: list) -> ChangeProposal:
        """Create a change proposal from code blocks.

        Code blocks come from the Coder agent's output. Each block has:
          filename, language, content, action

        Returns:
            ChangeProposal with diffs generated for each change.
        """
        from crosscheck.diff import unified_diff

        changes = []
        for block in code_blocks:
            filename = block.filename if hasattr(block, "filename") else block.get("filename", "")
            content = block.content if hasattr(block, "content") else block.get("content", "")
            action = block.action if hasattr(block, "action") else block.get("action", "create")
            language = block.language if hasattr(block, "language") else block.get("language", "")

            if not filename:
                continue

            # Read original if editing
            original = ""
            try:
                resolved = self._validate_path(filename)
                if resolved.is_file():
                    original = resolved.read_text(encoding="utf-8", errors="replace")
                    if action == "create":
                        action = "edit"  # File exists, so this is an edit
            except (ValueError, FileNotFoundError):
                pass

            # Generate diff
            diff = ""
            if action == "edit" and original:
                diff = unified_diff(original, content, fromfile=filename, tofile=filename)
            elif action == "create":
                diff = unified_diff("", content, fromfile="/dev/null", tofile=filename)
            elif action == "delete":
                diff = unified_diff(original, "", fromfile=filename, tofile="/dev/null")

            changes.append(FileChange(
                filename=filename,
                action=action,
                content=content,
                original=original,
                diff=diff,
                language=language,
            ))

        proposal = ChangeProposal(changes=changes)
        self.proposals[proposal.id] = proposal
        return proposal

    def apply_proposal(self, proposal_id: str) -> list[str]:
        """Apply an accepted proposal — write files to disk.

        Args:
            proposal_id: ID of the proposal to apply.

        Returns:
            List of filenames that were written.

        Raises:
            ValueError: If proposal not found or not pending.
        """
        proposal = self.proposals.get(proposal_id)
        if not proposal:
            raise ValueError(f"Proposal not found: {proposal_id}")
        if proposal.status != "pending":
            raise ValueError(f"Proposal already {proposal.status}: {proposal_id}")

        applied_files = []
        for change in proposal.changes:
            resolved = self._validate_path(change.filename)

            # Record undo entry
            existed = resolved.is_file()
            original = ""
            if existed:
                original = resolved.read_text(encoding="utf-8", errors="replace")

            self.undo_log.append(UndoEntry(
                proposal_id=proposal_id,
                filename=change.filename,
                action=change.action,
                original_content=original,
                existed=existed,
            ))

            # Apply the change
            if change.action in ("create", "edit"):
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(change.content, encoding="utf-8")
                applied_files.append(change.filename)
                logger.info("Applied %s: %s", change.action, change.filename)
            elif change.action == "delete":
                if resolved.is_file():
                    resolved.unlink()
                    applied_files.append(change.filename)
                    logger.info("Deleted: %s", change.filename)

        proposal.status = "accepted"
        return applied_files

    def rollback(self, proposal_id: str) -> list[str]:
        """Rollback all changes from a proposal.

        Args:
            proposal_id: ID of the proposal to rollback.

        Returns:
            List of filenames that were rolled back.
        """
        entries = [e for e in self.undo_log if e.proposal_id == proposal_id]
        if not entries:
            raise ValueError(f"No undo entries for proposal: {proposal_id}")

        rolled_back = []
        # Reverse order to undo correctly
        for entry in reversed(entries):
            resolved = self._validate_path(entry.filename)

            if entry.action in ("create", "edit"):
                if entry.existed:
                    # Restore original content
                    resolved.write_text(entry.original_content, encoding="utf-8")
                else:
                    # File was created — delete it
                    if resolved.is_file():
                        resolved.unlink()
            elif entry.action == "delete":
                # File was deleted — restore it
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(entry.original_content, encoding="utf-8")

            rolled_back.append(entry.filename)
            logger.info("Rolled back: %s", entry.filename)

        proposal = self.proposals.get(proposal_id)
        if proposal:
            proposal.status = "rolled_back"

        return rolled_back

    # ── Tool execution ────────────────────────────────────────────────────

    async def run_tools(self, tools: list[str] | None = None) -> list:
        """Run safety tools (pytest, ruff, mypy, etc.) in the project.

        Args:
            tools: Specific tools to run. None = auto-detect available.

        Returns:
            List of ToolResult objects from ExecutionSandbox.
        """
        from crosscheck.sandbox import ExecutionSandbox

        sandbox = ExecutionSandbox(project_root=str(self.root))
        available = await sandbox.available_tools()

        if tools:
            available = [t for t in available if t in tools]

        if not available:
            logger.info("No tools available in project: %s", self.root)
            return []

        results = await sandbox.run_all(available, parallel=True)
        return results

    def format_tool_results(self, results: list) -> str:
        """Format tool results for injection into agent prompts."""
        from crosscheck.sandbox import ExecutionSandbox
        return ExecutionSandbox.format_for_prompt(results)
