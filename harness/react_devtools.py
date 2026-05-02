"""
React DevTools 协议检查
通过 Chrome DevTools Protocol (CDP) 检查 React Fiber 树
绕过 DOM 时序问题，直接验证组件存在性和 props
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("harness")


@dataclass
class FiberNode:
    """React Fiber 节点（简化表示）"""
    name: str
    props: dict[str, Any] = field(default_factory=dict)
    state: dict[str, Any] = field(default_factory=dict)
    children: list[FiberNode] = field(default_factory=list)


class ReactDevToolsInspector:
    """React DevTools 检查器 — 通过 CDP 访问 Fiber 树"""

    # React 18+ 兼容的 Fiber 树提取脚本
    # 直接从 DOM 元素的 __reactFiber$ 属性读取，不依赖 DevTools hook
    _EXTRACT_FIBER_SCRIPT = """
    () => {
        // 1. 找到任意一个带有 __reactFiber$ 的 DOM 元素
        const allElements = document.querySelectorAll('*');
        let fiberKey = null;
        let fiberEl = null;
        
        for (let i = 0; i < Math.min(allElements.length, 500); i++) {
            const el = allElements[i];
            const keys = Object.keys(el);
            const fk = keys.find(k => k.startsWith('__reactFiber$'));
            if (fk) {
                fiberKey = fk;
                fiberEl = el;
                break;
            }
        }
        
        if (!fiberKey) {
            // Fallback: 检查是否有 React root 的内部属性
            const appDiv = document.getElementById('root') || document.getElementById('app');
            if (appDiv && appDiv.children.length > 0) {
                const rootKeys = Object.keys(appDiv);
                const hasReactContainer = rootKeys.some(k => k.startsWith('__reactContainer'));
                if (hasReactContainer) {
                    return { roots: [{ name: "App", props: {}, state: {}, children: [] }] };
                }
            }
            return { error: "React not detected (no reactFiber keys on DOM elements)" };
        }
        
        // 2. 向上遍历到根 fiber
        let rootFiber = fiberEl[fiberKey];
        let depth = 0;
        while (rootFiber && rootFiber.return && depth < 200) {
            rootFiber = rootFiber.return;
            depth++;
        }
        
        if (!rootFiber) {
            return { error: "Could not find root fiber" };
        }
        
        // 3. 序列化 Fiber 树（BFS 限制节点数，避免递归爆栈）
        const tree = serializeFiberBFS(rootFiber, 80);
        return { roots: tree ? [tree] : [] };
        
        function serializeFiberBFS(rootFiber, maxNodes) {
            if (!rootFiber) return null;
            
            const rootNode = makeNode(rootFiber);
            if (!rootNode) return null;
            
            const queue = [{ fiber: rootFiber, node: rootNode, depth: 0 }];
            let visited = 1;
            
            while (queue.length > 0 && visited < maxNodes) {
                const { fiber, node, depth } = queue.shift();
                if (depth >= 12) continue;
                
                let child = fiber.child;
                while (child && visited < maxNodes) {
                    const childNode = makeNode(child);
                    if (childNode) {
                        node.children.push(childNode);
                        queue.push({ fiber: child, node: childNode, depth: depth + 1 });
                        visited++;
                    }
                    child = child.sibling;
                }
            }
            
            return rootNode;
        }
        
        function makeNode(fiber) {
            try {
                return {
                    name: getDisplayName(fiber),
                    props: extractProps(fiber.memoizedProps),
                    state: extractState(fiber.memoizedState),
                    children: [],
                };
            } catch (e) {
                return { name: "error", props: {}, state: {}, children: [] };
            }
        }
        
        function getDisplayName(fiber) {
            if (!fiber) return "unknown";
            if (fiber.type === null) return "HostRoot";
            if (typeof fiber.type === 'string') return fiber.type;
            if (typeof fiber.type === 'function') {
                return fiber.type.name || fiber.type.displayName || "Anonymous";
            }
            if (typeof fiber.type === 'object' && fiber.type !== null) {
                // React elements (forwardRef, memo, etc.)
                return fiber.type.displayName || fiber.type.name || "Component";
            }
            if (typeof fiber.type === 'symbol') return "Symbol";
            return "unknown";
        }
        
        function extractProps(props) {
            if (!props) return {};
            const result = {};
            try {
                const keys = Object.keys(props);
                for (let i = 0; i < Math.min(keys.length, 20); i++) {
                    const key = keys[i];
                    if (key === 'children') continue;
                    try {
                        const val = props[key];
                        if (typeof val === 'function') {
                            result[key] = '[function]';
                        } else if (typeof val === 'object' && val !== null) {
                            result[key] = '[object]';
                        } else if (typeof val === 'symbol') {
                            result[key] = '[symbol]';
                        } else {
                            result[key] = val;
                        }
                    } catch (e) {
                        result[key] = '[unreadable]';
                    }
                }
            } catch (e) {
                // props 可能不可遍历
            }
            return result;
        }
        
        function extractState(state) {
            if (!state) return {};
            return { hasState: true };
        }
    }
    """

    def __init__(self, page=None):
        """
        Args:
            page: Playwright Page 对象或 BrowserSession 对象（可选）
        """
        self.page = page
        self._session = None  # BrowserSession 对象（如果有）
        self._hook_installed = False
        self._last_tree: list[FiberNode] = []

    async def _ensure_page(self):
        """确保有 page/session 对象可用"""
        if self.page is None:
            # 尝试从 playwright_mcp 获取当前 session
            try:
                from tools.playwright_mcp import _get_page
                self._session = await _get_page()
                self.page = self._session
            except Exception as e:
                logger.debug(f"[react_devtools] Could not get page: {e}")
                raise RuntimeError("No Playwright page available. Run browser_check first.")

    def _get_eval_method(self):
        """获取执行 JS 的方法（兼容 BrowserSession 和原生 Page）"""
        target = self.page or self._session
        if target is None:
            return None
        # BrowserSession 有 execute_script 方法
        if hasattr(target, 'execute_script'):
            return target.execute_script
        # 原生 Playwright Page 有 evaluate 方法
        if hasattr(target, 'evaluate'):
            return target.evaluate
        return None

    async def get_fiber_tree(self) -> list[FiberNode]:
        """获取 React Fiber 树（React 18+ 兼容）"""
        await self._ensure_page()

        try:
            eval_fn = self._get_eval_method()
            if eval_fn is None:
                raise RuntimeError("No evaluate method available on page/session")
            result = await eval_fn(self._EXTRACT_FIBER_SCRIPT)

            if isinstance(result, dict) and "error" in result:
                logger.debug(f"[react_devtools] Fiber extraction: {result['error']}")
                return []

            roots = result.get("roots", []) if isinstance(result, dict) else []
            tree = [self._dict_to_node(r) for r in roots if r]
            self._last_tree = tree
            logger.debug(f"[react_devtools] Extracted {len(tree)} root(s), {sum(len(r.children) for r in tree)} children")
            return tree

        except Exception as e:
            logger.debug(f"[react_devtools] Failed to extract fiber tree: {e}")
            return []

    def _dict_to_node(self, data: dict) -> FiberNode:
        """将字典转换为 FiberNode"""
        return FiberNode(
            name=data.get("name", "unknown"),
            props=data.get("props", {}),
            state=data.get("state", {}),
            children=[self._dict_to_node(c) for c in data.get("children", []) if c],
        )

    def _find_in_tree(
        self,
        tree: list[FiberNode],
        predicate: Callable[[FiberNode], bool],
    ) -> FiberNode | None:
        """在树中查找满足条件的节点"""
        def search(node: FiberNode) -> FiberNode | None:
            if predicate(node):
                return node
            for child in node.children:
                found = search(child)
                if found:
                    return found
            return None

        for root in tree:
            found = search(root)
            if found:
                return found
        return None

    async def find_component(
        self,
        name: str,
        props_filter: dict[str, Any] | None = None,
    ) -> FiberNode | None:
        """查找特定组件"""
        tree = await self.get_fiber_tree()

        def match(node: FiberNode) -> bool:
            if node.name != name:
                return False
            if props_filter:
                return all(
                    node.props.get(k) == v or str(node.props.get(k)) == str(v)
                    for k, v in props_filter.items()
                )
            return True

        return self._find_in_tree(tree, match)

    async def check_component_exists(
        self,
        name: str,
        min_count: int = 1,
    ) -> tuple[bool, int]:
        """检查组件是否存在"""
        tree = await self.get_fiber_tree()

        count = 0
        def count_nodes(nodes: list[FiberNode]):
            nonlocal count
            for node in nodes:
                if node.name == name:
                    count += 1
                count_nodes(node.children)

        count_nodes(tree)
        return count >= min_count, count

    async def get_component_props(self, name: str) -> dict[str, Any] | None:
        """获取组件 props"""
        component = await self.find_component(name)
        return component.props if component else None

    async def list_all_components(self) -> list[dict[str, Any]]:
        """列出所有组件（扁平化）"""
        tree = await self.get_fiber_tree()
        result = []

        def flatten(nodes: list[FiberNode], depth: int = 0):
            for node in nodes:
                result.append({
                    "name": node.name,
                    "depth": depth,
                    "props": node.props,
                })
                flatten(node.children, depth + 1)

        flatten(tree)
        return result


class ReactDevToolsChecker:
    """React DevTools 检查 — 针对功能组的快速验证"""

    def __init__(self, page=None):
        self.inspector = ReactDevToolsInspector(page)

    async def check_feature_group(
        self,
        feature_group: str,
        contract_criteria: list[ContractCriterion] | None = None,
    ) -> dict[str, Any]:
        """检查功能组"""
        results = {}

        # 基于功能组 ID 推断检查策略
        checks = self._get_checks_for_group(feature_group)

        for check_name, check_fn in checks:
            try:
                result = await check_fn()
                results[check_name] = result
            except Exception as e:
                results[check_name] = {
                    "passed": False,
                    "score": 0,
                    "details": f"Check error: {e}",
                }

        # 计算分数
        scores = [r.get("score", 0) for r in results.values()]
        avg_score = sum(scores) / len(scores) if scores else 0

        return {
            "feature_group": feature_group,
            "checks": results,
            "score": avg_score,
            "passed": avg_score >= 50,  # DevTools 检查相对宽松
        }

    def _get_checks_for_group(self, group_id: str) -> list[tuple[str, Callable]]:
        """获取功能组对应的检查列表
        
        支持旧格式 F1-F7 和新格式 G1-G4。
        新格式映射：
        - G1 (Core) → base + shape + cursor
        - G2 (Content) → text + shape
        - G3 (Editing) → cursor + shape
        - G4 (Output) → base
        """
        checks = []
        
        # 统一处理新旧格式
        is_core = group_id in ("F1", "F2", "F3", "F4", "G1")
        is_content = group_id in ("F4", "F5", "G2")
        is_editing = group_id in ("F5", "F6", "F7", "G3")
        is_output = group_id in ("F8", "F9", "F10", "F11", "G4")
        
        if is_core:
            checks.append(("base_components", self._check_base_components))
        
        if is_content or is_editing:
            checks.append(("cursor_components", self._check_cursor_components))
        
        if is_core or is_editing:
            checks.append(("shape_components", self._check_shape_components))
        
        if is_content:
            checks.append(("text_components", self._check_text_components))

        # 通用检查（所有组都做）
        checks.append(("react_mount", self._check_react_mount))

        return checks

    async def _check_react_mount(self) -> dict[str, Any]:
        """检查 React 是否正确挂载"""
        tree = await self.inspector.get_fiber_tree()
        has_root = len(tree) > 0

        return {
            "passed": has_root,
            "score": 100 if has_root else 0,
            "details": f"React root components: {len(tree)}",
            "evidence": {"root_count": len(tree)},
        }

    async def _check_base_components(self) -> dict[str, Any]:
        """检查基础 UI 组件"""
        components = await self.inspector.list_all_components()
        names = [c["name"] for c in components]

        common_components = ["App", "div", "span", "button", "input"]
        found = [n for n in common_components if n in names]

        score = min(len(found) * 20, 100)
        return {
            "passed": score >= 40,
            "score": score,
            "details": f"Found components: {found}",
            "evidence": {"components": names[:20]},
        }

    async def _check_cursor_components(self) -> dict[str, Any]:
        """检查光标组件"""
        exists, count = await self.inspector.check_component_exists("CursorElement", min_count=1)

        if exists:
            return {
                "passed": True,
                "score": 100,
                "details": f"Found {count} CursorElement component(s)",
                "evidence": {"count": count},
            }

        # 检查 Cursors 容器
        container_exists, _ = await self.inspector.check_component_exists("Cursors")
        if container_exists:
            return {
                "passed": True,
                "score": 70,
                "details": "Cursors container exists but no CursorElement instances",
                "evidence": {},
            }

        # 检查是否有任何 cursor 相关组件
        components = await self.inspector.list_all_components()
        cursor_related = [c for c in components if "cursor" in c["name"].lower()]

        if cursor_related:
            return {
                "passed": True,
                "score": 60,
                "details": f"Found cursor-related: {[c['name'] for c in cursor_related]}",
                "evidence": {"found": [c["name"] for c in cursor_related]},
            }

        return {
            "passed": False,
            "score": 0,
            "details": "No cursor components found in React tree",
            "evidence": {},
        }

    async def _check_shape_components(self) -> dict[str, Any]:
        """检查形状/绘图组件"""
        shape_types = ["Rect", "Circle", "Line", "Arrow", "Shape", "Path"]
        found_shapes = []

        for shape_type in shape_types:
            exists, count = await self.inspector.check_component_exists(shape_type)
            if exists:
                found_shapes.append({"type": shape_type, "count": count})

        if found_shapes:
            score = min(len(found_shapes) * 25 + 25, 100)
            return {
                "passed": True,
                "score": score,
                "details": f"Found shapes: {found_shapes}",
                "evidence": {"shapes": found_shapes},
            }

        # 检查 Konva Stage/Layer
        konva_exists, _ = await self.inspector.check_component_exists("Stage")
        if konva_exists:
            return {
                "passed": True,
                "score": 50,
                "details": "Konva Stage found but no shape components",
                "evidence": {},
            }

        return {
            "passed": False,
            "score": 0,
            "details": "No shape or drawing components found",
            "evidence": {},
        }

    async def _check_text_components(self) -> dict[str, Any]:
        """检查文本组件"""
        exists, count = await self.inspector.check_component_exists("Text")

        return {
            "passed": exists,
            "score": 100 if exists else 0,
            "details": f"Text components: {count}",
            "evidence": {"count": count},
        }
