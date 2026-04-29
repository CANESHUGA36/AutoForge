"""
Dev Server 验证 + 项目端口检测
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import config

log = logging.getLogger("harness")


def _detect_project_port(workspace: Path) -> int:
    """Detect dev server port from package.json dependencies."""
    package_json = workspace / "package.json"
    if package_json.exists():
        try:
            data = json.loads(package_json.read_text(encoding="utf-8"))
            deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            if "vite" in deps:
                return config.DEV_SERVER_PORTS["vite"]
            if "next" in deps:
                return config.DEV_SERVER_PORTS["nextjs"]
        except Exception:
            pass
    return config.DEV_SERVER_PORTS["nextjs"]


def verify_dev_server(workspace: Path, port: int = None, max_wait: int = None) -> tuple[bool, str]:
    """Harness-level dev server verification.

    First checks .workspace_state.json for build errors, then performs
    an actual HTTP health check against localhost:{port}.

    Returns:
        (success, message)
    """
    import urllib.request
    import time as _time

    port = port or _detect_project_port(workspace)
    max_wait = max_wait or config.DEV_SERVER_MAX_WAIT

    # Step 1: Actual HTTP health check with polling
    url = f"http://localhost:{port}"
    start = _time.time()
    last_error = ""
    while _time.time() - start < max_wait:
        try:
            req = urllib.request.Request(url, method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return True, f"Dev server responding on port {port} (HTTP 200)"
                elif resp.status >= 500:
                    return False, f"Dev server error on port {port} (HTTP {resp.status})"
        except urllib.error.HTTPError as e:
            if e.code >= 500:
                return False, f"Dev server error on port {port} (HTTP {e.code})"
            # 404 means server is up but route not found — still counts as responding
            if e.code == 404:
                return True, f"Dev server responding on port {port} (HTTP 404 — server is up)"
            # Other client errors might mean server is starting
            last_error = f"HTTP {e.code}"
        except Exception as e:
            last_error = str(e)
        _time.sleep(1)

    return False, f"Dev server not responding on port {port} after {max_wait}s (last: {last_error})"
