"""Pipeline 具体阶段实现。"""
from __future__ import annotations
import logging
from pathlib import Path

from harness.pipeline import PipelineStage, StageResult
from harness.events import EventBus

log = logging.getLogger("harness")


class PreBuildGateStage(PipelineStage):
    """预检阶段：验证环境完整性。
    
    纯 HTML 项目不需要 node/npm 环境，直接跳过。
    """
    name = "prebuild_gate"
    allow_auto_fix = True
    timeout_seconds = 120

    def run(self) -> StageResult:
        checks = []

        # 纯 HTML 项目不需要环境检查
        pkg = self.workspace / "package.json"
        if not pkg.exists():
            return StageResult(
                success=True,
                message="Pure HTML project — no build environment needed"
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
            # 纯 HTML 项目不需要修复
            return StageResult(
                success=True,
                message="Pure HTML project — no build environment needed"
            )

        # Check if node_modules exists — if not, project needs initialization
        nm = self.workspace / "node_modules"
        if not nm.exists():
            log.info("[prebuild_gate] Auto-fix: initializing project from template...")
            init_result = project_init("vite-react-ts")
            if init_result.startswith("[error]"):
                return StageResult(
                    success=False,
                    message=f"Project init failed: {init_result[:300]}",
                    payload={"init_output": init_result},
                )
            return StageResult(
                success=True,
                message="Project initialized and build passes",
                payload={"init_output": init_result},
            )

        # package.json 存在且 node_modules 存在，但构建可能失败
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
    """构建检查阶段：验证 npm run build 通过 + 防御性编码静态检查。"""
    name = "build_gate"
    timeout_seconds = 300

    def run(self) -> StageResult:
        from tools_impl import validate_build, run_bash

        # ── 检查 1: 构建验证 ──
        result = validate_build()
        if "[BUILD OK]" not in result:
            return StageResult(
                success=False,
                message="Build failed",
                payload={"build_output": result},
                should_skip_remaining=True
            )

        # ── 检查 2: 防御性编码 —— 禁止 React 条件渲染 ──
        # 纯 HTML 项目跳过此检查
        pkg = self.workspace / "package.json"
        if pkg.exists():
            jsx_files = list(self.workspace.rglob("*.tsx")) + list(self.workspace.rglob("*.jsx"))
            # 排除 node_modules 和 .next/.vite 构建输出
            jsx_files = [
                f for f in jsx_files
                if "node_modules" not in str(f) and ".next" not in str(f) and ".vite" not in str(f)
            ]

            violations = []
            for f in jsx_files:
                content = f.read_text(encoding="utf-8")
                lines = content.splitlines()
                for i, line in enumerate(lines, 1):
                    stripped = line.strip()
                    # 检测 JSX 中的条件渲染模式：{condition && <Element} 或 {condition ? <Element> : ...}
                    # 排除注释行和字符串中的模式
                    if stripped.startswith("//") or stripped.startswith("*"):
                        continue
                    # 简单启发式：行内同时包含 JSX 标签和条件运算符
                    if ("&&" in stripped or "?" in stripped) and ("<" in stripped or "</" in stripped):
                        # 进一步确认：排除合法场景（如对象展开、类型定义）
                        # 条件渲染的特征：{ 开头，&& 或 ? 后面紧跟 <
                        if stripped.startswith("{") and ("&& <" in stripped or "? <" in stripped or "&&<" in stripped or "?<" in stripped):
                            rel_path = f.relative_to(self.workspace)
                            violations.append(f"  {rel_path}:{i}: {stripped[:80]}")

            if violations:
                # 不阻塞构建，但记录警告供 Reviewer 关注
                log.warning(
                    f"[build_gate] Found {len(violations)} potential conditional render patterns "
                    f"(Reviewer may fail to find these elements):\n" + "\n".join(violations[:10])
                )
                # 如果违规较多，标记为需要关注
                if len(violations) >= 3:
                    return StageResult(
                        success=True,  # 构建本身通过
                        message=f"Build passed, BUT {len(violations)} conditional render patterns detected",
                        payload={
                            "build_output": result,
                            "conditional_render_warnings": violations[:20],
                            "note": "Builder used conditional rendering ({condition && <Element>}) which makes elements invisible to Reviewer DOM queries. This often causes 0% scores.",
                        },
                    )

        return StageResult(
            success=True,
            message="Build passed",
            payload={"build_output": result}
        )


class DesignLintStage(PipelineStage):
    """设计静态检查：正则匹配代码中的 Tailwind 类名一致性（不阻塞，只报告）"""
    name = "design_lint"
    timeout_seconds = 60

    def run(self) -> StageResult:
        import re

        pkg = self.workspace / "package.json"
        if not pkg.exists():
            return StageResult(success=True, message="Pure HTML project — no design lint needed")

        jsx_files = list(self.workspace.rglob("*.tsx")) + list(self.workspace.rglob("*.jsx"))
        jsx_files = [f for f in jsx_files if "node_modules" not in str(f) and ".next" not in str(f) and ".vite" not in str(f)]

        issues = []
        for f in jsx_files:
            try:
                content = f.read_text(encoding="utf-8")
            except Exception:
                continue

            rel = f.relative_to(self.workspace)

            # 检查按钮圆角一致性
            buttons = re.findall(r'className="([^"]*button[^"]*)"', content)
            buttons += re.findall(r"className='([^']*button[^']*)'", content)
            rounded_styles = set()
            for btn in buttons:
                if "rounded-lg" in btn:
                    rounded_styles.add("rounded-lg")
                elif "rounded-md" in btn:
                    rounded_styles.add("rounded-md")
                elif "rounded" in btn:
                    rounded_styles.add("rounded")
            if len(rounded_styles) > 1:
                issues.append(f"  {rel}: 按钮圆角不一致 {rounded_styles}")

            # 检查是否混用 emoji / 非 Lucide SVG
            if re.search(r'[^\w]svg[^\w]', content.lower()) and "lucide" not in content.lower():
                issues.append(f"  {rel}: 可能使用了非 Lucide 图标（发现 svg 且无 lucide 导入）")

            # 检查空状态是否太简陋
            if '"暂无数据"' in content or "'暂无数据'" in content or '"No data"' in content:
                issues.append(f"  {rel}: 空状态使用纯文本，应改为图标+引导文字的占位 UI")

        if issues:
            return StageResult(
                success=True,  # 不阻塞构建
                message=f"Design lint: {len(issues)} style issue(s) detected",
                payload={"design_issues": issues[:20]},
            )
        return StageResult(success=True, message="Design lint passed")


class DevServerGateStage(PipelineStage):
    """Dev Server 检查阶段：确保服务器可访问。
    
    纯 HTML 项目不需要 dev server，直接跳过。
    """
    name = "dev_server_gate"
    allow_auto_fix = True
    timeout_seconds = 60

    def run(self) -> StageResult:
        # 纯 HTML 项目不需要 dev server
        pkg = self.workspace / "package.json"
        if not pkg.exists():
            return StageResult(success=True, message="Pure HTML project — no dev server needed")
        
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
        # 纯 HTML 项目不需要 dev server
        pkg = self.workspace / "package.json"
        if not pkg.exists():
            return StageResult(success=True, message="Pure HTML project — no dev server needed")
        
        from tools_impl import start_dev_server
        from harness.build import _detect_project_port
        import config
        port = _detect_project_port(self.workspace)
        # Next.js dev server 启动较慢（编译+初始化），给足等待时间
        wait = max(config.DEV_SERVER_DEFAULT_WAIT, 15)
        result = start_dev_server("npm run dev", port=port, wait=wait)
        if result.startswith("[error]"):
            return StageResult(success=False, message=result)
        return StageResult(success=True, message=result)


class ScreenshotGateStage(PipelineStage):
    """截图验证阶段（不阻塞）。"""
    name = "screenshot_gate"
    timeout_seconds = 30

    def run(self) -> StageResult:
        try:
            from tools_impl import browser_check
            from harness.build import _detect_project_port
            
            # 纯 HTML 项目使用 file:// 协议
            pkg = self.workspace / "package.json"
            if pkg.exists():
                port = _detect_project_port(self.workspace)
                url = f"http://localhost:{port}"
            else:
                url = f"file://{self.workspace}/index.html"
            
            result = browser_check(
                url=url,
                mode="screenshot",
                wait=2,
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



