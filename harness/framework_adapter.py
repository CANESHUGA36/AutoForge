"""
框架适配层
支持 Vite、Next.js、Vue 等不同框架的统一适配
"""
from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

logger = logging.getLogger("harness")


class FrameworkAdapter(ABC):
    """框架适配器基类"""

    name: str = "unknown"

    @abstractmethod
    def get_dev_server_command(self) -> str:
        """获取开发服务器启动命令"""
        pass

    @abstractmethod
    def get_build_command(self) -> str:
        """获取构建命令"""
        pass

    @abstractmethod
    def get_dev_server_port(self) -> int:
        """获取开发服务器默认端口"""
        pass

    @abstractmethod
    def get_evaluation_weights(self) -> dict[str, float]:
        """获取评估权重配置"""
        pass

    @abstractmethod
    async def wait_for_ready(self, page, url: str, timeout: int = 30000):
        """等待应用就绪（处理 hydration 等）"""
        pass

    def get_path_aliases(self, project_path: Path) -> dict[str, str]:
        """获取路径别名映射（可选）"""
        return {}

    def detect_ssr(self, project_path: Path) -> bool:
        """检测是否使用 SSR"""
        return False


class ViteAdapter(FrameworkAdapter):
    """Vite + React 框架适配器"""

    name = "vite"

    def get_dev_server_command(self) -> str:
        return "npm run dev"

    def get_build_command(self) -> str:
        return "npm run build"

    def get_dev_server_port(self) -> int:
        return 5173

    def get_evaluation_weights(self) -> dict[str, float]:
        return {
            "code_review": 0.40,
            "contract_tests": 0.35,
            "react_devtools": 0.15,
            "browser_tests": 0.10,
        }

    async def wait_for_ready(self, page, url: str, timeout: int = 30000):
        """Vite 应用直接等待 networkidle"""
        await page.goto(url, wait_until="networkidle", timeout=timeout)

    def get_path_aliases(self, project_path: Path) -> dict[str, str]:
        """从 vite.config.ts/js 解析路径别名"""
        for filename in ["vite.config.ts", "vite.config.js", "vite.config.mjs"]:
            config_path = project_path / filename
            if config_path.exists():
                try:
                    content = config_path.read_text(encoding="utf-8")
                    # 简单正则提取 alias
                    aliases = {}
                    # 匹配 alias: { '@': path.resolve(...), ... }
                    import re
                    alias_matches = re.findall(
                        r'["\'](@[^"\']+)["\']\s*:\s*path\.resolve\(["\']([^"\']+)["\']\)',
                        content,
                    )
                    for alias, target in alias_matches:
                        aliases[alias] = target
                    return aliases
                except Exception as e:
                    logger.debug(f"[framework] Failed to parse vite config: {e}")
        return {}


class NextJsAdapter(FrameworkAdapter):
    """Next.js App Router 框架适配器"""

    name = "nextjs"

    def get_dev_server_command(self) -> str:
        return "npm run dev"

    def get_build_command(self) -> str:
        return "npm run build"

    def get_dev_server_port(self) -> int:
        return 3000

    def get_evaluation_weights(self) -> dict[str, float]:
        """Next.js 降低浏览器测试权重，增加 SSR 检查"""
        return {
            "code_review": 0.35,
            "contract_tests": 0.35,
            "react_devtools": 0.15,
            "ssr_check": 0.10,
            "browser_tests": 0.05,
        }

    async def wait_for_ready(self, page, url: str, timeout: int = 30000):
        """Next.js 需要等待 hydration 完成"""
        await page.goto(url, wait_until="networkidle", timeout=timeout)

        # 等待 hydration 完成
        try:
            await page.wait_for_function(
                """
                () => {
                    // 检查 __NEXT_DATA__
                    const nextData = document.getElementById('__NEXT_DATA__');
                    if (!nextData) return false;
                    
                    // 检查 React 是否已挂载（window.__NEXT_DATA__ 存在且页面已加载）
                    try {
                        const data = JSON.parse(nextData.textContent);
                        return data.page != null;
                    } catch (e) {
                        return false;
                    }
                }
                """,
                timeout=min(timeout, 10000),
            )
        except Exception:
            # hydration 检查失败但不阻塞，可能 CSR 模式
            pass

    def detect_ssr(self, project_path: Path) -> bool:
        """检测 Next.js 是否使用 SSR"""
        # 检查是否有 getServerSideProps / generateStaticParams 等
        for pattern in ["**/*.tsx", "**/*.ts"]:
            for f in project_path.rglob(pattern):
                if "node_modules" in str(f):
                    continue
                try:
                    content = f.read_text(encoding="utf-8", errors="replace")
                    if any(kw in content for kw in [
                        "getServerSideProps", "getStaticProps",
                        "generateStaticParams", "revalidate",
                    ]):
                        return True
                except Exception:
                    pass
        return False

    def get_path_aliases(self, project_path: Path) -> dict[str, str]:
        """从 tsconfig.json / jsconfig.json 解析路径别名"""
        for filename in ["tsconfig.json", "jsconfig.json"]:
            config_path = project_path / filename
            if config_path.exists():
                try:
                    config = json.loads(config_path.read_text(encoding="utf-8"))
                    paths = config.get("compilerOptions", {}).get("paths", {})
                    aliases = {}
                    for key, values in paths.items():
                        clean_key = key.replace("/*", "")
                        clean_value = values[0].replace("/*", "") if values else ""
                        aliases[clean_key] = clean_value
                    return aliases
                except Exception as e:
                    logger.debug(f"[framework] Failed to parse tsconfig: {e}")
        return {}


class PureHtmlAdapter(FrameworkAdapter):
    """纯 HTML 项目适配器（无构建工具）"""

    name = "pure-html"

    def get_dev_server_command(self) -> str:
        return "npx serve -s . -l 3000"

    def get_build_command(self) -> str:
        return ""  # 无构建步骤

    def get_dev_server_port(self) -> int:
        return 3000

    def get_evaluation_weights(self) -> dict[str, float]:
        """纯 HTML 项目：浏览器测试权重更高（无 React 时序问题）"""
        return {
            "code_review": 0.30,
            "contract_tests": 0.30,
            "browser_tests": 0.40,
        }

    async def wait_for_ready(self, page, url: str, timeout: int = 30000):
        await page.goto(url, wait_until="load", timeout=timeout)


class FrameworkDetector:
    """框架检测器"""

    _ADAPTERS: dict[str, type[FrameworkAdapter]] = {
        "nextjs": NextJsAdapter,
        "vite": ViteAdapter,
        "pure-html": PureHtmlAdapter,
    }

    @classmethod
    def detect(cls, project_path: Path | str) -> FrameworkAdapter:
        """检测项目使用的框架并返回适配器实例"""
        path = Path(project_path)

        # 检查 Next.js
        if any((path / f).exists() for f in [
            "next.config.js", "next.config.ts", "next.config.mjs",
            "next.config.cjs", "next.config.mts",
        ]):
            logger.info("[framework] Detected: Next.js")
            return NextJsAdapter()

        # 检查 Vite
        if any((path / f).exists() for f in [
            "vite.config.ts", "vite.config.js", "vite.config.mjs",
        ]):
            logger.info("[framework] Detected: Vite")
            return ViteAdapter()

        # 检查 package.json 中的依赖
        pkg_path = path / "package.json"
        if pkg_path.exists():
            try:
                pkg = json.loads(pkg_path.read_text(encoding="utf-8"))
                deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

                if "next" in deps:
                    logger.info("[framework] Detected: Next.js (from package.json)")
                    return NextJsAdapter()
                if "vite" in deps:
                    logger.info("[framework] Detected: Vite (from package.json)")
                    return ViteAdapter()
            except Exception:
                pass

        # 纯 HTML（有 index.html 但没有 package.json）
        if (path / "index.html").exists() and not pkg_path.exists():
            logger.info("[framework] Detected: Pure HTML")
            return PureHtmlAdapter()

        # 默认 Vite（有 package.json 但无法确定框架）
        if pkg_path.exists():
            logger.info("[framework] Detected: Unknown (defaulting to Vite)")
            return ViteAdapter()

        # 完全未知，默认纯 HTML
        logger.info("[framework] Detected: Unknown (defaulting to Pure HTML)")
        return PureHtmlAdapter()

    @classmethod
    def get_adapter(cls, name: str) -> FrameworkAdapter:
        """按名称获取适配器"""
        adapter_class = cls._ADAPTERS.get(name.lower())
        if adapter_class:
            return adapter_class()
        return ViteAdapter()  # 默认


def get_framework_adapter(project_path: Path | str) -> FrameworkAdapter:
    """获取项目框架适配器（便捷函数）"""
    return FrameworkDetector.detect(project_path)
