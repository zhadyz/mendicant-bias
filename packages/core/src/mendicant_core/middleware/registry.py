"""
semantic_registry.py
====================
Mendicant Bias V5 — Tool Registry Backend

Provides two public classes:

``RegistryBuilder``
    Constructs and persists a tool registry JSON file from tool
    definition dicts.  Optionally computes and caches sentence-transformer
    embeddings alongside each tool entry so that ``RegistryQuery`` can
    perform fast cosine-similarity lookup without re-encoding at runtime.

``RegistryQuery``
    Loads the registry and exposes search, filter, and lookup operations.
    Falls back gracefully to keyword-only search when sentence-transformers
    is not installed.

Registry JSON schema
--------------------
.. code-block:: json

    [
      {
        "name": "web_search",
        "description": "Search the web for current information.",
        "tags": ["web", "search", "research"],
        "domain": "web",
        "embedding": [0.012, -0.034, ...]
      },
      ...
    ]

``embedding`` is optional; entries without it fall back to keyword matching.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

_DEFAULT_MODEL = "all-MiniLM-L6-v2"
_SEED_TOOLS: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "Search the internet for current news, facts, and information.",
        "tags": ["web", "search", "research", "internet"],
        "domain": "web",
    },
    {
        "name": "web_fetch",
        "description": "Fetch and read the full content of a web page by URL.",
        "tags": ["web", "scrape", "html", "fetch", "url"],
        "domain": "web",
    },
    {
        "name": "read_file",
        "description": "Read the text contents of a local file on disk.",
        "tags": ["file", "read", "local", "disk", "io"],
        "domain": "file",
    },
    {
        "name": "write_file",
        "description": "Write or overwrite a text file on disk.",
        "tags": ["file", "write", "create", "local", "disk", "io"],
        "domain": "file",
    },
    {
        "name": "run_python",
        "description": "Execute a Python code snippet and return stdout/stderr.",
        "tags": ["code", "python", "execute", "run", "compute"],
        "domain": "code",
    },
    {
        "name": "run_bash",
        "description": "Execute a bash shell command and return the output.",
        "tags": ["bash", "shell", "command", "execute", "run", "system"],
        "domain": "code",
    },
    {
        "name": "mcp_tool_call",
        "description": "Invoke a tool exposed by an MCP (Model Context Protocol) server.",
        "tags": ["mcp", "tool", "protocol", "extension"],
        "domain": "mcp",
    },
    {
        "name": "image_search",
        "description": "Search for images online and return URLs and metadata.",
        "tags": ["image", "visual", "photo", "search", "media"],
        "domain": "web",
    },
]


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    return dot / (norm_a * norm_b)


def _keyword_score(query: str, tool: dict[str, Any]) -> float:
    """Simple keyword overlap score in [0, 1]."""
    q_tokens = set(query.lower().split())
    haystack_tokens: set[str] = set()
    haystack_tokens.update(tool.get("name", "").lower().replace("_", " ").split())
    haystack_tokens.update(tool.get("description", "").lower().split())
    for tag in tool.get("tags", []):
        haystack_tokens.update(tag.lower().split())
    if not q_tokens or not haystack_tokens:
        return 0.0
    overlap = len(q_tokens & haystack_tokens)
    return overlap / len(q_tokens)


# ---------------------------------------------------------------------------
# RegistryBuilder
# ---------------------------------------------------------------------------


class RegistryBuilder:
    """
    Build and persist a tool registry JSON file.

    Parameters
    ----------
    output_path : str
        Destination for the registry JSON file.
    embedding_model : str
        Sentence-transformer model name.  Pass ``None`` to skip embedding.
    """

    def __init__(
        self,
        output_path: str = ".mendicant/tool_registry.json",
        embedding_model: str | None = _DEFAULT_MODEL,
    ) -> None:
        self.output_path = Path(output_path)
        self.embedding_model = embedding_model
        self._encoder: Any | None = None
        self._entries: list[dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def add_tool(
        self,
        *,
        name: str,
        description: str,
        tags: list[str] | None = None,
        domain: str = "generic",
        extra: dict[str, Any] | None = None,
    ) -> "RegistryBuilder":
        """Add a single tool definition.  Returns self for chaining."""
        entry: dict[str, Any] = {
            "name": name,
            "description": description,
            "tags": tags or [],
            "domain": domain,
        }
        if extra:
            entry.update(extra)
        self._entries.append(entry)
        return self

    def add_tools(self, tools: list[dict[str, Any]]) -> "RegistryBuilder":
        """
        Bulk-add tool definitions.

        Each dict must have at minimum ``name`` and ``description`` keys.
        """
        for t in tools:
            self.add_tool(
                name=t["name"],
                description=t["description"],
                tags=t.get("tags", []),
                domain=t.get("domain", "generic"),
                extra={k: v for k, v in t.items() if k not in {"name", "description", "tags", "domain"}},
            )
        return self

    def add_seed_tools(self) -> "RegistryBuilder":
        """Add the built-in seed tool definitions."""
        return self.add_tools(_SEED_TOOLS)

    def build(self, compute_embeddings: bool = True) -> list[dict[str, Any]]:
        """
        Finalise the registry, optionally compute embeddings, write to disk.

        Returns the list of tool entry dicts (with embeddings if computed).
        """
        entries = [dict(e) for e in self._entries]

        if compute_embeddings and self.embedding_model:
            encoder = self._load_encoder()
            if encoder is not None:
                logger.info("[RegistryBuilder] Computing embeddings for %d tools…", len(entries))
                texts = [f"{e['name']} {e['description']} {' '.join(e.get('tags', []))}" for e in entries]
                try:
                    vectors = encoder.encode(texts, convert_to_numpy=True)
                    for entry, vec in zip(entries, vectors):
                        entry["embedding"] = vec.tolist()
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[RegistryBuilder] Embedding failed: %s", exc)

        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=2)

        logger.info("[RegistryBuilder] Wrote %d tools to %s", len(entries), self.output_path)
        return entries

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _load_encoder(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._encoder = SentenceTransformer(self.embedding_model)  # type: ignore[arg-type]
        except ImportError:
            logger.debug("[RegistryBuilder] sentence-transformers not installed; skipping embeddings.")
            self._encoder = None
        return self._encoder


# ---------------------------------------------------------------------------
# RegistryQuery
# ---------------------------------------------------------------------------


class RegistryQuery:
    """
    Load and query a tool registry.

    Parameters
    ----------
    registry_path : str
        Path to the tool registry JSON file.
    embedding_model : str | None
        Sentence-transformer model for query embedding.  Pass ``None`` to
        use keyword matching only.
    """

    def __init__(
        self,
        registry_path: str = ".mendicant/tool_registry.json",
        embedding_model: str | None = _DEFAULT_MODEL,
    ) -> None:
        self.registry_path = Path(registry_path)
        self.embedding_model = embedding_model
        self._tools: list[dict[str, Any]] = []
        self._encoder: Any | None = None
        self._loaded = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def load(self) -> "RegistryQuery":
        """Load (or reload) the registry from disk."""
        if not self.registry_path.exists():
            logger.warning("[RegistryQuery] Registry not found at %s", self.registry_path)
            self._tools = []
        else:
            with self.registry_path.open("r", encoding="utf-8") as fh:
                data = json.load(fh)
            self._tools = data if isinstance(data, list) else []
            logger.debug("[RegistryQuery] Loaded %d tools from %s", len(self._tools), self.registry_path)
        self._loaded = True
        return self

    def search(
        self,
        query: str,
        top_k: int = 5,
        similarity_threshold: float = 0.0,
        domain: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Search for tools relevant to *query*.

        Uses embedding cosine similarity when available, falls back to
        keyword scoring.  Results are sorted by score descending.

        Parameters
        ----------
        query : str
            Natural-language task description.
        top_k : int
            Maximum number of results to return.
        similarity_threshold : float
            Minimum score for a tool to be included.
        domain : str | None
            Optional domain filter (``"web"``, ``"file"``, ``"code"``, etc.).

        Returns
        -------
        list[dict]
            Tool dicts with an added ``_score`` key.
        """
        self._ensure_loaded()
        tools = self._tools
        if domain:
            tools = [t for t in tools if t.get("domain") == domain]

        query_vec: list[float] | None = self._encode_query(query)

        scored: list[tuple[float, dict[str, Any]]] = []
        for tool in tools:
            if query_vec and tool.get("embedding"):
                score = _cosine_similarity(query_vec, tool["embedding"])
            else:
                score = _keyword_score(query, tool)

            if score >= similarity_threshold:
                scored.append((score, tool))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {**t, "_score": round(s, 4)}
            for s, t in scored[:top_k]
        ]

    def lookup(self, name: str) -> dict[str, Any] | None:
        """Return a tool by exact name, or ``None`` if not found."""
        self._ensure_loaded()
        for tool in self._tools:
            if tool.get("name") == name:
                return dict(tool)
        return None

    def filter_by_domain(self, domain: str) -> list[dict[str, Any]]:
        """Return all tools in a given domain."""
        self._ensure_loaded()
        return [t for t in self._tools if t.get("domain") == domain]

    def filter_by_tags(self, tags: list[str], match_all: bool = False) -> list[dict[str, Any]]:
        """
        Return tools that match the given tags.

        Parameters
        ----------
        tags : list[str]
            Tags to match.
        match_all : bool
            If ``True``, every tag must be present.  If ``False`` (default),
            at least one tag must match.
        """
        self._ensure_loaded()
        tag_set = {t.lower() for t in tags}
        result = []
        for tool in self._tools:
            tool_tags = {t.lower() for t in tool.get("tags", [])}
            if match_all:
                if tag_set <= tool_tags:
                    result.append(tool)
            else:
                if tag_set & tool_tags:
                    result.append(tool)
        return result

    def all_tools(self) -> list[dict[str, Any]]:
        """Return all tool entries (without ``_score``)."""
        self._ensure_loaded()
        return list(self._tools)

    def tool_names(self) -> list[str]:
        """Return sorted list of all tool names."""
        self._ensure_loaded()
        return sorted(t.get("name", "") for t in self._tools)

    # ------------------------------------------------------------------
    # Private
    # ------------------------------------------------------------------

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.load()

    def _encode_query(self, query: str) -> list[float] | None:
        if not self.embedding_model:
            return None
        try:
            encoder = self._load_encoder()
            if encoder is None:
                return None
            vec = encoder.encode(query, convert_to_numpy=True)
            return vec.tolist()
        except Exception:  # noqa: BLE001
            return None

    def _load_encoder(self) -> Any:
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore

            self._encoder = SentenceTransformer(self.embedding_model)  # type: ignore[arg-type]
        except ImportError:
            self._encoder = None
        return self._encoder


# ---------------------------------------------------------------------------
# CLI helper
# ---------------------------------------------------------------------------


def build_default_registry(output_path: str = ".mendicant/tool_registry.json") -> None:
    """Write the seed tool registry (no embeddings by default) to disk."""
    RegistryBuilder(output_path=output_path, embedding_model=None).add_seed_tools().build(
        compute_embeddings=False
    )
    print(f"Registry written to {output_path}")


if __name__ == "__main__":
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else ".mendicant/tool_registry.json"
    build_default_registry(path)
