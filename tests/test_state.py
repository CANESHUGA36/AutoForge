from harness.state import StateManager
import json


def test_save_is_atomic(tmp_path):
    mgr = StateManager(tmp_path)
    mgr.save({"completed_rounds": 3})
    data = json.loads((tmp_path / "harness_state.json").read_text())
    assert data["completed_rounds"] == 3


def test_clear_removes_file(tmp_path):
    mgr = StateManager(tmp_path)
    mgr.save({"test": True})
    mgr.clear()
    assert not (tmp_path / "harness_state.json").exists()
