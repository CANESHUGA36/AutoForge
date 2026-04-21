#!/usr/bin/env python3
"""
AutoForge entry point — runs the harness with a user prompt.

Usage:
    python run.py "Build a pomodoro timer web app"
"""
import sys
from harness.core import Harness


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: python run.py <prompt>")
        print('Example: python run.py "Build a pomodoro timer"')
        return 1

    prompt = sys.argv[1]
    harness = Harness()
    harness.run(prompt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
