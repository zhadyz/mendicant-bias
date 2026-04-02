"""
mendicant_core.orchestrator
===========================
Mendicant Bias V5 — Task Orchestrator with Named Agent Routing

Modernized async-capable orchestrator combining V3 domain-based routing
with V4 FR1 embedding-powered semantic search.  Routes queries to the
appropriate specialist agent(s) via tool registry semantic search and
domain-to-agent mapping.

Usage
-----
>>> from mendicant_core.orchestrator import MendicantOrchestrator
>>> orch = MendicantOrchestrator(
...     registry_path="tools/tools_schema.json",
...     agents_dir="agents/",
...     agent_mapping_path="config/agent_mapping.json",
... )
>>> decision = orch.route("Deploy my Next.js app to Vercel")
>>> decision["primary_domain"]
'cloud_infra'
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MendicantOrchestrator:
    """
    Mendicant Bias V5 — Task Orchestrator with Named Agent Routing.

    Combines semantic tool search (via RegistryQuery) with domain scoring
    and named agent assignment to produce routing decisions for incoming
    queries.

    Parameters
    ----------
    registry_path : str | Path
        Path to the tool registry JSON file consumed by RegistryQuery.
    agents_dir : str | Path
        Directory containing agent profile ``.md`` files.
    agent_mapping_path : str | Path
        Path to the ``agent_mapping.json`` file that maps domains to
        named agents.
    """

    def __init__(
        self,
        registry_path: str | Path,
        agents_dir: str | Path,
        agent_mapping_path: str | Path,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.agents_dir = Path(agents_dir)
        self.agent_mapping_path = Path(agent_mapping_path)

        # Load tool registry via FR1 semantic search
        from mendicant_core.middleware.registry import RegistryQuery

        self._registry = RegistryQuery(str(self.registry_path))

        # Load agent mapping
        self._agent_mapping = self._load_agent_mapping()

        logger.info(
            "[MendicantOrchestrator] Initialized — registry=%s, agents=%s",
            self.registry_path,
            self.agents_dir,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def execute(self, query: str, top_k: int = 10) -> dict[str, Any]:
        """
        Route query and return a full routing decision with agent assignments.

        This is the primary async entry point.  It performs semantic search,
        domain scoring, strategy selection, and agent assignment.

        Parameters
        ----------
        query : str
            Natural-language task description.
        top_k : int
            Number of relevant tools to retrieve from the registry.

        Returns
        -------
        dict
            Routing decision containing keys: ``query``, ``primary_domain``,
            ``secondary_domains``, ``execution_strategy``, ``assigned_agents``,
            ``relevant_tools``, ``domain_scores``.
        """
        routing = self.route(query, top_k=top_k)

        # Augment with agent assignments
        assigned_agents: list[dict[str, str]] = []

        primary_agent = self.get_agent_for_domain(routing["primary_domain"])
        assigned_agents.append(
            {
                "agent": primary_agent or routing["primary_domain"],
                "domain": routing["primary_domain"],
                "role": "primary",
            }
        )

        for sec_domain in routing["secondary_domains"]:
            sec_agent = self.get_agent_for_domain(sec_domain)
            assigned_agents.append(
                {
                    "agent": sec_agent or sec_domain,
                    "domain": sec_domain,
                    "role": "secondary",
                }
            )

        routing["assigned_agents"] = assigned_agents
        return routing

    def route(self, query: str, top_k: int = 10) -> dict[str, Any]:
        """
        Synchronous routing — find relevant tools, score domains, select strategy.

        Parameters
        ----------
        query : str
            Natural-language task description.
        top_k : int
            Number of relevant tools to retrieve.

        Returns
        -------
        dict
            Routing decision with keys: ``query``, ``primary_domain``,
            ``secondary_domains``, ``relevant_tools``, ``domain_scores``,
            ``execution_strategy``.
        """
        # Step 1: Semantic search for relevant tools
        relevant_tools = self._registry.find_relevant_tools(query, top_k=top_k)

        if not relevant_tools:
            return {
                "query": query,
                "primary_domain": "general",
                "secondary_domains": [],
                "relevant_tools": [],
                "domain_scores": {},
                "execution_strategy": "single_agent",
            }

        # Step 2: Score domains by accumulated similarity
        domain_scores = self._score_domains(relevant_tools)

        # Step 3: Identify primary + secondary domains
        primary_domain = max(domain_scores, key=domain_scores.get)
        secondary_domains = sorted(
            [d for d in domain_scores if d != primary_domain],
            key=lambda d: domain_scores[d],
            reverse=True,
        )[:2]

        # Step 4: Determine execution strategy
        execution_strategy = self._determine_strategy(
            primary_domain, secondary_domains, relevant_tools
        )

        return {
            "query": query,
            "primary_domain": primary_domain,
            "secondary_domains": secondary_domains,
            "relevant_tools": relevant_tools,
            "domain_scores": domain_scores,
            "execution_strategy": execution_strategy,
        }

    def get_agent_for_domain(self, domain: str) -> str | None:
        """
        Look up the named agent assigned to *domain*.

        Parameters
        ----------
        domain : str
            Domain key (e.g. ``"code_engineering"``).

        Returns
        -------
        str | None
            Agent name, or ``None`` if no mapping exists (falls back to
            the ``"fallback_agent"`` if configured).
        """
        domain_map = self._agent_mapping.get("domain_to_agent", {})
        agent = domain_map.get(domain)
        if agent is None:
            agent = self._agent_mapping.get("fallback_agent")
        return agent

    def get_stats(self) -> dict[str, Any]:
        """
        Return orchestrator statistics.

        Returns
        -------
        dict
            Keys: ``registry_tools``, ``mapped_domains``, ``mapped_agents``,
            ``fallback_agent``.
        """
        domain_map = self._agent_mapping.get("domain_to_agent", {})
        capabilities = self._agent_mapping.get("agent_capabilities", {})
        return {
            "registry_tools": self._registry.tool_count()
            if hasattr(self._registry, "tool_count")
            else 0,
            "mapped_domains": list(domain_map.keys()),
            "mapped_agents": list(set(domain_map.values())),
            "agent_capabilities": list(capabilities.keys()),
            "fallback_agent": self._agent_mapping.get("fallback_agent"),
        }

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _load_agent_mapping(self) -> dict[str, Any]:
        """Load the domain-to-agent mapping JSON file."""
        if not self.agent_mapping_path.exists():
            logger.warning(
                "[MendicantOrchestrator] Agent mapping not found: %s — using defaults",
                self.agent_mapping_path,
            )
            return {
                "domain_to_agent": {
                    "code_engineering": "hollowed_eyes",
                    "cloud_infra": "the_cartographer",
                    "memory_knowledge": "the_didact",
                    "browser_testing": "loveless",
                    "ai_data": "the_didact",
                    "commerce": "the_analyst",
                    "design_media": "cinna",
                    "documentation": "the_scribe",
                    "productivity": "the_analyst",
                    "orchestration": "mendicant_bias",
                    "core": "hollowed_eyes",
                },
                "fallback_agent": "hollowed_eyes",
            }

        try:
            with self.agent_mapping_path.open("r", encoding="utf-8") as fh:
                return json.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "[MendicantOrchestrator] Failed to load agent mapping: %s", exc
            )
            return {"domain_to_agent": {}, "fallback_agent": "hollowed_eyes"}

    @staticmethod
    def _score_domains(tools: list[dict[str, Any]]) -> dict[str, float]:
        """
        Score domains by accumulating similarity scores from retrieved tools.

        Parameters
        ----------
        tools : list[dict]
            Tool records, each with ``"domain"`` and ``"similarity_score"`` keys.

        Returns
        -------
        dict[str, float]
            Mapping of domain name to accumulated similarity score.
        """
        domain_scores: dict[str, float] = {}
        for tool in tools:
            domain = tool.get("domain", "unknown")
            similarity = tool.get("similarity_score", 0.0)
            domain_scores[domain] = domain_scores.get(domain, 0.0) + similarity
        return domain_scores

    @staticmethod
    def _determine_strategy(
        primary_domain: str,
        secondary_domains: list[str],
        tools: list[dict[str, Any]],
    ) -> str:
        """
        Determine execution strategy based on domain distribution.

        Returns
        -------
        str
            One of ``"single_agent"``, ``"multi_agent"``, or ``"sequential"``.
        """
        if not secondary_domains:
            return "single_agent"

        # Calculate max similarity per domain
        primary_score = max(
            (t.get("similarity_score", 0.0) for t in tools if t.get("domain") == primary_domain),
            default=0.0,
        )

        secondary_score = max(
            (
                t.get("similarity_score", 0.0)
                for t in tools
                if t.get("domain") in secondary_domains
            ),
            default=0.0,
        )

        # If secondary domains score less than half of primary, a single agent suffices
        if secondary_score < primary_score * 0.5:
            return "single_agent"

        return "multi_agent"
