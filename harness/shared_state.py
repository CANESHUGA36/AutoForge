"""
Shared State — 跨 Agent 全局状态共享

核心思想：让不同 Agent 的关键发现自动共享，避免重复推理和信息丢失。

共享内容：
- Architect: 技术选型、架构决策、关键设计约束
- SprintMaster: Sprint 计划、依赖关系、风险评估  
- Builder: 代码结构发现、构建问题、解决方案
- Reviewer: 测试发现、常见失败模式、验证捷径

存储：workspace/.shared_state.json（JSON，所有 Agent 可读）
更新：Agent 运行结束后自动写入
读取：Agent 启动时自动注入到 system prompt
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import config

log = logging.getLogger("harness")


@dataclass
class AgentDiscovery:
    """单个 Agent 的发现条目"""
    agent: str           # "Architect", "Builder", etc.
    round: int           # 第几轮
    category: str        # "tech_choice", "pattern", "pitfall", "shortcut"
    content: str         # 发现内容
    confidence: str      # "high", "medium", "low"
    
    def to_dict(self) -> dict:
        return {
            "agent": self.agent,
            "round": self.round,
            "category": self.category,
            "content": self.content,
            "confidence": self.confidence,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "AgentDiscovery":
        return cls(
            agent=data.get("agent", ""),
            round=data.get("round", 0),
            category=data.get("category", ""),
            content=data.get("content", ""),
            confidence=data.get("confidence", "medium"),
        )


@dataclass
class SharedState:
    """跨 Agent 共享的全局状态"""
    
    # 技术选型决策（Architect → 所有 Agent）
    tech_stack: dict[str, str] = field(default_factory=dict)
    # 例如: {"framework": "React", "state_management": "Zustand", "styling": "Tailwind"}
    
    # 架构决策（Architect → Builder/SprintMaster）
    architecture_decisions: list[str] = field(default_factory=list)
    # 例如: ["使用 Canvas API 而非 SVG 绘制", "坐标系采用世界坐标+视口变换"]
    
    # 关键约束（Architect → 所有 Agent）
    constraints: list[str] = field(default_factory=list)
    # 例如: ["必须支持负坐标", "CSS 显隐而非条件渲染"]
    
    # 已验证的模式（Builder → Builder/Reviewer）
    verified_patterns: list[dict] = field(default_factory=list)
    # 例如: [{"pattern": "CSS visibility", "context": "conditional rendering", "result": "PASS"}]
    
    # 已知陷阱（Reviewer → Builder）
    known_pitfalls: list[dict] = field(default_factory=list)
    # 例如: [{"pitfall": "Vite path alias 未配置", "solution": "vite.config.ts 添加 resolve.alias", "round": 1}]
    
    # 验证捷径（Reviewer → Reviewer）
    verification_shortcuts: list[dict] = field(default_factory=list)
    # 例如: [{"criterion": "G2.A.1", "shortcut": "检查 JSX 存在即可，无需浏览器测试"}]
    
    # 跨轮次发现（所有 Agent）
    discoveries: list[AgentDiscovery] = field(default_factory=list)
    
    # 项目元数据
    project_type: str = ""      # "vite-react-ts", "nextjs-app", "pure-html"
    total_rounds: int = 0
    current_group: str = ""
    
    def add_discovery(
        self,
        agent: str,
        round: int,
        category: str,
        content: str,
        confidence: str = "medium",
    ) -> None:
        """添加新的发现"""
        discovery = AgentDiscovery(
            agent=agent,
            round=round,
            category=category,
            content=content,
            confidence=confidence,
        )
        self.discoveries.append(discovery)
        # 限制总数
        if len(self.discoveries) > 100:
            self.discoveries = self.discoveries[-80:]
        log.info(f"[shared_state] Discovery added by {agent}: {content[:80]}...")
    
    def add_pitfall(self, pitfall: str, solution: str, round: int, agent: str = "") -> None:
        """添加已知陷阱（Reviewer 发现 → Builder 避免）"""
        self.known_pitfalls.append({
            "pitfall": pitfall,
            "solution": solution,
            "round": round,
            "agent": agent,
        })
        # 去重：相同 pitfall 只保留最新的
        seen = set()
        unique = []
        for p in reversed(self.known_pitfalls):
            key = p["pitfall"][:50]
            if key not in seen:
                seen.add(key)
                unique.append(p)
        self.known_pitfalls = list(reversed(unique))[-20:]
    
    def add_verified_pattern(self, pattern: str, context: str, result: str) -> None:
        """添加已验证的模式"""
        self.verified_patterns.append({
            "pattern": pattern,
            "context": context,
            "result": result,
        })
    
    def get_relevant_for_agent(self, agent_name: str, current_round: int) -> dict:
        """获取对指定 Agent 相关的共享状态"""
        result = {
            "tech_stack": self.tech_stack,
            "constraints": self.constraints,
        }
        
        if agent_name == "Builder":
            # Builder 需要知道：架构决策、已知陷阱、已验证模式
            result["architecture_decisions"] = self.architecture_decisions[-10:]
            result["known_pitfalls"] = self.known_pitfalls[-10:]
            result["verified_patterns"] = self.verified_patterns[-10:]
            
        elif agent_name == "Reviewer":
            # Reviewer 需要知道：验证捷径、项目类型
            result["verification_shortcuts"] = self.verification_shortcuts[-10:]
            result["project_type"] = self.project_type
            
        elif agent_name == "SprintMaster":
            # SprintMaster 需要知道：架构决策、当前组
            result["architecture_decisions"] = self.architecture_decisions[-5:]
            result["current_group"] = self.current_group
            
        # 所有 Agent 都能看到最近的高置信度发现
        recent_discoveries = [
            d for d in self.discoveries[-20:]
            if d.confidence == "high" or d.round >= current_round - 2
        ]
        result["recent_discoveries"] = [d.to_dict() for d in recent_discoveries]
        
        return result
    
    def to_prompt_section(self, agent_name: str, current_round: int) -> str:
        """生成给 Agent prompt 的共享状态段落 — 带显式引导，确保 Agent 注意到并引用"""
        relevant = self.get_relevant_for_agent(agent_name, current_round)
        
        # 如果没有内容，返回空
        has_content = any([
            relevant.get("tech_stack"),
            relevant.get("constraints"),
            relevant.get("known_pitfalls"),
            relevant.get("architecture_decisions"),
            relevant.get("verification_shortcuts"),
            relevant.get("verified_patterns"),
            relevant.get("recent_discoveries"),
        ])
        if not has_content:
            return ""
        
        parts = [
            "═══════════════════════════════════════════════════════════════",
            "  📋 项目积累的知识库（来自前 " + str(current_round) + " 轮的经验）",
            "═══════════════════════════════════════════════════════════════",
            "",
            "⚠️ 重要：以下信息是本项目已验证的经验和约束，",
            "         请在决策时主动参考，避免重复踩坑或偏离既定方向。",
            "",
        ]
        
        if relevant.get("tech_stack"):
            parts.append("【技术选型】（不可更改）")
            for k, v in relevant["tech_stack"].items():
                parts.append(f"  • {k}: {v}")
            parts.append("")
        
        if relevant.get("constraints"):
            parts.append("【关键约束】（必须遵守）")
            for c in relevant["constraints"][-5:]:
                parts.append(f"  • {c}")
            parts.append("")
        
        if relevant.get("architecture_decisions"):
            parts.append("【架构决策】（已确定的方向）")
            for d in relevant["architecture_decisions"][-5:]:
                parts.append(f"  • {d}")
            parts.append("")
        
        if relevant.get("known_pitfalls"):
            parts.append("【已知陷阱】（务必避免）")
            for p in relevant["known_pitfalls"][-5:]:
                parts.append(f"  • {p['pitfall']}")
                parts.append(f"    → 解决方案: {p['solution']}")
            parts.append("")
        
        if relevant.get("verified_patterns"):
            parts.append("【已验证模式】（可复用）")
            for vp in relevant["verified_patterns"][-5:]:
                status = vp.get('result', 'PASS')
                parts.append(f"  • [{status}] {vp['pattern']} ({vp['context']})")
            parts.append("")
        
        if relevant.get("verification_shortcuts"):
            parts.append("【验证捷径】（Reviewer 专用）")
            for s in relevant["verification_shortcuts"][-5:]:
                parts.append(f"  • {s.get('criterion', '')}: {s.get('shortcut', '')}")
            parts.append("")
        
        if relevant.get("recent_discoveries"):
            parts.append("【最新发现】")
            for d in relevant["recent_discoveries"][-3:]:
                parts.append(f"  • [{d['agent']} 第{d['round']}轮] {d['content'][:80]}")
            parts.append("")
        
        parts.append("═══════════════════════════════════════════════════════════════")
        parts.append("")
        
        return "\n".join(parts)
    
    def to_dict(self) -> dict:
        """序列化为字典"""
        return {
            "tech_stack": self.tech_stack,
            "architecture_decisions": self.architecture_decisions,
            "constraints": self.constraints,
            "verified_patterns": self.verified_patterns,
            "known_pitfalls": self.known_pitfalls,
            "verification_shortcuts": self.verification_shortcuts,
            "discoveries": [d.to_dict() for d in self.discoveries],
            "project_type": self.project_type,
            "total_rounds": self.total_rounds,
            "current_group": self.current_group,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "SharedState":
        """从字典反序列化"""
        state = cls()
        state.tech_stack = data.get("tech_stack", {})
        state.architecture_decisions = data.get("architecture_decisions", [])
        state.constraints = data.get("constraints", [])
        state.verified_patterns = data.get("verified_patterns", [])
        state.known_pitfalls = data.get("known_pitfalls", [])
        state.verification_shortcuts = data.get("verification_shortcuts", [])
        state.discoveries = [
            AgentDiscovery.from_dict(d) for d in data.get("discoveries", [])
        ]
        state.project_type = data.get("project_type", "")
        state.total_rounds = data.get("total_rounds", 0)
        state.current_group = data.get("current_group", "")
        return state
    
    def save(self, workspace: str) -> None:
        """保存到 workspace"""
        path = Path(workspace) / ".shared_state.json"
        try:
            path.write_text(
                json.dumps(self.to_dict(), indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            log.debug(f"[shared_state] Saved to {path}")
        except Exception as e:
            log.warning(f"[shared_state] Failed to save: {e}")
    
    @classmethod
    def load(cls, workspace: str) -> "SharedState":
        """从 workspace 加载"""
        path = Path(workspace) / ".shared_state.json"
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls.from_dict(data)
        except Exception as e:
            log.warning(f"[shared_state] Failed to load: {e}")
            return cls()


# ── 便捷函数 ──

def load_shared_state(workspace: str | None = None) -> SharedState:
    """加载共享状态"""
    ws = workspace or config.WORKSPACE
    return SharedState.load(ws)


def save_shared_state(state: SharedState, workspace: str | None = None) -> None:
    """保存共享状态"""
    ws = workspace or config.WORKSPACE
    state.save(ws)
