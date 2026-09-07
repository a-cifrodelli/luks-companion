#!/usr/bin/env python3
# ===================================================================
# LUKS-MANAGER WEB APP SERVER (Desktop-First HTTP Gateway)
# Wrapper delegating directly to luks_companion.web.app
# ===================================================================
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from luks_companion.web.app import run_web_server

if __name__ == "__main__":
    run_web_server()
