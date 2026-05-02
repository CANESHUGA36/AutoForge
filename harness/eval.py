"""
评分解析 — 纯函数，无状态，易于测试
"""
from __future__ import annotations

import logging
import re

import config

log = logging.getLogger("harness")


def parse_scores(text: str) -> tuple[float, float]:
    """Parse SPRINT_SCORE and OVERALL_SCORE from evaluator feedback.

    Returns (sprint_score, overall_score).
    If new format not found, falls back to legacy SCORE line.
    """
    sprint_match = re.search(r'SPRINT_SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)
    overall_match = re.search(r'OVERALL_SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)

    if sprint_match and overall_match:
        return float(sprint_match.group(1)), float(overall_match.group(1))

    # Fallback: legacy single SCORE line
    legacy_match = re.search(r'SCORE:\s*(\d+(?:\.\d+)?)\s*/\s*10', text, re.IGNORECASE)
    if legacy_match:
        score = float(legacy_match.group(1))
        log.warning("Legacy single SCORE found — using it for both sprint and overall")
        return score, score

    log.warning("Could not parse any score, defaulting to 0")
    return 0.0, 0.0


def parse_group_pass_rates(text: str) -> tuple[float | None, float | None]:
    """Parse GROUP_PASS_RATE and OVERALL_PASS_RATE from evaluator feedback.

    Returns (group_pass_rate, overall_pass_rate) as decimals (0.0-1.0).
    Returns (None, None) if not found.
    """
    group_match = re.search(
        r'GROUP_PASS_RATE:\s*(\d+(?:\.\d+)?)\s*%?',
        text, re.IGNORECASE
    )
    overall_match = re.search(
        r'OVERALL_PASS_RATE:\s*(\d+(?:\.\d+)?)\s*%?',
        text, re.IGNORECASE
    )

    group_rate = None
    overall_rate = None

    if group_match:
        val = float(group_match.group(1))
        group_rate = val / 100.0 if val > 1.0 else val

    if overall_match:
        val = float(overall_match.group(1))
        overall_rate = val / 100.0 if val > 1.0 else val

    return group_rate, overall_rate


def parse_pass_rates(text: str) -> tuple[float | None, float | None]:
    """Parse SPRINT_PASS_RATE and CONTRACT_PASS_RATE from evaluator feedback.

    Returns (sprint_pass_rate, contract_pass_rate) as decimals (0.0-1.0).
    Returns (None, None) if not found.
    Supports formats: '65%', '65 %', '0.65', '65/100'
    """
    # Match patterns like: SPRINT_PASS_RATE: 65%  or  SPRINT_PASS_RATE: 0.65
    sprint_match = re.search(
        r'SPRINT_PASS_RATE:\s*(\d+(?:\.\d+)?)\s*%?',
        text, re.IGNORECASE
    )
    contract_match = re.search(
        r'CONTRACT_PASS_RATE:\s*(\d+(?:\.\d+)?)\s*%?',
        text, re.IGNORECASE
    )

    sprint_rate = None
    contract_rate = None

    if sprint_match:
        val = float(sprint_match.group(1))
        sprint_rate = val / 100.0 if val > 1.0 else val

    if contract_match:
        val = float(contract_match.group(1))
        contract_rate = val / 100.0 if val > 1.0 else val

    return sprint_rate, contract_rate


def parse_skip_rate(text: str) -> float:
    """Parse skipped criteria count from evaluator feedback.

    Returns SKIP ratio (0.0-1.0) based on:
    - Passed Criteria section: count '- [x]' lines
    - Failed Criteria section: count '- [ ]' lines  
    - Skipped Criteria section: count '- [ ]' lines

    Returns 0.0 if no criteria sections found.
    """
    # Extract sections
    passed_section = re.search(
        r'###\s*Passed Criteria.*?(?=###|\Z)',
        text, re.IGNORECASE | re.DOTALL
    )
    failed_section = re.search(
        r'###\s*Failed Criteria.*?(?=###|\Z)',
        text, re.IGNORECASE | re.DOTALL
    )
    skipped_section = re.search(
        r'###\s*Skipped Criteria.*?(?=###|\Z)',
        text, re.IGNORECASE | re.DOTALL
    )

    passed = len(re.findall(r'^\s*-\s+\[x\]', passed_section.group(0) if passed_section else '', re.MULTILINE))
    failed = len(re.findall(r'^\s*-\s+\[\s*\]', failed_section.group(0) if failed_section else '', re.MULTILINE))
    skipped = len(re.findall(r'^\s*-\s+\[\s*\]', skipped_section.group(0) if skipped_section else '', re.MULTILINE))

    total = passed + failed + skipped
    if total == 0:
        return 0.0
    return skipped / total


def parse_dimension_scores(text: str) -> dict:
    """Parse per-dimension scores from '### Dimension Name: X/10' headings."""
    _name_map = {
        "design quality": "design_quality",
        "design_quality": "design_quality",
        "originality":    "originality",
        "craft":          "craft",
        "functionality":  "functionality",
    }
    scores: dict = {}
    pattern = r'###\s*([\w\s]+?):\s*(\d+(?:\.\d+)?)\s*/\s*10'
    for match in re.finditer(pattern, text, re.IGNORECASE):
        raw = match.group(1).strip().lower()
        key = _name_map.get(raw)
        if key:
            scores[key] = float(match.group(2))
    return scores


def check_dimension_thresholds(dim_scores: dict) -> list[str]:
    """Return list of failure strings for dimensions below hard thresholds.
    
    Note: In big-group mode, dimension thresholds are deprecated.
    This function is kept for backward compatibility and always returns [].
    """
    # Big-group mode: dimension thresholds no longer used
    # Reviewer makes autonomous pass/fail decisions
    return []


# --------------------------------------------------------------------------- #
#  Cross-validation: Reviewer FAIL vs Judge PASS
# --------------------------------------------------------------------------- #

_REVIEWER_FAIL_RE = re.compile(
    r'([A-Z]\d+(?:\.\d+)?)[:\s].*?(?:❌|FAIL|NOT FOUND|NOT IMPLEMENTED|missing|absent)',
    re.IGNORECASE,
)

# Patterns that indicate a browser-test-only failure (not a real code failure)
_BROWSER_LIMITATION_PATTERNS = [
    r'(?i)browser.*(?:could not|unable|failed|cannot).*trigger',
    r'(?i)browser.*automation.*limit',
    r'(?i)programmatic.*event.*(?:not work|fail)',
    r'(?i)react.*controlled.*(?:input|component).*limit',
    r'(?i)test.*skipped.*(?:browser|automation)',
    r'(?i)code.*review.*pass.*browser.*fail',
    r'(?i)verified.*via.*code.*review',
    r'(?i)browser.*test.*limitation',
    r'(?i)cannot.*test.*(?:programmatically|via browser)',
    r'(?i)rely.*on.*code.*review',
]


def _is_browser_limitation_line(line: str) -> bool:
    """Check if a line describes a browser automation limitation rather than code failure."""
    return any(re.search(p, line) for p in _BROWSER_LIMITATION_PATTERNS)


def extract_reviewer_fails(review_text: str) -> set[str]:
    """Extract criteria IDs that Reviewer explicitly marks as FAIL/missing.

    Looks for patterns like:
      - 'F3.1-F3.6: NOT IMPLEMENTED'
      - 'C4: Preset names | ❌ FAIL'
      - 'F3.1: Frequency bars — NOT IMPLEMENTED'
    
    IMPORTANT: Ignores failures that are clearly browser automation limitations
    (e.g., "browser could not trigger React controlled input"). These are NOT
    real code failures — they indicate the feature requires real user interaction
    that cannot be simulated programmatically.
    
    Returns a set of criteria IDs (e.g., {'F3.1', 'C4', 'D5'}).
    """
    fails: set[str] = set()
    lines = review_text.splitlines()
    
    for i, line in enumerate(lines):
        # Check for explicit FAIL indicators in the line
        if not re.search(r'❌|FAIL|NOT FOUND|NOT IMPLEMENTED|missing|absent', line, re.IGNORECASE):
            continue
        
        # SKIP: If this line describes a browser automation limitation, not a code failure
        if _is_browser_limitation_line(line):
            # Also check next 2 lines for context (Reviewer might explain in following lines)
            context = ' '.join(lines[i:min(i+3, len(lines))])
            if _is_browser_limitation_line(context):
                continue
        
        # SKIP: If line mentions "code review PASS" or "verified via code", it's not a real fail
        if re.search(r'(?i)code\s+review.*pass|verified.*code|implementation.*correct|handler.*non-empty|jsx.*exist', line):
            continue
        
        # Extract all criteria IDs from this line
        ids = re.findall(r'\b([A-Z]\d+(?:\.\d+)?)\b', line)
        fails.update(ids)
    return fails


def cross_validate_passes(
    eval_text: str, review_text: str
) -> tuple[int, list[str]]:
    """Cross-check Judge's PASS items against Reviewer's FAIL items.

    Returns (corrected_pass_count, list_of_overrides).
    For each criteria that Judge marked PASS but Reviewer marked FAIL,
    we count it as FAIL instead and record an override message.
    """
    reviewer_fails = extract_reviewer_fails(review_text)
    if not reviewer_fails:
        return None, []

    # Find PASS items in Judge's feedback
    pass_section = re.search(
        r'###\s*Passed Criteria.*?(?=###|\Z)',
        eval_text, re.IGNORECASE | re.DOTALL
    )
    pass_text = pass_section.group(0) if pass_section else eval_text

    overrides: list[str] = []
    corrected_pass = 0

    for line in pass_text.splitlines():
        line_stripped = line.strip()
        if not line_stripped.startswith('- [x]'):
            continue

        ids = re.findall(r'\b([A-Z]\d+(?:\.\d+)?)\b', line_stripped)
        count = _expand_criteria_range(line_stripped)

        # Check if any ID in this line was marked FAIL by Reviewer
        fail_ids = [i for i in ids if i in reviewer_fails]
        if fail_ids:
            overrides.append(
                f"{', '.join(fail_ids)}: Judge PASS overridden to FAIL "
                f"(Reviewer found NOT IMPLEMENTED/NOT FOUND)"
            )
        else:
            corrected_pass += count

    return corrected_pass, overrides


# --------------------------------------------------------------------------- #
#  Contract criteria counting — real denominator enforcement
# --------------------------------------------------------------------------- #

_CONTRACT_CRITERIA_RE = re.compile(
    r'^\s*-\s+\[[^\]]*\]\s+\*\*([A-Z]\d+(?:\.\d+)?)\*\*',
    re.MULTILINE,
)


def count_contract_criteria(contract_text: str) -> int:
    """Count total acceptance criteria in contract.md.

    Matches lines like:
      - [ ] **F1.1**: ...
      - [ ] **D1**: ...
      - [ ] **T1**: ...
    Returns the total number of criteria found.
    """
    matches = _CONTRACT_CRITERIA_RE.findall(contract_text)
    return len(matches)


_JUDGE_PASS_RE = re.compile(r'^\s*-\s+\[[^\]]*\]', re.MULTILINE)
_JUDGE_SKIP_RE = re.compile(r'^\s*-\s+\[[^\]]*\].*SKIP', re.MULTILINE | re.IGNORECASE)
# Match range patterns like F3.1-F3.6 or F10.1-F10.5
_CRITERIA_RANGE_RE = re.compile(r'([A-Z])(\d+)\.(\d+)\s*[-~–—]\s*(?:\1)?(\d+)\.(\d+)')


def _expand_criteria_range(line: str) -> int:
    """Expand a criteria range like 'F3.1-F3.6' into item count.
    Returns 1 if no range found.
    """
    match = _CRITERIA_RANGE_RE.search(line)
    if not match:
        return 1
    prefix, start_major, start_minor, end_major, end_minor = match.groups()
    # Count items in range (e.g., F3.1-F3.6 = 6 items)
    count = (int(end_major) - int(start_major)) * 100 + (int(end_minor) - int(start_minor)) + 1
    return max(1, count)


def count_judge_criteria(eval_text: str) -> tuple[int, int, int]:
    """Count PASS / FAIL / SKIP criteria from Judge's feedback text.

    Returns (passed, failed, skipped).
    Handles range notation like 'F3.1-F3.6' by expanding to individual criteria count.
    """
    # Find the Contract Evaluation section
    contract_section = re.search(
        r'##\s*Contract Evaluation.*?(?=##\s*(?:Sprint|Strength|Issue|Action|Scoring|Round-Over-Round)|\Z)',
        eval_text, re.IGNORECASE | re.DOTALL
    )
    text_to_search = contract_section.group(0) if contract_section else eval_text

    passed = 0
    skipped = 0
    failed = 0

    for line in text_to_search.splitlines():
        line_stripped = line.strip()
        if not line_stripped.startswith('- ['):
            continue

        count = _expand_criteria_range(line_stripped)

        if '[x]' in line_stripped:
            passed += count
        elif 'SKIP' in line_stripped.upper():
            skipped += count
        elif '[ ]' in line_stripped:
            failed += count

    return passed, failed, skipped


def count_group_criteria(contract_text: str, group_prefix: str) -> int:
    """Count criteria belonging to a specific group (e.g., 'F1').

    Matches lines like:
      - [ ] **F1.1**: ...
      - [ ] **F1.2**: ...
    Returns count of criteria whose ID matches the group exactly.
    e.g., group_prefix='F1' matches F1.1, F1.2 but NOT F10.1, F11.1
    """
    matches = _CONTRACT_CRITERIA_RE.findall(contract_text)
    return sum(
        1 for m in matches
        if m == group_prefix or m.startswith(group_prefix + ".")
    )


def compute_actual_contract_rate(
    eval_text: str, contract_text: str, review_text: str = "",
    current_group_id: str | None = None,
) -> tuple[float, int, int, int, int, list[str]]:
    """Compute the true CONTRACT_PASS_RATE using the real contract denominator.

    Returns (rate, passed, failed, skipped, total_contract, overrides).

    Logic:
    1. total_contract = count of all criteria in contract.md (real denominator)
       OR count of criteria in current group (when current_group_id is set)
    2. From Judge's feedback, count how many criteria Judge actually evaluated
    3. Cross-validate: if Reviewer marked FAIL but Judge marked PASS, override to FAIL
    4. If Judge evaluated fewer than total_contract, the missing ones are treated as FAIL
    5. If SKIP ratio > 20%, excess SKIP items are treated as FAIL
    6. Final rate = passed / total_contract

    Args:
        current_group_id: If set (e.g., 'F1'), only count criteria in this group.
                          This is used in feature-group mode where Judge only
                          evaluates one group per round.
    """
    if current_group_id:
        total_contract = count_group_criteria(contract_text, current_group_id)
        mode_label = f"group {current_group_id}"
    else:
        total_contract = count_contract_criteria(contract_text)
        mode_label = "full contract"

    judge_passed, judge_failed, judge_skipped = count_judge_criteria(eval_text)

    if total_contract == 0:
        log.warning(f"No criteria found for {mode_label}, cannot compute real rate")
        return 0.0, 0, 0, 0, 0, []

    # Cross-validation: Reviewer FAIL overrides Judge PASS
    overrides: list[str] = []
    if review_text:
        corrected_pass, overrides = cross_validate_passes(eval_text, review_text)
        if corrected_pass is not None and corrected_pass < judge_passed:
            log.warning(
                f"[contract_rate] Cross-validation: {judge_passed - corrected_pass} "
                f"Judge PASS items overridden to FAIL based on Reviewer report"
            )
            judge_failed += (judge_passed - corrected_pass)
            judge_passed = corrected_pass

    # Items Judge explicitly evaluated
    judge_evaluated = judge_passed + judge_failed + judge_skipped
    # Items Judge missed entirely (not in PASS/FAIL/SKIP sections)
    judge_missed = max(0, total_contract - judge_evaluated)

    # Enforce 20% SKIP limit: excess SKIP becomes FAIL
    max_skip = int(total_contract * 0.20)
    excess_skip = max(0, judge_skipped - max_skip)
    effective_skipped = judge_skipped - excess_skip
    effective_failed = judge_failed + excess_skip + judge_missed

    # Compute true rate: passed / total_contract
    true_passed = judge_passed
    true_failed = effective_failed + effective_skipped  # SKIP counts as FAIL for rate calc

    rate = true_passed / total_contract if total_contract > 0 else 0.0

    log.info(
        f"[contract_rate] Judge: {judge_passed}P/{judge_failed}F/{judge_skipped}S "
        f"(evaluated {judge_evaluated}/{total_contract} in {mode_label}), "
        f"missed={judge_missed}, excess_skip={excess_skip}, "
        f"overrides={len(overrides)} "
        f"-> true rate={rate:.1%} ({true_passed}/{total_contract})"
    )

    return rate, true_passed, true_failed, effective_skipped, total_contract, overrides
