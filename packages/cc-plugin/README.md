# Mendicant Bias — Claude Code Plugin

One-command install for Mendicant Bias intelligence middleware.

## Install

```bash
# From the plugin directory
claude plugins add ./packages/cc-plugin

# Or install globally
cp -r packages/cc-plugin ~/.claude/plugins/mendicant-bias
```

## What You Get

### 16 MCP Tools (auto-configured)
- mendicant_delegate — autonomous task delegation
- mendicant_classify_task — task classification
- mendicant_verify — blind quality verification
- mendicant_route_tools — semantic tool routing
- mendicant_optimize_context — semantic context ranking
- mendicant_recommend — pattern recommendations
- mendicant_record_pattern — adaptive learning
- mendicant_remember/recall/forget — persistent memory
- mendicant_session_init — session bootstrap
- mendicant_status — system health
- mendicant_list_agents/get_agent — agent discovery

### 13 Named Agents (auto-registered)
hollowed_eyes, the_didact, loveless, zhadyz, cinna, the_architect,
the_oracle, the_scribe, the_curator, the_cartographer, the_librarian,
the_sentinel, the_analyst

### /mendicant_bias Skill (auto-available)
Full tool reference, workflow guide, agent roster.

## Prerequisites

```bash
pip install mendicant-core mendicant-mcp-server mendicant-runtime
```

## How It Works

The plugin tells Claude Code to:
1. Start Mendicant's MCP server (via `mendicant mcp` command)
2. Register 13 agent definitions
3. Register the /mendicant_bias skill

No manual mcp.json editing. No manual agent file copying. One install.
