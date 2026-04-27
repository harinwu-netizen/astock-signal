#!/usr/bin/env python3
"""Direct wrapper for run_watch.py"""
import sys
import os

os.chdir("/root/.openclaw/workspace/astock-signal")
sys.path.insert(0, "/root/.openclaw/workspace/astock-signal")

from main import main
sys.exit(main())