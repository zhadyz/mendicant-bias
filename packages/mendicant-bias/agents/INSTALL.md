# Installing Mendicant Agents in Claude Code

These are Claude Code agent definition files for all 13 Mendicant Bias named agents.
Each agent becomes a native CC citizen, spawnable via `Agent({subagent_type: "hollowed_eyes"})`.

## Quick Install

Copy these files to your project's `.claude/agents/` directory:

```bash
cp packages/mendicant-bias/agents/*.md .claude/agents/
```

Or for global availability across all projects:

```bash
cp packages/mendicant-bias/agents/*.md ~/.claude/agents/
```

After copying, restart Claude Code. The agents will be available via:

```
Agent({subagent_type: "hollowed_eyes", description: "...", prompt: "..."})
```

## Agent Roster

| Agent | Color | Domain | Description |
|-------|-------|--------|-------------|
| hollowed_eyes | cyan | Code | Elite developer with semantic code search and GitHub operations |
| the_didact | blue | Research | Research and intelligence with web scraping and documentation |
| loveless | red | QA/Security | QA specialist with cross-browser testing and live debugging |
| zhadyz | yellow | DevOps | DevOps with GitHub workflows, containers, and releases |
| cinna | purple | Design | Design systems and UI/UX with purpose-driven philosophy |
| the_architect | blue | Architecture | System architecture and technical design |
| the_oracle | yellow | Validation | Strategic validation and risk assessment advisor |
| the_scribe | green | Documentation | Technical writing and documentation |
| the_curator | green | Maintenance | Repository maintenance and code health |
| the_cartographer | yellow | Deployment | Deployment and infrastructure management |
| the_librarian | purple | Requirements | Requirements clarification and specification |
| the_sentinel | red | CI/CD | Pipeline automation and monitoring |
| the_analyst | yellow | Analytics | Data analysis and business intelligence |

## Prerequisites

All agents declare `mendicant-bias` as a required MCP server. Ensure the Mendicant Bias
MCP server is configured in your Claude Code settings before using these agents.

## Agent Collaboration

These agents are designed to work together:

- **Research and Implementation**: the_didact -> hollowed_eyes
- **Design and Implementation**: cinna -> hollowed_eyes
- **Implementation and Testing**: hollowed_eyes -> loveless
- **Deployment Pipeline**: the_sentinel -> the_cartographer -> zhadyz
- **Pre-decision Validation**: the_oracle (before any major decision)
- **Requirements to Code**: the_librarian -> hollowed_eyes
- **Maintenance Cycle**: the_curator <-> zhadyz (handoff protocol)

## Excluding INSTALL.md

When copying to `.claude/agents/`, you may want to exclude this file:

```bash
for f in packages/mendicant-bias/agents/*.md; do
  [ "$(basename "$f")" != "INSTALL.md" ] && cp "$f" .claude/agents/
done
```
