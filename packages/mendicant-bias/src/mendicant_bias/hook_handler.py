"""
Mendicant Bias V6 — Ambient Intelligence Harness
=================================================

The nervous system of Mendicant Bias. Fires on every Claude Code event.
Injects DIRECTIVES (behavioral instructions), not just data tags.

Mendicant is not a skill you invoke. It is the air Claude breathes.

WHAT THIS DOES:

SessionStart:
  → Decays stale Mahoraga rules
  → Loads ALL adaptation rules + memory + patterns
  → Probes gateway health → auto-starts if not running
  → Ensures .mendicant/aletheia/ state directory
  → Signals statusline badge

UserPromptSubmit:
  → Classifies task via FR5 (embedding + keyword)
  → Maps to Aletheia verification tier
  → Injects BEHAVIORAL DIRECTIVES for the classified tier
  → Matches + injects relevant Mahoraga rules
  → Records classification for downstream hooks

PreToolUse (Write/Edit):
  → Injects code-relevant Mahoraga rules as guidance

PostToolUse (Write/Edit):
  → Runs inline FR2 blind verification
  → For CRITICAL_CODE: injects directive to spawn evaluator teammate
  → Records pattern with tier for adaptive learning

Stop:
  → Context lifecycle monitoring (via separate script)
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# ── Thresholds ──────────────────────────────────────────────────────────────

MIN_RELEVANCE_SCORE = 0.15
MIN_INJECTION_CONFIDENCE = 0.5
MAX_RULES_PER_EVENT = 4
GATEWAY_URL = "http://localhost:8001"
GATEWAY_PROBE_TIMEOUT = 1.5  # seconds

# ── Aletheia verification tier mapping ──────────────────────────────────────

VERIFICATION_TIERS: dict[str, str] = {
    "SIMPLE": "none",
    "RESEARCH": "lightweight",
    "CODE_GENERATION": "quick",
    "CRITICAL_CODE": "full",
    "MULTI_MODAL": "design",
}

# ── Directives per tier ─────────────────────────────────────────────────────
# These are BEHAVIORAL INSTRUCTIONS, not informational tags.
# Claude follows directives in system-reminders like CLAUDE.md instructions.

TIER_DIRECTIVES: dict[str, str] = {
    "none": "",
    "lightweight": (
        "<mendicant_directive>\n"
        "Task classified: CODE/RESEARCH. Verification: lightweight.\n"
        "After completing this task, run a self-review against the requirements.\n"
        "Check for: missing edge cases, unvalidated inputs, incomplete implementations.\n"
        "</mendicant_directive>"
    ),
    "quick": (
        "<mendicant_directive>\n"
        "Task classified: CODE_GENERATION. Verification: REQUIRED (quick).\n"
        "After completing implementation, you MUST:\n"
        "1. Run all tests and type checks\n"
        "2. Review your output against the original requirements\n"
        "3. Check for stubs, TODOs, or incomplete implementations\n"
        "4. Verify input validation and error handling on all endpoints\n"
        "If issues are found, fix them before presenting to the user.\n"
        "</mendicant_directive>"
    ),
    "full": (
        "<mendicant_directive>\n"
        "Task classified: CRITICAL_CODE. Verification: MANDATORY (full Aletheia pipeline).\n"
        "This task involves security-sensitive or architecturally critical code.\n"
        "After completing implementation, you MUST:\n"
        "1. Spawn an aletheia-evaluator TEAMMATE (team_name: \"mendicant\") with blind pre-analysis\n"
        "   — The evaluator must NOT see your reasoning. It gets only: task spec + file paths\n"
        "   — Read evaluator.md from ~/.claude/skills/mendicant_bias/evaluator.md and inline it\n"
        "   — The evaluator writes verdict to .mendicant/aletheia/evaluation.md\n"
        "2. Wait for the evaluation verdict\n"
        "3. If FIXABLE: address every issue by severity, then re-evaluate (max 3 iterations)\n"
        "4. If CORRECT: spawn loveless (subagent_type: \"loveless\") for security audit\n"
        "5. If WRONG: stop. Present the architectural critique. Do not attempt revision.\n"
        "Do NOT skip verification. Do NOT self-evaluate in place of the teammate.\n"
        "</mendicant_directive>"
    ),
    "design": (
        "<mendicant_directive>\n"
        "Task classified: MULTI_MODAL. Verification: REQUIRED (design).\n"
        "After completing implementation, you MUST:\n"
        "1. Take screenshots of the UI using claude-in-chrome\n"
        "2. Evaluate against Design Quality criteria: coherent identity, not generic AI patterns,\n"
        "   responsive, accessible\n"
        "3. Check console for errors, verify all interactions work\n"
        "4. Consider spawning cinna (subagent_type: \"cinna\") for design review\n"
        "</mendicant_directive>"
    ),
}

_TMPDIR = Path(tempfile.gettempdir())


# ── Utilities ───────────────────────────────────────────────────────────────

def _find_mendicant_dir() -> Path:
    candidates = [Path.cwd() / ".mendicant", Path.home() / ".mendicant"]
    for c in candidates:
        if c.exists():
            return c
    default = Path.cwd() / ".mendicant"
    default.mkdir(parents=True, exist_ok=True)
    return default


def _ensure_aletheia_dir(mendicant_dir: Path) -> Path:
    aletheia_dir = mendicant_dir / "aletheia"
    aletheia_dir.mkdir(parents=True, exist_ok=True)
    return aletheia_dir


def _signal(name: str, msg: str = "active") -> None:
    try:
        (_TMPDIR / f"mendicant_{name}").write_text(msg)
    except OSError:
        pass


def _probe_gateway() -> bool:
    """Check if the gateway is running. Returns True if healthy."""
    try:
        import urllib.request
        req = urllib.request.Request(
            f"{GATEWAY_URL}/health",
            method="GET",
        )
        with urllib.request.urlopen(req, timeout=GATEWAY_PROBE_TIMEOUT) as resp:
            return resp.status == 200
    except Exception:
        return False


def _auto_start_gateway() -> bool:
    """Start the gateway in the background if not running. Non-blocking."""
    try:
        # Use the mendicant CLI to start the gateway
        subprocess.Popen(
            ["mendicant", "start", "--background"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return True
    except Exception:
        # Gateway start failed — this is fine, everything works without it
        return False


def _load_mahoraga(mendicant_dir: Path):
    try:
        from mendicant_core.mahoraga import MahoragaEngine
        return MahoragaEngine(store_path=str(mendicant_dir / "mahoraga.json"))
    except Exception:
        return None


def _load_memory(mendicant_dir: Path):
    try:
        from mendicant_core.memory.store import MemoryStore
        return MemoryStore(storage_path=str(mendicant_dir / "memory.json"))
    except Exception:
        return None


def _load_classifier():
    try:
        from mendicant_core.middleware.smart_task_router import SmartTaskRouterMiddleware
        return SmartTaskRouterMiddleware()
    except Exception:
        return None


def _load_patterns(mendicant_dir: Path) -> list[dict]:
    try:
        path = mendicant_dir / "orchestration_patterns.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, list) else []
    except Exception:
        pass
    return []


def _filter_relevant_rules(rules: list) -> list:
    """Filter to only genuinely relevant rules using RELEVANCE SCORE."""
    filtered = []
    for r in rules:
        relevance = getattr(r, "_relevance_score", 0.0)
        if relevance >= MIN_RELEVANCE_SCORE and r.confidence >= MIN_INJECTION_CONFIDENCE:
            filtered.append(r)
    return filtered[:MAX_RULES_PER_EVENT]


def _format_rules(rules: list, tag: str = "mendicant_adaptations") -> str:
    """Format rules for injection."""
    if not rules:
        return ""
    lines = [f"<{tag}>"]
    for rule in rules:
        if rule.trigger and rule.trigger != "general":
            lines.append(f"- When {rule.trigger}: {rule.action}")
        else:
            lines.append(f"- {rule.action}")
    lines.append(f"</{tag}>")
    return "\n".join(lines)


# ── SessionStart ────────────────────────────────────────────────────────────

def handle_session_start(hook_input: dict) -> dict:
    """Full context load + decay + patterns + gateway probe."""
    _signal("session")
    _signal("hook_active", "initializing...")

    mendicant_dir = _find_mendicant_dir()
    _ensure_aletheia_dir(mendicant_dir)
    context_parts: list[str] = []

    # Decay stale Mahoraga rules
    engine = _load_mahoraga(mendicant_dir)
    if engine:
        decayed = engine.decay_unused_rules(days=14)
        if decayed > 0:
            _signal("adapted", f"decayed {decayed} stale rules")

        all_rules = engine.get_all_rules(active_only=True)
        if all_rules:
            injection = engine.format_rules_for_injection(all_rules)
            if injection:
                context_parts.append(injection)

    # Load memory facts
    store = _load_memory(mendicant_dir)
    if store:
        facts = store.get_facts(min_confidence=0.6)
        if facts:
            fact_lines = ["<mendicant_memory>"]
            for f in sorted(facts, key=lambda x: -x.confidence)[:15]:
                fact_lines.append(f"- [{f.category}] {f.content}")
            fact_lines.append("</mendicant_memory>")
            context_parts.append("\n".join(fact_lines))

    # Load pattern recommendations
    patterns = _load_patterns(mendicant_dir)
    if patterns:
        successful = [
            p for p in patterns
            if p.get("outcome") == "success"
            and p.get("task_type")
            and p.get("strategy_tags")
            and p.get("task_text")
        ]
        if successful:
            def _ts(p):
                t = p.get("timestamp", 0)
                return float(t) if isinstance(t, (int, float)) else 0.0

            recent = sorted(successful, key=_ts, reverse=True)[:5]
            pattern_lines = ["<mendicant_learned_patterns>"]
            pattern_lines.append("Strategies that worked in previous sessions:")
            for p in recent:
                task = p.get("task_text", "")[:60]
                strategy = ", ".join(p.get("strategy_tags", []))
                pattern_lines.append(f"- {task} → {strategy}")
            pattern_lines.append("</mendicant_learned_patterns>")
            context_parts.append("\n".join(pattern_lines))

    # Probe gateway — auto-start if not running
    gateway_status = "offline"
    if _probe_gateway():
        gateway_status = "online"
    else:
        if _auto_start_gateway():
            gateway_status = "starting"

    # Write gateway state for downstream hooks
    try:
        state_file = mendicant_dir / "gateway_state.json"
        state_file.write_text(json.dumps({
            "status": gateway_status,
            "url": GATEWAY_URL,
            "timestamp": time.time(),
        }))
    except OSError:
        pass

    # System identity block
    context_parts.append(
        f"<mendicant_system>"
        f"\nMendicant Bias V6 — ambient intelligence harness active."
        f"\nGateway: {gateway_status}"
        f"\nHooks: SessionStart, UserPromptSubmit, PreToolUse, PostToolUse, Stop"
        f"\nAletheia verification: integrated (tier auto-selected by FR5 classification)"
        f"\n</mendicant_system>"
    )

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": "\n\n".join(context_parts),
        },
    }


# ── UserPromptSubmit ────────────────────────────────────────────────────────

def handle_user_prompt(hook_input: dict) -> dict:
    """Classify task → inject behavioral DIRECTIVE for the verification tier."""
    prompt = hook_input.get("prompt", "")
    if not prompt or len(prompt) < 5:
        return {"continue": True}

    _signal("hook_active", "classifying...")

    mendicant_dir = _find_mendicant_dir()
    context_parts: list[str] = []

    # FR5 classification
    classifier = _load_classifier()
    task_type = None
    verification_tier = "none"

    if classifier:
        try:
            emb_type, emb_conf = classifier._classify_by_examples(prompt)
            kw_type, kw_conf = classifier._classify_keywords(prompt)
            task_type = classifier._blend_all(emb_type, emb_conf, kw_type, kw_conf, None, 0.0)
            verification_tier = VERIFICATION_TIERS.get(task_type or "SIMPLE", "none")
        except Exception:
            pass

    # Keyword fallback if classifier unavailable
    if not task_type:
        task_type = _classify_by_keywords_fallback(prompt)
        verification_tier = VERIFICATION_TIERS.get(task_type, "none")

    # Save classification for downstream hooks
    try:
        cls_file = mendicant_dir / "last_classification.json"
        cls_file.write_text(json.dumps({
            "task_type": task_type,
            "verification_tier": verification_tier,
            "prompt_preview": prompt[:120],
            "timestamp": time.time(),
        }))
    except OSError:
        pass

    # Inject classification (informational)
    if task_type and task_type != "SIMPLE":
        context_parts.append(
            f"<mendicant_classification>"
            f"\nTask type: {task_type}"
            f"\nAletheia verification tier: {verification_tier}"
            f"\n</mendicant_classification>"
        )

    # Inject DIRECTIVE for this verification tier
    directive = TIER_DIRECTIVES.get(verification_tier, "")
    if directive:
        context_parts.append(directive)

    # Mahoraga rule matching
    engine = _load_mahoraga(mendicant_dir)
    if engine:
        rules = engine.get_applicable_rules(prompt)
        strong = _filter_relevant_rules(rules)
        if strong:
            for rule in strong:
                engine.apply_and_track(rule.id)
            context_parts.append(_format_rules(strong))
            _signal("hook_active", f"applied {len(strong)} rules")

    if not context_parts:
        return {"continue": True}

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "\n\n".join(context_parts),
        },
    }


def _classify_by_keywords_fallback(prompt: str) -> str:
    """Simple keyword-based classification when the embedding classifier is unavailable."""
    lower = prompt.lower()

    critical_keywords = [
        "auth", "jwt", "token", "password", "secret", "encrypt", "decrypt",
        "security", "oauth", "permission", "rbac", "acl", "credential",
        "payment", "billing", "stripe", "financial", "hipaa", "pci",
        "certificate", "ssl", "tls", "csrf", "xss", "injection",
    ]
    code_keywords = [
        "build", "create", "implement", "write", "add", "feature",
        "endpoint", "api", "function", "component", "service", "module",
        "refactor", "migrate", "upgrade", "integrate",
    ]
    research_keywords = [
        "research", "investigate", "analyze", "find", "search", "explore",
        "understand", "explain", "how does", "what is", "compare",
    ]
    design_keywords = [
        "design", "ui", "ux", "frontend", "component", "layout",
        "responsive", "tailwind", "css", "visual", "landing page",
    ]

    if any(kw in lower for kw in critical_keywords):
        return "CRITICAL_CODE"
    if any(kw in lower for kw in design_keywords):
        return "MULTI_MODAL"
    if any(kw in lower for kw in code_keywords):
        return "CODE_GENERATION"
    if any(kw in lower for kw in research_keywords):
        return "RESEARCH"
    return "SIMPLE"


# ── PreToolUse ──────────────────────────────────────────────────────────────

def handle_pre_tool_use(hook_input: dict) -> dict:
    """Inject code-relevant Mahoraga guidance on Write/Edit."""
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    if tool_name not in ("Write", "Edit", "NotebookEdit"):
        return {"continue": True}

    _signal("hook_active", "checking code rules...")

    mendicant_dir = _find_mendicant_dir()
    engine = _load_mahoraga(mendicant_dir)
    if not engine:
        return {"continue": True}

    file_path = tool_input.get("file_path", "")
    content = tool_input.get("content", "") or tool_input.get("new_string", "")
    context = f"writing code to {file_path}: {content[:300]}"

    rules = engine.get_applicable_rules(context)
    code_rules = [
        r for r in rules
        if r.category in ("STYLE", "TOOL", "PREFERENCE", "CORRECTION")
        and r.confidence >= MIN_INJECTION_CONFIDENCE
    ][:3]

    if not code_rules:
        return {"continue": True}

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": _format_rules(code_rules, "mendicant_code_guidance"),
        },
    }


# ── PostToolUse ─────────────────────────────────────────────────────────────

def handle_post_tool_use(hook_input: dict) -> dict:
    """Verify output + record patterns. Reinforce directive for critical code."""
    tool_name = hook_input.get("tool_name", "")
    tool_input = hook_input.get("tool_input", {})

    if tool_name not in ("Write", "Edit", "Bash"):
        return {"continue": True}

    mendicant_dir = _find_mendicant_dir()
    context_parts: list[str] = []

    if tool_name in ("Write", "Edit"):
        _signal("hook_active", "verifying...")

        # Load classification
        verification_tier = "none"
        task_type = "SIMPLE"
        task_desc = f"Code written to {tool_input.get('file_path', 'unknown')}"
        try:
            cls_file = mendicant_dir / "last_classification.json"
            if cls_file.exists():
                cls_data = json.loads(cls_file.read_text())
                task_type = cls_data.get("task_type", "SIMPLE")
                verification_tier = cls_data.get("verification_tier", "none")
                task_desc = cls_data.get("prompt_preview", task_desc)
        except Exception:
            pass

        # Inline FR2 verification for quick/full/design tiers
        if verification_tier in ("quick", "full", "design"):
            try:
                from mendicant_core.middleware.verification import VerificationMiddleware

                verifier = VerificationMiddleware(max_retries=1)
                content = tool_input.get("content", "") or tool_input.get("new_string", "")

                criteria = verifier._run_pre_analysis(task_desc)
                if criteria:
                    result = verifier._run_grading(task_desc, content[:2000], criteria)
                    verdict = result.get("verdict", "CORRECT")
                    feedback = result.get("feedback", "")
                    confidence = float(result.get("confidence", 0.0))

                    if verdict == "FIXABLE" and confidence >= 0.6:
                        context_parts.append(
                            f"<mendicant_verification>\n"
                            f"Aletheia/{verification_tier}: FIXABLE\n"
                            f"Feedback: {feedback}\n"
                            f"Address these issues before continuing.\n"
                            f"</mendicant_verification>"
                        )
                    elif verdict == "WRONG" and confidence >= 0.5:
                        context_parts.append(
                            f"<mendicant_verification>\n"
                            f"Aletheia/{verification_tier}: WRONG\n"
                            f"Feedback: {feedback}\n"
                            f"This approach has fundamental issues. Reconsider before continuing.\n"
                            f"</mendicant_verification>"
                        )

            except Exception as exc:
                sys.stderr.write(f"[Mendicant/Aletheia] {exc}\n")

        # Reinforce the directive for full tier (teammate evaluator reminder)
        if verification_tier == "full":
            aletheia_dir = _ensure_aletheia_dir(mendicant_dir)
            try:
                signal_file = aletheia_dir / "pending_evaluation.json"
                signal_file.write_text(json.dumps({
                    "task_type": task_type,
                    "verification_tier": "full",
                    "task_description": task_desc,
                    "file_path": tool_input.get("file_path", ""),
                    "timestamp": time.time(),
                    "status": "pending",
                }))
            except OSError:
                pass
            context_parts.append(
                "<mendicant_directive>\n"
                "Reminder: CRITICAL_CODE verification is MANDATORY.\n"
                "When implementation is complete, spawn aletheia-evaluator teammate.\n"
                "Do not present final output to the user without Aletheia evaluation.\n"
                "</mendicant_directive>"
            )

    # Record pattern
    try:
        from mendicant_core.patterns import PatternStore
        store_path = mendicant_dir / "orchestration_patterns.json"
        pattern_store = PatternStore(store_path=str(store_path))

        cls_file = mendicant_dir / "last_classification.json"
        if cls_file.exists():
            cls_data = json.loads(cls_file.read_text())
            pattern_store.append({
                "task_type": cls_data.get("task_type", "SIMPLE"),
                "verification_tier": cls_data.get("verification_tier", "none"),
                "task_text": cls_data.get("prompt_preview", ""),
                "tools_used": [tool_name],
                "outcome": "success",
                "timestamp": time.time(),
                "strategy_tags": [tool_name.lower()],
            })
    except Exception:
        pass

    if not context_parts:
        return {"continue": True}

    return {
        "continue": True,
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": "\n\n".join(context_parts),
        },
    }


# ── Main ────────────────────────────────────────────────────────────────────

def main():
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            print(json.dumps({"continue": True}))
            return
        hook_input = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        print(json.dumps({"continue": True}))
        return

    event = hook_input.get("hook_event_name", "")

    try:
        if event == "SessionStart":
            result = handle_session_start(hook_input)
        elif event == "UserPromptSubmit":
            result = handle_user_prompt(hook_input)
        elif event == "PreToolUse":
            result = handle_pre_tool_use(hook_input)
        elif event == "PostToolUse":
            result = handle_post_tool_use(hook_input)
        else:
            result = {"continue": True}
    except Exception as exc:
        result = {"continue": True}
        sys.stderr.write(f"[Mendicant hook error] {exc}\n")

    print(json.dumps(result))


if __name__ == "__main__":
    main()
