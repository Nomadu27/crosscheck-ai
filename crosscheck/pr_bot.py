"""
crosscheck.pr_bot
-----------------
Phase 1 Feature 3: GitHub / GitLab PR bot mode.

Competes with CodeRabbit / Anthropic code review bots while keeping
the local observer. Two integration paths:

  A) Webhook server (FastAPI) — receive GitHub/GitLab webhook events,
     run crosscheck, post review comments back via the REST API.

  B) CLI command:
       crosscheck pr review --repo owner/repo --pr 42
       crosscheck pr post   --repo owner/repo --pr 42 --result result.json

Design:
  - PRReviewBot wraps MultiAgentSession
  - Posts inline comments on the diff hunks that have issues
  - Posts a summary comment with the full report
  - Sets PR status check (optional, requires repo write access)
  - Supports GitHub and GitLab via provider abstraction
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
from dataclasses import dataclass, field
from typing import Optional

import httpx

from crosscheck.core    import MultiAgentSession, SessionResult
from crosscheck.models  import Mode, Task
from crosscheck.diff    import unified_diff, diff_stats


# ---------------------------------------------------------------------------
# Provider abstraction
# ---------------------------------------------------------------------------

class PRProvider:
    """Abstract base for GitHub / GitLab PR interactions."""

    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        raise NotImplementedError

    async def get_pr_files(self, repo: str, pr_number: int) -> list[dict]:
        raise NotImplementedError

    async def post_review_comment(
        self,
        repo:       str,
        pr_number:  int,
        body:       str,
        commit_sha: str,
        path:       str,
        line:       int,
    ) -> None:
        raise NotImplementedError

    async def post_summary_comment(self, repo: str, pr_number: int, body: str) -> None:
        raise NotImplementedError

    async def set_status_check(
        self,
        repo:       str,
        sha:        str,
        state:      str,   # "success" | "failure" | "pending"
        description: str,
        context:    str = "crosscheck-ai",
    ) -> None:
        raise NotImplementedError


class GitHubProvider(PRProvider):
    """GitHub REST API v3 integration."""

    BASE = "https://api.github.com"

    def __init__(self, token: str):
        self.token = token
        self._headers = {
            "Authorization":        f"Bearer {token}",
            "Accept":               "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(
                url,
                headers={**self._headers, "Accept": "application/vnd.github.diff"},
            )
            r.raise_for_status()
            return r.text

    async def get_pr_files(self, repo: str, pr_number: int) -> list[dict]:
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}/files"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json()

    async def get_pr_head_sha(self, repo: str, pr_number: int) -> str:
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json()["head"]["sha"]

    async def post_review_comment(
        self,
        repo:       str,
        pr_number:  int,
        body:       str,
        commit_sha: str,
        path:       str,
        line:       int,
    ) -> None:
        url = f"{self.BASE}/repos/{repo}/pulls/{pr_number}/comments"
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(url, headers=self._headers, json={
                "body":        body,
                "commit_id":   commit_sha,
                "path":        path,
                "line":        line,
                "side":        "RIGHT",
            })

    async def post_summary_comment(self, repo: str, pr_number: int, body: str) -> None:
        url = f"{self.BASE}/repos/{repo}/issues/{pr_number}/comments"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self._headers, json={"body": body})
            r.raise_for_status()

    async def set_status_check(
        self,
        repo:        str,
        sha:         str,
        state:       str,
        description: str,
        context:     str = "crosscheck-ai",
    ) -> None:
        url = f"{self.BASE}/repos/{repo}/statuses/{sha}"
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(url, headers=self._headers, json={
                "state":       state,
                "description": description,
                "context":     context,
            })


class GitLabProvider(PRProvider):
    """GitLab REST API v4 integration."""

    def __init__(self, token: str, base_url: str = "https://gitlab.com"):
        self.token    = token
        self.base_url = base_url.rstrip("/")
        self._headers = {
            "PRIVATE-TOKEN": token,
            "Content-Type":  "application/json",
        }

    def _api(self, path: str) -> str:
        return f"{self.base_url}/api/v4{path}"

    async def get_pr_diff(self, repo: str, pr_number: int) -> str:
        # repo = "group/project" → URL-encoded
        encoded = repo.replace("/", "%2F")
        url     = self._api(f"/projects/{encoded}/merge_requests/{pr_number}/diffs")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            diffs = r.json()
            return "\n".join(d.get("diff", "") for d in diffs)

    async def get_pr_files(self, repo: str, pr_number: int) -> list[dict]:
        encoded = repo.replace("/", "%2F")
        url     = self._api(f"/projects/{encoded}/merge_requests/{pr_number}/changes")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json().get("changes", [])

    async def post_summary_comment(self, repo: str, pr_number: int, body: str) -> None:
        encoded = repo.replace("/", "%2F")
        url     = self._api(f"/projects/{encoded}/merge_requests/{pr_number}/notes")
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(url, headers=self._headers, json={"body": body})
            r.raise_for_status()

    # Stub implementations for interface compliance
    async def post_review_comment(self, repo, pr_number, body, commit_sha, path, line):
        await self.post_summary_comment(repo, pr_number, f"**{path}:{line}**\n\n{body}")

    async def set_status_check(self, repo, sha, state, description, context="crosscheck-ai"):
        pass  # GitLab uses pipeline statuses (more complex — out of scope for v1)


# ---------------------------------------------------------------------------
# PR Bot
# ---------------------------------------------------------------------------

@dataclass
class PRReviewConfig:
    repo:            str              # "owner/repo" or "group/project"
    pr_number:       int
    provider:        str = "github"   # "github" | "gitlab"
    max_rounds:      int = 3
    mode:            str = "balanced"
    post_inline:     bool = True      # post inline diff comments
    post_summary:    bool = True      # post a summary comment
    set_status:      bool = True      # set commit status check
    max_files:       int  = 10        # max files to review in one PR
    max_diff_chars:  int  = 40_000    # truncate huge diffs


class PRReviewBot:
    """
    Runs crosscheck on a PR diff and posts results back to GitHub/GitLab.

    Usage:
        bot = PRReviewBot(
            crosscheck_api_key = "sk-or-...",
            github_token       = "ghp_...",
        )
        result = await bot.review_pr("owner/repo", 42)
    """

    def __init__(
        self,
        crosscheck_api_key: str,
        github_token:       Optional[str] = None,
        gitlab_token:       Optional[str] = None,
        gitlab_url:         str           = "https://gitlab.com",
    ):
        self.api_key       = crosscheck_api_key
        self._gh_token     = github_token or os.environ.get("GITHUB_TOKEN", "")
        self._gl_token     = gitlab_token or os.environ.get("GITLAB_TOKEN", "")
        self._gl_url       = gitlab_url

    def _get_provider(self, name: str) -> PRProvider:
        if name == "gitlab":
            return GitLabProvider(self._gl_token, self._gl_url)
        return GitHubProvider(self._gh_token)

    async def review_pr(self, config: PRReviewConfig) -> SessionResult:
        """
        Full PR review pipeline:
          1. Fetch diff from GitHub/GitLab
          2. Run crosscheck on the diff
          3. Post inline comments and summary
          4. Set commit status
        """
        provider = self._get_provider(config.provider)

        # Fetch diff
        diff_text = await provider.get_pr_diff(config.repo, config.pr_number)
        if len(diff_text) > config.max_diff_chars:
            diff_text = diff_text[:config.max_diff_chars] + "\n... [diff truncated]"

        if not diff_text.strip():
            return None

        # Set pending status
        sha = ""
        if config.set_status and isinstance(provider, GitHubProvider):
            sha = await provider.get_pr_head_sha(config.repo, config.pr_number)
            await provider.set_status_check(
                config.repo, sha, "pending",
                f"crosscheck-ai reviewing PR #{config.pr_number}…",
            )

        # Run crosscheck
        session = MultiAgentSession(
            api_key     = self.api_key,
            review_type = Task.CODE,
            mode        = Mode(config.mode),
            max_rounds  = config.max_rounds,
        )
        content = (
            f"=== PR DIFF: {config.repo} #{config.pr_number} ===\n"
            f"{diff_text}"
        )
        result = await session.run_async(content)

        # Post summary comment
        if config.post_summary:
            summary_md = self._format_summary(result, config)
            await provider.post_summary_comment(config.repo, config.pr_number, summary_md)

        # Post inline comments on specific files
        if config.post_inline and sha and isinstance(provider, GitHubProvider):
            files = await provider.get_pr_files(config.repo, config.pr_number)
            await self._post_inline_comments(provider, config, result, files, sha)

        # Set final status
        if config.set_status and sha and isinstance(provider, GitHubProvider):
            state = "success" if result.verdict == "APPROVED" else "failure"
            desc  = (
                f"✅ Approved ({result.final_score:.1f}/10)"
                if result.verdict == "APPROVED"
                else f"⚠️ Issues found ({result.final_score:.1f}/10) — see comments"
            )
            await provider.set_status_check(config.repo, sha, state, desc)

        return result

    @staticmethod
    def _format_summary(result: SessionResult, config: PRReviewConfig) -> str:
        verdict_icon = "✅" if result.verdict == "APPROVED" else "⚠️"
        lines = [
            f"## {verdict_icon} crosscheck-ai PR Review",
            "",
            f"**Verdict:** {result.verdict}  |  "
            f"**Score:** {result.final_score:.1f}/10  |  "
            f"**Rounds:** {result.total_rounds}  |  "
            f"**Cost:** ${result.cost_usd:.4f}",
            "",
        ]

        last_synthesis = result.rounds[-1].synthesis if result.rounds else None
        if last_synthesis:
            lines += [f"**Summary:** {last_synthesis.summary}", ""]

            if last_synthesis.critical_issues:
                lines += ["### 🚨 Critical Issues", ""]
                for issue in last_synthesis.critical_issues:
                    lines.append(f"- {issue}")
                lines.append("")

            if last_synthesis.approved_aspects:
                lines += ["### ✅ Approved Aspects", ""]
                for aspect in last_synthesis.approved_aspects:
                    lines.append(f"- {aspect}")
                lines.append("")

        # Analyzer breakdown
        if result.analyzer_reports:
            lines += ["### Analyzer Breakdown", "", "| Model | Angle | Score | Verdict |", "|---|---|---|---|"]
            for r in result.analyzer_reports:
                model = r.model_id.split("/")[-1]
                lines.append(f"| {model} | {r.angle} | {r.score:.1f}/10 | {r.verdict} |")
            lines.append("")

        lines += [
            "---",
            f"*Powered by [crosscheck-ai](https://github.com/Nomadu27/crosscheck-ai) — "
            f"multi-agent AI code review*",
        ]
        return "\n".join(lines)

    async def _post_inline_comments(
        self,
        provider:  GitHubProvider,
        config:    PRReviewConfig,
        result:    SessionResult,
        files:     list[dict],
        sha:       str,
    ) -> None:
        """Map issues to specific file/line locations and post as inline comments."""
        file_paths = {f["filename"] for f in files}

        for rnd in result.rounds:
            for report in rnd.analyzer_reports:
                for issue in report.issues:
                    if issue.get("severity") not in ("critical", "major"):
                        continue
                    location = issue.get("location", "")
                    # Try to extract filename from location string
                    matched_file = None
                    for fp in file_paths:
                        if fp in location or location in fp:
                            matched_file = fp
                            break

                    if not matched_file:
                        continue

                    # Extract line number
                    line_num = 1
                    import re
                    m = re.search(r"line[s]?\s*(\d+)", location, re.I)
                    if m:
                        line_num = int(m.group(1))

                    severity = issue.get("severity", "major").upper()
                    body = (
                        f"**[{severity}]** {issue.get('description', '')}\n\n"
                        f"**Fix:** {issue.get('fix', 'See summary comment.')}\n\n"
                        f"*Raised by crosscheck-ai [{report.model_id.split('/')[-1]}]*"
                    )

                    try:
                        await provider.post_review_comment(
                            config.repo, config.pr_number,
                            body, sha, matched_file, line_num,
                        )
                    except Exception:
                        pass  # Inline comment placement can fail — summary is the fallback


# ---------------------------------------------------------------------------
# Webhook server (FastAPI)
# ---------------------------------------------------------------------------

def create_webhook_app(
    crosscheck_api_key: str,
    github_token:       str,
    webhook_secret:     str = "",
    mode:               str = "balanced",
):
    """
    Create a FastAPI webhook handler for GitHub PR events.
    
    Usage:
        app = create_webhook_app(api_key, github_token, webhook_secret)
        uvicorn.run(app, host="0.0.0.0", port=8080)
    """
    try:
        from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
        import hmac
        import hashlib
    except ImportError:
        raise ImportError(
            "FastAPI required for webhook mode.\n"
            "Install: pip install 'crosscheck-ai[webhook]'"
        )

    app = FastAPI(title="crosscheck-ai PR Bot")
    bot = PRReviewBot(crosscheck_api_key=crosscheck_api_key, github_token=github_token)

    def _verify_signature(payload: bytes, signature: str) -> bool:
        if not webhook_secret:
            return True
        expected = hmac.new(
            webhook_secret.encode(), payload, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(f"sha256={expected}", signature)

    async def _handle_pr(repo: str, pr_number: int) -> None:
        config = PRReviewConfig(
            repo      = repo,
            pr_number = pr_number,
            mode      = mode,
        )
        await bot.review_pr(config)

    @app.post("/webhook/github")
    async def github_webhook(request: Request, background_tasks: BackgroundTasks):
        payload_bytes = await request.body()
        sig = request.headers.get("X-Hub-Signature-256", "")
        if not _verify_signature(payload_bytes, sig):
            raise HTTPException(status_code=403, detail="Invalid signature")

        event = request.headers.get("X-GitHub-Event", "")
        body  = await request.json()

        if event == "pull_request" and body.get("action") in ("opened", "synchronize"):
            repo      = body["repository"]["full_name"]
            pr_number = body["pull_request"]["number"]
            background_tasks.add_task(_handle_pr, repo, pr_number)

        return {"status": "ok"}

    return app
