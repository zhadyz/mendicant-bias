"""
Mahoraga -- Intelligent Adaptation System for Mendicant Bias

Named after the Shikigami that adapts to any attack. Mahoraga observes
how the user works, extracts behavioral rules, and applies them
automatically so the user never has to repeat themselves.

This is NOT memory (storing facts). It's BEHAVIORAL ADAPTATION -- learning
HOW the user wants things done, then doing it automatically.

The adaptation flywheel:
  1. OBSERVE  -- Watch user corrections, preferences, approvals, rejections
  2. EXTRACT  -- Identify the rule from the observation
  3. STORE    -- Persist as an adaptation rule with confidence
  4. APPLY    -- On next similar situation, apply automatically
  5. VERIFY   -- Track if the rule produced good outcomes. Adjust confidence.

Categories of adaptation:
  PREFERENCE  -- "always do X" / "never do Y" (hard rules)
  PATTERN     -- "when user asks X, they usually want Y" (soft inference)
  CORRECTION  -- "no not like that, like this" (learned from mistakes)
  TOOL        -- "use pytest not unittest" (tool selection)
  STYLE       -- "I prefer functional style" (code generation)
  WORKFLOW    -- "always run tests after implementation" (process rules)
  AGENT       -- "use hollowed_eyes for refactoring" (agent routing)
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mendicant_core.mahoraga.store import AdaptationStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CATEGORIES = frozenset({
    "PREFERENCE", "PATTERN", "CORRECTION", "TOOL", "STYLE", "WORKFLOW", "AGENT",
})

VALID_SOURCES = frozenset({"explicit", "inferred", "correction"})

# Minimum confidence to apply a rule
MIN_APPLY_CONFIDENCE = 0.3

# Confidence deltas
SUCCESS_BOOST = 0.05
FAILURE_PENALTY = 0.10
DECAY_RATE = 0.05
DEACTIVATION_THRESHOLD = 0.2


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AdaptationRule:
    """A single behavioral adaptation rule."""

    id: str
    category: str  # PREFERENCE, PATTERN, CORRECTION, TOOL, STYLE, WORKFLOW, AGENT
    trigger: str  # When this rule applies (natural language pattern)
    action: str  # What to do (natural language instruction)
    confidence: float  # 0.0-1.0, increases with successful application
    source: str  # How it was learned: "explicit", "inferred", "correction"
    created_at: str  # ISO timestamp
    last_applied: str | None = None  # Last time this rule fired
    apply_count: int = 0  # How many times it's been applied
    success_count: int = 0  # How many times it led to a good outcome
    failure_count: int = 0  # How many times it was overridden/corrected
    tags: list[str] = field(default_factory=list)
    examples: list[str] = field(default_factory=list)
    active: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdaptationRule:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            category=data.get("category", "PATTERN"),
            trigger=data.get("trigger", ""),
            action=data.get("action", ""),
            confidence=float(data.get("confidence", 0.5)),
            source=data.get("source", "inferred"),
            created_at=data.get("created_at", _now_iso()),
            last_applied=data.get("last_applied"),
            apply_count=int(data.get("apply_count", 0)),
            success_count=int(data.get("success_count", 0)),
            failure_count=int(data.get("failure_count", 0)),
            tags=data.get("tags", []),
            examples=data.get("examples", []),
            active=data.get("active", True),
        )


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _generate_id() -> str:
    return str(uuid.uuid4())[:12]


# ---------------------------------------------------------------------------
# Heuristic rule extraction (no LLM needed)
# ---------------------------------------------------------------------------

# Each pattern: (compiled regex, category, trigger_group, action_group)
# Groups are 1-indexed regex group numbers

_EXTRACTION_PATTERNS: list[tuple[re.Pattern, str, int | None, int | None]] = [
    # --- Specific structural patterns first (before "always"/"never" catch-alls) ---

    # "when X, (always) Y" — must come before "always" to avoid "always Y when X" eating it
    (
        re.compile(r"^when\s+(.+?),\s*(?:always\s+)?(.+)", re.IGNORECASE),
        "PATTERN",
        1,
        2,
    ),
    # "after X, (always) Y" — must come before "always" to avoid misclassification
    (
        re.compile(r"^after\s+(.+?),\s*(?:always\s+)?(.+)", re.IGNORECASE),
        "WORKFLOW",
        1,  # trigger = "after X"
        2,
    ),
    # "for X, use/spawn/route to Y"
    (
        re.compile(r"^for\s+(.+?),?\s+(?:use|spawn|route\s+to)\s+(.+)", re.IGNORECASE),
        "AGENT",
        1,
        2,
    ),
    # "use X instead of Y" / "use X not Y"
    (
        re.compile(r"^use\s+(.+?)\s+(?:instead of|not)\s+(.+)", re.IGNORECASE),
        "TOOL",
        None,
        0,  # special: full match as action
    ),
    # "prefer X over Y" / "prefer X to Y"
    (
        re.compile(r"^prefer\s+(.+?)\s+(?:over|to)\s+(.+)", re.IGNORECASE),
        "STYLE",
        None,
        0,
    ),

    # --- "always" / "never" patterns ---

    # "always X when Y" or "always X"
    (
        re.compile(r"^always\s+(.+?)\s+when\s+(.+)", re.IGNORECASE),
        "PREFERENCE",
        2,  # trigger = "when Y"
        1,  # action = "always X"
    ),
    (
        re.compile(r"^always\s+(.+)", re.IGNORECASE),
        "PREFERENCE",
        None,  # no explicit trigger
        1,
    ),
    # "never X when Y" or "never X"
    (
        re.compile(r"^never\s+(.+?)\s+when\s+(.+)", re.IGNORECASE),
        "PREFERENCE",
        2,
        1,  # action will be prefixed with "never"
    ),
    (
        re.compile(r"^never\s+(.+)", re.IGNORECASE),
        "PREFERENCE",
        None,
        1,
    ),
    # "don't X" / "do not X"
    (
        re.compile(r"^(?:don'?t|do\s+not)\s+(.+)", re.IGNORECASE),
        "PREFERENCE",
        None,
        1,
    ),

    # --- Correction patterns (least specific, go last) ---

    # "no, X" / "not X, Y" -> CORRECTION
    (
        re.compile(r"^no[,.]?\s+(.+)", re.IGNORECASE),
        "CORRECTION",
        None,
        1,
    ),
    (
        re.compile(r"^not\s+(.+?),?\s+(.+)", re.IGNORECASE),
        "CORRECTION",
        None,
        0,  # full match
    ),
]


def _extract_rule_heuristic(text: str) -> tuple[str, str, str] | None:
    """Extract (category, trigger, action) from natural language.

    Returns None if no pattern matches.
    """
    text = text.strip()
    if not text:
        return None

    for pattern, category, trigger_group, action_group in _EXTRACTION_PATTERNS:
        m = pattern.search(text)
        if m:
            # Extract trigger
            if trigger_group is None:
                trigger = "general"
            elif trigger_group == 0:
                trigger = "general"
            else:
                trigger = m.group(trigger_group).strip().rstrip(".")

            # Extract action
            if action_group == 0:
                action = text.strip().rstrip(".")
            else:
                raw_action = m.group(action_group).strip().rstrip(".")
                # For "never" patterns, prefix the action
                if "never" in pattern.pattern:
                    action = f"never {raw_action}"
                # For "don't" patterns, prefix the action
                elif "don" in pattern.pattern or "do\\s+not" in pattern.pattern:
                    action = f"do not {raw_action}"
                else:
                    action = raw_action

            return category, trigger, action

    return None


def _extract_tags(text: str) -> list[str]:
    """Extract relevant tags from text for searchability."""
    # Simple keyword extraction: words that look meaningful (4+ chars, not stopwords)
    stopwords = {
        "always", "never", "when", "that", "this", "with", "from", "have",
        "been", "will", "would", "could", "should", "into", "than", "then",
        "them", "they", "their", "there", "these", "those", "some", "more",
        "also", "just", "about", "after", "before", "while", "during",
        "instead", "over", "like", "want", "need", "make", "does", "done",
        "doing", "being", "only", "very", "much", "each", "every",
        "prefer", "preferred", "route", "spawn", "used",
    }
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{3,}", text.lower())
    seen: set[str] = set()
    tags: list[str] = []
    for w in words:
        if w not in stopwords and w not in seen:
            seen.add(w)
            tags.append(w)
    return tags[:10]  # cap at 10


def _normalize(text: str) -> str:
    """Normalize text for comparison."""
    return re.sub(r"\s+", " ", text.strip().lower())


def _text_similarity(a: str, b: str) -> float:
    """Simple keyword-overlap Jaccard similarity."""
    words_a = set(re.findall(r"\w+", a.lower()))
    words_b = set(re.findall(r"\w+", b.lower()))
    if not words_a or not words_b:
        return 0.0
    intersection = words_a & words_b
    union = words_a | words_b
    return len(intersection) / len(union)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class MahoragaEngine:
    """The adaptation flywheel.

    Observes user behavior, extracts rules, persists them, and applies them
    automatically on matching contexts. Pure heuristic -- no LLM required.
    """

    def __init__(
        self,
        store_path: str | Path = ".mendicant/mahoraga.json",
    ) -> None:
        self._store = AdaptationStore(store_path)
        self._rules: list[AdaptationRule] = []
        self._load()

    # === OBSERVE ===

    def observe_correction(
        self,
        original: str,
        correction: str,
        context: str = "",
    ) -> AdaptationRule:
        """User corrected something. Extract and store the rule.

        Example: User said "no, always use pytest not unittest"
        -> trigger: "when writing tests"
        -> action: "use pytest, not unittest"
        -> category: CORRECTION (or extracted category)
        -> source: correction
        """
        # Try to extract a structured rule from the correction text
        extracted = _extract_rule_heuristic(correction)

        if extracted:
            category, trigger, action = extracted
        else:
            # Fallback: create a CORRECTION rule from raw text
            category = "CORRECTION"
            trigger = _normalize(original)[:120] if original else "general"
            action = correction.strip()

        rule = AdaptationRule(
            id=_generate_id(),
            category=category,
            trigger=trigger,
            action=action,
            confidence=0.85,  # corrections are high confidence -- user was explicit
            source="correction",
            created_at=_now_iso(),
            tags=_extract_tags(f"{correction} {context}"),
            examples=[f"Original: {original}" if original else "", f"Correction: {correction}"],
        )
        rule.examples = [e for e in rule.examples if e]  # clean empties

        self.add_rule(rule)
        return rule

    def observe_preference(
        self,
        preference: str,
        context: str = "",
    ) -> AdaptationRule:
        """User stated a preference explicitly.

        Example: "always test using claude in chrome"
        -> trigger: "when testing"
        -> action: "use Claude in Chrome browser tools"
        -> category: PREFERENCE
        -> source: explicit
        -> confidence: 0.95 (explicit = high confidence)
        """
        extracted = _extract_rule_heuristic(preference)

        if extracted:
            category, trigger, action = extracted
        else:
            category = "PREFERENCE"
            trigger = "general"
            action = preference.strip()

        rule = AdaptationRule(
            id=_generate_id(),
            category=category,
            trigger=trigger,
            action=action,
            confidence=0.95,  # explicit preference = very high confidence
            source="explicit",
            created_at=_now_iso(),
            tags=_extract_tags(f"{preference} {context}"),
            examples=[preference],
        )

        self.add_rule(rule)
        return rule

    def observe_approval(self, approach: str, context: str = "") -> None:
        """User approved an approach. Boost matching rules' confidence."""
        matching = self.get_applicable_rules(f"{approach} {context}")
        for rule in matching:
            self.record_success(rule.id)

    def observe_rejection(self, approach: str, context: str = "") -> None:
        """User rejected an approach. Reduce matching rules' confidence
        or create a CORRECTION rule if no rules match."""
        matching = self.get_applicable_rules(f"{approach} {context}")
        if matching:
            for rule in matching:
                self.record_failure(rule.id)
        else:
            # No existing rule matched -- create a correction rule
            self.observe_correction(
                original=approach,
                correction=f"rejected approach: {approach}",
                context=context,
            )

    def observe_workflow(
        self,
        steps: list[str],
        context: str = "",
    ) -> AdaptationRule:
        """User established a workflow pattern.

        Example: user always does: implement -> test -> commit
        -> trigger: "after implementation"
        -> action: "run tests before committing"
        -> category: WORKFLOW
        """
        if len(steps) < 2:
            # Need at least two steps for a workflow
            rule = AdaptationRule(
                id=_generate_id(),
                category="WORKFLOW",
                trigger="general",
                action=" -> ".join(steps) if steps else "unspecified workflow",
                confidence=0.7,
                source="inferred",
                created_at=_now_iso(),
                tags=_extract_tags(f"{' '.join(steps)} {context}"),
                examples=[" -> ".join(steps)],
            )
            self.add_rule(rule)
            return rule

        # Build trigger from first step, action from subsequent steps
        trigger = f"after {steps[0]}"
        action_parts = []
        for i, step in enumerate(steps[1:], 1):
            if i < len(steps) - 1:
                action_parts.append(step)
            else:
                action_parts.append(step)
        action = " then ".join(action_parts)

        rule = AdaptationRule(
            id=_generate_id(),
            category="WORKFLOW",
            trigger=trigger,
            action=action,
            confidence=0.8,
            source="inferred",
            created_at=_now_iso(),
            tags=_extract_tags(f"{' '.join(steps)} {context}"),
            examples=[" -> ".join(steps)],
        )

        self.add_rule(rule)
        return rule

    def observe_agent_preference(
        self,
        task_type: str,
        agent_name: str,
        outcome: str,
    ) -> None:
        """Track which agents work for which task types.

        Example: User always uses the_didact for architecture questions
        -> trigger: "architecture analysis"
        -> action: "route to the_didact"
        -> category: AGENT
        """
        # Check if we already have a rule for this task_type + agent
        existing = [
            r for r in self._rules
            if r.category == "AGENT"
            and r.active
            and _text_similarity(r.trigger, task_type) > 0.5
            and agent_name.lower() in r.action.lower()
        ]

        if existing:
            # Update existing rule based on outcome
            for rule in existing:
                if outcome in ("success", "good", "approved"):
                    self.record_success(rule.id)
                else:
                    self.record_failure(rule.id)
            return

        # Create new agent routing rule
        conf = 0.75 if outcome in ("success", "good", "approved") else 0.4
        rule = AdaptationRule(
            id=_generate_id(),
            category="AGENT",
            trigger=task_type,
            action=f"route to {agent_name}",
            confidence=conf,
            source="inferred",
            created_at=_now_iso(),
            tags=_extract_tags(f"{task_type} {agent_name}"),
            examples=[f"task: {task_type}, agent: {agent_name}, outcome: {outcome}"],
        )
        self.add_rule(rule)

    # === EXTRACT ===

    def extract_rule_from_text(self, text: str) -> AdaptationRule | None:
        """Extract an adaptation rule from natural language.

        Handles patterns like:
        - "always X" -> PREFERENCE rule
        - "never X" -> PREFERENCE rule (negative)
        - "when X, do Y" -> PATTERN rule
        - "use X instead of Y" -> TOOL/STYLE rule
        - "no, X" / "not X, Y" -> CORRECTION rule
        - "after X, always Y" -> WORKFLOW rule
        - "for X, use Y" -> AGENT rule

        Uses keyword matching. Returns None if no pattern is recognized.
        """
        extracted = _extract_rule_heuristic(text)
        if extracted is None:
            return None

        category, trigger, action = extracted

        rule = AdaptationRule(
            id=_generate_id(),
            category=category,
            trigger=trigger,
            action=action,
            confidence=0.7,  # extracted rule starts at moderate confidence
            source="inferred",
            created_at=_now_iso(),
            tags=_extract_tags(text),
            examples=[text],
        )
        return rule

    # === STORE ===

    def add_rule(self, rule: AdaptationRule) -> None:
        """Add a rule. Deduplicates by trigger+action similarity."""
        # Check for duplicates
        for existing in self._rules:
            trigger_sim = _text_similarity(existing.trigger, rule.trigger)
            action_sim = _text_similarity(existing.action, rule.action)
            if trigger_sim > 0.7 and action_sim > 0.7:
                # Duplicate found -- boost confidence of existing rule instead
                existing.confidence = min(1.0, existing.confidence + 0.05)
                existing.examples.extend(rule.examples)
                # Keep only last 5 examples
                existing.examples = existing.examples[-5:]
                logger.debug(
                    "[Mahoraga] Deduplicated rule '%s' into existing '%s' (confidence: %.2f)",
                    rule.id,
                    existing.id,
                    existing.confidence,
                )
                self._save()
                return

        self._rules.append(rule)
        self._save()
        logger.info(
            "[Mahoraga] Added rule '%s': [%s] %s -> %s (confidence: %.2f)",
            rule.id,
            rule.category,
            rule.trigger,
            rule.action,
            rule.confidence,
        )

    def update_rule(self, rule_id: str, **updates: Any) -> None:
        """Update a rule's fields."""
        for rule in self._rules:
            if rule.id == rule_id:
                for key, value in updates.items():
                    if hasattr(rule, key):
                        setattr(rule, key, value)
                self._save()
                return
        logger.warning("[Mahoraga] Rule '%s' not found for update", rule_id)

    def remove_rule(self, rule_id: str) -> bool:
        """Remove a rule. Returns True if found and removed."""
        before = len(self._rules)
        self._rules = [r for r in self._rules if r.id != rule_id]
        if len(self._rules) < before:
            self._save()
            return True
        return False

    def _save(self) -> None:
        """Persist rules to disk."""
        self._store.save([r.to_dict() for r in self._rules])

    def _load(self) -> None:
        """Load rules from disk."""
        raw_rules = self._store.load()
        self._rules = [AdaptationRule.from_dict(r) for r in raw_rules]
        logger.debug("[Mahoraga] Loaded %d adaptation rules", len(self._rules))

    # === APPLY ===

    def get_applicable_rules(
        self,
        context: str,
        category: str | None = None,
    ) -> list[AdaptationRule]:
        """Find rules that apply to the current context.

        Uses keyword-overlap similarity. Returns rules sorted by confidence
        (highest first). Only returns active rules with confidence > MIN_APPLY_CONFIDENCE.
        """
        if not context:
            return []

        candidates: list[tuple[float, AdaptationRule]] = []

        for rule in self._rules:
            if not rule.active:
                continue
            if rule.confidence < MIN_APPLY_CONFIDENCE:
                continue
            if category and rule.category != category.upper():
                continue

            # Score: combine trigger similarity + tag overlap
            trigger_score = _text_similarity(rule.trigger, context)

            # Also check tag overlap with context
            context_words = set(re.findall(r"\w+", context.lower()))
            tag_overlap = len(set(rule.tags) & context_words) / max(len(rule.tags), 1)

            # Also check if action keywords appear in context
            action_score = _text_similarity(rule.action, context) * 0.3

            # Combined relevance score
            relevance = (trigger_score * 0.5) + (tag_overlap * 0.3) + (action_score * 0.2)

            # Only include if there's some meaningful overlap
            if relevance > 0.05 or trigger_score > 0.15:
                # Final ranking = relevance * confidence
                rank = relevance * rule.confidence
                candidates.append((rank, rule))

        # Sort by rank descending
        candidates.sort(key=lambda x: x[0], reverse=True)
        return [rule for _, rule in candidates]

    def format_rules_for_injection(self, rules: list[AdaptationRule]) -> str:
        """Format applicable rules as a prompt injection block.

        Returns a structured block that can be prepended to system prompts.
        """
        if not rules:
            return ""

        lines = ["<adaptation_rules>", "## User Preferences (apply these automatically)"]

        # Group by category for readability
        by_category: dict[str, list[AdaptationRule]] = {}
        for rule in rules:
            by_category.setdefault(rule.category, []).append(rule)

        category_labels = {
            "PREFERENCE": "Hard Preferences",
            "PATTERN": "Behavioral Patterns",
            "CORRECTION": "Learned Corrections",
            "TOOL": "Tool Preferences",
            "STYLE": "Style Preferences",
            "WORKFLOW": "Workflow Rules",
            "AGENT": "Agent Routing",
        }

        for cat in ["PREFERENCE", "CORRECTION", "TOOL", "STYLE", "WORKFLOW", "AGENT", "PATTERN"]:
            cat_rules = by_category.get(cat, [])
            if not cat_rules:
                continue
            label = category_labels.get(cat, cat)
            lines.append(f"\n### {label}")
            for rule in cat_rules:
                conf_pct = int(rule.confidence * 100)
                if rule.trigger and rule.trigger != "general":
                    lines.append(f"- When {rule.trigger}: {rule.action} [confidence: {conf_pct}%]")
                else:
                    lines.append(f"- {rule.action} [confidence: {conf_pct}%]")

        lines.append("</adaptation_rules>")
        return "\n".join(lines)

    def apply_and_track(self, rule_id: str) -> None:
        """Mark a rule as applied. Updates last_applied and apply_count."""
        for rule in self._rules:
            if rule.id == rule_id:
                rule.last_applied = _now_iso()
                rule.apply_count += 1
                self._save()
                return

    # === VERIFY ===

    def record_success(self, rule_id: str) -> None:
        """Rule application led to good outcome. Boost confidence."""
        for rule in self._rules:
            if rule.id == rule_id:
                rule.success_count += 1
                rule.confidence = min(1.0, rule.confidence + SUCCESS_BOOST)
                rule.last_applied = _now_iso()
                self._save()
                logger.debug(
                    "[Mahoraga] Success for rule '%s', confidence now %.2f",
                    rule_id,
                    rule.confidence,
                )
                return

    def record_failure(self, rule_id: str) -> None:
        """Rule application was overridden/corrected. Reduce confidence."""
        for rule in self._rules:
            if rule.id == rule_id:
                rule.failure_count += 1
                rule.confidence = max(0.0, rule.confidence - FAILURE_PENALTY)
                # Auto-deactivate if confidence drops too low
                if rule.confidence < DEACTIVATION_THRESHOLD:
                    rule.active = False
                    logger.info(
                        "[Mahoraga] Deactivated rule '%s' (confidence: %.2f)",
                        rule_id,
                        rule.confidence,
                    )
                self._save()
                return

    def decay_unused_rules(self, days: int = 30) -> int:
        """Reduce confidence of rules not applied in N days.
        Deactivate rules with confidence < DEACTIVATION_THRESHOLD.
        Returns count of deactivated rules.
        """
        now = datetime.now(tz=timezone.utc)
        deactivated = 0

        for rule in self._rules:
            if not rule.active:
                continue

            # Check if rule has been applied recently
            if rule.last_applied:
                try:
                    last = datetime.fromisoformat(rule.last_applied)
                    # Make timezone-aware if naive
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    delta = (now - last).days
                except (ValueError, TypeError):
                    delta = days + 1  # treat parse failures as stale
            else:
                # Never applied -- use created_at
                try:
                    created = datetime.fromisoformat(rule.created_at)
                    if created.tzinfo is None:
                        created = created.replace(tzinfo=timezone.utc)
                    delta = (now - created).days
                except (ValueError, TypeError):
                    delta = days + 1

            if delta >= days:
                rule.confidence = max(0.0, rule.confidence - DECAY_RATE)
                if rule.confidence < DEACTIVATION_THRESHOLD:
                    rule.active = False
                    deactivated += 1
                    logger.info(
                        "[Mahoraga] Decayed and deactivated rule '%s'",
                        rule.id,
                    )

        if deactivated > 0:
            self._save()

        return deactivated

    # === QUERY ===

    def get_all_rules(
        self,
        category: str | None = None,
        active_only: bool = True,
    ) -> list[AdaptationRule]:
        """Get all rules, optionally filtered."""
        results = self._rules
        if active_only:
            results = [r for r in results if r.active]
        if category:
            results = [r for r in results if r.category == category.upper()]
        return results

    def get_stats(self) -> dict[str, Any]:
        """Return adaptation statistics."""
        total = len(self._rules)
        active = sum(1 for r in self._rules if r.active)
        inactive = total - active

        by_category: dict[str, int] = {}
        by_source: dict[str, int] = {}
        total_applies = 0
        total_successes = 0
        total_failures = 0

        for rule in self._rules:
            by_category[rule.category] = by_category.get(rule.category, 0) + 1
            by_source[rule.source] = by_source.get(rule.source, 0) + 1
            total_applies += rule.apply_count
            total_successes += rule.success_count
            total_failures += rule.failure_count

        avg_confidence = (
            sum(r.confidence for r in self._rules) / total if total > 0 else 0.0
        )

        return {
            "total_rules": total,
            "active_rules": active,
            "inactive_rules": inactive,
            "by_category": by_category,
            "by_source": by_source,
            "total_applications": total_applies,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "success_rate": (
                round(total_successes / total_applies, 3)
                if total_applies > 0
                else 0.0
            ),
            "average_confidence": round(avg_confidence, 3),
        }

    def search_rules(self, query: str, top_k: int = 5) -> list[AdaptationRule]:
        """Search rules by keyword similarity across trigger, action, and tags."""
        if not query:
            return []

        scored: list[tuple[float, AdaptationRule]] = []

        for rule in self._rules:
            # Search across all text fields
            combined = f"{rule.trigger} {rule.action} {' '.join(rule.tags)} {' '.join(rule.examples)}"
            sim = _text_similarity(combined, query)
            if sim > 0.05:
                scored.append((sim, rule))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [rule for _, rule in scored[:top_k]]


__all__ = [
    "AdaptationRule",
    "AdaptationStore",
    "MahoragaEngine",
    "VALID_CATEGORIES",
    "VALID_SOURCES",
]
