---
name: mendicant_bias
description: Mendicant Bias — spawn intelligent agent teams with 13 specialists. Classifies tasks, creates CC-native teammate swarms, delegates autonomously.
user-invocable: true
when_to_use: "Use for complex tasks that benefit from parallel specialist agents, deep analysis, architecture review, code + QA workflows, or any multi-domain work"
allowed-tools: []
---

# Mendicant Bias — Contender-class Intelligence System

## EXECUTION PROTOCOL

When this skill is invoked, follow this EXACT sequence:

### Step 1: Classify the task
Call `mendicant_classify_task` with the user's request. This determines the task type and strategy.

### Step 2: Choose execution path

**If task_type is SIMPLE:**
Handle it directly. No team needed. Use Mendicant MCP tools if helpful (route_tools, verify, remember).

**If task_type is RESEARCH, CODE_GENERATION, CRITICAL_CODE, or MULTI_MODAL:**
Create a Mendicant team and spawn specialist teammates:

1. Call `TeamCreate` with `team_name: "mendicant"`
2. Based on the task type, spawn the right agents as teammates:

**RESEARCH tasks:**
```
Agent({name: "the_didact", team_name: "mendicant", subagent_type: "the_didact", description: "Research specialist", prompt: "[detailed research brief]"})
```
Add `the_architect` if architecture is involved. Add `the_analyst` if data/metrics are involved.

**CODE_GENERATION tasks:**
```
Agent({name: "hollowed_eyes", team_name: "mendicant", subagent_type: "hollowed_eyes", description: "Code implementation", prompt: "[detailed implementation brief]"})
```
Add `the_didact` for research phase. Add `loveless` for QA AFTER implementation is done.

**CRITICAL_CODE tasks:**
```
Agent({name: "hollowed_eyes", team_name: "mendicant", subagent_type: "hollowed_eyes", description: "Implementation", prompt: "[brief]"})
Agent({name: "loveless", team_name: "mendicant", subagent_type: "loveless", description: "Security review", prompt: "[brief]"})
```
ALWAYS include loveless for critical code. Consider `the_architect` for design review.

**MULTI_MODAL tasks:**
```
Agent({name: "cinna", team_name: "mendicant", subagent_type: "cinna", description: "Design specialist", prompt: "[brief]"})
```

### Step 3: Coordinate
- Use `SendMessage` to communicate between teammates
- Monitor teammate progress
- Synthesize results from all agents

### Step 4: Verify and record
- Call `mendicant_verify` on any code output
- Call `mendicant_record_pattern` with the task outcome

## Agent Roster

| Agent | Spawn When | Specialty |
|---|---|---|
| **hollowed_eyes** | Code tasks | Implementation, refactoring, debugging |
| **the_didact** | Research tasks | Deep analysis, codebase exploration |
| **loveless** | Security/QA tasks | Testing, security audit, verification |
| **zhadyz** | DevOps tasks | Deployment, releases, infrastructure |
| **cinna** | Design tasks | UI/UX, visual design, frontend |
| **the_architect** | Architecture tasks | System design, scalability, trade-offs |
| **the_oracle** | Validation tasks | Risk assessment, go/no-go decisions |
| **the_scribe** | Documentation tasks | Technical writing, API docs |
| **the_curator** | Cleanup tasks | Refactoring, tech debt, dependency management |
| **the_cartographer** | Deploy tasks | Cloud, Docker, DNS, hosting |
| **the_librarian** | Requirements tasks | Stakeholder comms, requirement extraction |
| **the_sentinel** | CI/CD tasks | Pipeline setup, build automation |
| **the_analyst** | Analytics tasks | Data analysis, metrics, business intelligence |

## Parallel Patterns

**Research + Implementation:**
Spawn `the_didact` and `hollowed_eyes` in parallel. Didact researches while hollowed_eyes starts implementation with what's known.

**Implementation + QA:**
Spawn `hollowed_eyes` first. When done, spawn `loveless` to verify the work. NEVER spawn both simultaneously — QA needs something to verify.

**Architecture + Implementation + Docs:**
Spawn `the_architect` first for design. Then `hollowed_eyes` for implementation. Then `the_scribe` for documentation. Sequential when dependencies exist.

**Full review:**
Spawn `the_didact` (research), `the_architect` (architecture), `the_analyst` (metrics) in parallel. Synthesize findings.

## MCP Tools (always available, no team needed)

| Tool | Purpose |
|---|---|
| `mendicant_classify_task` | Classify task type and set strategy flags |
| `mendicant_verify` | Two-stage blind quality verification |
| `mendicant_route_tools` | Find relevant tools by semantic similarity |
| `mendicant_recommend` | Find similar historical patterns |
| `mendicant_record_pattern` | Record task outcome for learning |
| `mendicant_remember` | Store a fact in persistent memory |
| `mendicant_recall` | Retrieve stored facts |
| `mendicant_delegate` | Full autonomous delegation (single agent) |
| `mendicant_optimize_context` | Semantic context ranking |
| `mendicant_session_init` | Load memory + middleware state |
| `mendicant_status` | System health check |
