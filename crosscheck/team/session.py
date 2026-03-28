"""
crosscheck.team.session
-----------------------
TeamSession: the brain of v2.0.0.

Orchestrates an AI dev team through unified phases:
    PLANNING -> DISCUSSION -> CODING -> REVIEW -> (iterate or DONE)

Claude codes. Others advise. The user can jump in at any point.

Architecture ported from Android AI Bridge:
- DebateViewModel round flow -> phases with max_rounds
- CollabViewModel consensus -> DISCUSSION phase (parallel calls)
- SolveViewModel analytical angles -> each role gets specialized prompt
- Privacy-first context -> for_model_context(last_n=6)
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from typing import AsyncIterator, Optional

from crosscheck.team.roles import TeamRole, RoleSpec, DEFAULT_TEAM, build_team
from crosscheck.team.chat import (
    SessionPhase, TeamMessage, ChatHistory, CodeBlock, extract_code_blocks,
)
from crosscheck.team.command_parser import CommandParser
from crosscheck.team.language import LanguageDetector

logger = logging.getLogger(__name__)


class ReviewResult:
    """Aggregated result from the REVIEW phase."""

    def __init__(self, messages: list[TeamMessage]):
        self.messages = messages
        self.issues: list[str] = []
        for msg in messages:
            content_lower = msg.content.lower()
            if any(kw in content_lower for kw in [
                "bug", "vulnerability", "issue", "problem", "fix",
                "error", "wrong", "incorrect", "missing",
            ]):
                self.issues.append(f"[{msg.display_name}] {msg.content[:200]}")

    @property
    def approved(self) -> bool:
        """True if no significant issues found by any reviewer."""
        return len(self.issues) == 0


class TeamSession:
    """Orchestrate an AI dev team session.

    Args:
        client: OpenRouterClient instance (from crosscheck.client).
        team: List of RoleSpec. Defaults to DEFAULT_TEAM.
        max_rounds: Maximum coding/review iterations. Default 3.

    Usage:
        async with OpenRouterClient(api_key) as client:
            session = TeamSession(client=client)
            history = await session.run(task="Add input validation to app.py")
    """

    def __init__(
        self,
        client,
        team: Optional[list[RoleSpec]] = None,
        max_rounds: int = 3,
    ):
        self.client = client
        self.team = team or list(DEFAULT_TEAM)
        self.max_rounds = max_rounds
        self.history = ChatHistory(
            session_id=str(uuid.uuid4())[:8],
        )
        self.phase = SessionPhase.PLANNING
        self._message_queue: asyncio.Queue[TeamMessage] = asyncio.Queue()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run(self, task: str, code: str = "") -> ChatHistory:
        """Execute a full team session.

        Args:
            task: What the team should do (e.g. "Add authentication to app.py").
            code: Optional existing code to work with.

        Returns:
            ChatHistory with the full conversation transcript.
        """
        self.history.topic = task
        logger.info("TeamSession started: %s", task)

        # Detect language for all agent prompts
        language = LanguageDetector.detect(task)
        lang_instruction = LanguageDetector.get_instruction(language)

        # Phase 1: PLANNING
        self.phase = SessionPhase.PLANNING
        plan = await self._run_planning(task, code, lang_instruction)

        # Iterate: DISCUSSION -> CODING -> REVIEW
        for round_num in range(self.max_rounds):
            logger.info("Round %d/%d", round_num + 1, self.max_rounds)

            # Phase 2: DISCUSSION (parallel)
            self.phase = SessionPhase.DISCUSSION
            await self._run_discussion(task, plan, code, round_num, lang_instruction)

            # Phase 3: CODING (sequential — CODER only)
            self.phase = SessionPhase.CODING
            code_output = await self._run_coding(task, round_num, lang_instruction)

            # Phase 4: REVIEW (parallel)
            self.phase = SessionPhase.REVIEW
            review = await self._run_review(code_output, round_num, lang_instruction)

            if review.approved:
                logger.info("Team approved at round %d", round_num + 1)
                break
            else:
                logger.info("Issues found, iterating: %s", review.issues[:2])

        self.phase = SessionPhase.DONE
        logger.info("TeamSession complete: %d messages", len(self.history.messages))
        return self.history

    async def inject_human_message(
        self, content: str, target: Optional[str] = None
    ) -> list[TeamMessage]:
        """User speaks to the team.

        Args:
            content: User's message (may contain @mentions).
            target: Optional explicit target (e.g. "@debugger").

        Returns:
            List of response messages from targeted agents.
        """
        # Parse @mentions
        parse_result = CommandParser.parse(content)

        human_msg = TeamMessage(
            role="human",
            model_id="human",
            display_name="Human",
            content=content,
            phase=self.phase,
            target=target,
        )
        self.history.add(human_msg)
        await self._emit(human_msg)

        # Determine language
        language = LanguageDetector.detect(content)
        lang_instruction = LanguageDetector.get_instruction(language)

        responses: list[TeamMessage] = []

        if parse_result.has_mentions:
            # Route to specific agents
            for role in parse_result.targets:
                spec = self._get_spec(role)
                if spec:
                    msg = await self._call_agent(
                        spec, parse_result.global_message, lang_instruction,
                    )
                    responses.append(msg)
        else:
            # Broadcast to all (parallel)
            tasks = [
                self._call_agent(spec, content, lang_instruction)
                for spec in self.team
            ]
            responses = list(await asyncio.gather(*tasks))

        return responses

    async def stream(self) -> AsyncIterator[TeamMessage]:
        """Async generator for WebSocket streaming.

        Yields TeamMessages as they're produced during run().
        Call this concurrently with run() to get real-time updates.
        """
        while True:
            msg = await self._message_queue.get()
            yield msg
            if self.phase == SessionPhase.DONE:
                break

    # ------------------------------------------------------------------
    # Phase runners
    # ------------------------------------------------------------------

    async def _run_planning(
        self, task: str, code: str, lang_instruction: str,
    ) -> str:
        """PLANNING: Planner decomposes the task."""
        planner = self._get_spec(TeamRole.PLANNER)
        if planner is None:
            return task

        prompt = f"Task: {task}"
        if code:
            prompt += f"\n\nExisting code:\n```\n{code}\n```"
        prompt += "\n\nBreak this into subtasks and assign to team members."

        msg = await self._call_agent(planner, prompt, lang_instruction)
        return msg.content

    async def _run_discussion(
        self,
        task: str,
        plan: str,
        code: str,
        round_num: int,
        lang_instruction: str,
    ) -> list[TeamMessage]:
        """DISCUSSION: All advisors share perspective (parallel)."""
        advisors = [s for s in self.team if s.role != TeamRole.CODER]

        prompt = f"Task: {task}\n\nPlan:\n{plan}"
        if code:
            prompt += f"\n\nExisting code:\n```\n{code}\n```"
        prompt += f"\n\nRound {round_num + 1}/{self.max_rounds}. Share your perspective."

        tasks = [
            self._call_agent(spec, prompt, lang_instruction, round_num)
            for spec in advisors
        ]
        results = list(await asyncio.gather(*tasks))
        return results

    async def _run_coding(
        self, task: str, round_num: int, lang_instruction: str,
    ) -> list[CodeBlock]:
        """CODING: Coder writes code based on team discussion."""
        coder = self._get_spec(TeamRole.CODER)
        if coder is None:
            return []

        # Build context from recent discussion
        context = self.history.for_model_context(TeamRole.CODER.value, last_n=10)
        discussion_summary = "\n".join(
            f"{d['content']}" for d in context[-8:]
        )

        prompt = (
            f"Task: {task}\n\n"
            f"Team discussion:\n{discussion_summary}\n\n"
            f"Write the complete code. Use fenced blocks with filename comments:\n"
            f"```python\n# filename: path/to/file.py\n<code>\n```"
        )

        msg = await self._call_agent(
            coder, prompt, lang_instruction, round_num,
        )

        # Extract code blocks
        blocks = extract_code_blocks(msg.content)
        if blocks:
            msg.code_blocks = blocks
            msg.is_code_output = True

        return blocks

    async def _run_review(
        self,
        code_blocks: list[CodeBlock],
        round_num: int,
        lang_instruction: str,
    ) -> ReviewResult:
        """REVIEW: All advisors review the code (parallel)."""
        reviewers = [s for s in self.team if s.role != TeamRole.CODER]

        code_text = "\n\n".join(
            f"```{cb.language}\n# {cb.filename}\n{cb.content}\n```"
            for cb in code_blocks
        ) if code_blocks else "(No code produced yet)"

        prompt = (
            f"Review this code from your specialized perspective:\n\n{code_text}\n\n"
            f"List any issues you find. If the code is good, say APPROVED."
        )

        tasks = [
            self._call_agent(spec, prompt, lang_instruction, round_num)
            for spec in reviewers
        ]
        results = list(await asyncio.gather(*tasks))
        return ReviewResult(results)

    # ------------------------------------------------------------------
    # Agent call
    # ------------------------------------------------------------------

    async def _call_agent(
        self,
        spec: RoleSpec,
        user_content: str,
        lang_instruction: str = "",
        round_num: int = 0,
    ) -> TeamMessage:
        """Call a single agent via OpenRouter and record the response."""
        system_prompt = spec.system_prompt
        if lang_instruction:
            system_prompt += f"\n\n{lang_instruction}"

        # Build messages for the API call
        messages = [
            {"role": "system", "content": system_prompt},
        ]

        # Add recent context (privacy-first: last 6 messages)
        context = self.history.for_model_context(spec.role.value, last_n=6)
        messages.extend(context)

        # Add the current prompt
        messages.append({"role": "user", "content": user_content})

        # Emit typing indicator
        typing_msg = TeamMessage(
            role=spec.role.value,
            model_id=spec.default_model,
            display_name=spec.display_name,
            content="",
            phase=self.phase,
            round_num=round_num,
        )
        # Typing indicator has empty content — UI shows "X is typing..."

        try:
            response = await self.client.chat(
                model=spec.default_model,
                messages=messages,
            )
        except Exception as e:
            logger.error("%s failed: %s", spec.title, e)
            response = f"[Error: {spec.title} failed — {e}]"

        msg = TeamMessage(
            role=spec.role.value,
            model_id=spec.default_model,
            display_name=spec.display_name,
            content=response,
            phase=self.phase,
            round_num=round_num,
        )

        self.history.add(msg)
        await self._emit(msg)

        logger.debug(
            "%s (%s): %s...",
            spec.title,
            spec.default_model,
            response[:80],
        )
        return msg

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_spec(self, role: TeamRole) -> Optional[RoleSpec]:
        """Find spec by role."""
        for spec in self.team:
            if spec.role == role:
                return spec
        return None

    async def _emit(self, msg: TeamMessage) -> None:
        """Emit message to the streaming queue."""
        try:
            self._message_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass
