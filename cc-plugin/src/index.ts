/**
 * Mendicant Bias — Claude Code Plugin
 *
 * Integrates Mendicant Bias intelligence middleware natively into Claude Code.
 *
 * What this plugin provides:
 * - MCP server: 16 tools (classify, route, verify, compress, optimize, delegate, etc.)
 * - 13 named specialist agents (hollowed_eyes, the_didact, loveless, etc.)
 * - /mendicant_bias skill with full tool reference
 * - Auto-configures everything on install
 *
 * The MCP server is managed by the plugin — no manual mcp.json configuration needed.
 * Agents and skills are bundled — no manual file copying needed.
 *
 * Plugin loading:
 *   Claude Code discovers this plugin via plugin.json in the package root.
 *   - mcpServers: auto-starts `mendicant mcp` as a stdio MCP server
 *   - agents/: auto-registers all 13 .md agent definitions
 *   - skills/mendicant_bias/SKILL.md: auto-registers the /mendicant_bias skill
 */

export const name = "mendicant-bias";
export const version = "5.5.0";
