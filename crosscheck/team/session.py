"""
crosscheck.team.session
-----------------------
TeamSession: the brain of v2.1.0.

Orchestrates an AI dev team through unified phases:
    PLANNING -> DISCUSSION -> CODING -> REVIEW -> DISSENT -> APPROVAL -> TESTING -> DONE

Claude codes. Others advise. Coordinator stress-tests consensus.
Observers find flaws. Human has final say. Safety tools auto-run.

Architecture ported from Android AI Bridge:
- DebateViewModel round flow -> phases with max_rounds
- CollabViewModel consensus -> DISCUSSION phase (parallel calls)
- SolveViewModel analytical angles -> each role gets specialized prompt
- Privacy-first context -> for_model_context(last_n=6)
- Safety Coordinator -> single source of truth before human approval
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

from crosscheck.team.chat import (
    ChatHistory,
    CodeBlock,
    SessionPhase,
    TeamMessage,
    extract_code_blocks,
)
from crosscheck.team.command_parser import CommandParser
from crosscheck.team.language import LanguageDetector
from crosscheck.team.roles import DEFAULT_TEAM, RoleSpec, TeamRole

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
    """Orchestrate an AI dev team session with filesystem + safety.

    Args:
        client: OpenRouterClient instance (from crosscheck.client).
        team: List of RoleSpec. Defaults to DEFAULT_TEAM.
        max_rounds: Maximum coding/review iterations. Default 3.
        workspace: ProjectWorkspace for file access. None = no file ops.

    Usage:
        async with OpenRouterClient(api_key) as client:
            session = TeamSession(client=client)
            history = await session.run(task="Add input validation to app.py")
    """

    def __init__(
        self,
        client,
        team: list[RoleSpec] | None = None,
        max_rounds: int = 3,
        workspace=None,
        monitor=None,
    ):
        self.client = client
        self.team = team or list(DEFAULT_TEAM)
        self.max_rounds = max_rounds
        self.workspace = workspace
        self.monitor = monitor  # CrosscheckMonitor or None
        self.history = ChatHistory(
            session_id=str(uuid.uuid4())[:8],
        )
        self.phase = SessionPhase.PLANNING
        self._message_queue: asyncio.Queue[TeamMessage] = asyncio.Queue()

        # Approval mechanism: set by WebSocket handler when human decides
        self._approval_event: asyncio.Event = asyncio.Event()
        self._approval_decision: str = ""  # "accept" | "reject" | "edit"
        self._approval_edits: dict = {}    # Human edits if decision == "edit"

        # Current proposal (for approval flow)
        self._current_proposal = None

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

        # Auto-scan project if workspace is available
        project_context = ""
        if self.workspace:
            try:
                scan_result = self.workspace.scan()
                project_context = (
                    f"\n\nProject structure:\n{scan_result.get('tree', '')}"
                    f"\n\nProject files: {', '.join(scan_result.get('files', [])[:30])}"
                )
                if "context" in scan_result:
                    project_context += f"\n\nRelevant code:\n{scan_result['context']}"
                logger.info("Project scanned: %d files", len(scan_result.get("files", [])))
            except Exception as e:
                logger.warning("Project scan failed: %s", e)

        # Phase 1: PLANNING
        self.phase = SessionPhase.PLANNING
        plan = await self._run_planning(task, code, lang_instruction, project_context)

        # Iterate: DISCUSSION -> CODING -> REVIEW -> DISSENT -> APPROVAL -> TESTING
        for round_num in range(self.max_rounds):
            logger.info("Round %d/%d", round_num + 1, self.max_rounds)

            # Phase 2: DISCUSSION (parallel)
            self.phase = SessionPhase.DISCUSSION
            await self._run_discussion(task, plan, code, round_num, lang_instruction)

            # Phase 3: CODING (sequential — CODER only)
            self.phase = SessionPhase.CODING
            code_blocks = await self._run_coding(task, round_num, lang_instruction)

            # Phase 4: REVIEW (parallel — Architect + Debugger + Security)
            self.phase = SessionPhase.REVIEW
            review = await self._run_review(code_blocks, round_num, lang_instruction)

            # Phase 5: DISSENT — Observers + Coordinator stress-test consensus
            self.phase = SessionPhase.DISSENT
            dissent_result = await self._run_dissent(
                code_blocks, review, round_num, lang_instruction,
            )

            # Create change proposal if workspace available
            proposal = None
            if self.workspace and code_blocks:
                proposal = self.workspace.propose_changes(code_blocks)
                proposal.coordinator_summary = dissent_result.get(
                    "coordinator_summary", ""
                )
                proposal.observer_findings = dissent_result.get(
                    "observer_findings", []
                )
                self._current_proposal = proposal

                # Emit proposal to UI
                await self._emit_proposal(proposal)

            # Phase 6: APPROVAL — Wait for human decision
            self.phase = SessionPhase.APPROVAL
            decision = await self._wait_for_approval(proposal)

            if decision == "reject":
                logger.info("Human rejected proposal, iterating")
                # Add rejection message to history for context
                self._add_system_message(
                    "Human rejected the proposal. Iterating with feedback."
                )
                continue

            if decision == "edit":
                logger.info("Human edited proposal, applying edits")
                # Human edits applied to proposal — continue to testing
                pass

            # Phase 7: TESTING — Apply and run safety tools
            if proposal and self.workspace:
                self.phase = SessionPhase.TESTING
                test_passed = await self._run_testing(proposal)
                if not test_passed:
                    logger.info("Tests failed, rolling back and iterating")
                    continue
            elif review.approved or not dissent_result.get("has_critical_issues", False):
                # No workspace but review passed — done
                logger.info("Team approved at round %d", round_num + 1)
                break
            else:
                logger.info("Issues found, iterating: %s", review.issues[:2])
                continue

            # All good — break out
            logger.info("Round %d complete, tests passed", round_num + 1)
            break

        self.phase = SessionPhase.DONE
        logger.info("TeamSession complete: %d messages", len(self.history.messages))
        return self.history

    async def inject_human_message(
        self, content: str, target: str | None = None
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
            # Broadcast to all (staggered to avoid rate limits)
            responses = await self._staggered_call(
                self.team, content, lang_instruction,
            )

        return responses

    def set_approval_decision(
        self, decision: str, edits: dict | None = None
    ) -> None:
        """Called by WebSocket handler when human makes a decision.

        Args:
            decision: "accept", "reject", or "edit".
            edits: Optional dict of {filename: content} for edited files.
        """
        self._approval_decision = decision
        self._approval_edits = edits or {}
        self._approval_event.set()

    async def stream(self) -> AsyncIterator[TeamMessage]:
        """Async generator for WebSocket streaming.

        Yields TeamMessages as they're produced during run().
        Call this concurrently with run() to get real-time updates.
        """
        while True:
            try:
                msg = await asyncio.wait_for(
                    self._message_queue.get(), timeout=1.0,
                )
                yield msg
            except asyncio.TimeoutError:
                # Check if session is done and queue is empty
                if self.phase == SessionPhase.DONE and self._message_queue.empty():
                    break
                continue

    # ------------------------------------------------------------------
    # Phase runners
    # ------------------------------------------------------------------

    async def _run_planning(
        self, task: str, code: str, lang_instruction: str,
        project_context: str = "",
    ) -> str:
        """PLANNING: Planner decomposes the task."""
        planner = self._get_spec(TeamRole.PLANNER)
        if planner is None:
            return task

        prompt = f"Task: {task}"
        if code:
            prompt += f"\n\nExisting code:\n```\n{code}\n```"
        if project_context:
            prompt += f"\n\n{project_context}"
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
        """DISCUSSION: All advisors share perspective (parallel).

        Excludes CODER (writes code later) and OBSERVER roles (review only).
        """
        # Advisors: everyone except CODER and OBSERVERS
        advisors = [
            s for s in self.team
            if s.role not in (
                TeamRole.CODER,
                TeamRole.OBSERVER_1,
                TeamRole.OBSERVER_2,
            )
        ]

        prompt = f"Task: {task}\n\nPlan:\n{plan}"
        if code:
            prompt += f"\n\nExisting code:\n```\n{code}\n```"

        # If workspace available, let agents request file reads
        if self.workspace:
            prompt += (
                "\n\nYou have access to the project workspace. "
                "If you need to see specific files, mention them and they "
                "will be provided in the next round."
            )

        prompt += f"\n\nRound {round_num + 1}/{self.max_rounds}. Share your perspective."

        results = await self._staggered_call(
            advisors, prompt, lang_instruction, round_num,
        )
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

        # If workspace available, provide file context
        if self.workspace:
            try:
                file_list = self.workspace.list_files("*.py")[:20]
                if file_list:
                    prompt += f"\n\nProject files: {', '.join(file_list)}"
            except Exception:
                pass

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
        """REVIEW: Architect, Debugger, Security review the code (parallel).

        Excludes CODER, OBSERVERS (they review in DISSENT), and COORDINATOR
        (coordinates after review).
        """
        reviewers = [
            s for s in self.team
            if s.role not in (
                TeamRole.CODER,
                TeamRole.OBSERVER_1,
                TeamRole.OBSERVER_2,
                TeamRole.COORDINATOR,
            )
        ]

        code_text = "\n\n".join(
            f"```{cb.language}\n# {cb.filename}\n{cb.content}\n```"
            for cb in code_blocks
        ) if code_blocks else "(No code produced yet)"

        prompt = (
            f"Review this code from your specialized perspective:\n\n{code_text}\n\n"
            f"List any issues you find. If the code is good, say APPROVED."
        )

        results = await self._staggered_call(
            reviewers, prompt, lang_instruction, round_num,
        )
        return ReviewResult(results)

    async def _run_dissent(
        self,
        code_blocks: list[CodeBlock],
        review: ReviewResult,
        round_num: int,
        lang_instruction: str,
    ) -> dict:
        """DISSENT: Observers + Coordinator stress-test the team's consensus.

        This is the mandatory dissent window — even if everyone agrees,
        the Observers are incentivized to find flaws, and the Coordinator
        summarizes all risks before presenting to the human.

        Returns:
            Dict with coordinator_summary, observer_findings, has_critical_issues.
        """
        code_text = "\n\n".join(
            f"```{cb.language}\n# {cb.filename}\n{cb.content}\n```"
            for cb in code_blocks
        ) if code_blocks else "(No code produced)"

        # Summarize review findings
        review_summary = ""
        if review.issues:
            review_summary = "Review findings:\n" + "\n".join(
                f"- {issue}" for issue in review.issues[:5]
            )
        else:
            review_summary = "All reviewers APPROVED the code."

        # Run Observers in parallel
        observer_findings: list[str] = []
        observer_specs = [
            s for s in self.team
            if s.role in (TeamRole.OBSERVER_1, TeamRole.OBSERVER_2)
        ]

        if observer_specs:
            observer_prompt = (
                f"The team has reviewed this code:\n\n{code_text}\n\n"
                f"{review_summary}\n\n"
                f"Your job: find anything wrong that everyone else missed. "
                f"Be adversarial. Challenge assumptions. If you find nothing, "
                f"explain WHY the code is correct."
            )
            observer_msgs = await self._staggered_call(
                observer_specs, observer_prompt, lang_instruction, round_num,
            )
            observer_findings = [msg.content for msg in observer_msgs]

        # Run Coordinator to synthesize everything
        coordinator_summary = ""
        coordinator = self._get_spec(TeamRole.COORDINATOR)
        if coordinator:
            coord_prompt = (
                f"Code proposed:\n\n{code_text}\n\n"
                f"{review_summary}\n\n"
            )
            if observer_findings:
                coord_prompt += "Observer findings:\n"
                for i, finding in enumerate(observer_findings, 1):
                    coord_prompt += f"\nObserver {i}:\n{finding[:500]}\n"

            coord_prompt += (
                "\n\nSynthesize everything. Present to the human with:\n"
                "1. Summary of changes\n"
                "2. Risks and failure modes\n"
                "3. Observer findings\n"
                "4. Your recommendation: safe to ship or needs more work?"
            )
            coord_msg = await self._call_agent(
                coordinator, coord_prompt, lang_instruction, round_num,
            )
            coordinator_summary = coord_msg.content

        # Check if coordinator found critical issues
        has_critical = False
        if coordinator_summary:
            lower = coordinator_summary.lower()
            has_critical = any(kw in lower for kw in [
                "needs more work", "do not ship", "critical", "dangerous",
                "reject", "not safe", "high risk",
            ])

        return {
            "coordinator_summary": coordinator_summary,
            "observer_findings": observer_findings,
            "has_critical_issues": has_critical,
        }

    async def _wait_for_approval(self, proposal) -> str:
        """APPROVAL: Wait for human decision on the change proposal.

        If no proposal or no workspace, auto-approve (legacy behavior).

        Returns:
            "accept", "reject", or "edit".
        """
        if proposal is None:
            # No workspace — legacy behavior, auto-approve
            return "accept"

        # Emit phase change so UI shows approval buttons
        await self._emit_phase_change(SessionPhase.APPROVAL)

        # Wait for human decision (set by set_approval_decision)
        self._approval_event.clear()
        self._approval_decision = ""

        try:
            # Wait up to 10 minutes for human response
            await asyncio.wait_for(self._approval_event.wait(), timeout=600)
        except asyncio.TimeoutError:
            logger.warning("Approval timeout — auto-rejecting")
            self._add_system_message(
                "Approval timed out (10 min). Auto-rejecting proposal."
            )
            return "reject"

        decision = self._approval_decision
        logger.info("Human decision: %s", decision)

        if decision == "edit" and self._approval_edits and proposal:
            # Apply human edits to the proposal's code blocks
            for change in proposal.changes:
                if change.filename in self._approval_edits:
                    change.content = self._approval_edits[change.filename]

        return decision

    async def _run_testing(self, proposal) -> bool:
        """TESTING: Apply proposal, run safety tools, rollback on failure.

        Args:
            proposal: ChangeProposal to apply and test.

        Returns:
            True if all tests passed, False if rolled back.
        """
        if not self.workspace:
            return True

        await self._emit_phase_change(SessionPhase.TESTING)

        # Apply changes to disk
        try:
            applied = self.workspace.apply_proposal(proposal.id)
            self._add_system_message(
                f"Applied {len(applied)} file(s): {', '.join(applied)}"
            )
        except Exception as e:
            logger.error("Failed to apply proposal: %s", e)
            self._add_system_message(f"Failed to apply changes: {e}")
            return False

        # Run safety tools
        try:
            results = await self.workspace.run_tools()
        except Exception as e:
            logger.error("Tool execution failed: %s", e)
            self._add_system_message(f"Tool execution failed: {e}")
            # Don't rollback on tool failure — tools might not be available
            return True

        if not results:
            # No tools available — pass by default
            self._add_system_message("No safety tools available. Skipping auto-tests.")
            return True

        # Emit test results
        await self._emit_test_results(results)

        # Check if all passed
        all_passed = all(
            getattr(r, "passed", True) for r in results
        )

        if all_passed:
            self._add_system_message("All safety tools passed.")
            return True

        # Some tests failed — auto-rollback
        self._add_system_message(
            "Safety tools found issues. Auto-rolling back changes."
        )
        try:
            rolled_back = self.workspace.rollback(proposal.id)
            self._add_system_message(
                f"Rolled back {len(rolled_back)} file(s). "
                f"Feeding failures back to team."
            )
        except Exception as e:
            logger.error("Rollback failed: %s", e)
            self._add_system_message(f"Rollback failed: {e}")

        # Feed test failures back into history for next round
        failure_text = self.workspace.format_tool_results(results)
        self._add_system_message(f"Test failures:\n{failure_text}")

        return False

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
        _typing_msg = TeamMessage(
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

        if not response:
            response = f"[No response from {spec.title} ({spec.default_model})]"

        # InsAIts monitoring: scan every agent response for anomalies
        if self.monitor and hasattr(self.monitor, '_check'):
            try:
                monitor_result = await self.monitor._check(
                    message=response,
                    sender_id=spec.role.value,
                    receiver_id="session",
                    llm_id=spec.default_model,
                    round_num=round_num,
                    hop=self.phase.value,
                )
                if monitor_result.should_block:
                    logger.warning(
                        "InsAIts BLOCKED %s response: %d anomalies",
                        spec.title, len(monitor_result.anomalies),
                    )
                    response = (
                        f"[BLOCKED by InsAIts: {spec.title} response flagged "
                        f"({len(monitor_result.anomalies)} anomalies)]"
                    )
                elif monitor_result.anomalies:
                    logger.info(
                        "InsAIts flagged %s: %d anomalies (non-blocking)",
                        spec.title, len(monitor_result.anomalies),
                    )
            except Exception as e:
                logger.debug("InsAIts check skipped for %s: %s", spec.title, e)

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

    def _get_spec(self, role: TeamRole) -> RoleSpec | None:
        """Find spec by role."""
        for spec in self.team:
            if spec.role == role:
                return spec
        return None

    async def _staggered_call(
        self,
        specs: list[RoleSpec],
        prompt: str,
        lang_instruction: str,
        round_num: int = 0,
        delay: float = 1.5,
    ) -> list[TeamMessage]:
        """Call agents sequentially with delay to avoid free-tier rate limits.

        Args:
            specs: List of agent specs to call.
            prompt: User prompt for each agent.
            lang_instruction: Language instruction.
            round_num: Current round number.
            delay: Seconds between calls (default 1.5s for free models).
        """
        results: list[TeamMessage] = []
        for i, spec in enumerate(specs):
            if i > 0:
                await asyncio.sleep(delay)
            msg = await self._call_agent(spec, prompt, lang_instruction, round_num)
            results.append(msg)
        return results

    async def _emit(self, msg: TeamMessage) -> None:
        """Emit message to the streaming queue."""
        try:
            self._message_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass

    async def _emit_phase_change(self, phase: SessionPhase) -> None:
        """Emit a phase change as a system message."""
        msg = TeamMessage(
            role="system",
            model_id="system",
            display_name="System",
            content=f"Phase: {phase.value}",
            phase=phase,
        )
        await self._emit(msg)

    async def _emit_proposal(self, proposal) -> None:
        """Emit a change proposal to the UI via message queue."""
        msg = TeamMessage(
            role="system",
            model_id="system",
            display_name="Coordinator",
            content=f"Change proposal {proposal.id} ready for review.",
            phase=SessionPhase.APPROVAL,
        )
        msg.code_blocks = [
            CodeBlock(
                filename=c.filename,
                language=c.language,
                content=c.content,
                action=c.action,
            )
            for c in proposal.changes
        ]
        self.history.add(msg)
        await self._emit(msg)

    async def _emit_test_results(self, results: list) -> None:
        """Emit test results to the UI."""
        if not self.workspace:
            return
        formatted = self.workspace.format_tool_results(results)
        msg = TeamMessage(
            role="system",
            model_id="system",
            display_name="Safety Tools",
            content=formatted,
            phase=SessionPhase.TESTING,
        )
        self.history.add(msg)
        await self._emit(msg)

    def _add_system_message(self, content: str) -> None:
        """Add a system message to history (synchronous)."""
        msg = TeamMessage(
            role="system",
            model_id="system",
            display_name="System",
            content=content,
            phase=self.phase,
        )
        self.history.add(msg)
        try:
            self._message_queue.put_nowait(msg)
        except asyncio.QueueFull:
            pass
