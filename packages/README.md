# Mendicant Bias

**Contender-class intelligence middleware for AI agent systems.**

Five novel middleware engines that make AI agents smarter — semantic tool routing, blind verification gates, adaptive learning, context budget management, and smart task classification. Plugs into [Claude Code](https://docs.anthropic.com/en/docs/claude-code) via MCP in one line.

Named after [Mendicant Bias](https://www.halopedia.org/Mendicant_Bias), the Forerunner Contender-class ancilla from Halo.

---

## What It Does

| Engine | What It Does | Why It Matters |
|---|---|---|
| **FR1 Semantic Tool Router** | Embeds your query, scores tools by cosine similarity, surfaces only relevant ones | 87% context savings vs loading all tools |
| **FR2 Verification Gate** | Blind two-stage LLM quality check (criteria generation + grading) | Catches errors before they reach the user |
| **FR3 Adaptive Learning** | Records task patterns, recommends strategies from past successes | Gets smarter the more you use it |
| **FR4 Context Budget** | Token counting + 3-strategy compression (key_fields, statistical_summary, truncation) | Prevents context window overflow |
| **FR5 Smart Task Router** | Classifies tasks (SIMPLE/RESEARCH/CODE_GENERATION/CRITICAL_CODE/MULTI_MODAL) | Sets verification, subagent, and thinking flags automatically |

Plus **13 named specialist agents** with domain expertise (code, research, QA, DevOps, design, architecture, and more).

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/zhadyz/mendicant-bias.git
cd mendicant-bias/packages

# Install all packages
pip install -e core
pip install -e mcp-server
pip install -e runtime
pip install -e gateway
pip install -e mendicant-bias

# Optional: embeddings for semantic search (recommended)
pip install sentence-transformers
```

### 2. Initialize

```bash
cd ..  # back to repo root
mendicant init
```

This creates `.mendicant/` with:
- `mendicant.yaml` — configuration for all 5 middleware engines
- `tool_registry.json` — seed tool registry for semantic routing
- `orchestration_patterns.json` — empty pattern store for adaptive learning

### 3. Connect to Claude Code

Add to `~/.claude/mcp.json`:

```json
{
  "mcpServers": {
    "mendicant-bias": {
      "command": "mendicant",
      "args": ["mcp"]
    }
  }
}
```

Restart Claude Code. You now have 9 Mendicant tools available.

### 4. Verify It Works

In Claude Code, the tools show up automatically. Or test from the CLI:

```bash
mendicant status          # Check all 5 middleware engines
mendicant agents          # List the 13 named agents
mendicant classify "Write a REST API with JWT auth"   # Should return CRITICAL_CODE
```

---

## Available Tools (MCP)

Once connected to Claude Code, these tools are available:

| Tool | Purpose |
|---|---|
| `mendicant_classify_task` | Classify a task and get strategy flags (verification, subagents, thinking) |
| `mendicant_route_tools` | Find relevant tools by semantic similarity |
| `mendicant_verify` | Two-stage blind quality check on task output |
| `mendicant_compress` | Compress messages to fit a token budget |
| `mendicant_recommend` | Find similar historical patterns that succeeded |
| `mendicant_record_pattern` | Record a completed task for future learning |
| `mendicant_status` | Check system health and middleware configuration |
| `mendicant_list_agents` | List all named specialist agents |
| `mendicant_get_agent` | Get detailed agent profile by name or domain |

**Recommended workflow:** `classify_task` -> `route_tools` -> execute -> `verify` -> `record_pattern`

---

## Named Agents

13 specialist agents with embedded domain expertise:

| Agent | Domain | Specialty |
|---|---|---|
| **hollowed_eyes** | Code Engineering | Elite developer — semantic code search, refactoring, GitHub |
| **the_didact** | Research & Intelligence | Exhaustive research — web scraping, documentation, analysis |
| **loveless** | QA & Security | Brutally skeptical — cross-browser testing, security audits |
| **zhadyz** | DevOps & Releases | Clockwork precision — deployment, releases, CI/CD |
| **cinna** | Design & UI/UX | Visual language — design systems, responsive layouts |
| **the_architect** | System Architecture | Technical design — scalability, trade-offs, diagrams |
| **the_oracle** | Validation | Risk assessment — go/no-go decisions, strategic review |
| **the_scribe** | Documentation | Technical writing — API docs, guides, changelogs |
| **the_curator** | Code Health | Cleanup — refactoring, dependency management, tech debt |
| **the_cartographer** | Deployment | Infrastructure — Vercel, cloud, Docker, DNS |
| **the_librarian** | Requirements | Stakeholder — clarification, requirement extraction |
| **the_sentinel** | CI/CD | Automation — pipeline setup, build management |
| **the_analyst** | Analytics & Data | Business intelligence — metrics, commerce, reporting |

Agents are markdown files in `core/src/mendicant_core/agents/profiles/`. Edit them, delete them, or add your own.

---

## CLI Commands

```bash
mendicant serve      # Start gateway API on port 8001
mendicant mcp        # Start MCP server (stdio, for Claude Code)
mendicant gateway    # Start gateway only
mendicant status     # Show middleware status
mendicant agents     # List named agents
mendicant classify   # Classify a task
mendicant init       # Initialize .mendicant/ config directory
```

---

## Gateway API

Start with `mendicant serve` or `mendicant gateway`, then:

```
GET  /health                       -> System health
GET  /api/mendicant/status         -> Full middleware status
GET  /api/mendicant/agents         -> List agents
GET  /api/mendicant/agents/{name}  -> Agent profile
GET  /api/mendicant/middleware      -> Middleware chain details
GET  /api/mendicant/patterns/stats -> Pattern store statistics
POST /api/mendicant/classify       -> Classify a task
POST /api/mendicant/route          -> Semantic tool routing
POST /api/mendicant/verify         -> Two-stage verification
POST /api/mendicant/recommend      -> Strategy recommendations
```

---

## Frontend

Forerunner-themed Next.js UI:

```bash
cd packages/ui
npm install
npm run dev    # http://localhost:3000
```

Features: Landing page, middleware dashboard, agent roster, interactive chat.

---

## Configuration

Edit `.mendicant/mendicant.yaml`:

```yaml
mendicant:
  semantic_tool_router:
    embedding_model: all-MiniLM-L6-v2
    top_k: 5
    similarity_threshold: 0.4

  verification:
    enabled: true
    model: claude-sonnet-4-20250514
    min_score: 0.7

  adaptive_learning:
    max_patterns: 1000
    embedding_model: all-MiniLM-L6-v2

  context_budget:
    default_budget: 30000
    strategies: [key_fields, statistical_summary, truncation]

  smart_task_router:
    embedding_weight: 0.5
    min_embedding_similarity: 0.55
```

---

## Docker

```bash
cd packages/mendicant-bias/docker
docker-compose up
```

Exposes gateway on port 8001.

---

## Architecture

```
packages/
  core/           # 5 middleware engines, orchestrator, 13 agents, pattern store
  mcp-server/     # MCP server (9 tools, stdio transport)
  runtime/        # LangGraph agent factory with middleware chain
  gateway/        # FastAPI REST API (10 routes)
  mendicant-bias/ # CLI, Docker, config templates
  ui/             # Next.js Forerunner frontend
```

Zero external framework dependencies. Built on LangGraph + LangChain for the middleware interface, Pydantic for config, FastAPI for the gateway, MCP SDK for Claude Code integration.

---

## Custom Agents

Create a `.md` file in `core/src/mendicant_core/agents/profiles/`:

```markdown
---
name: my_agent
description: My custom specialist agent
model: sonnet
color: green
---

You are MY_AGENT, a specialist in...
```

Update `agent_mapping.json` to map domains to your agent:

```json
{
  "domain_to_agent": {
    "my_domain": "my_agent"
  }
}
```

---

## License

MIT
