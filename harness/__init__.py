"""
Harness package — 多 Agent 长时间自主开发架构

向后兼容：from harness import Harness 仍然工作
"""
from harness.core import Harness
from harness.cli import main

__all__ = ["Harness", "main"]
