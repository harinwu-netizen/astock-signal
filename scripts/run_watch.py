#!/usr/bin/env python3
"""Wrapper script for cron-triggered watch execution."""
import subprocess
import sys
import os

# Change to project directory
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Run main.py watch
result = subprocess.run(
    [sys.executable, "main.py", "watch"],
    capture_output=False  # Let output flow through to parent
)
sys.exit(result.returncode)
