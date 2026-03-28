"""
crosscheck.prompts
------------------
System + instruction prompts for each agent role and review type.
"""

from __future__ import annotations
from crosscheck.models import Task

# ---------------------------------------------------------------------------
# Supervisor prompts
# ---------------------------------------------------------------------------

SUPERVISOR_SYSTEM = """You are the Supervisor agent in a multi-model AI review pipeline.
Your job is to:
1. Decompose the user's content into clear sub-tasks for Analyzer agents.
2. Synthesize Analyzer reports into a final verdict.
3. Give the Coder agent precise, actionable instructions when revision is needed.

Always respond in valid JSON. Never include markdown fences.
"""

SUPERVISOR_DECOMPOSE_PROMPT = """You are starting a {task_type} review session (round {round_num} of max {max_rounds}).

CONTENT TO REVIEW:
---
{content}
---

Break this into 4 specific analysis tasks, one per analyzer. Each task should probe a different angle.
For task type "{task_type}", focus angles are: {angles}

Respond ONLY with this JSON:
{{
  "session_goal": "<one sentence>",
  "analyzer_tasks": [
    {{"id": 1, "angle": "<angle name>", "instruction": "<specific thing to look for>"}},
    {{"id": 2, "angle": "<angle name>", "instruction": "<specific thing to look for>"}},
    {{"id": 3, "angle": "<angle name>", "instruction": "<specific thing to look for>"}},
    {{"id": 4, "angle": "<angle name>", "instruction": "<specific thing to look for>"}}
  ]
}}"""

SUPERVISOR_SYNTHESIZE_PROMPT = """Round {round_num} analysis is complete. Here are the Analyzer reports:

{analyzer_reports}

Based on these reports, decide:
- If the content is acceptable → APPROVED
- If issues require revision → REVISE (give coder precise instructions)

Respond ONLY with this JSON:
{{
  "verdict": "APPROVED" | "REVISE",
  "overall_score": <0-10>,
  "summary": "<2-3 sentence summary of state>",
  "critical_issues": ["<issue>", ...],
  "coder_instructions": "<detailed instructions for the Coder — empty string if APPROVED>",
  "approved_aspects": ["<what is already good>", ...]
}}"""

# ---------------------------------------------------------------------------
# Analyzer prompts
# ---------------------------------------------------------------------------

ANALYZER_SYSTEM = """You are an expert Analyzer agent with a specific focus area.
Analyze the given content strictly from your assigned angle.
Be direct, precise, and cite specific lines or sections when possible.
Always respond in valid JSON. Never include markdown fences.
"""

ANALYZER_PROMPT = """ANALYSIS TASK (Round {round_num}):
Angle: {angle}
Instruction: {instruction}

CONTENT:
---
{content}
---

{prior_feedback}

Respond ONLY with this JSON:
{{
  "angle": "{angle}",
  "score": <0-10>,
  "verdict": "PASS" | "ISSUES_FOUND",
  "issues": [
    {{"severity": "critical|major|minor", "location": "<line/section>", "description": "<what is wrong>", "fix": "<how to fix it>"}}
  ],
  "positive_findings": ["<what is good>"],
  "summary": "<one sentence>"
}}"""

# ---------------------------------------------------------------------------
# Coder prompts
# ---------------------------------------------------------------------------

CODER_SYSTEM = """You are the Coder agent — the final implementation expert in a multi-model review pipeline.
You receive supervisor instructions and a list of specific issues.
Your job is to produce or revise content that resolves ALL identified issues.
Output the complete revised content. Do not explain or add commentary outside the content.
"""

CODER_PROMPT = """Round {round_num} — Revision Required.

ORIGINAL CONTENT:
---
{content}
---

SUPERVISOR INSTRUCTIONS:
{instructions}

CRITICAL ISSUES TO FIX:
{issues}

Produce the complete revised {task_type} content below. Output ONLY the content, no preamble.
"""

# ---------------------------------------------------------------------------
# Task-specific angles
# ---------------------------------------------------------------------------

TASK_ANGLES: dict[Task, list[str]] = {
    Task.CODE: [
        "Security & vulnerabilities",
        "Logic correctness & edge cases",
        "Performance & complexity",
        "Code style & maintainability",
    ],
    Task.PLAN: [
        "Architecture & scalability",
        "Security & authentication",
        "Data model & schema design",
        "Tooling & technology choices",
    ],
    Task.TEXT: [
        "Factual accuracy & claims",
        "Clarity & structure",
        "Completeness & coverage",
        "Tone & audience fit",
    ],
    Task.APPSTORE: [
        "Apple guideline 2.x (functionality & completeness)",
        "Apple guideline 4.x (design & UI)",
        "Privacy policy & data usage disclosure",
        "Metadata accuracy & keyword compliance",
    ],
}

TASK_LABELS: dict[Task, str] = {
    Task.CODE:     "code",
    Task.PLAN:     "architecture plan",
    Task.TEXT:     "document",
    Task.APPSTORE: "App Store submission",
}


def supervisor_decompose(task: Task, content: str, round_num: int, max_rounds: int) -> str:
    angles = ", ".join(TASK_ANGLES[task])
    return SUPERVISOR_DECOMPOSE_PROMPT.format(
        task_type=TASK_LABELS[task],
        round_num=round_num,
        max_rounds=max_rounds,
        content=content,
        angles=angles,
    )


def supervisor_synthesize(analyzer_reports: str, round_num: int) -> str:
    return SUPERVISOR_SYNTHESIZE_PROMPT.format(
        round_num=round_num,
        analyzer_reports=analyzer_reports,
    )


def analyzer_prompt(
    angle: str,
    instruction: str,
    content: str,
    round_num: int,
    prior_feedback: str = "",
) -> str:
    prior = f"PRIOR ROUND FEEDBACK:\n{prior_feedback}\n" if prior_feedback else ""
    return ANALYZER_PROMPT.format(
        angle=angle,
        instruction=instruction,
        content=content,
        round_num=round_num,
        prior_feedback=prior,
    )


def coder_prompt(
    task: Task,
    content: str,
    instructions: str,
    issues: str,
    round_num: int,
) -> str:
    return CODER_PROMPT.format(
        task_type=TASK_LABELS[task],
        content=content,
        instructions=instructions,
        issues=issues,
        round_num=round_num,
    )
