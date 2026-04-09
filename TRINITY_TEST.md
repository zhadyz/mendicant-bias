# Project Trinity — Mendicant Bias Stress Test

Paste this ENTIRE block as your first message in a fresh Claude Code session in this directory.

---

Build a complete real-time task management API with WebSocket notifications. This is a stress test of the Mendicant Bias middleware system — I need you to use /mendicant_bias for the full workflow. Every step must go through the Mendicant pipeline.

## Requirements

1. **REST API** (TypeScript + Hono on Bun):
   - `POST /api/tasks` — create task (title, description, priority, assignee)
   - `GET /api/tasks` — list tasks with filtering (status, priority, assignee)
   - `PATCH /api/tasks/:id` — update task (status transitions: todo → in_progress → review → done)
   - `DELETE /api/tasks/:id` — soft delete with audit trail
   - All inputs validated with zod schemas
   - JWT authentication middleware

2. **WebSocket** (real-time):
   - `/ws` endpoint for live task updates
   - Broadcast task changes to all connected clients
   - Connection health with ping/pong

3. **Database** (SQLite via Drizzle ORM):
   - Tasks table with timestamps, soft delete, audit fields
   - Users table for auth
   - Migration system

4. **Tests** (vitest):
   - Unit tests for zod schemas
   - Integration tests for each endpoint
   - WebSocket connection test

5. **Security review**:
   - After building, have loveless audit the code for vulnerabilities
   - JWT secret handling, SQL injection, input sanitization

## What I'm Actually Testing

This prompt should trigger the ENTIRE Mendicant pipeline:

- **SessionStart hook**: Should inject all Mahoraga rules + memory (TypeScript, zod, vitest, pnpm, Tailwind preferences)
- **FR5 classification**: Should classify as CRITICAL_CODE (JWT + auth + API)
- **UserPromptSubmit**: Should inject adaptation rules (zod validation, vitest not jest, typed API client)
- **PreToolUse**: When writing code, should inject style guidance (TypeScript strict, error handling)
- **PostToolUse**: Should run FR2 verification on every Write/Edit
- **Named agents**: Should spawn hollowed_eyes (implementation) + loveless (security)
- **Pattern recording**: Should record the outcome for future sessions
- **Mahoraga**: Should enforce "use vitest not jest", "validate with zod", "handle errors with try-catch"

After building everything, report:
1. Which Mendicant hooks fired and what they injected
2. Whether any Mahoraga rules influenced your decisions
3. Whether FR2 verification caught anything
4. Whether FR5 classified correctly
5. Honest assessment: did Mendicant make you better at this task, or was it overhead?
