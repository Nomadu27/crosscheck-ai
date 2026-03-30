"""
crosscheck.sandbox
------------------
Phase 1 Feature 4: Tool calling / execution sandbox.

Agents can invoke real linters, test runners, and security scanners,
then feed actual tool output back into the analysis pipeline.

Supported tools (auto-detected from project):
  - pytest          Python tests
  - ruff check      Python linting
  - eslint          JavaScript/TypeScript linting
  - mypy            Python type checking
  - bandit          Python security scanning
  - semgrep         Multi-language SAST
  - cargo test      Rust tests
  - go test         Go tests
  - npm test        Node.js tests

Usage:
    sandbox = ExecutionSandbox(project_root=".")
    results = await sandbox.run_all(["pytest", "ruff", "mypy"])
    feedback = sandbox.format_for_prompt(results)
"""

from __future__ import annotations

import asyncio
import shutil
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class ToolResult:
    tool:        str
    command:     list[str]
    returncode:  int
    stdout:      str
    stderr:      str
    success:     bool
    duration_sec: float = 0.0
    truncated:   bool   = False

    @property
    def output(self) -> str:
        return (self.stdout + self.stderr).strip()

    @property
    def passed(self) -> bool:
        return self.returncode == 0


# ---------------------------------------------------------------------------
# Tool definitions
# ---------------------------------------------------------------------------

@dataclass
class ToolDef:
    name:         str
    detect_files: list[str]     # files that signal this tool is usable
    detect_cmd:   str           # binary to check via shutil.which
    command:      list[str]     # command to run (cwd = project root)
    languages:    list[str]     # applicable file extensions
    timeout:      int = 60      # seconds
    success_on:   set[int] = field(default_factory=lambda: {0})


_TOOLS: dict[str, ToolDef] = {
    "pytest": ToolDef(
        name         = "pytest",
        detect_files = ["pytest.ini", "pyproject.toml", "setup.cfg", "conftest.py"],
        detect_cmd   = "pytest",
        command      = ["pytest", "--tb=short", "-q", "--no-header"],
        languages    = [".py"],
        timeout      = 120,
        success_on   = {0, 5},   # 5 = no tests collected
    ),
    "ruff": ToolDef(
        name         = "ruff",
        detect_files = ["pyproject.toml", "ruff.toml", ".ruff.toml"],
        detect_cmd   = "ruff",
        command      = ["ruff", "check", "--output-format", "concise", "."],
        languages    = [".py"],
        timeout      = 30,
        success_on   = {0},
    ),
    "mypy": ToolDef(
        name         = "mypy",
        detect_files = ["mypy.ini", "setup.cfg", "pyproject.toml"],
        detect_cmd   = "mypy",
        command      = ["mypy", "--ignore-missing-imports", "--no-error-summary", "."],
        languages    = [".py"],
        timeout      = 60,
        success_on   = {0},
    ),
    "bandit": ToolDef(
        name         = "bandit",
        detect_files = [],
        detect_cmd   = "bandit",
        command      = ["bandit", "-r", ".", "-q", "-f", "text", "-ll"],
        languages    = [".py"],
        timeout      = 60,
        success_on   = {0, 1},   # 1 = issues found (still valid output)
    ),
    "eslint": ToolDef(
        name         = "eslint",
        detect_files = [".eslintrc.js", ".eslintrc.json", ".eslintrc.yml", "eslint.config.js"],
        detect_cmd   = "eslint",
        command      = ["eslint", "--format", "compact", "."],
        languages    = [".js", ".jsx", ".ts", ".tsx"],
        timeout      = 60,
        success_on   = {0, 1},
    ),
    "semgrep": ToolDef(
        name         = "semgrep",
        detect_files = [".semgrep.yml", "semgrep.yml"],
        detect_cmd   = "semgrep",
        command      = ["semgrep", "--config", "auto", "--quiet", "--json"],
        languages    = [".py", ".js", ".ts", ".go", ".java", ".cpp"],
        timeout      = 120,
        success_on   = {0, 1},
    ),
    "cargo test": ToolDef(
        name         = "cargo test",
        detect_files = ["Cargo.toml"],
        detect_cmd   = "cargo",
        command      = ["cargo", "test", "--", "--quiet"],
        languages    = [".rs"],
        timeout      = 180,
        success_on   = {0},
    ),
    "go test": ToolDef(
        name         = "go test",
        detect_files = ["go.mod"],
        detect_cmd   = "go",
        command      = ["go", "test", "./...", "-count=1"],
        languages    = [".go"],
        timeout      = 120,
        success_on   = {0},
    ),
    "npm test": ToolDef(
        name         = "npm test",
        detect_files = ["package.json"],
        detect_cmd   = "npm",
        command      = ["npm", "test", "--", "--watchAll=false", "--passWithNoTests"],
        languages    = [".js", ".jsx", ".ts", ".tsx"],
        timeout      = 120,
        success_on   = {0},
    ),
}


# ---------------------------------------------------------------------------
# Sandbox
# ---------------------------------------------------------------------------

class ExecutionSandbox:
    """
    Discovers and runs developer tools in a project directory.
    Results are structured for injection into the analysis pipeline.
    """

    MAX_OUTPUT_CHARS = 8_000   # truncate long tool output

    def __init__(
        self,
        project_root: str | Path = ".",
        timeout_override: int | None = None,
    ):
        self.root             = Path(project_root).resolve()
        self.timeout_override = timeout_override

    # ── Discovery ────────────────────────────────────────────────────────

    def available_tools(self, target_extensions: list[str] | None = None) -> list[str]:
        """
        Return names of tools that are installed and applicable to this project.
        Filters by target_extensions if provided (e.g. ['.py', '.ts']).
        """
        available = []
        for name, tool in _TOOLS.items():
            if not shutil.which(tool.detect_cmd):
                continue  # binary not installed
            if target_extensions:
                if not any(ext in tool.languages for ext in target_extensions):
                    continue
            # Check for project config files
            if tool.detect_files:
                found = any((self.root / f).exists() for f in tool.detect_files)
                if not found:
                    continue
            available.append(name)
        return available

    # ── Execution ─────────────────────────────────────────────────────────

    async def run_tool(self, tool_name: str) -> ToolResult:
        """Run a single tool and return its result."""
        tool = _TOOLS.get(tool_name)
        if not tool:
            return ToolResult(
                tool=tool_name, command=[], returncode=-1,
                stdout="", stderr=f"Unknown tool: {tool_name}",
                success=False,
            )

        timeout = self.timeout_override or tool.timeout
        import time
        t0 = time.monotonic()

        try:
            proc = await asyncio.create_subprocess_exec(
                *tool.command,
                cwd    = str(self.root),
                stdout = asyncio.subprocess.PIPE,
                stderr = asyncio.subprocess.PIPE,
            )
            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                return ToolResult(
                    tool=tool_name, command=tool.command,
                    returncode=-1, stdout="", stderr=f"Timed out after {timeout}s",
                    success=False, duration_sec=timeout,
                )

            stdout    = stdout_b.decode("utf-8", errors="replace")
            stderr    = stderr_b.decode("utf-8", errors="replace")
            truncated = False

            combined = stdout + stderr
            if len(combined) > self.MAX_OUTPUT_CHARS:
                truncated = True
                stdout    = (stdout + stderr)[:self.MAX_OUTPUT_CHARS]
                stderr    = ""

            duration = time.monotonic() - t0
            return ToolResult(
                tool        = tool_name,
                command     = tool.command,
                returncode  = proc.returncode,
                stdout      = stdout,
                stderr      = stderr,
                success     = proc.returncode in tool.success_on,
                duration_sec= duration,
                truncated   = truncated,
            )

        except FileNotFoundError:
            return ToolResult(
                tool=tool_name, command=tool.command, returncode=-1,
                stdout="", stderr=f"Command not found: {tool.command[0]}",
                success=False,
            )

    async def run_all(
        self,
        tools: list[str] | None = None,
        parallel: bool = True,
    ) -> list[ToolResult]:
        """
        Run all (or specified) available tools.
        Returns results in order.
        """
        if tools is None:
            tools = self.available_tools()

        if not tools:
            return []

        if parallel:
            results = await asyncio.gather(*[self.run_tool(t) for t in tools])
        else:
            results = []
            for t in tools:
                results.append(await self.run_tool(t))

        return list(results)

    # ── Formatting for prompt injection ──────────────────────────────────

    @staticmethod
    def format_for_prompt(results: list[ToolResult]) -> str:
        """
        Format tool results as a prompt section injected before analysis.
        """
        if not results:
            return ""

        lines = ["=== AUTOMATED TOOL RESULTS ===", ""]

        for r in results:
            status = "✅ PASS" if r.passed else "❌ FAIL"
            cmd_str = " ".join(r.command)
            lines += [
                f"Tool: {r.tool} ({cmd_str})",
                f"Status: {status}  |  Exit code: {r.returncode}  |  Time: {r.duration_sec:.1f}s",
            ]
            if r.output:
                output = r.output
                if r.truncated:
                    output += "\n[... output truncated ...]"
                lines += ["Output:", output, ""]
            else:
                lines += ["Output: (no output)", ""]

        lines.append("=== END TOOL RESULTS ===")
        return "\n".join(lines)

    @staticmethod
    def issues_from_results(results: list[ToolResult]) -> list[dict]:
        """
        Convert tool failures into structured issue dicts compatible with
        the crosscheck issue format (for injection into analyzer reports).
        """
        issues = []
        for r in results:
            if r.passed:
                continue
            issues.append({
                "severity":    "major",
                "location":    r.tool,
                "description": f"{r.tool} failed (exit {r.returncode}): {r.output[:300]}",
                "fix":         f"Fix {r.tool} errors before merging.",
                "source":      "tool_runner",
            })
        return issues
