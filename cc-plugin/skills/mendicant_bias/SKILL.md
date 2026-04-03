---
name: mendicant_bias
description: Mendicant Bias V5 -- intelligence middleware. ALWAYS use mendicant MCP tools when this skill is invoked.
user-invocable: true
when_to_use: "Use for task classification, blind code verification, semantic tool routing, adaptive learning, context optimization, memory, named agents, or autonomous task delegation"
allowed-tools: [bash]
---

# Mendicant Bias V5 -- Intelligence Middleware + Autonomous Delegation

## CRITICAL INSTRUCTION

When this skill is invoked:
1. If the task is complex (analysis, research, review, architecture, strategy):
   Call `mendicant_delegate` with the full task. Let Mendicant autonomous agent handle it.
2. If the task is a quick action (classify, verify, remember, check status):
   Use the individual MCP tools directly.
3. Always call `mendicant_classify_task` first to determine which path.

**Decision flow:**
1. Call `mendicant_classify_task` with the user request
2. If task_type is RESEARCH, CODE_GENERATION, CRITICAL_CODE, or MULTI_MODAL:
   - Call `mendicant_delegate` with mode "pro" (or "ultra" for complex multi-agent work)
   - The delegate tool handles agent selection, middleware orchestration, and returns a complete result
3. If task_type is SIMPLE:
   - Use individual MCP tools (route_tools, verify, remember, etc.)
4. After any work: call `mendicant_record_pattern` to record what worked

**For complex tasks -- use delegation:**
- `mendicant_delegate` with mode "pro" for most analysis/research/review tasks
- `mendicant_delegate` with mode "ultra" for architecture design, multi-domain analysis
- `mendicant_delegate` with specific agents for targeted work (e.g. `agents: ["hollowed_eyes", "loveless"]` for code review)

**For quick actions -- use individual tools:**
- `mendicant_classify_task` to determine task type
- `mendicant_verify` after code writes
- `mendicant_remember` / `mendicant_recall` for memory
- `mendicant_status` for health checks
- `mendicant_route_tools` for tool discovery

**After completing work:**
- Call `mendicant_verify` on any code/file output
- Call `mendicant_record_pattern` to record what worked

## System Check

!```bash
mendicant status 2>/dev/null || echo "Mendicant CLI not installed"
```

## Available MCP Tools

| Tool | When To Call |
|---|---|
| `mendicant_delegate` | PRIMARY -- complex tasks, analysis, research, architecture, strategy |
| `mendicant_classify_task` | FIRST -- always classify before acting |
| `mendicant_route_tools` | When choosing which tools to use |
| `mendicant_verify` | After any code write/edit |
| `mendicant_optimize_context` | When context is large (>20k tokens) |
| `mendicant_recommend` | Before starting -- check what worked before |
| `mendicant_record_pattern` | After completing -- record for learning |
| `mendicant_remember` | Store important facts |
| `mendicant_recall` | Retrieve stored facts |
| `mendicant_session_init` | Start of session -- load memory |
| `mendicant_list_agents` | Discover available specialists |
| `mendicant_get_agent` | Get specialist profile for spawning |
| `mendicant_status` | Check system health |

## Delegation Modes

| Mode | Description | Use When |
|---|---|---|
| `flash` | Fast, no thinking | Simple lookups, quick answers |
| `standard` | Thinking enabled | Medium complexity tasks |
| `pro` | Planning + thinking | **Default** -- analysis, research, review |
| `ultra` | Planning + sub-agents + thinking | Architecture design, multi-domain work |

## Named Agents -- Available via delegation or `mendicant_get_agent`

| Agent | Use When |
|---|---|
| **hollowed_eyes** | Code implementation, refactoring |
| **the_didact** | Deep research, codebase exploration |
| **loveless** | QA, security audit, testing |
| **zhadyz** | DevOps, deployment, releases |
| **cinna** | UI/UX design, visual polish |
| **the_architect** | Architecture analysis, system design |
| **the_oracle** | Validation, go/no-go decisions |
| **the_scribe** | Documentation, technical writing |
| **the_analyst** | Data analysis, metrics, business intelligence |
