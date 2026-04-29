"""Tests for Pipeline Stage implementations."""
from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from harness.events import EventBus
from harness.stages import (
    PreBuildGateStage,
    BuildGateStage,
    DevServerGateStage,
    ScreenshotGateStage,
    GitCommitStage,
)


# --------------------------------------------------------------------------- #
#  PreBuildGateStage
# --------------------------------------------------------------------------- #

class TestPreBuildGateStage:
    def test_missing_package_json_pure_html(self, mock_workspace):
        """Pure HTML project without package.json should pass immediately."""
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        result = stage.execute()
        assert result.success is True
        assert "Pure HTML" in result.message

    def test_missing_node_modules(self, mock_workspace):
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        (mock_workspace / "node_modules").mkdir()
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.run_bash") as mock_bash:
            mock_bash.return_value = "[error] npm install failed"
            result = stage.execute()
        assert result.success is False
        # execute() 现在返回 auto_fix 的结果，让用户看到真正失败原因
        assert "npm install failed" in result.message
        assert result.auto_fix_attempted is True

    def test_missing_build_tools(self, mock_workspace):
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        (mock_workspace / "node_modules").mkdir()
        (mock_workspace / "node_modules" / ".bin").mkdir()
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.run_bash") as mock_bash:
            mock_bash.return_value = "[error] npm install failed"
            result = stage.execute()
        assert result.success is False
        # execute() 现在返回 auto_fix 的结果，让用户看到真正失败原因
        assert "npm install failed" in result.message
        assert result.auto_fix_attempted is True

    def test_success_with_vite(self, mock_workspace):
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        (mock_workspace / "node_modules").mkdir()
        (mock_workspace / "node_modules" / ".bin").mkdir()
        (mock_workspace / "node_modules" / ".bin" / "vite").write_text("")
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.validate_build") as mock_build:
            mock_build.return_value = "[BUILD OK] Production build succeeded."
            result = stage.execute()
        assert result.success is True
        assert "vite" in result.message
        assert "build passes" in result.message

    def test_success_with_tsc_cmd(self, mock_workspace):
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        (mock_workspace / "node_modules").mkdir()
        (mock_workspace / "node_modules" / ".bin").mkdir()
        (mock_workspace / "node_modules" / ".bin" / "tsc.cmd").write_text("")
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.validate_build") as mock_build:
            mock_build.return_value = "[BUILD OK] Production build succeeded."
            result = stage.execute()
        assert result.success is True

    def test_auto_fix_project_init(self, mock_workspace):
        """空项目（有 package.json 但没有 node_modules）时 auto_fix 应调用 project_init 初始化项目。"""
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.project_init") as mock_init, \
             patch("tools_impl.validate_build") as mock_build:
            mock_init.return_value = "[BUILD OK] Project initialized from vite-react-ts template."
            mock_build.return_value = "[BUILD OK] Build passes"
            result = stage.execute()
            assert result.success is True
            assert "Project initialized" in result.message
            mock_init.assert_called_once_with("vite-react-ts")

    def test_auto_fix_triggers_npm_install(self, mock_workspace):
        """package.json 存在且 node_modules 存在但构建失败时，auto_fix 应运行 npm install。"""
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        (mock_workspace / "node_modules").mkdir()
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.run_bash") as mock_bash, \
             patch("tools_impl.validate_build") as mock_build:
            mock_bash.return_value = "added 42 packages"
            mock_build.return_value = "[BUILD OK] Production build succeeded."
            result = stage.execute()
            assert result.success is True
            assert "npm install" in result.message
            mock_bash.assert_called_once()

    def test_build_verification_fails(self, mock_workspace):
        (mock_workspace / "package.json").write_text('{"name": "test"}')
        (mock_workspace / "node_modules").mkdir()
        (mock_workspace / "node_modules" / ".bin").mkdir()
        (mock_workspace / "node_modules" / ".bin" / "vite").write_text("")
        bus = EventBus(mock_workspace)
        stage = PreBuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.validate_build") as mock_build, \
             patch("tools_impl.run_bash") as mock_bash:
            mock_bash.return_value = "added 42 packages"
            mock_build.return_value = "[BUILD WARNING] TypeScript compilation failed"
            result = stage.execute()
        assert result.success is False
        # execute() 现在返回 auto_fix 的结果
        assert "build still fails" in result.message
        assert result.auto_fix_attempted is True


# --------------------------------------------------------------------------- #
#  BuildGateStage
# --------------------------------------------------------------------------- #

class TestBuildGateStage:
    def test_build_pass(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = BuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.validate_build") as mock_build:
            mock_build.return_value = "[BUILD OK] output"
            result = stage.execute()
            assert result.success is True
            assert result.message == "Build passed"

    def test_build_fail_sets_skip_remaining(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = BuildGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.validate_build") as mock_build:
            mock_build.return_value = "error: something failed"
            result = stage.execute()
            assert result.success is False
            assert result.should_skip_remaining is True
            assert "Build failed" in result.message


# --------------------------------------------------------------------------- #
#  DevServerGateStage
# --------------------------------------------------------------------------- #

class TestDevServerGateStage:
    def test_server_running(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = DevServerGateStage(mock_workspace, bus, 1)
        with patch("harness.build.verify_dev_server") as mock_verify:
            mock_verify.return_value = (True, "Server OK at localhost:5173")
            result = stage.execute()
            assert result.success is True

    def test_server_not_running_triggers_auto_fix(self, mock_workspace):
        # Create a mock package.json so it's not treated as pure HTML
        (mock_workspace / "package.json").write_text('{"dependencies": {}}')
        bus = EventBus(mock_workspace)
        stage = DevServerGateStage(mock_workspace, bus, 1)
        with patch("harness.build.verify_dev_server") as mock_verify, \
             patch("tools_impl.start_dev_server") as mock_start, \
             patch("harness.build._detect_project_port") as mock_port:
            mock_verify.return_value = (False, "Connection refused")
            mock_port.return_value = 5173
            mock_start.return_value = "Server started on port 5173"
            result = stage.execute()
            assert result.success is True
            assert "Server started" in result.message

    def test_server_start_fails(self, mock_workspace):
        # Create a mock package.json so it's not treated as pure HTML
        (mock_workspace / "package.json").write_text('{"dependencies": {}}')
        bus = EventBus(mock_workspace)
        stage = DevServerGateStage(mock_workspace, bus, 1)
        with patch("harness.build.verify_dev_server") as mock_verify, \
             patch("tools_impl.start_dev_server") as mock_start, \
             patch("harness.build._detect_project_port") as mock_port:
            mock_verify.return_value = (False, "Connection refused")
            mock_port.return_value = 5173
            mock_start.return_value = "[error] failed to start"
            result = stage.execute()
            assert result.success is False


# --------------------------------------------------------------------------- #
#  ScreenshotGateStage
# --------------------------------------------------------------------------- #

class TestScreenshotGateStage:
    def test_screenshot_success(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = ScreenshotGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.browser_check") as mock_browser:
            mock_browser.return_value = "Page loaded successfully"
            result = stage.execute()
            assert result.success is True  # never blocks
            assert result.payload["render_ok"] is True

    def test_screenshot_error_not_blocking(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = ScreenshotGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.browser_check") as mock_browser:
            mock_browser.return_value = "[error] page not found"
            result = stage.execute()
            assert result.success is True  # still not blocking
            assert result.payload["render_ok"] is False

    def test_screenshot_exception_not_blocking(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = ScreenshotGateStage(mock_workspace, bus, 1)
        with patch("tools_impl.browser_check") as mock_browser:
            mock_browser.side_effect = Exception("browser crashed")
            result = stage.execute()
            assert result.success is True
            assert "skipped" in result.message


# --------------------------------------------------------------------------- #
#  GitCommitStage
# --------------------------------------------------------------------------- #

class TestGitCommitStage:
    def test_commit_success(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = GitCommitStage(mock_workspace, bus, 1)
        with patch("harness.git.GitManager") as MockGit:
            mock_git = MagicMock()
            mock_git.commit_round.return_value = "abc123"
            MockGit.return_value = mock_git
            result = stage.execute()
            assert result.success is True
            assert result.payload["commit_hash"] == "abc123"
            mock_git.commit_round.assert_called_once_with(1)

    def test_commit_failure(self, mock_workspace):
        bus = EventBus(mock_workspace)
        stage = GitCommitStage(mock_workspace, bus, 1)
        with patch("harness.git.GitManager") as MockGit:
            mock_git = MagicMock()
            mock_git.commit_round.side_effect = Exception("git error")
            MockGit.return_value = mock_git
            result = stage.execute()
            assert result.success is False
            assert "crashed" in result.message
