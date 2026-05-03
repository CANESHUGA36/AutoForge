"""Tests for tools_impl core file and bash operations."""
from __future__ import annotations

import pytest
from pathlib import Path

import config
from tools_impl import read_file, write_file, edit_file, list_files, run_bash


@pytest.fixture(autouse=True)
def set_workspace(tmp_path, monkeypatch):
    """Each test gets its own workspace."""
    monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
    return tmp_path


class TestReadFile:
    def test_reads_existing_file(self, set_workspace):
        (set_workspace / "test.txt").write_text("hello world", encoding="utf-8")
        result = read_file("test.txt")
        assert result == "hello world"

    def test_returns_error_for_missing_file(self, set_workspace):
        result = read_file("missing.txt")
        assert result.startswith("[error]")
        assert "not found" in result

    def test_truncates_long_files(self, set_workspace):
        # Test with .html file (limit 120K) — use 150K to trigger truncation
        content = "x" * 150_000
        (set_workspace / "long.html").write_text(content, encoding="utf-8")
        result = read_file("long.html")
        assert "[TRUNCATED]" in result
        assert len(result) < 150_000

    def test_truncates_tsx_files(self, set_workspace):
        # Test with .tsx file (limit 80K) — use 100K to trigger truncation
        content = "x" * 100_000
        (set_workspace / "long.tsx").write_text(content, encoding="utf-8")
        result = read_file("long.tsx")
        assert "[TRUNCATED]" in result
        assert len(result) < 100_000

    def test_no_truncation_for_small_files(self, set_workspace):
        # Small file should not be truncated
        content = "x" * 1000
        (set_workspace / "small.txt").write_text(content, encoding="utf-8")
        result = read_file("small.txt")
        assert "[TRUNCATED]" not in result
        assert result == content


class TestWriteFile:
    def test_creates_new_file(self, set_workspace):
        result = write_file("new.txt", "content")
        assert "wrote" in result.lower() or "written" in result.lower() or "ok" in result.lower()
        assert (set_workspace / "new.txt").read_text(encoding="utf-8") == "content"

    def test_overwrites_existing_file(self, set_workspace):
        (set_workspace / "existing.txt").write_text("old", encoding="utf-8")
        result = write_file("existing.txt", "new")
        assert (set_workspace / "existing.txt").read_text(encoding="utf-8") == "new"

    def test_creates_nested_directories(self, set_workspace):
        result = write_file("deep/nested/file.txt", "deep content")
        assert (set_workspace / "deep" / "nested" / "file.txt").exists()


class TestEditFile:
    def test_replaces_string(self, set_workspace):
        (set_workspace / "edit.txt").write_text("hello world", encoding="utf-8")
        result = edit_file("edit.txt", "hello", "goodbye")
        assert "replaced" in result.lower() or "ok" in result.lower()
        assert (set_workspace / "edit.txt").read_text(encoding="utf-8") == "goodbye world"

    def test_returns_error_for_missing_file(self, set_workspace):
        result = edit_file("missing.txt", "a", "b")
        assert result.startswith("[error]")

    def test_returns_error_when_old_string_not_found(self, set_workspace):
        (set_workspace / "edit.txt").write_text("content", encoding="utf-8")
        result = edit_file("edit.txt", "notfound", "replacement")
        assert result.startswith("[error]")


class TestListFiles:
    def test_lists_files_in_workspace(self, set_workspace):
        (set_workspace / "a.txt").write_text("a", encoding="utf-8")
        (set_workspace / "b.txt").write_text("b", encoding="utf-8")
        result = list_files(".")
        assert "a.txt" in result
        assert "b.txt" in result

    def test_excludes_ignored_patterns(self, set_workspace):
        (set_workspace / "node_modules").mkdir()
        (set_workspace / "node_modules" / "pkg").mkdir()
        (set_workspace / "node_modules" / "pkg" / "index.js").write_text("x", encoding="utf-8")
        (set_workspace / "src").mkdir()
        (set_workspace / "src" / "app.tsx").write_text("x", encoding="utf-8")
        result = list_files(".")
        assert "app.tsx" in result
        assert "node_modules" not in result


class TestRunBash:
    def test_executes_command(self, set_workspace):
        result = run_bash("echo hello")
        assert "hello" in result

    def test_returns_error_for_invalid_command(self, set_workspace):
        result = run_bash("this_command_does_not_exist_12345")
        # Windows returns exit code 1 with error message, not always "[error]"
        assert "[error]" in result or "[exit code:" in result

    def test_respects_timeout(self, set_workspace):
        # Use a command that sleeps longer than timeout (Windows compatible)
        result = run_bash("ping -n 6 127.0.0.1 > nul", timeout=1)
        assert "[error]" in result or "timeout" in result.lower() or "[exit code:" in result
