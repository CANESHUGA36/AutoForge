# AutoForge — Agent Development Guide

## Project Overview

AutoForge is an AI-powered software development harness that orchestrates multiple specialized agents (Architect, SprintMaster, Builder, Reviewer, Judge) to build web applications from user prompts. It uses a round-based iterative approach with feature-grouped sprints, automated evaluation, and git-based checkpointing.

**Tech Stack**: Python 3.13+, OpenAI-compatible LLM API, Playwright (browser automation), Docker (optional)

## Architecture

```
run.py              — Entry point: creates workspace, runs harness
harness/core.py     — Harness: orchestrates the full build-eval loop
agents.py           — Agent: LLM wrapper with tool execution, context lifecycle
config.py           — Configuration: thresholds, timeouts, env vars
context.py          — Context lifecycle: token counting, compaction, anxiety detection
skills.py           — Skill system: progressive disclosure of domain knowledge
prompts.py          — System prompt templates for all agents
workspace_state.py  — Workspace state tracking for Builder agent
dashboard.py        — Real-time build dashboard
eval_cache.py       — Evaluation result caching

tools/              — Tool implementations (file ops, bash, browser, etc.)
harness/            — Core harness modules
  ├── core.py       — Main harness orchestration
  ├── eval.py       — Evaluation parsing utilities
  ├── events.py     — Event bus for dashboard
  ├── feature_groups.py — Feature group state machine
  ├── git.py        — Git operations
  ├── logging.py    — Structured logging
  ├── pipeline.py   — Pipeline runner
  ├── sprint.py     — Sprint planning
  ├── stages.py     — Build pipeline stages
  ├── state.py      — Harness state persistence
  └── strategy.py   — Strategy parsing

prompts/            — Agent system prompts (Markdown)
skills/             — Domain skill guides (SKILL.md files)
tests/              — Test suite
projects/           — Generated project workspaces
workspace/          — Default workspace directory
```

## Key Concepts

### Agent System
- **Architect**: Analyzes user prompt → produces `spec.md` + `contract.md`
- **SprintMaster**: Determines which feature group to build next → writes `sprint.md`
- **Builder**: Implements the current sprint's feature group → writes code
- **Reviewer**: Tests the implementation via browser automation → writes review report
- **Judge**: Maps review findings to contract criteria → produces PASS/FAIL scores

### Feature Groups
Contract criteria are organized into tiers:
- **Tier 1 (MVP)**: F1-F4, must pass 100%
- **Tier 2 (Core)**: F5-F9, must pass ≥80%
- **Tier 3 (Extended)**: F10-F17, must pass ≥70%
- **Design (D)** and **Technical (T)** standards apply across all tiers

### Build Loop
1. Architect creates spec + contract
2. For each feature group (in tier order):
   a. SprintMaster writes sprint.md for current group
   b. Builder implements the group's features
   c. Reviewer tests via browser (Playwright)
   d. Judge scores against contract criteria
   e. If pass → commit, move to next group
   f. If fail → feedback to Builder for retry
3. Final evaluation when all groups complete or max rounds reached

## Development Guidelines

### Adding a New Tool
1. Add tool schema to `tools_impl.py` (`TOOL_SCHEMAS` or `BROWSER_TOOL_SCHEMAS`)
2. Implement the tool function in `tools_impl.py`
3. Update agent tool permissions in `harness/core.py` (e.g., `builder_tools`)
4. Add tests in `tests/`

### Adding a New Skill
1. Create directory under `skills/<skill-name>/`
2. Write `SKILL.md` with frontmatter (description required)
3. Optionally add code examples and reference material
4. Agents automatically see available skills via `skills.build_catalog_prompt()`

### Modifying Agent Behavior
- System prompts live in `prompts/<agent>.md`
- Prompt templates are loaded via `prompts.py` (supports `{{WORKSPACE}}` substitution)
- Agent iteration limits: `config.AGENT_ITERATION_LIMITS`
- Tool permissions: `harness/core.py` `*_tools` sets

### Context Management
- Token thresholds: `config.COMPRESS_THRESHOLD` (180K), `config.RESET_THRESHOLD` (200K)
- Context compaction: `context.compact_messages()` — summarizes old messages
- Context reset: `context.create_checkpoint()` + `restore_from_checkpoint()`
- Anxiety detection: `context.detect_anxiety()` — detects token-limit anxiety patterns

### Environment Configuration
Copy `.env.example` to `.env` and configure:
```
OPENAI_API_KEY=       # LLM API key
OPENAI_BASE_URL=      # API base URL (default: OpenAI)
HARNESS_MODEL=        # Model name (default: gpt-4o)
MINIMAX_API_KEY=      # For image generation (falls back to OPENAI_API_KEY)
HARNESS_WORKSPACE=    # Default workspace path
HARNESS_PROJECTS_DIR= # Auto-generated project parent dir
```

## Testing

Run the test suite:
```bash
pytest tests/ -v
```

Key test files:
- `test_agents.py` — Agent iteration and tool execution
- `test_context.py` — Context compaction and token counting
- `test_eval.py` — Evaluation parsing
- `test_harness_core.py` — Harness orchestration logic

## Common Tasks

### Running a Build
```bash
python run.py "Build a personal finance dashboard"
```

### Resuming an Interrupted Build
The harness auto-saves state to `harness_state.json`. Restarting with the same workspace resumes from the last completed round.

### Viewing Build Dashboard
During builds, the dashboard is served at `http://localhost:8080` (if enabled). Check `dashboard.py` for details.

## Code Style

- **Python**: Type hints required (`from __future__ import annotations`)
- **Logging**: Use `logging.getLogger("harness")` or per-instance loggers
- **Error handling**: Return `[error] ...` strings for tool failures; don't raise exceptions to LLM
- **File paths**: Use `pathlib.Path`, resolve to absolute paths for workspace operations
- **Subprocess**: Always use `config.SUBPROCESS_TEXT_KWARGS` for text encoding (UTF-8)
