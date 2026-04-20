"""
Evaluator Result Cache — 评估结果缓存与摘要

核心思想：将每轮的 CodeReview + BrowserTest 完整报告保存到磁盘，
Evaluator 只接收结构化摘要而非完整报告，大幅降低上下文长度。

缓存文件：
- .eval_cache/round_{N}_code_review.md — 完整代码审查报告
- .eval_cache/round_{N}_browser.md — 完整浏览器测试报告
- .eval_cache/round_{N}_summary.json — 结构化摘要（给 Evaluator）
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class EvalSummary:
    """Evaluator 所需的结构化摘要"""
    round_num: int
    code_review: dict = field(default_factory=dict)
    browser_test: dict = field(default_factory=dict)
    
    def to_markdown(self) -> str:
        """生成给 Evaluator 的精简报告（目标 < 1500 字符）"""
        lines = [f"## Round {self.round_num} Evaluation Summary"]
        
        # Code Review 摘要
        cr = self.code_review
        lines.append(f"\n### Code Review")
        lines.append(f"- Files examined: {cr.get('files_examined', 'N/A')}")
        lines.append(f"- Critical issues: {cr.get('critical_count', 0)}")
        lines.append(f"- Warnings: {cr.get('warning_count', 0)}")
        lines.append(f"- Feature coverage: {cr.get('coverage', 'N/A')}")
        if cr.get('top_issues'):
            lines.append("- Top issues:")
            for issue in cr['top_issues'][:3]:
                lines.append(f"  • {issue[:120]}")
        
        # Browser Test 摘要
        bt = self.browser_test
        lines.append(f"\n### Browser Test")
        lines.append(f"- Desktop: {bt.get('desktop_status', 'N/A')}")
        lines.append(f"- Mobile: {bt.get('mobile_status', 'N/A')}")
        lines.append(f"- Console errors: {bt.get('console_errors', 0)}")
        lines.append(f"- Navigation errors: {bt.get('nav_errors', 0)}")
        if bt.get('failures'):
            lines.append("- Failures:")
            for f in bt['failures'][:3]:
                lines.append(f"  • {f[:120]}")
        
        if bt.get('screenshots'):
            lines.append(f"- Screenshots: {', '.join(bt['screenshots'])}")
        
        return "\n".join(lines)


def _extract_code_review_stats(text: str) -> dict:
    """从 CodeReviewer 报告中提取结构化数据"""
    result = {
        "files_examined": "N/A",
        "critical_count": 0,
        "warning_count": 0,
        "coverage": "N/A",
        "top_issues": [],
    }
    
    # 文件数
    files_match = re.search(r'(?:examined|reviewed|checked)\s+(\d+)\s+files?', text, re.I)
    if files_match:
        result["files_examined"] = files_match.group(1)
    
    # Critical issues 计数
    critical_matches = re.findall(r'(?:critical|severe|blocking|error)[\s\-]*(?:issue|problem|bug)', text, re.I)
    result["critical_count"] = len(critical_matches)
    
    # Warnings 计数
    warning_matches = re.findall(r'(?:warning|minor|suggestion|improvement)', text, re.I)
    result["warning_count"] = len(warning_matches)
    
    # Feature coverage
    coverage_match = re.search(r'(?:coverage|implemented|complete)[\s:]*(\d+%|~?\d+\s*%)', text, re.I)
    if coverage_match:
        result["coverage"] = coverage_match.group(1)
    
    # 提取问题列表（以 - 或 * 开头的行）
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("- ", "* ", "• ")) and len(stripped) > 5:
            # 过滤掉文件列表行
            if not stripped.startswith(("- `", "* `")):
                result["top_issues"].append(stripped[2:])
    
    # 去重并限制
    seen = set()
    unique = []
    for issue in result["top_issues"]:
        key = issue[:40].lower()
        if key not in seen:
            seen.add(key)
            unique.append(issue)
    result["top_issues"] = unique[:10]
    
    return result


def _extract_browser_stats(text: str) -> dict:
    """从 BrowserTester 报告中提取结构化数据"""
    result = {
        "desktop_status": "unknown",
        "mobile_status": "unknown",
        "console_errors": 0,
        "nav_errors": 0,
        "failures": [],
        "screenshots": [],
    }
    
    # 检测 viewport 和状态
    has_desktop = "1280" in text or "720" in text
    has_mobile = "375" in text or "812" in text
    
    desktop_errors = text.count("[error]") if has_desktop else 0
    mobile_errors = text.count("[error]") if has_mobile else 0
    
    # Navigation 错误
    nav_error_matches = re.findall(r'navigation failed', text, re.I)
    result["nav_errors"] = len(nav_error_matches)
    
    # Console errors
    console_match = re.search(r'console errors?\s*\((\d+)\)', text, re.I)
    if console_match:
        result["console_errors"] = int(console_match.group(1))
    
    # 状态判断
    if has_desktop:
        result["desktop_status"] = "PASS" if desktop_errors == 0 else f"FAIL ({desktop_errors} errors)"
    if has_mobile:
        result["mobile_status"] = "PASS" if mobile_errors == 0 else f"FAIL ({mobile_errors} errors)"
    
    # 提取失败项（包含 FAIL 的行）
    for line in text.splitlines():
        stripped = line.strip()
        if "FAIL" in stripped and len(stripped) > 3:
            result["failures"].append(stripped)
        if "Screenshot saved" in stripped:
            fname = stripped.split("to ")[-1].strip()
            result["screenshots"].append(fname)
    
    result["failures"] = result["failures"][:10]
    result["screenshots"] = result["screenshots"][:5]
    
    return result


class EvalCache:
    """评估结果缓存管理器"""
    
    def __init__(self, workspace: str):
        self.workspace = Path(workspace)
        self.cache_dir = self.workspace / ".eval_cache"
        self.cache_dir.mkdir(exist_ok=True)
    
    def save_round(
        self,
        round_num: int,
        code_review_result: str,
        browser_result: str,
    ) -> EvalSummary:
        """保存完整报告并生成摘要"""
        # 保存完整报告
        cr_path = self.cache_dir / f"round_{round_num}_code_review.md"
        bt_path = self.cache_dir / f"round_{round_num}_browser.md"
        
        cr_path.write_text(code_review_result, encoding="utf-8")
        bt_path.write_text(browser_result, encoding="utf-8")
        
        # 生成结构化摘要
        summary = EvalSummary(
            round_num=round_num,
            code_review=_extract_code_review_stats(code_review_result),
            browser_test=_extract_browser_stats(browser_result),
        )
        
        # 保存摘要 JSON
        summary_path = self.cache_dir / f"round_{round_num}_summary.json"
        summary_path.write_text(
            json.dumps({
                "round": round_num,
                "code_review": summary.code_review,
                "browser_test": summary.browser_test,
            }, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        
        return summary
    
    def load_summary(self, round_num: int) -> EvalSummary | None:
        """加载指定轮次的摘要"""
        summary_path = self.cache_dir / f"round_{round_num}_summary.json"
        if not summary_path.exists():
            return None
        
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
            return EvalSummary(
                round_num=data["round"],
                code_review=data.get("code_review", {}),
                browser_test=data.get("browser_test", {}),
            )
        except Exception:
            return None
    
    def get_full_report(self, round_num: int, report_type: str = "code_review") -> str | None:
        """获取完整报告（用于调试或详细分析）"""
        if report_type == "code_review":
            path = self.cache_dir / f"round_{round_num}_code_review.md"
        else:
            path = self.cache_dir / f"round_{round_num}_browser.md"
        
        if path.exists():
            return path.read_text(encoding="utf-8")
        return None
    
    def get_all_summaries(self) -> list[EvalSummary]:
        """获取所有轮次的摘要"""
        summaries = []
        for path in sorted(self.cache_dir.glob("round_*_summary.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                summaries.append(EvalSummary(
                    round_num=data["round"],
                    code_review=data.get("code_review", {}),
                    browser_test=data.get("browser_test", {}),
                ))
            except Exception:
                pass
        return summaries
    
    def build_evaluator_prompt(
        self,
        round_num: int,
        code_review_result: str,
        browser_result: str,
        contract_ref: str,
        previous_rounds: int = 2,
    ) -> str:
        """构建 Evaluator 的任务提示（使用摘要替代完整报告）"""
        # 保存当前轮次
        current_summary = self.save_round(round_num, code_review_result, browser_result)
        
        parts = [
            "You are the lead QA engineer. Synthesize the following specialist reports into a final evaluation.",
            "",
            "You do NOT need to read source files or run browser tests — the specialists have already done that.",
            "Your job is to apply the scoring rubric and write the final feedback.",
        ]
        
        # 包含当前轮次摘要
        parts.append("\n## Current Round Reports")
        parts.append(current_summary.to_markdown())
        
        # 可选：包含最近几轮的历史摘要（用于趋势分析）
        if previous_rounds > 0:
            all_summaries = self.get_all_summaries()
            past = [s for s in all_summaries if s.round_num < round_num][-previous_rounds:]
            if past:
                parts.append("\n## Previous Rounds Trend")
                for s in past:
                    parts.append(f"\n### Round {s.round_num}")
                    cr = s.code_review
                    bt = s.browser_test
                    parts.append(f"- Code: {cr.get('critical_count', 0)} critical, {cr.get('warning_count', 0)} warnings")
                    parts.append(f"- Browser: desktop={bt.get('desktop_status', 'N/A')}, mobile={bt.get('mobile_status', 'N/A')}")
        
        parts.append(f"\n## Instructions")
        parts.append(f"1. Read {contract_ref} for the acceptance criteria context.")
        parts.append(f"2. Read {contract_ref.replace('sprint_contract', 'contract')} for broader quality standards.")
        parts.append("3. Score each dimension with concrete evidence from the reports above.")
        parts.append("4. Calculate and output BOTH scores:")
        parts.append("   - SPRINT_SCORE: X/10 (how well this sprint's tasks were completed)")
        parts.append("   - OVERALL_SCORE: X/10 (weighted overall, using the 40/30/15/15 formula)")
        parts.append("5. Save feedback to feedback.md.")
        
        return "\n".join(parts)
