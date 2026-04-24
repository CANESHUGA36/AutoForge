import pytest
from tools_impl import _resolve
import config


def test_resolve_allows_workspace_subpath(tmp_path):
    config.WORKSPACE = str(tmp_path)
    p = _resolve("src/app/page.tsx")
    assert p == tmp_path / "src/app/page.tsx"


def test_resolve_blocks_escape_attempt():
    config.WORKSPACE = "/home/user/workspace"
    with pytest.raises(ValueError):
        _resolve("../../etc/passwd")
