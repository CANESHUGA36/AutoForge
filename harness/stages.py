"""Pipeline 具体阶段实现。"""
from __future__ import annotations
import logging
from pathlib import Path

from harness.pipeline import PipelineStage, StageResult
from harness.events import EventBus

log = logging.getLogger("harness")


class PreBuildGateStage(PipelineStage):
    """预检阶段：验证环境完整性。"""
    name = "prebuild_gate"
    allow_auto_fix = True
    timeout_seconds = 120

    def run(self) -> StageResult:
        checks = []

        # 检查 1: package.json
        pkg = self.workspace / "package.json"
        if not pkg.exists():
            return StageResult(
                success=False,
                message="No package.json found",
                payload={"missing": "package.json", "auto_fix": "project_init"}
            )
        checks.append("package.json OK")

        # 检查 2: node_modules
        nm = self.workspace / "node_modules"
        if not nm.exists():
            return StageResult(
                success=False,
                message="node_modules missing",
                payload={"missing": "node_modules", "auto_fix": "npm_install"}
            )
        checks.append("node_modules OK")

        # 检查 3: 关键二进制
        bin_dir = nm / ".bin"
        critical_bins = ["vite", "next", "tsc"]
        found_bins = []
        for b in critical_bins:
            if (bin_dir / b).exists() or (bin_dir / (b + ".cmd")).exists():
                found_bins.append(b)

        if not found_bins:
            return StageResult(
                success=False,
                message="No build tools found in node_modules/.bin",
                payload={"missing": "build_tools", "auto_fix": "npm_install"}
            )
        checks.append(f"build tools: {', '.join(found_bins)}")

        # 检查 4: 实际构建验证（核心新增）
        from tools_impl import validate_build
        build_result = validate_build()
        if "[BUILD OK]" not in build_result:
            return StageResult(
                success=False,
                message=f"Build verification failed: {build_result[:200]}",
                payload={
                    "build_output": build_result,
                    "auto_fix": "npm_install",
                },
            )
        checks.append("build passes")

        return StageResult(success=True, message="; ".join(checks))

    def auto_fix(self) -> StageResult:
        """尝试自动修复环境问题。修复后必须验证构建通过。"""
        from tools_impl import run_bash, validate_build, project_init

        pkg = self.workspace / "package.json"
        if not pkg.exists():
            # 空项目：需要初始化
            log.info("[prebuild_gate] Auto-fix: initializing project from template...")
            init_result = project_init("vite-react-ts")
            if init_result.startswith("[error]"):
                return StageResult(
                    success=False,
                    message=f"Project init failed: {init_result[:300]}",
                    payload={"init_output": init_result},
                )
            # project_init 已经验证构建通过，直接返回成功
            return StageResult(
                success=True,
                message="Project initialized and build passes",
                payload={"init_output": init_result},
            )

        # package.json 存在但依赖可能缺失
        result = run_bash("npm install 2>&1", timeout=180)
        if result.startswith("[error]"):
            return StageResult(success=False, message=f"npm install failed: {result}")

        # 修复后再次验证构建
        build_result = validate_build()
        if "[BUILD OK]" not in build_result:
            return StageResult(
                success=False,
                message=f"npm install completed but build still fails: {build_result[:200]}",
                payload={"build_output": build_result},
            )

        return StageResult(success=True, message="npm install completed and build passes")


class BuildGateStage(PipelineStage):
    """构建检查阶段：验证 npm run build 通过。"""
    name = "build_gate"
    timeout_seconds = 300

    def run(self) -> StageResult:
        from tools_impl import validate_build
        result = validate_build()
        if "[BUILD OK]" in result:
            return StageResult(
                success=True,
                message="Build passed",
                payload={"build_output": result}
            )
        return StageResult(
            success=False,
            message="Build failed",
            payload={"build_output": result},
            should_skip_remaining=True
        )


class DevServerGateStage(PipelineStage):
    """Dev Server 检查阶段：确保服务器可访问。"""
    name = "dev_server_gate"
    allow_auto_fix = True
    timeout_seconds = 60

    def run(self) -> StageResult:
        from harness.build import verify_dev_server
        ok, msg = verify_dev_server(self.workspace)
        if ok:
            return StageResult(success=True, message=msg)
        return StageResult(
            success=False,
            message=msg,
            payload={"auto_fix": "start_server"}
        )

    def auto_fix(self) -> StageResult:
        """自动启动 dev server。"""
        from tools_impl import start_dev_server
        from harness.build import _detect_project_port
        port = _detect_project_port(self.workspace)
        result = start_dev_server("npm run dev", port=port, wait=5)
        if result.startswith("[error]"):
            return StageResult(success=False, message=result)
        return StageResult(success=True, message=result)


class ScreenshotGateStage(PipelineStage):
    """截图验证阶段（不阻塞）。"""
    name = "screenshot_gate"
    timeout_seconds = 30

    def run(self) -> StageResult:
        try:
            from tools.playwright_mcp import browser_test_mcp
            result = browser_test_mcp(
                url="http://localhost:5173",
                actions=[{"type": "wait", "delay": 2000}],
                screenshot=True,
            )
            has_error = "[error]" in result
            return StageResult(
                success=True,  # 从不阻塞
                message=result[:200] if has_error else "Page renders correctly",
                payload={"render_ok": not has_error, "full_result": result},
            )
        except Exception as e:
            return StageResult(success=True, message=f"Screenshot skipped: {e}")


class GitCommitStage(PipelineStage):
    """Git 提交阶段。"""
    name = "git_commit"
    timeout_seconds = 30

    def run(self) -> StageResult:
        from harness.git import GitManager
        git = GitManager(self.workspace)
        hash_ = git.commit_round(self.round_num)
        return StageResult(
            success=True,
            message=f"Committed: {hash_}",
            payload={"commit_hash": hash_}
        )


class ReviewStage(PipelineStage):
    """Reviewer Agent 阶段。"""
    name = "review"
    timeout_seconds = 1800  # 30 min

    def run(self) -> StageResult:
        # ReviewStage 需要访问 Harness 的 Agent 实例
        # 由于 Stage 是独立类，这里通过 payload 传递 Reviewer 结果
        # 实际调用在 Harness._build_round 中处理
        return StageResult(
            success=True,
            message="Review delegated to Harness",
            payload={"delegated": True}
        )


class JudgeStage(PipelineStage):
    """Judge Agent 阶段。"""
    name = "judge"
    timeout_seconds = 600  # 10 min

    def run(self) -> StageResult:
        # JudgeStage 同样需要 Harness 的 Agent 实例
        return StageResult(
            success=True,
            message="Judge delegated to Harness",
            payload={"delegated": True}
        )
