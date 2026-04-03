"""
mendicant_runtime.thread_state
==============================
Full thread state schema for Mendicant Bias runtime, modeled after
DeerFlow's ``ThreadState`` with Mendicant-specific extensions.

Includes sandbox state, thread data paths, artifacts with deduplication,
verification state, task classification, and memory injection status.
"""

from __future__ import annotations

from typing import Annotated, Any, NotRequired, TypedDict

from langgraph.prebuilt import MessagesState


# ---------------------------------------------------------------------------
# Sub-state TypedDicts
# ---------------------------------------------------------------------------


class SandboxState(TypedDict):
    """Sandbox lifecycle state tracked in thread."""

    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    """Per-thread filesystem paths (workspace, uploads, outputs)."""

    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class VerificationState(TypedDict):
    """Accumulated verification results for the current thread."""

    enabled: NotRequired[bool]
    results: NotRequired[list[dict[str, Any]]]
    pass_count: NotRequired[int]
    fail_count: NotRequired[int]


class TaskClassification(TypedDict):
    """Cached task classification from SmartTaskRouter."""

    task_type: NotRequired[str | None]
    complexity: NotRequired[str | None]
    domains: NotRequired[list[str]]
    flags: NotRequired[dict[str, Any]]


# ---------------------------------------------------------------------------
# Reducers — merge logic for annotated state fields
# ---------------------------------------------------------------------------


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Merge and deduplicate artifact paths, preserving insertion order."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return list(dict.fromkeys(existing + new))


def merge_verification(
    existing: VerificationState | None, new: VerificationState | None
) -> VerificationState:
    """Merge verification state, accumulating results and counts."""
    if existing is None:
        return new or VerificationState(enabled=False, results=[], pass_count=0, fail_count=0)
    if new is None:
        return existing

    merged_results = list(existing.get("results", []))
    merged_results.extend(new.get("results", []))

    return VerificationState(
        enabled=new.get("enabled", existing.get("enabled", False)),
        results=merged_results,
        pass_count=existing.get("pass_count", 0) + new.get("pass_count", 0),
        fail_count=existing.get("fail_count", 0) + new.get("fail_count", 0),
    )


def merge_subagent_results(
    existing: list[dict[str, Any]] | None,
    new: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Merge subagent result lists, appending new results."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    return existing + new


# ---------------------------------------------------------------------------
# MendicantThreadState — the full state schema
# ---------------------------------------------------------------------------


class MendicantThreadState(MessagesState):
    """
    Full thread state for the Mendicant Bias runtime.

    Extends LangGraph's ``MessagesState`` (which provides ``messages`` with
    the standard message reducer) with sandbox lifecycle, thread paths,
    middleware state, and execution metadata.

    Fields
    ------
    sandbox : SandboxState
        Sandbox ID and lifecycle state.
    thread_data : ThreadDataState
        Per-thread filesystem paths.
    artifacts : list[str]
        Deduplicated list of artifact paths (files produced by agent).
    task_classification : TaskClassification
        Cached task type from SmartTaskRouter (avoids re-classification).
    verification : VerificationState
        Accumulated verification pass/fail results.
    subagent_results : list[dict]
        Results from spawned subagents.
    memory_injected : bool
        Whether memory context has been injected this turn.
    task_start_time : float
        Monotonic timestamp when the current invocation started.
    agent_name : str | None
        Name of the active agent profile (if any).
    turn_count : int
        Number of user turns processed in this thread.
    """

    # Sandbox & thread paths
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]

    # Artifacts with deduplication reducer
    artifacts: Annotated[list[str], merge_artifacts]

    # Task classification (cached per-thread by SmartTaskRouter)
    task_classification: NotRequired[TaskClassification | None]

    # Verification accumulator
    verification: Annotated[VerificationState, merge_verification]

    # Subagent results accumulator
    subagent_results: Annotated[list[dict[str, Any]], merge_subagent_results]

    # Memory injection flag (reset each turn)
    memory_injected: NotRequired[bool]

    # Execution metadata
    task_start_time: NotRequired[float | None]
    agent_name: NotRequired[str | None]
    turn_count: NotRequired[int]
