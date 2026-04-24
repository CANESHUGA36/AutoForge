"""Tests for EvalCache (runtime-discovered fix: Judge reads from EvalCache)."""
from eval_cache import EvalCache


def test_save_and_get_round(tmp_path):
    cache = EvalCache(str(tmp_path))
    cache.save_round(1, "Reviewer report for round 1")
    report = cache.get_full_report(1)
    assert report == "Reviewer report for round 1"


def test_get_missing_round_returns_none(tmp_path):
    cache = EvalCache(str(tmp_path))
    assert cache.get_full_report(99) is None


def test_multiple_rounds(tmp_path):
    cache = EvalCache(str(tmp_path))
    cache.save_round(1, "Report 1")
    cache.save_round(2, "Report 2")
    assert cache.get_full_report(1) == "Report 1"
    assert cache.get_full_report(2) == "Report 2"
