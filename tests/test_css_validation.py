"""Tests for CSS class validation in validate_build."""
from pathlib import Path


def test_validate_css_classes_finds_classes(tmp_path, monkeypatch):
    """_validate_css_classes should detect missing Tailwind classes."""
    from tools_impl import _validate_css_classes
    import config

    # Create a fake workspace with CSS that HAS the expected classes
    monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
    dist = tmp_path / "dist" / "assets"
    dist.mkdir(parents=True)
    css = dist / "index-abc123.css"
    css.write_text(".bg-background{--tw-bg-opacity:1}.text-primary{color:#00f0ff}")

    result = _validate_css_classes(["bg-background", "text-primary"])
    assert "[CSS OK]" in result
    assert "2 expected classes found" in result


def test_validate_css_classes_reports_missing(tmp_path, monkeypatch):
    """_validate_css_classes should report missing classes."""
    from tools_impl import _validate_css_classes
    import config

    monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
    dist = tmp_path / "dist" / "assets"
    dist.mkdir(parents=True)
    css = dist / "index-abc123.css"
    css.write_text(".bg-background{--tw-bg-opacity:1}")  # missing text-primary

    result = _validate_css_classes(["bg-background", "text-primary"])
    assert "[CSS ERROR]" in result
    assert "text-primary" in result


def test_validate_css_classes_no_css_file(tmp_path, monkeypatch):
    """_validate_css_classes should return empty when no CSS files exist."""
    from tools_impl import _validate_css_classes
    import config

    monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
    result = _validate_css_classes()
    assert result == ""
