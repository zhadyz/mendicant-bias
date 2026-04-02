"""Mendicant Bias CLI — Command-line interface for the Contender-class AI agent system."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

import click
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from mendicant_bias import __system__, __version__

console = Console()

BANNER = r"""
  __  __                _ _           _     ____  _
 |  \/  | ___ _ __   __| (_) ___ __ _| |_  | __ )(_) __ _ ___
 | |\/| |/ _ \ '_ \ / _` | |/ __/ _` | __| |  _ \| |/ _` / __|
 | |  | |  __/ | | | (_| | | (_| (_| | |_  | |_) | | (_| \__ \
 |_|  |_|\___|_| |_|\__,_|_|\___\__,_|\__| |____/|_|\__,_|___/
"""

CONFIG_TEMPLATE_PATH = Path(__file__).parent.parent.parent / "config" / "mendicant.yaml"
DEFAULT_CONFIG_ENV = "MENDICANT_CONFIG_PATH"


def _print_banner() -> None:
    """Display the Mendicant Bias identity banner."""
    console.print(
        Panel(
            Text(BANNER, style="cyan", justify="center"),
            title=f"[bold cyan]{__system__}[/bold cyan]",
            subtitle=f"[dim]v{__version__}[/dim]",
            border_style="cyan",
        )
    )


def _resolve_config() -> Path:
    """Resolve the configuration file path from env or default."""
    env_path = os.environ.get(DEFAULT_CONFIG_ENV)
    if env_path:
        return Path(env_path)
    # Check .mendicant/ in cwd first, then fallback to bundled config
    local = Path.cwd() / ".mendicant" / "mendicant.yaml"
    if local.exists():
        return local
    if CONFIG_TEMPLATE_PATH.exists():
        return CONFIG_TEMPLATE_PATH
    return local  # Will fail later with a helpful message


def _load_config(path: Path) -> dict:
    """Load YAML configuration, with a fallback if PyYAML is not installed."""
    try:
        import yaml
    except ImportError:
        console.print(
            "[yellow]Warning:[/yellow] PyYAML not installed. "
            "Using minimal default config. Install with: pip install pyyaml"
        )
        return {
            "system": {"name": __system__, "version": __version__},
            "gateway": {"host": "0.0.0.0", "port": 8001},
        }

    if not path.exists():
        console.print(
            f"[yellow]Warning:[/yellow] Config not found at {path}. "
            "Run [bold]mendicant init[/bold] to create one."
        )
        return {
            "system": {"name": __system__, "version": __version__},
            "gateway": {"host": "0.0.0.0", "port": 8001},
        }

    with open(path, "r") as f:
        return yaml.safe_load(f) or {}


# ---------------------------------------------------------------------------
# CLI group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(__version__, prog_name=__system__)
@click.pass_context
def cli(ctx: click.Context) -> None:
    """Mendicant Bias -- Contender-class AI agent system."""
    ctx.ensure_object(dict)
    config_path = _resolve_config()
    ctx.obj["config_path"] = config_path
    ctx.obj["config"] = _load_config(config_path)


# ---------------------------------------------------------------------------
# mendicant serve
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default=None, help="Gateway host (default from config)")
@click.option("--port", default=None, type=int, help="Gateway port (default from config)")
@click.pass_context
def serve(ctx: click.Context, host: Optional[str], port: Optional[int]) -> None:
    """Start all services (gateway + MCP server)."""
    _print_banner()
    config = ctx.obj["config"]
    gw = config.get("gateway", {})
    final_host = host or gw.get("host", "0.0.0.0")
    final_port = port or gw.get("port", 8001)

    console.print(f"[bold cyan]Starting Mendicant Bias[/bold cyan]")
    console.print(f"  Gateway : http://{final_host}:{final_port}")
    console.print(f"  MCP     : available via [bold]mendicant mcp[/bold] (stdio)")
    console.print(f"  Config  : {ctx.obj['config_path']}")
    console.print()

    try:
        from mendicant_gateway.app import create_app  # type: ignore[import-untyped]
    except ImportError:
        console.print(
            "[red]Error:[/red] mendicant-gateway package not installed. "
            "Install with: pip install mendicant-gateway"
        )
        raise SystemExit(1)

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Error:[/red] uvicorn not installed. Install with: pip install uvicorn"
        )
        raise SystemExit(1)

    app = create_app(config)
    console.print("[green]Gateway starting...[/green]")
    uvicorn.run(app, host=final_host, port=final_port, log_level="info")


# ---------------------------------------------------------------------------
# mendicant mcp
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def mcp(ctx: click.Context) -> None:
    """Start MCP server in stdio mode (for Claude Code integration)."""
    config = ctx.obj["config"]

    try:
        from mendicant_mcp_server.server import run_stdio  # type: ignore[import-untyped]
    except ImportError:
        # Fallback: try to run as a subprocess
        console.print(
            "[red]Error:[/red] mendicant-mcp-server package not installed. "
            "Install with: pip install mendicant-mcp-server",
            err=True,
        )
        raise SystemExit(1)

    run_stdio(config)


# ---------------------------------------------------------------------------
# mendicant gateway
# ---------------------------------------------------------------------------

@cli.command()
@click.option("--host", default=None, help="Gateway host (default from config)")
@click.option("--port", default=None, type=int, help="Gateway port (default from config)")
@click.pass_context
def gateway(ctx: click.Context, host: Optional[str], port: Optional[int]) -> None:
    """Start gateway API only."""
    _print_banner()
    config = ctx.obj["config"]
    gw = config.get("gateway", {})
    final_host = host or gw.get("host", "0.0.0.0")
    final_port = port or gw.get("port", 8001)

    console.print(f"[bold cyan]Starting Gateway[/bold cyan]")
    console.print(f"  Listening : http://{final_host}:{final_port}")
    console.print(f"  Config    : {ctx.obj['config_path']}")
    console.print()

    try:
        from mendicant_gateway.app import create_app  # type: ignore[import-untyped]
    except ImportError:
        console.print(
            "[red]Error:[/red] mendicant-gateway package not installed. "
            "Install with: pip install mendicant-gateway"
        )
        raise SystemExit(1)

    try:
        import uvicorn
    except ImportError:
        console.print(
            "[red]Error:[/red] uvicorn not installed. Install with: pip install uvicorn"
        )
        raise SystemExit(1)

    app = create_app(config)
    uvicorn.run(app, host=final_host, port=final_port, log_level="info")


# ---------------------------------------------------------------------------
# mendicant status
# ---------------------------------------------------------------------------

MIDDLEWARE_NAMES = {
    "semantic_tool_router": "FR1 — Semantic Tool Router",
    "verification": "FR2 — Verification Gates",
    "adaptive_learning": "FR3 — Adaptive Learning",
    "context_budget": "FR4 — Context Budget",
    "smart_task_router": "FR5 — Smart Task Router",
}


@cli.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show system status (middleware, agents, patterns)."""
    _print_banner()
    config = ctx.obj["config"]
    sys_cfg = config.get("system", {})
    mendicant_cfg = config.get("mendicant", {})

    # System info
    console.print(
        Panel(
            f"[bold]{sys_cfg.get('name', __system__)}[/bold] v{sys_cfg.get('version', __version__)}",
            title="[cyan]System[/cyan]",
            border_style="cyan",
        )
    )

    # Middleware table
    table = Table(
        title="Middleware Status",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("ID", style="bold")
    table.add_column("Middleware", style="white")
    table.add_column("Status", justify="center")
    table.add_column("Detail", style="dim")

    for key, label in MIDDLEWARE_NAMES.items():
        mw_cfg = mendicant_cfg.get(key, {})
        if mw_cfg:
            enabled = mw_cfg.get("enabled", True)
            status_text = "[green]active[/green]" if enabled else "[red]disabled[/red]"
            # Build a short detail string from key config values
            detail_parts = []
            if "similarity_threshold" in mw_cfg:
                detail_parts.append(f"thresh={mw_cfg['similarity_threshold']}")
            if "top_k" in mw_cfg:
                detail_parts.append(f"top_k={mw_cfg['top_k']}")
            if "min_score" in mw_cfg:
                detail_parts.append(f"min_score={mw_cfg['min_score']}")
            if "max_retries" in mw_cfg:
                detail_parts.append(f"retries={mw_cfg['max_retries']}")
            if "default_budget" in mw_cfg:
                detail_parts.append(f"budget={mw_cfg['default_budget']}")
            if "max_patterns" in mw_cfg:
                detail_parts.append(f"max_pat={mw_cfg['max_patterns']}")
            if "embedding_weight" in mw_cfg:
                detail_parts.append(f"emb_w={mw_cfg['embedding_weight']}")
            detail = ", ".join(detail_parts) if detail_parts else "—"
        else:
            status_text = "[yellow]not configured[/yellow]"
            detail = "—"

        table.add_row(key, label, status_text, detail)

    console.print(table)

    # Runtime info
    runtime = config.get("runtime", {})
    if runtime:
        console.print()
        rt_table = Table(
            title="Runtime",
            show_header=True,
            header_style="bold cyan",
            border_style="dim",
        )
        rt_table.add_column("Setting", style="bold")
        rt_table.add_column("Value")
        rt_table.add_row("Model", str(runtime.get("model", "—")))
        rt_table.add_row("Max Turns", str(runtime.get("max_turns", "—")))
        rt_table.add_row("Thinking", str(runtime.get("thinking_enabled", "—")))
        console.print(rt_table)

    # Config path
    console.print()
    console.print(f"[dim]Config: {ctx.obj['config_path']}[/dim]")


# ---------------------------------------------------------------------------
# mendicant agents
# ---------------------------------------------------------------------------

@cli.command()
@click.pass_context
def agents(ctx: click.Context) -> None:
    """List named agents."""
    _print_banner()
    config = ctx.obj["config"]
    agents_cfg = config.get("agents", {})
    mapping_path = agents_cfg.get("mapping_path")

    if not mapping_path:
        console.print("[yellow]No agent mapping path configured.[/yellow]")
        console.print("Set [bold]agents.mapping_path[/bold] in your config.")
        return

    mapping_file = Path(mapping_path)
    if not mapping_file.is_absolute():
        # Resolve relative to config file location
        config_dir = ctx.obj["config_path"].parent
        mapping_file = config_dir / mapping_file

    if not mapping_file.exists():
        console.print(f"[yellow]Agent mapping not found:[/yellow] {mapping_file}")
        console.print("Create the file or update [bold]agents.mapping_path[/bold].")
        return

    with open(mapping_file, "r") as f:
        mapping = json.load(f)

    table = Table(
        title="Named Agents",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Agent", style="bold")
    table.add_column("Profile", style="white")
    table.add_column("Description", style="dim")

    if isinstance(mapping, dict):
        for name, info in mapping.items():
            if isinstance(info, dict):
                table.add_row(
                    name,
                    info.get("profile", "—"),
                    info.get("description", "—"),
                )
            else:
                table.add_row(name, str(info), "—")
    elif isinstance(mapping, list):
        for agent in mapping:
            table.add_row(
                agent.get("name", "—"),
                agent.get("profile", "—"),
                agent.get("description", "—"),
            )

    console.print(table)


# ---------------------------------------------------------------------------
# mendicant classify
# ---------------------------------------------------------------------------

@cli.command()
@click.argument("task", required=False)
@click.pass_context
def classify(ctx: click.Context, task: Optional[str]) -> None:
    """Classify a task (from argument or stdin)."""
    if task is None:
        if sys.stdin.isatty():
            console.print("[yellow]Provide a task as an argument or pipe via stdin.[/yellow]")
            console.print("  mendicant classify 'research quantum computing'")
            console.print("  echo 'research quantum computing' | mendicant classify")
            raise SystemExit(1)
        task = sys.stdin.read().strip()

    if not task:
        console.print("[red]Error:[/red] Empty task provided.")
        raise SystemExit(1)

    console.print(f"[bold cyan]Classifying task:[/bold cyan] {task}")
    console.print()

    try:
        from mendicant_core.middleware.smart_task_router import SmartTaskRouterMiddleware  # type: ignore[import-untyped]
    except ImportError:
        console.print(
            "[red]Error:[/red] mendicant-core package not installed. "
            "Install with: pip install mendicant-core"
        )
        raise SystemExit(1)

    config = ctx.obj["config"]
    router_cfg = config.get("mendicant", {}).get("smart_task_router", {})

    try:
        router = SmartTaskRouterMiddleware(
            patterns_store_path=router_cfg.get("patterns_store_path"),
            embedding_model=router_cfg.get("embedding_model", "all-MiniLM-L6-v2"),
            embedding_weight=float(router_cfg.get("embedding_weight", 0.5)),
            min_embedding_similarity=float(router_cfg.get("min_embedding_similarity", 0.55)),
        )
        task_type, confidence = router._classify_keywords(task)
        from mendicant_core.middleware.smart_task_router import _FLAGS
        flags = _FLAGS.get(task_type, {})
        result = {
            "task_type": task_type,
            "confidence": confidence,
            "verification_enabled": flags.get("verification_enabled", False),
            "subagent_enabled": flags.get("subagent_enabled", False),
            "thinking_enabled": flags.get("thinking_enabled", False),
        }
    except Exception as e:
        console.print(f"[red]Classification error:[/red] {e}")
        raise SystemExit(1)

    # Display result
    table = Table(
        title="Classification Result",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Field", style="bold")
    table.add_column("Value")

    for key, value in result.items():
        table.add_row(str(key), str(value))

    console.print(table)


# ---------------------------------------------------------------------------
# mendicant init
# ---------------------------------------------------------------------------

MENDICANT_YAML_TEMPLATE = """\
# Mendicant Bias V5 Configuration
# ================================

system:
  name: mendicant-bias
  version: "5.0.0"

# Runtime settings
runtime:
  model: claude-sonnet-4-20250514
  max_turns: 25
  thinking_enabled: true

# Middleware configuration (FR1-FR5)
mendicant:
  semantic_tool_router:
    registry_path: .mendicant/tool_registry.json
    embedding_model: all-MiniLM-L6-v2
    top_k: 5
    similarity_threshold: 0.4

  verification:
    enabled: true
    model: claude-sonnet-4-20250514
    temperature: 0.1
    min_score: 0.7
    max_retries: 2

  adaptive_learning:
    store_path: .mendicant/orchestration_patterns.json
    max_patterns: 1000
    min_success_rate: 0.6
    embedding_model: all-MiniLM-L6-v2

  context_budget:
    default_budget: 30000
    strategies:
      - key_fields
      - statistical_summary
      - truncation

  smart_task_router:
    patterns_store_path: .mendicant/orchestration_patterns.json
    embedding_model: all-MiniLM-L6-v2
    embedding_weight: 0.5
    min_embedding_similarity: 0.55

# Gateway settings
gateway:
  host: "0.0.0.0"
  port: 8001

# Agent settings
agents:
  profiles_dir: agents/profiles
  mapping_path: agents/agent_mapping.json
"""


@cli.command()
@click.option(
    "--dir",
    "target_dir",
    default=".",
    help="Directory to initialize (default: current directory)",
)
def init(target_dir: str) -> None:
    """Initialize .mendicant/ directory with config."""
    _print_banner()
    base = Path(target_dir).resolve()
    mendicant_dir = base / ".mendicant"

    if mendicant_dir.exists():
        console.print(
            f"[yellow]Directory already exists:[/yellow] {mendicant_dir}"
        )
        if not click.confirm("Overwrite existing configuration?"):
            console.print("[dim]Aborted.[/dim]")
            return

    mendicant_dir.mkdir(parents=True, exist_ok=True)

    # Write mendicant.yaml
    config_file = mendicant_dir / "mendicant.yaml"
    config_file.write_text(MENDICANT_YAML_TEMPLATE)

    # Create empty data files
    tool_registry = mendicant_dir / "tool_registry.json"
    if not tool_registry.exists():
        tool_registry.write_text(json.dumps({"tools": []}, indent=2))

    patterns_file = mendicant_dir / "orchestration_patterns.json"
    if not patterns_file.exists():
        patterns_file.write_text(json.dumps({"patterns": []}, indent=2))

    # Summary
    console.print(f"[green]Initialized Mendicant Bias[/green] at {mendicant_dir}")
    console.print()

    table = Table(show_header=False, border_style="dim")
    table.add_column("File", style="bold")
    table.add_column("Purpose", style="dim")
    table.add_row("mendicant.yaml", "Main configuration")
    table.add_row("tool_registry.json", "Semantic tool router registry")
    table.add_row("orchestration_patterns.json", "Adaptive learning patterns")
    console.print(table)

    console.print()
    console.print("[dim]Next steps:[/dim]")
    console.print("  1. Edit .mendicant/mendicant.yaml with your settings")
    console.print("  2. Set ANTHROPIC_API_KEY in your environment")
    console.print("  3. Run [bold]mendicant serve[/bold] to start")


# ---------------------------------------------------------------------------
# mendicant install-hooks
# ---------------------------------------------------------------------------

@cli.command("install-hooks")
@click.option(
    "--gateway-url",
    default="http://localhost:8001",
    help="Mendicant Bias gateway URL (default: http://localhost:8001)",
)
@click.option(
    "--settings-path",
    default=None,
    type=click.Path(),
    help="Path to Claude Code settings.json (default: auto-detect)",
)
def install_hooks(gateway_url: str, settings_path: Optional[str]) -> None:
    """Install Mendicant Bias hooks into Claude Code settings."""
    _print_banner()

    try:
        from mendicant_bias.hooks import (
            install_hooks as _install,
            generate_hooks_config,
            _default_cc_settings_path,
        )
    except ImportError:
        console.print(
            "[red]Error:[/red] Could not import hooks module. "
            "Ensure mendicant-bias is installed."
        )
        raise SystemExit(1)

    resolved_path = Path(settings_path) if settings_path else _default_cc_settings_path()
    console.print(f"[bold cyan]Installing Mendicant Bias hooks[/bold cyan]")
    console.print(f"  Gateway URL   : {gateway_url}")
    console.print(f"  Settings file : {resolved_path}")
    console.print()

    # Show the hooks being installed
    config = generate_hooks_config(gateway_url)
    hooks = config["hooks"]
    table = Table(
        title="Hook Endpoints",
        show_header=True,
        header_style="bold cyan",
        border_style="dim",
    )
    table.add_column("Event", style="bold")
    table.add_column("Matcher", style="white")
    table.add_column("URL", style="dim")
    table.add_column("Timeout", justify="right")

    for event_name, event_hooks in hooks.items():
        for hook_group in event_hooks:
            matcher = hook_group.get("matcher", "*")
            for hook in hook_group.get("hooks", []):
                table.add_row(
                    event_name,
                    matcher or "(all)",
                    hook.get("url", ""),
                    f"{hook.get('timeout', '?')}s",
                )

    console.print(table)
    console.print()

    success = _install(
        settings_path=Path(settings_path) if settings_path else None,
        gateway_url=gateway_url,
    )

    if success:
        console.print("[green]Hooks installed successfully.[/green]")
        console.print()
        console.print("[dim]Next steps:[/dim]")
        console.print("  1. Start the gateway: [bold]mendicant serve[/bold]")
        console.print("  2. Restart Claude Code to pick up the new hooks")
        console.print(
            "  3. Mendicant will now run inside CC's execution pipeline"
        )
    else:
        console.print("[red]Failed to install hooks.[/red]")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# mendicant remove-hooks
# ---------------------------------------------------------------------------

@cli.command("remove-hooks")
@click.option(
    "--settings-path",
    default=None,
    type=click.Path(),
    help="Path to Claude Code settings.json (default: auto-detect)",
)
@click.option(
    "--gateway-url",
    default="http://localhost:8001",
    help="Gateway URL to match when removing hooks (default: http://localhost:8001)",
)
def remove_hooks(settings_path: Optional[str], gateway_url: str) -> None:
    """Remove Mendicant Bias hooks from Claude Code settings."""
    _print_banner()

    try:
        from mendicant_bias.hooks import (
            remove_hooks as _remove,
            _default_cc_settings_path,
        )
    except ImportError:
        console.print(
            "[red]Error:[/red] Could not import hooks module. "
            "Ensure mendicant-bias is installed."
        )
        raise SystemExit(1)

    resolved_path = Path(settings_path) if settings_path else _default_cc_settings_path()
    console.print(f"[bold cyan]Removing Mendicant Bias hooks[/bold cyan]")
    console.print(f"  Settings file : {resolved_path}")
    console.print()

    success = _remove(
        settings_path=Path(settings_path) if settings_path else None,
        gateway_url=gateway_url,
    )

    if success:
        console.print("[green]Hooks removed successfully.[/green]")
        console.print("[dim]Restart Claude Code for changes to take effect.[/dim]")
    else:
        console.print("[red]Failed to remove hooks.[/red]")
        raise SystemExit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    cli()
