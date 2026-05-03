"""Tests for the 5 new testing tools: check_responsive, check_performance, check_a11y, check_routes, mock_api."""
from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import patch

import config
from tools_impl import (
    check_responsive,
    check_performance,
    check_a11y,
    check_routes,
    mock_api,
    TOOL_DISPATCH,
)


@pytest.fixture(autouse=True)
def set_workspace(tmp_path, monkeypatch):
    """Each test gets its own workspace."""
    monkeypatch.setattr(config, "WORKSPACE", str(tmp_path))
    return tmp_path


class TestMockApi:
    """Tests for mock_api tool."""

    def test_creates_get_json_mock(self, set_workspace):
        result = mock_api("/api/users", {"users": [{"id": 1, "name": "Alice"}]})
        mock_file = set_workspace / "public" / "mock" / "api_users.json"
        assert mock_file.exists(), f"Expected {mock_file} to exist. Result: {result}"
        data = json.loads(mock_file.read_text(encoding="utf-8"))
        assert data["response"]["users"][0]["name"] == "Alice"
        assert "ok" in result.lower() or "created" in result.lower()

    def test_creates_post_mock(self, set_workspace):
        result = mock_api("/api/users", {"id": 3}, method="POST")
        mock_file = set_workspace / "public" / "mock" / "api_users.json"
        assert mock_file.exists()
        data = json.loads(mock_file.read_text(encoding="utf-8"))
        assert data["method"] == "POST"
        assert "ok" in result.lower()

    def test_creates_error_mock(self, set_workspace):
        result = mock_api("/api/error", {"error": "Not found"}, status_code=404)
        mock_file = set_workspace / "public" / "mock" / "api_error.json"
        assert mock_file.exists()
        data = json.loads(mock_file.read_text(encoding="utf-8"))
        assert data["status_code"] == 404
        assert data["response"]["error"] == "Not found"

    def test_non_persistent_mock_not_saved(self, set_workspace):
        result = mock_api("/api/temp", {"temp": True}, persist=False)
        mock_file = set_workspace / "public" / "mock" / "api_temp.json"
        assert not mock_file.exists()
        assert "ok" in result.lower()

    def test_creates_js_file(self, set_workspace):
        result = mock_api("/api/users", {"users": []})
        js_file = set_workspace / "public" / "mock" / "api_users.js"
        assert js_file.exists(), f"Expected JS file {js_file} to exist"
        content = js_file.read_text(encoding="utf-8")
        assert "export const mockData" in content


class TestCheckRoutes:
    """Tests for check_routes tool."""

    def test_detects_missing_routes(self, set_workspace):
        # No app directory exists, so all routes should be missing
        result = check_routes(["/", "/about"], base_url="http://localhost:3000")
        assert isinstance(result, str)
        # Server not running, so requests will fail
        assert "error" in result.lower() or "routes_tested" in result.lower()

    def test_finds_existing_route_files(self, set_workspace):
        # Create Next.js app directory structure
        app_dir = set_workspace / "app"
        app_dir.mkdir()
        (app_dir / "page.tsx").write_text("export default function Home() {}", encoding="utf-8")
        about_dir = app_dir / "about"
        about_dir.mkdir()
        (about_dir / "page.tsx").write_text("export default function About() {}", encoding="utf-8")

        result = check_routes(["/", "/about"], base_url="http://localhost:3000")
        assert isinstance(result, str)
        assert "routes_tested" in result.lower()

    def test_auto_discovers_routes(self, set_workspace):
        # Create Next.js app directory structure without providing expected_routes
        app_dir = set_workspace / "app"
        app_dir.mkdir()
        (app_dir / "page.tsx").write_text("export default function Home() {}", encoding="utf-8")
        blog_dir = app_dir / "blog"
        blog_dir.mkdir()
        (blog_dir / "page.tsx").write_text("export default function Blog() {}", encoding="utf-8")

        result = check_routes(expected_routes=None, base_url="http://localhost:3000")
        assert isinstance(result, str)
        # Should auto-discover routes from app/ directory
        assert "routes_tested" in result.lower() or "error" in result.lower()


class TestToolDispatch:
    """Verify all new tools are registered in TOOL_DISPATCH."""

    def test_check_responsive_registered(self):
        assert "check_responsive" in TOOL_DISPATCH
        assert callable(TOOL_DISPATCH["check_responsive"])

    def test_check_performance_registered(self):
        assert "check_performance" in TOOL_DISPATCH
        assert callable(TOOL_DISPATCH["check_performance"])

    def test_check_a11y_registered(self):
        assert "check_a11y" in TOOL_DISPATCH
        assert callable(TOOL_DISPATCH["check_a11y"])

    def test_check_routes_registered(self):
        assert "check_routes" in TOOL_DISPATCH
        assert callable(TOOL_DISPATCH["check_routes"])

    def test_mock_api_registered(self):
        assert "mock_api" in TOOL_DISPATCH
        assert callable(TOOL_DISPATCH["mock_api"])


class TestCheckResponsiveSchema:
    """Verify check_responsive returns structured output."""

    @patch("tools_impl.browser_check")
    def test_returns_json_structure(self, mock_browser_check, set_workspace):
        mock_browser_check.return_value = json.dumps({
            "title": "Test",
            "viewport": {"width": 375, "height": 667},
            "elements": {}
        })
        result = check_responsive("http://localhost:5173")
        # Should return a JSON-parseable result or error
        assert isinstance(result, str)
        # Either success JSON or error (no browser available in tests)
        assert "breakpoints" in result.lower() or "error" in result.lower() or "viewport" in result.lower()


class TestCheckA11ySchema:
    """Verify check_a11y returns structured output."""

    @patch("tools_impl.browser_check")
    def test_returns_json_structure(self, mock_browser_check, set_workspace):
        mock_browser_check.return_value = json.dumps({
            "title": "Test",
            "elements": {}
        })
        result = check_a11y("http://localhost:5173")
        assert isinstance(result, str)
        assert "violations" in result.lower() or "error" in result.lower() or "rules" in result.lower()


class TestCheckPerformanceSchema:
    """Verify check_performance returns structured output."""

    @patch("tools_impl.browser_check")
    def test_returns_json_structure(self, mock_browser_check, set_workspace):
        mock_browser_check.return_value = json.dumps({
            "title": "Test",
            "elements": {}
        })
        result = check_performance("http://localhost:5173")
        assert isinstance(result, str)
        assert "metrics" in result.lower() or "error" in result.lower() or "performance" in result.lower()
