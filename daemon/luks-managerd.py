#!/usr/bin/env python3
# ===================================================================
# LUKS-MANAGER DAEMON: UNIX Domain Socket API Server
# Wrapper delegating directly to luks_companion.daemon.server
# ===================================================================
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from luks_companion.daemon.server import run_daemon

if __name__ == "__main__":
    run_daemon()
