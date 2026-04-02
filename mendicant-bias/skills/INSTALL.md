# Installing Mendicant Bias Skills

## Prerequisites

- Claude Code CLI installed and configured
- The `.claude/skills/` directory exists in your project root

## Installation

Copy the skill directory into your Claude Code skills folder:

```bash
cp -r packages/mendicant-bias/skills/mendicant .claude/skills/
```

This registers the `/mendicant` slash command in Claude Code.

## Verification

After copying, the `/mendicant` command should appear in Claude Code's skill list.
You can invoke it by typing `/mendicant` in the Claude Code prompt.

## Updating

To update to a newer version, repeat the copy command. It will overwrite the
existing skill files.
