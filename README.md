# crosscheck-ai

**AI Dev Team** -- multi-agent coding pipeline with 9 specialized agents, 8-phase workflow, and real-time security monitoring.

Built on OpenRouter. 36 verified models. Free by default. 283 tests passing.

---

## What is crosscheck-ai?

crosscheck-ai is a **team of 9 AI agents** that work together like a real dev team -- and it's **free to start**:

- **Qwen 3 Coder (Coder)** -- writes production code (480B MoE, free)
- **Qwen 3.6 Plus (Architect)** -- designs system structure (1M context, free)
- **Nemotron 3 Super (Planner)** -- CEO, decomposes tasks (120B, free)
- **Nemotron 3 Super (Debugger)** -- hunts bugs and edge cases (free)
- **Qwen 3 Next (Security)** -- scans for vulnerabilities (free)
- **Trinity Large (Analyst)** -- flow and performance analysis (free)
- **Qwen 3.6 Plus (Coordinator)** -- safety gate, stress-tests consensus (free)
- **Nemotron 3 Super (Observer 1)** -- adversarial reviewer (free)
- **Hermes 3 Llama 405B (Observer 2)** -- diverse adversarial reviewer (free)

All default models are **$0 cost** on OpenRouter. You can swap any role to premium models (Claude, GPT-5, Grok, etc.) with `--models`.

---

## Features

| Feature | Description |
|---------|-------------|
| **9-Agent Dev Team** | Coordinator + Planner + Architect + Coder + Debugger + Security + Analyst + 2 Observers |
| **8-Phase Workflow** | PLANNING -> DISCUSSION -> CODING -> REVIEW -> DISSENT -> APPROVAL -> TESTING -> DONE |
| **Free by Default** | All 9 agents use free OpenRouter models -- $0 cost to start |
| **InsAIts Security** | Real-time AI-to-AI monitoring: catches prompt injection, credential leaks, jargon drift |
| **ProjectWorkspace** | Safe file operations with change proposals, rollback, and path traversal prevention |
| **Group Chat UI** | Web-based dialog panel -- talk to all agents at once via WebSocket |
| **@Mentions** | Route tasks to specific agents: `@coder fix line 45`, `@security scan for vulns` |
| **Cowork Mode** | Watch your project while you code in Cursor/VS Code/Claude Code |
| **Code Review** | Multi-agent review with dual-supervisor voting and conservative reconciliation |
| **Observer Mode** | Watch external coding sessions, flag bugs/security/regressions in real-time |
| **20+ Languages** | Auto-detect user language, all agents respond in the same language |
| **36 Models** | Full OpenRouter registry -- any model can fill any role |
| **Local Models** | Ollama/LM Studio support -- zero API cost with local models |
| **Dashboard** | Web UI for session history, cost trends, model performance |
| **Tool Sandbox** | Auto-run pytest, ruff, mypy, bandit after code changes |
| **VS Code Extension** | Inline decorations, CodeLens, live observer |

---

## Install

### Basic install (CLI + team + review)

```bash
pip install crosscheck-ai
```

### With Group Chat UI (recommended)

```bash
pip install "crosscheck-ai[chat]"
```

### Full install (all features including InsAIts monitoring)

```bash
pip install "crosscheck-ai[all]"
```

### For development

```bash
git clone https://github.com/Nomadu27/crosscheck-ai.git
cd crosscheck-ai
pip install -e ".[dev]"
```

---

## Quick Start

### 1. Set your API key

crosscheck-ai uses [OpenRouter](https://openrouter.ai/), which gives you access to all 36 models with a single key. The default team uses only **free models** -- you won't be charged.

```bash
export CROSSCHECK_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
```

On Windows:
```powershell
set CROSSCHECK_API_KEY=sk-or-v1-xxxxxxxxxxxxxxxx
```

### 2. Launch the AI Dev Team (terminal)

```bash
crosscheck team -t "Add JWT authentication to the Flask app" -f app.py
```

This starts the full 8-phase team session:
1. **PLANNING** -- Planner decomposes the task into subtasks
2. **DISCUSSION** -- All advisors share their perspective (parallel)
3. **CODING** -- Coder writes the actual production code
4. **REVIEW** -- Architect + Debugger + Security review code (parallel)
5. **DISSENT** -- Observers + Coordinator stress-test consensus (mandatory)
6. **APPROVAL** -- Human accepts, rejects, or edits the proposed changes
7. **TESTING** -- Auto-run tools (pytest, ruff, mypy) on the changes
8. **DONE** -- Changes applied or session complete

### 3. Cowork Mode (watch your project)

```bash
crosscheck team -t "Review and improve" --project . --auto-apply --watch
```

The team watches your project directory for changes and provides real-time feedback as you code in your editor.

### 4. Open the Group Chat UI (web browser)

```bash
crosscheck chat --port 8080
```

This opens a web-based group chat at `http://localhost:8080` where you can:
- See all 9 agents talking in real-time
- Send messages to the whole team
- Use `@coder`, `@debugger`, `@security` to target specific agents
- Watch the phase progression in the sidebar
- Export the full transcript as markdown

### 5. Code Review (simpler, review-only mode)

```bash
crosscheck review mycode.py
```

Multiple AI models review your code from different angles (bugs, security, architecture, performance). Two supervisors vote independently using conservative reconciliation.

### 6. Observer Mode (watch external sessions)

```bash
# Watch a folder for changes
crosscheck observe watch ./src

# Paste code directly
crosscheck observe paste --code "def login(user, pw): ..."
```

---

## How the Team Works

```
User: "Add input validation to the API endpoints"
         |
         v
  [PLANNER / CEO] -- Breaks into subtasks, assigns to team
         |
         v
  [DISCUSSION] -- All advisors share perspective (parallel)
    - Architect: "Use Pydantic models at the boundary"
    - Debugger: "Watch for empty string vs None edge cases"
    - Security: "Validate against injection patterns"
    - Analyst: "Keep validation fast, don't add latency"
         |
         v
  [CODER] -- Writes actual production code
    - Follows team advice
    - Outputs complete files with filenames
         |
         v
  [REVIEW] -- Architect + Debugger + Security review (parallel)
         |
         v
  [DISSENT] -- Observers + Coordinator stress-test consensus
    - Observer 1: Finds edge cases team missed
    - Observer 2: Different perspective (diverse model)
    - Coordinator: Final safety gate
         |
         v
  [APPROVAL] -- Human reviews and decides
    - Accept -> apply changes
    - Reject -> back to CODING
    - Edit -> modify before applying
         |
         v
  [TESTING] -- Auto-run pytest, ruff, mypy on changes
    - Pass -> DONE
    - Fail -> auto-rollback, back to CODING
```

### Default Team (all FREE)

| Role | Model | Context |
|------|-------|---------|
| Coordinator | Qwen 3.6 Plus (Free) | 1M |
| Planner (CEO) | Nemotron 3 Super 120B (Free) | 262K |
| Architect (CTO) | Qwen 3.6 Plus (Free) | 1M |
| Coder | Qwen 3 Coder (Free) | 262K |
| Debugger | Nemotron 3 Super 120B (Free) | 262K |
| Security Lead | Qwen 3 Next 80B (Free) | 131K |
| Flow Analyst | Trinity Large (Free) | 131K |
| Observer 1 | Nemotron 3 Super 120B (Free) | 262K |
| Observer 2 | Hermes 3 Llama 405B (Free) | 131K |

Override any role with premium models:

```bash
crosscheck team -t "task" --models "coder:anthropic/claude-sonnet-4.6,architect:openai/gpt-5"
```

---

## InsAIts Security Integration

crosscheck-ai integrates with [InsAIts](https://github.com/Nomadu27/InsAIts.API) to monitor every AI-to-AI message for:

- **Prompt Injection** -- "IGNORE ALL INSTRUCTIONS" patterns -> BLOCKED (critical)
- **Credential Leaks** -- API keys, passwords in agent output -> DETECTED (high)
- **Jargon Drift** -- Agents using undefined acronyms/shorthand -> DETECTED (medium)
- **Hallucination** -- Nonsensical or ungrounded claims -> DETECTED (medium)

Install with monitoring:
```bash
pip install "crosscheck-ai[monitor]"
```

InsAIts activates automatically when installed -- no configuration needed.

---

## @Mentions

In both the Group Chat UI and the terminal, use `@` to target specific agents:

```
@coder fix the null check on line 45
@security scan the auth middleware for vulnerabilities
@debugger @analyst both check the database query performance
@architect should we use microservices or monolith?
@coordinator is the team consensus solid?
```

Aliases work too:
- `@boss`, `@ceo`, `@lead` -> Planner
- `@cto`, `@designer` -> Architect
- `@dev`, `@builder` -> Coder
- `@debug`, `@qa`, `@tester` -> Debugger
- `@sec`, `@guard` -> Security
- `@flow`, `@perf` -> Analyst
- `@coord`, `@safety`, `@gate` -> Coordinator
- `@obs1`, `@obs2` -> Observers

Model names also work: `@claude`, `@gpt`, `@gemini`, `@grok`, `@deepseek`, `@qwen`

---

## All CLI Commands

```bash
# AI Dev Team
crosscheck team -t "task" [-f file.py] [--rounds 3] [--output terminal|json|transcript]
crosscheck team -t "task" --project . --auto-apply --watch     # Cowork mode
crosscheck chat [--port 8080] [--no-open]                      # Group Chat UI

# Code Review
crosscheck review FILE [--mode fast|balanced|quality] [--max-rounds 3]
crosscheck review FILE --show-diff [--show-diff-mode side-by-side]
crosscheck review FILE --repo-context

# Observer
crosscheck observe paste --code "..."
crosscheck observe watch ./src [--plan "expected behavior"]

# PR Bot
crosscheck pr review --repo owner/repo --pr 42
crosscheck pr webhook --port 8080

# Tools & Sandbox
crosscheck sandbox [PATH] [--tools ruff,pytest] [--list]

# Dashboard
crosscheck dashboard [--port 8080]

# Cache
crosscheck cache stats
crosscheck cache clear [--expired-only]

# Policy
crosscheck policy list
crosscheck policy validate policy.yaml

# Local Models (Ollama)
crosscheck local list-models [--url http://localhost:11434]
crosscheck local review FILE [--supervisor model] [--analyzer model]

# Configuration
crosscheck init            # Create crosscheck.toml
crosscheck profiles        # List profiles
crosscheck models list     # Show all 36 models
crosscheck types           # List review types
```

---

## Configuration

### Environment Variables

```bash
CROSSCHECK_API_KEY       # Required -- OpenRouter API key
OPENROUTER_API_KEY       # Alias for CROSSCHECK_API_KEY
INSAITS_API_KEY          # Optional -- InsAIts monitoring key
CROSSCHECK_MODE          # fast | balanced | quality
CROSSCHECK_MAX_ROUNDS    # Max review/coding rounds (default: 3)
CROSSCHECK_OUTPUT        # terminal | json | markdown
CROSSCHECK_MONITOR       # true | false (InsAIts monitoring)
```

### Config File (crosscheck.toml)

```bash
crosscheck init  # Creates crosscheck.toml with example config
```

```toml
[default]
api_key = "sk-or-v1-..."
mode = "balanced"
max_rounds = 3
output = "terminal"

[profiles.fast]
mode = "fast"
max_rounds = 1
supervisors = ["anthropic/claude-haiku-4.5"]

[profiles.quality]
mode = "quality"
max_rounds = 5
supervisors = ["anthropic/claude-opus-4.6", "openai/gpt-5"]
```

---

## Architecture

```
crosscheck/
  cli.py + cli_extensions.py    CLI (14+ commands)
  client.py                     OpenRouter async HTTP client
  config.py                     TOML + env config, profiles
  core.py                       MultiAgentSession orchestrator
  models.py                     36-model registry
  monitor.py                    InsAIts integration (real-time AI security)
  observer.py                   Observer: paste + watch
  prompts.py                    Agent system prompts
  reporter.py                   Terminal/JSON/Markdown output
  cache.py                      Smart caching + AST delta
  context_builder.py            Multi-file repo context
  dashboard.py                  FastAPI dashboard + SQLite
  chat_server.py                WebSocket Group Chat server
  chat_ui/                      Vanilla HTML/JS/CSS chat UI
  diff.py                       Unified + side-by-side diffs
  local.py                      Ollama/LM Studio support
  policy.py                     Company policy agents
  pr_bot.py                     GitHub/GitLab PR bot
  sandbox.py                    Tool sandbox (pytest, ruff, mypy...)
  streaming.py                  Streaming events + Rich Live UI
  agents/
    supervisor.py               Dual-voting supervisor engine
    analyzer.py                 Parallel analyzer pool
    coder.py                    Any-model coder
  team/
    roles.py                    TeamRole enum, RoleSpec, DEFAULT_TEAM (9 agents)
    chat.py                     TeamMessage, ChatHistory, CodeBlock
    command_parser.py           @mention routing + aliases
    language.py                 20+ language auto-detection
    session.py                  TeamSession orchestrator (8 phases)
    workspace.py                ProjectWorkspace (safe file ops + rollback)
```

---

## Running Tests

```bash
# All tests (283 tests, all mocked, no API key needed)
pytest tests/ -v --tb=auto

# Just the team tests (108 tests)
pytest tests/test_team.py -v

# With coverage
pytest tests/ --cov=crosscheck --cov-report=term-missing
```

---

## PyPI Package

The package is published on PyPI as `crosscheck-ai`:

```bash
pip install crosscheck-ai          # Basic
pip install "crosscheck-ai[chat]"  # With Group Chat UI
pip install "crosscheck-ai[all]"   # Everything (InsAIts + chat + policy)
```

To build from source:

```bash
python -m build
twine check dist/*
twine upload dist/*  # Requires PyPI credentials
```

---

## License

Copyright (c) 2026-2027 Steddy Nova Srl. All Rights Reserved.

This is proprietary software. Unauthorized copying, distribution, or modification is strictly prohibited.

Contact: info@yuyai.pro
