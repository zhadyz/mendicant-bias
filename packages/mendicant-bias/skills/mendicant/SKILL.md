---
name: mendicant
description: Mendicant Bias intelligence middleware — classify tasks, verify outputs, get recommendations
user-invocable: true
when_to_use: "Use to classify tasks, verify code quality, get strategy recommendations, check middleware status, or interact with named agents"
allowed-tools: []
---

# Mendicant Bias

Intelligence middleware for Claude Code. Available tools:

## Quick Reference

| Tool | Purpose |
|---|---|
| `mendicant_classify_task` | Classify task type and set strategy flags |
| `mendicant_route_tools` | Find relevant tools by semantic similarity |
| `mendicant_verify` | Two-stage blind quality verification |
| `mendicant_compress` | Compress messages to fit token budget |
| `mendicant_recommend` | Find similar historical patterns |
| `mendicant_record_pattern` | Record task outcome for learning |
| `mendicant_remember` | Store a fact in memory |
| `mendicant_recall` | Retrieve facts from memory |
| `mendicant_status` | Check system health |
| `mendicant_list_agents` | List named specialist agents |
| `mendicant_get_agent` | Get agent profile details |

## Recommended Workflow

1. **Classify** the task first: `mendicant_classify_task`
2. **Route** relevant tools: `mendicant_route_tools`
3. **Execute** the task using appropriate tools
4. **Verify** the output: `mendicant_verify`
5. **Record** the pattern: `mendicant_record_pattern`

## Named Agents

13 specialist agents available via `Agent({subagent_type: "name"})`:

- **hollowed_eyes** — Code engineering
- **the_didact** — Research & analysis
- **loveless** — QA & security testing
- **zhadyz** — DevOps & releases
- **cinna** — UI/UX design
- **the_architect** — System architecture
- **the_oracle** — Validation & review
- **the_scribe** — Documentation
- **the_curator** — Code health & cleanup
- **the_cartographer** — Deployment & infra
- **the_librarian** — Requirements
- **the_sentinel** — CI/CD pipelines
- **the_analyst** — Data & metrics
