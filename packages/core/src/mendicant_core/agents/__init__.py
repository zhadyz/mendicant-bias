"""
mendicant_core.agents
=====================
Load and manage named agent profiles with YAML frontmatter parsing
and domain-to-agent mapping.

Usage
-----
>>> from mendicant_core.agents import AgentLoader
>>> loader = AgentLoader("agents/profiles/", "config/agent_mapping.json")
>>> profile = loader.get_profile("hollowed_eyes")
>>> profile.name
'hollowed_eyes'
>>> loader.get_agent_for_domain("code_engineering")
'hollowed_eyes'
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# YAML frontmatter regex — matches content between opening and closing ---
# ---------------------------------------------------------------------------

_FRONTMATTER_RE = re.compile(
    r"\A\s*---\s*\n(.*?)\n---\s*\n?(.*)",
    re.DOTALL,
)


# ---------------------------------------------------------------------------
# AgentProfile dataclass
# ---------------------------------------------------------------------------


@dataclass
class AgentProfile:
    """Parsed agent profile loaded from a ``.md`` file with YAML frontmatter."""

    name: str
    description: str = ""
    model: str = "sonnet"
    color: str = "white"
    domains: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    content: str = ""  # Full markdown content (body after frontmatter)


# ---------------------------------------------------------------------------
# AgentLoader
# ---------------------------------------------------------------------------


class AgentLoader:
    """
    Load and manage named agent profiles.

    Profiles are ``.md`` files with optional YAML frontmatter delimited by
    ``---``.  The loader parses frontmatter keys into ``AgentProfile``
    dataclasses and retains the full markdown body for prompt injection.

    The optional ``agent_mapping.json`` provides domain-to-agent-name
    lookups used by the orchestrator.

    Parameters
    ----------
    profiles_dir : str | Path
        Directory containing agent ``.md`` profile files.
    mapping_path : str | Path | None
        Path to the ``agent_mapping.json`` domain-to-agent mapping.
        If ``None``, domain lookups will use agent-declared domains only.
    """

    def __init__(
        self,
        profiles_dir: str | Path,
        mapping_path: str | Path | None = None,
    ) -> None:
        self.profiles_dir = Path(profiles_dir)
        self.mapping_path = Path(mapping_path) if mapping_path else None

        self._profiles: dict[str, AgentProfile] = {}
        self._domain_map: dict[str, str] = {}
        self._fallback_agent: str = "hollowed_eyes"
        self._capabilities: dict[str, Any] = {}

        self._load_profiles()
        self._load_mapping()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_profile(self, name: str) -> AgentProfile | None:
        """
        Return the parsed profile for the agent with the given *name*.

        Parameters
        ----------
        name : str
            Agent name (file stem, e.g. ``"hollowed_eyes"``).

        Returns
        -------
        AgentProfile | None
            The profile, or ``None`` if no matching file was found.
        """
        return self._profiles.get(name)

    def get_agent_for_domain(self, domain: str) -> str:
        """
        Look up which agent handles *domain*.

        Checks the explicit mapping first, then falls back to scanning
        agent-declared domains, and finally returns the fallback agent.

        Parameters
        ----------
        domain : str
            Domain key (e.g. ``"code_engineering"``).

        Returns
        -------
        str
            The agent name.
        """
        # Check explicit mapping
        if domain in self._domain_map:
            return self._domain_map[domain]

        # Check agent-declared domains
        for name, profile in self._profiles.items():
            if domain in profile.domains:
                return name

        return self._fallback_agent

    def list_agents(self) -> list[str]:
        """
        Return all available agent names (sorted).

        Returns
        -------
        list[str]
        """
        return sorted(self._profiles.keys())

    def list_domains(self) -> list[str]:
        """
        Return all mapped domain names (sorted).

        Includes both explicitly mapped domains and domains declared in
        agent profiles.

        Returns
        -------
        list[str]
        """
        all_domains: set[str] = set(self._domain_map.keys())
        for profile in self._profiles.values():
            all_domains.update(profile.domains)
        return sorted(all_domains)

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def _load_profiles(self) -> None:
        """Scan profiles_dir for .md files and parse each into an AgentProfile."""
        if not self.profiles_dir.exists():
            logger.warning(
                "[AgentLoader] Profiles directory does not exist: %s",
                self.profiles_dir,
            )
            return

        for md_path in sorted(self.profiles_dir.glob("*.md")):
            try:
                profile = self._parse_profile(md_path)
                self._profiles[profile.name] = profile
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentLoader] Failed to parse %s: %s", md_path.name, exc
                )

        logger.info(
            "[AgentLoader] Loaded %d agent profiles from %s",
            len(self._profiles),
            self.profiles_dir,
        )

    def _load_mapping(self) -> None:
        """Load the domain-to-agent mapping from JSON."""
        if self.mapping_path is None or not self.mapping_path.exists():
            logger.debug("[AgentLoader] No agent mapping file; using profile-declared domains only")
            return

        try:
            with self.mapping_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)

            self._domain_map = data.get("domain_to_agent", {})
            self._fallback_agent = data.get("fallback_agent", "hollowed_eyes")
            self._capabilities = data.get("agent_capabilities", {})

            # Backfill profile metadata from capabilities if profiles were sparse
            for agent_name, caps in self._capabilities.items():
                if agent_name in self._profiles:
                    profile = self._profiles[agent_name]
                    if not profile.description and caps.get("description"):
                        profile.description = caps["description"]
                    if not profile.domains and caps.get("domains"):
                        profile.domains = caps["domains"]
                    if not profile.tools and caps.get("tools"):
                        profile.tools = caps["tools"]

            logger.info(
                "[AgentLoader] Loaded domain mapping with %d domains, fallback=%s",
                len(self._domain_map),
                self._fallback_agent,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("[AgentLoader] Failed to load mapping: %s", exc)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_profile(md_path: Path) -> AgentProfile:
        """
        Parse a ``.md`` file with optional YAML frontmatter into an AgentProfile.

        Frontmatter is delimited by ``---`` at the top of the file.  Supported
        keys: ``name``, ``description``, ``model``, ``color``, ``domains``,
        ``tools``.  The body (everything after the closing ``---``) is stored
        as ``content``.
        """
        raw = md_path.read_text(encoding="utf-8")
        name = md_path.stem

        match = _FRONTMATTER_RE.match(raw)
        if not match:
            # No frontmatter — entire file is content
            return AgentProfile(name=name, content=raw.strip())

        frontmatter_block = match.group(1)
        body = match.group(2).strip()

        # Lightweight YAML parsing — handles simple key: value and lists
        meta = _parse_simple_yaml(frontmatter_block)

        return AgentProfile(
            name=meta.get("name", name),
            description=meta.get("description", ""),
            model=meta.get("model", "sonnet"),
            color=meta.get("color", "white"),
            domains=_ensure_list(meta.get("domains", [])),
            tools=_ensure_list(meta.get("tools", [])),
            content=body,
        )


# ---------------------------------------------------------------------------
# Lightweight YAML helpers
# ---------------------------------------------------------------------------


def _parse_simple_yaml(text: str) -> dict[str, Any]:
    """
    Parse a minimal subset of YAML (scalar values and inline/block lists).

    This avoids requiring PyYAML as a dependency for simple frontmatter.
    For production use with complex YAML, consider ``yaml.safe_load``.
    """
    result: dict[str, Any] = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        # List continuation: "  - value"
        if stripped.startswith("- ") and current_key is not None and current_list is not None:
            current_list.append(stripped[2:].strip().strip("\"'"))
            result[current_key] = current_list
            continue

        # Key: value pair
        colon_idx = stripped.find(":")
        if colon_idx == -1:
            continue

        # Flush any pending list
        current_list = None

        key = stripped[:colon_idx].strip()
        value = stripped[colon_idx + 1 :].strip()

        if not value:
            # Might be a block list starting on the next line
            current_key = key
            current_list = []
            result[key] = current_list
            continue

        # Inline list: [a, b, c]
        if value.startswith("[") and value.endswith("]"):
            items = [
                item.strip().strip("\"'")
                for item in value[1:-1].split(",")
                if item.strip()
            ]
            result[key] = items
            current_key = key
            current_list = None
            continue

        # Scalar
        result[key] = value.strip("\"'")
        current_key = key
        current_list = None

    return result


def _ensure_list(value: Any) -> list[str]:
    """Coerce *value* into a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if isinstance(value, str):
        return [s.strip() for s in value.split(",") if s.strip()]
    return []
