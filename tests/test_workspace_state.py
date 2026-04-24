"""Tests for WorkspaceState serialization/deserialization (Bug #3 fix)."""
import json
from workspace_state import WorkspaceState, FileState


def test_load_restores_files_dict(tmp_path):
    """BUG #3: WorkspaceState.load() must restore the files dictionary."""
    ws = tmp_path / "workspace"
    ws.mkdir()

    state = WorkspaceState()
    state.files["src/App.tsx"] = FileState(
        path="src/App.tsx", size=1200, lines=45, summary="Main app component"
    )
    state.files["src/main.tsx"] = FileState(
        path="src/main.tsx", size=300, lines=12, summary="Entry point"
    )
    state.total_files = 2
    state.total_lines = 57
    state.last_build_status = "ok"
    state.save(str(ws))

    # Verify serialized data contains files
    raw = json.loads((ws / ".workspace_state.json").read_text())
    assert "files" in raw
    assert "src/App.tsx" in raw["files"]

    # Load and verify restoration
    loaded = WorkspaceState.load(str(ws))
    assert "src/App.tsx" in loaded.files
    assert loaded.files["src/App.tsx"].size == 1200
    assert loaded.files["src/App.tsx"].lines == 45
    assert loaded.files["src/App.tsx"].summary == "Main app component"
    assert loaded.total_files == 2
    assert loaded.total_lines == 57
    assert loaded.last_build_status == "ok"


def test_load_empty_files_is_safe(tmp_path):
    """Loading state with no files field should not crash."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    data = {"total_files": 0, "total_lines": 0, "last_build_status": "unknown"}
    (ws / ".workspace_state.json").write_text(json.dumps(data))

    loaded = WorkspaceState.load(str(ws))
    assert loaded.files == {}
    assert loaded.total_files == 0


def test_load_missing_file_returns_default(tmp_path):
    """Loading from non-existent path returns fresh instance."""
    loaded = WorkspaceState.load(str(tmp_path / "nonexistent"))
    assert loaded.files == {}
    assert loaded.last_build_status == "unknown"
