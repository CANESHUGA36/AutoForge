#!/usr/bin/env python3
"""
Harness — 向后兼容入口

旧代码: from harness import Harness
新代码: from harness import Harness (相同)

实际实现已迁移到 harness/ 包中。
"""
from harness import Harness, main

__all__ = ["Harness", "main"]
