"""
Tests for mendicant_core.sandbox -- Sandbox execution system.

Covers:
- LocalSandbox path resolution (virtual -> physical, longest-prefix-first)
- Path validation (traversal rejection)
- Output masking (physical paths replaced with virtual)
- Command execution (stdout, stderr, timeout)
- File operations (read, write, append, list_dir)
- LocalSandboxProvider lifecycle (acquire, get, release, reuse)
- MendicantThreadState structure
- merge_artifacts reducer
- SandboxState / ThreadDataState typing
- Thread safety of LocalSandboxProvider
"""

from __future__ import annotations

import os
import threading
import sys

import pytest

from mendicant_core.sandbox import (
    LocalSandbox,
    LocalSandboxProvider,
    MendicantThreadState,
    Sandbox,
    SandboxProvider,
    SandboxState,
    ThreadDataState,
    merge_artifacts,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def sandbox_dir(tmp_path):
    """Create a temporary sandbox base directory."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    return workspace


@pytest.fixture()
def sandbox(sandbox_dir):
    """Create a LocalSandbox with standard path mappings."""
    uploads = sandbox_dir.parent / "uploads"
    outputs = sandbox_dir.parent / "outputs"
    uploads.mkdir(exist_ok=True)
    outputs.mkdir(exist_ok=True)

    return LocalSandbox(
        sandbox_id="test_sandbox",
        base_path=sandbox_dir,
        path_mappings={
            "/mnt/user-data/workspace": str(sandbox_dir),
            "/mnt/user-data/uploads": str(uploads),
            "/mnt/user-data/outputs": str(outputs),
            "/mnt/user-data": str(sandbox_dir.parent),
        },
    )


@pytest.fixture()
def provider(tmp_path):
    """Create a LocalSandboxProvider with a temp base directory."""
    return LocalSandboxProvider(base_dir=str(tmp_path / "threads"))


# ---------------------------------------------------------------------------
# Abstract interface tests
# ---------------------------------------------------------------------------


class TestSandboxABC:
    def test_cannot_instantiate(self):
        with pytest.raises(TypeError):
            Sandbox()  # type: ignore[abstract]

    def test_cannot_instantiate_provider(self):
        with pytest.raises(TypeError):
            SandboxProvider()  # type: ignore[abstract]


# ---------------------------------------------------------------------------
# LocalSandbox: path resolution
# ---------------------------------------------------------------------------


class TestLocalSandboxPathResolution:
    def test_workspace_path(self, sandbox, sandbox_dir):
        resolved = sandbox._resolve_path("/mnt/user-data/workspace/hello.py")
        assert resolved == sandbox_dir / "hello.py"

    def test_uploads_path(self, sandbox, sandbox_dir):
        uploads = sandbox_dir.parent / "uploads"
        resolved = sandbox._resolve_path("/mnt/user-data/uploads/data.csv")
        assert resolved == uploads / "data.csv"

    def test_outputs_path(self, sandbox, sandbox_dir):
        outputs = sandbox_dir.parent / "outputs"
        resolved = sandbox._resolve_path("/mnt/user-data/outputs/report.pdf")
        assert resolved == outputs / "report.pdf"

    def test_base_data_path(self, sandbox, sandbox_dir):
        # /mnt/user-data directly should resolve to the thread_dir (parent)
        resolved = sandbox._resolve_path("/mnt/user-data")
        assert resolved == sandbox_dir.parent

    def test_longest_prefix_wins(self, sandbox, sandbox_dir):
        """Ensure /mnt/user-data/workspace beats /mnt/user-data."""
        resolved = sandbox._resolve_path("/mnt/user-data/workspace/foo.txt")
        assert resolved == sandbox_dir / "foo.txt"
        # Not sandbox_dir.parent / "workspace" / "foo.txt"

    def test_fallback_strips_prefix(self, sandbox, sandbox_dir):
        """Paths with /mnt/user-data/ prefix but no matching mapping."""
        # Remove all mappings to test fallback
        sb = LocalSandbox(
            sandbox_id="bare",
            base_path=sandbox_dir,
            path_mappings={},
        )
        resolved = sb._resolve_path("/mnt/user-data/workspace/test.py")
        assert resolved == sandbox_dir / "workspace" / "test.py"

    def test_nested_path(self, sandbox, sandbox_dir):
        resolved = sandbox._resolve_path(
            "/mnt/user-data/workspace/src/deep/module.py"
        )
        assert resolved == sandbox_dir / "src" / "deep" / "module.py"


# ---------------------------------------------------------------------------
# LocalSandbox: path validation
# ---------------------------------------------------------------------------


class TestLocalSandboxPathValidation:
    def test_traversal_rejected(self, sandbox):
        with pytest.raises(PermissionError, match="traversal"):
            sandbox._validate_path("/mnt/user-data/workspace/../../etc/passwd")

    def test_dot_dot_in_filename_ok(self, sandbox):
        # "foo..bar" is NOT path traversal -- only literal ".." segments
        resolved = sandbox._validate_path("/mnt/user-data/workspace/foo..bar")
        assert "foo..bar" in str(resolved)

    def test_normal_path_passes(self, sandbox):
        resolved = sandbox._validate_path("/mnt/user-data/workspace/main.py")
        assert resolved.name == "main.py"


# ---------------------------------------------------------------------------
# LocalSandbox: output masking
# ---------------------------------------------------------------------------


class TestLocalSandboxMasking:
    def test_masks_workspace_path(self, sandbox, sandbox_dir):
        output = f"Reading {sandbox_dir}/test.py"
        masked = sandbox._mask_paths(output)
        assert str(sandbox_dir) not in masked
        assert "/mnt/user-data/workspace" in masked

    def test_masks_base_path(self, sandbox, sandbox_dir):
        output = f"Base: {sandbox_dir}"
        masked = sandbox._mask_paths(output)
        assert str(sandbox_dir) not in masked

    def test_no_physical_paths_leak(self, sandbox, sandbox_dir):
        uploads = sandbox_dir.parent / "uploads"
        outputs = sandbox_dir.parent / "outputs"
        output = f"{sandbox_dir}/a {uploads}/b {outputs}/c"
        masked = sandbox._mask_paths(output)
        assert str(sandbox_dir) not in masked
        assert str(uploads) not in masked
        assert str(outputs) not in masked


# ---------------------------------------------------------------------------
# LocalSandbox: file operations
# ---------------------------------------------------------------------------


class TestLocalSandboxFileOps:
    def test_write_and_read(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/hello.txt", "Hello World")
        content = sandbox.read_file("/mnt/user-data/workspace/hello.txt")
        assert content == "Hello World"

    def test_write_creates_dirs(self, sandbox):
        sandbox.write_file(
            "/mnt/user-data/workspace/a/b/c/deep.txt", "deep content"
        )
        content = sandbox.read_file("/mnt/user-data/workspace/a/b/c/deep.txt")
        assert content == "deep content"

    def test_write_append(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/log.txt", "line1\n")
        sandbox.write_file(
            "/mnt/user-data/workspace/log.txt", "line2\n", append=True
        )
        content = sandbox.read_file("/mnt/user-data/workspace/log.txt")
        assert content == "line1\nline2\n"

    def test_write_overwrite(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/over.txt", "original")
        sandbox.write_file("/mnt/user-data/workspace/over.txt", "replaced")
        content = sandbox.read_file("/mnt/user-data/workspace/over.txt")
        assert content == "replaced"

    def test_read_nonexistent(self, sandbox):
        with pytest.raises(FileNotFoundError):
            sandbox.read_file("/mnt/user-data/workspace/nope.txt")

    def test_read_directory_raises(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/d/file.txt", "x")
        with pytest.raises(IsADirectoryError):
            sandbox.read_file("/mnt/user-data/workspace/d")

    def test_list_dir(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/a.txt", "a")
        sandbox.write_file("/mnt/user-data/workspace/b.txt", "b")
        sandbox.write_file("/mnt/user-data/workspace/sub/c.txt", "c")

        entries = sandbox.list_dir("/mnt/user-data/workspace")
        names = [e.rstrip("/") for e in entries]
        assert "a.txt" in names
        assert "b.txt" in names
        assert "sub" in names
        assert "sub/c.txt" in names

    def test_list_dir_max_depth_zero(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/a.txt", "a")
        sandbox.write_file("/mnt/user-data/workspace/sub/b.txt", "b")
        entries = sandbox.list_dir("/mnt/user-data/workspace", max_depth=0)
        assert entries == []

    def test_list_dir_not_a_directory(self, sandbox):
        sandbox.write_file("/mnt/user-data/workspace/file.txt", "x")
        with pytest.raises(NotADirectoryError):
            sandbox.list_dir("/mnt/user-data/workspace/file.txt")


# ---------------------------------------------------------------------------
# LocalSandbox: command execution
# ---------------------------------------------------------------------------


class TestLocalSandboxCommands:
    @pytest.mark.skipif(
        sys.platform == "win32",
        reason="echo command behaves differently on Windows",
    )
    def test_simple_echo(self, sandbox):
        output = sandbox.execute_command("echo hello")
        assert "hello" in output

    def test_command_cwd_is_base_path(self, sandbox, sandbox_dir):
        """The command runs with cwd = base_path (workspace)."""
        sandbox.write_file("/mnt/user-data/workspace/marker.txt", "here")
        # Use python to check if marker exists relative to cwd.
        output = sandbox.execute_command(
            'python -c "import os; print(os.path.exists(\'marker.txt\'))"'
        )
        assert "True" in output

    def test_command_timeout(self, sandbox_dir):
        sb = LocalSandbox(
            sandbox_id="timeout_test",
            base_path=sandbox_dir,
            command_timeout=1,
        )
        output = sb.execute_command("python -c \"import time; time.sleep(10)\"")
        assert "timed out" in output.lower() or "ERROR" in output

    def test_command_path_translation(self, sandbox, sandbox_dir):
        """Virtual paths in the command are translated to physical paths.

        On Windows, translated paths contain backslashes that would break
        inline ``python -c`` strings, so we write a helper script instead.
        """
        sandbox.write_file("/mnt/user-data/workspace/data.txt", "contents!")
        # Write a small script that reads the file using the *virtual* path.
        # After path translation the ``open()`` call receives the physical
        # path and should succeed.
        sandbox.write_file(
            "/mnt/user-data/workspace/reader.py",
            "import pathlib, sys\n"
            "p = pathlib.Path(sys.argv[1])\n"
            "print(p.read_text())\n",
        )
        output = sandbox.execute_command(
            "python /mnt/user-data/workspace/reader.py "
            "/mnt/user-data/workspace/data.txt"
        )
        # The physical path should have been substituted in the command.
        assert "contents!" in output

    def test_command_output_masked(self, sandbox, sandbox_dir):
        """Physical paths in stdout are replaced with virtual paths."""
        output = sandbox.execute_command(
            f'python -c "print(\'{sandbox_dir}\')"'
        )
        assert str(sandbox_dir) not in output


# ---------------------------------------------------------------------------
# LocalSandbox: properties
# ---------------------------------------------------------------------------


class TestLocalSandboxProperties:
    def test_id(self, sandbox):
        assert sandbox.id == "test_sandbox"

    def test_base_path(self, sandbox, sandbox_dir):
        assert sandbox.base_path == sandbox_dir


# ---------------------------------------------------------------------------
# LocalSandboxProvider
# ---------------------------------------------------------------------------


class TestLocalSandboxProvider:
    def test_acquire_creates_directories(self, provider):
        sandbox = provider.acquire("thread-1")
        thread_dir = provider.base_dir / "thread-1" / "user-data"
        assert (thread_dir / "workspace").is_dir()
        assert (thread_dir / "uploads").is_dir()
        assert (thread_dir / "outputs").is_dir()

    def test_acquire_returns_same_sandbox(self, provider):
        s1 = provider.acquire("thread-1")
        s2 = provider.acquire("thread-1")
        assert s1 is s2

    def test_acquire_different_threads(self, provider):
        s1 = provider.acquire("thread-1")
        s2 = provider.acquire("thread-2")
        assert s1.id != s2.id
        assert s1 is not s2

    def test_sandbox_id_format(self, provider):
        sandbox = provider.acquire("abc123")
        assert sandbox.id == "local_abc123"

    def test_get_existing(self, provider):
        provider.acquire("thread-1")
        found = provider.get("local_thread-1")
        assert found is not None
        assert found.id == "local_thread-1"

    def test_get_missing(self, provider):
        assert provider.get("nonexistent") is None

    def test_release(self, provider):
        sandbox = provider.acquire("thread-1")
        assert provider.active_count() == 1
        provider.release(sandbox.id)
        assert provider.active_count() == 0
        assert provider.get(sandbox.id) is None

    def test_release_unknown(self, provider):
        # Should not raise.
        provider.release("nope")

    def test_active_count(self, provider):
        assert provider.active_count() == 0
        provider.acquire("t1")
        assert provider.active_count() == 1
        provider.acquire("t2")
        assert provider.active_count() == 2
        provider.release("local_t1")
        assert provider.active_count() == 1

    def test_thread_ids(self, provider):
        provider.acquire("alpha")
        provider.acquire("beta")
        ids = provider.thread_ids()
        assert set(ids) == {"alpha", "beta"}

    def test_file_operations_through_provider(self, provider):
        """End-to-end: acquire, write, read, release."""
        sandbox = provider.acquire("e2e-thread")
        sandbox.write_file("/mnt/user-data/workspace/test.py", "print(42)")
        content = sandbox.read_file("/mnt/user-data/workspace/test.py")
        assert content == "print(42)"
        provider.release(sandbox.id)


# ---------------------------------------------------------------------------
# Provider thread safety
# ---------------------------------------------------------------------------


class TestLocalSandboxProviderThreadSafety:
    def test_concurrent_acquire_same_thread(self, provider):
        """Multiple threads acquiring the same thread_id get the same sandbox."""
        results: list[LocalSandbox] = []
        errors: list[Exception] = []

        def worker():
            try:
                results.append(provider.acquire("shared"))
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert len(results) == 20
        assert all(r is results[0] for r in results)

    def test_concurrent_acquire_different_threads(self, provider):
        errors: list[Exception] = []

        def worker(tid: str):
            try:
                s = provider.acquire(tid)
                s.write_file(f"/mnt/user-data/workspace/{tid}.txt", tid)
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker, args=(f"t-{i}",))
            for i in range(30)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert provider.active_count() == 30


# ---------------------------------------------------------------------------
# State types
# ---------------------------------------------------------------------------


class TestSandboxState:
    def test_sandbox_state_dict(self):
        state: SandboxState = {"sandbox_id": "local_abc"}
        assert state["sandbox_id"] == "local_abc"

    def test_sandbox_state_empty(self):
        state: SandboxState = {}
        assert "sandbox_id" not in state

    def test_thread_data_state(self):
        state: ThreadDataState = {
            "workspace_path": "/tmp/ws",
            "uploads_path": "/tmp/up",
            "outputs_path": "/tmp/out",
        }
        assert state["workspace_path"] == "/tmp/ws"


# ---------------------------------------------------------------------------
# merge_artifacts reducer
# ---------------------------------------------------------------------------


class TestMergeArtifacts:
    def test_both_none(self):
        assert merge_artifacts(None, None) == []

    def test_existing_none(self):
        assert merge_artifacts(None, ["a.py"]) == ["a.py"]

    def test_new_none(self):
        assert merge_artifacts(["a.py"], None) == ["a.py"]

    def test_deduplication(self):
        result = merge_artifacts(["a.py", "b.py"], ["b.py", "c.py"])
        assert result == ["a.py", "b.py", "c.py"]

    def test_preserves_order(self):
        result = merge_artifacts(["z.py", "a.py"], ["m.py"])
        assert result == ["z.py", "a.py", "m.py"]

    def test_empty_lists(self):
        assert merge_artifacts([], []) == []


# ---------------------------------------------------------------------------
# MendicantThreadState
# ---------------------------------------------------------------------------


class TestMendicantThreadState:
    def test_can_construct(self):
        state: MendicantThreadState = {
            "messages": [],
        }
        assert state["messages"] == []

    def test_with_sandbox(self):
        state: MendicantThreadState = {
            "messages": [],
            "sandbox": {"sandbox_id": "local_t1"},
            "thread_data": {
                "workspace_path": "/tmp/ws",
                "uploads_path": "/tmp/up",
                "outputs_path": "/tmp/out",
            },
        }
        assert state["sandbox"]["sandbox_id"] == "local_t1"

    def test_with_middleware_fields(self):
        state: MendicantThreadState = {
            "messages": [],
            "task_type": "CODE_GENERATION",
            "verification_enabled": True,
            "selected_tools": ["bash", "write_file"],
            "tool_scores": {"bash": 0.95, "write_file": 0.87},
        }
        assert state["task_type"] == "CODE_GENERATION"
        assert state["tool_scores"]["bash"] == 0.95
