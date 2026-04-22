#!/usr/bin/env python3
"""
AutoForge entry point — runs the harness with a user prompt.

Usage:
    python run.py "Build a pomodoro timer web app"
"""
import sys
import io

# Force UTF-8 on Windows to prevent GBK codec errors when logging Unicode chars
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

import os
from pathlib import Path
from harness.core import Harness


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run.py <prompt>")
        print('Example: python run.py "Build a pomodoro timer"')
        return 1

    prompt = sys.argv[1]
    
    # Create timestamped workspace under projects/
    projects_dir = Path(os.environ.get("HARNESS_PROJECTS_DIR", "./projects"))
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    
    # Create a safe directory name from the prompt
    safe_name = "".join(c if c.isalnum() or c in "-_" else "-" for c in prompt[:40]).strip("-").lower()
    workspace = projects_dir / f"{safe_name}-{timestamp}"
    workspace.mkdir(parents=True, exist_ok=True)
    
    harness = Harness(str(workspace))
    result = harness.run(prompt)
    
    print(f"\n=== Build Complete ===")
    print(f"Workspace: {workspace}")
    print(f"Result: {result}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
