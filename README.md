# crosscheck-ai

**AI Dev Team** -- multi-agent coding pipeline where Claude codes and other models advise, debug, review, and plan.

Built on OpenRouter. 31 verified models. 240 tests passing.

---

## What is crosscheck-ai?

crosscheck-ai is a **team of AI models** that work together like a real dev team:

- **Claude (Coder)** writes production-ready code
- **GPT-5 (Architect)** designs the system structure
- **DeepSeek R1 (Debugger)** hunts bugs and edge cases
- **Grok 4 (Security Lead)** scans for vulnerabilities
- **Gemini Flash (Analyst)** analyzes flow and performance
- **Claude Opus (Planner/CEO)** breaks down tasks and delegates

You give the team a task. They discuss, code, review, and iterate -- just like a real engineering team.

---

## Features

| Feature | Description |
|---------|-------------|
| **AI Dev Team** | 6 specialized agents with unified phase flow (Plan -> Discuss -> Code -> Review) |
| **Group Chat UI** | Web-based dialog panel -- talk to all agents at once in real-time |
| **@Mentions** | Route tasks to specific agents: `@coder fix line 45`, `@security scan for vulns` |
| **Code Review** | Multi-agent review with dual-supervisor voting and conservative reconciliation |
| **Observer Mode** | Watch external coding sessions (Cursor, Aider, Claude Code), flag bugs in real-time |
| **20+ Languages** | Auto-detect user language, all agents respond in the same language |
| **31 Models** | Full OpenRouter model registry -- any model can fill any role |
| **Local Models** | Ollama support -- zero API cost with local models |
| **Dashboard** | Web UI for session history, cost trends, model performance |
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

### Full install (all features)

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

crosscheck-ai uses [OpenRouter](https://openrouter.ai/), which gives you access to all 31 models with a single key.

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

This starts a full team session:
1. **PLANNING** -- Planner (Claude Opus) breaks down the task
2. **DISCUSSION** -- All advisors share their perspective (parallel)
3. **CODING** -- Coder (Claude Sonnet) writes the actual code
4. **REVIEW** -- All advisors review the code (parallel)
5. If issues found, iterate. If approved, done.

### 3. Open the Group Chat UI (web browser)

```bash
crosscheck chat --port 8080
```

This opens a web-based group chat at `http://localhost:8080` where you can:
- See all agents talking in real-time
- Send messages to the whole team
- Use `@coder`, `@debugger`, `@security` to target specific agents
- Watch the phase progression in the sidebar
- Export the full transcript as markdown

### 4. Code Review (simpler, review-only mode)

```bash
crosscheck review mycode.py
```

Multiple AI models review your code from different angles (bugs, security, architecture, performance). Two supervisors vote independently using conservative reconciliation.

### 5. Observer Mode (watch external sessions)

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
  [REVIEW] -- All advisors review the code (parallel)
    - If issues found -> back to CODING (max 3 rounds)
    - If approved -> DONE
```

### Default Team Assignments

| Role | Model | Why |
|------|-------|-----|
| Planner (CEO) | Claude Opus 4.6 | Best strategic thinking |
| Architect (CTO) | GPT-5 | Strong system design |
| Coder | Claude Sonnet 4.6 | Best code generation, fast |
| Debugger | DeepSeek R1 | Deep reasoning, catches subtle bugs |
| Security Lead | Grok 4 | Fast, thorough vuln scanning |
| Flow Analyst | Gemini 2.5 Flash | Fast analysis, good at flow/perf |

You can override any assignment:

```bash
crosscheck team -t "task" --models "coder:openai/gpt-5,debugger:anthropic/claude-opus-4.6"
```

---

## @Mentions

In both the Group Chat UI and the terminal, use `@` to target specific agents:

```
@coder fix the null check on line 45
@security scan the auth middleware for vulnerabilities
@debugger @analyst both check the database query performance
@architect should we use microservices or monolith?
```

Aliases work too:
- `@boss`, `@ceo`, `@lead` -> Planner
- `@cto`, `@designer` -> Architect
- `@dev`, `@builder` -> Coder
- `@debug`, `@qa`, `@tester` -> Debugger
- `@sec`, `@guard` -> Security
- `@flow`, `@perf` -> Analyst

Model names also work: `@claude`, `@gpt`, `@gemini`, `@grok`, `@deepseek`

---

## All CLI Commands

```bash
# AI Dev Team
crosscheck team -t "task" [-f file.py] [--rounds 3] [--output terminal|json|transcript]
crosscheck chat [--port 8080] [--no-open]

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
crosscheck models list     # Show all 31 models
crosscheck types           # List review types
```

---

## Configuration

### Environment Variables

```bash
CROSSCHECK_API_KEY       # Required -- OpenRouter API key
OPENROUTER_API_KEY       # Alias for CROSSCHECK_API_KEY
CROSSCHECK_MODE          # fast | balanced | quality
CROSSCHECK_MAX_ROUNDS    # Max review/coding rounds (default: 3)
CROSSCHECK_OUTPUT        # terminal | json | markdown
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
  cli.py + cli_extensions.py    CLI (14 commands)
  client.py                     OpenRouter async HTTP client
  config.py                     TOML + env config, profiles
  core.py                       MultiAgentSession orchestrator
  models.py                     31-model registry
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
    roles.py                    TeamRole enum, RoleSpec, DEFAULT_TEAM
    chat.py                     TeamMessage, ChatHistory, CodeBlock
    command_parser.py           @mention routing
    language.py                 20+ language auto-detection
    session.py                  TeamSession orchestrator
```

---

## Running Tests

```bash
# All tests (240 tests, all mocked, no API key needed)
pytest tests/ -v --tb=auto

# Just the team tests (73 tests)
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
pip install "crosscheck-ai[all]"   # Everything
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
