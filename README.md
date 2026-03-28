# crosscheck-ai

**AI Dev Team** — multi-agent coding pipeline where Claude codes and other models advise, debug, review, and plan.

Built on OpenRouter. 31 verified models. 229 tests passing.

## Features

- **AI Dev Team**: Claude writes code, GPT-5 architects, DeepSeek debugs, Grok scans security, Gemini analyzes flow
- **Group Chat UI**: Talk to all agents at once via web-based dialog panel
- **Code Review**: Multi-agent review with dual-supervisor voting
- **Observer Mode**: Watch external coding sessions, flag bugs in real-time
- **@Mentions**: Route tasks to specific agents (`@coder fix line 45`, `@security scan for vulns`)
- **20+ Languages**: Auto-detect user language, respond in same language

## Install

```bash
pip install crosscheck-ai
```

## Quick Start

```bash
export CROSSCHECK_API_KEY=sk-or-...

# Launch AI Dev Team
crosscheck team -t "Add authentication" -f app.py

# Open Group Chat UI
crosscheck chat --port 8080

# Code review
crosscheck review mycode.py

# Observer mode
crosscheck observe watch ./src
```

## License

Copyright (c) 2026-2027 Steddy Nova Srl. All Rights Reserved.

Contact: info@yuyai.pro
