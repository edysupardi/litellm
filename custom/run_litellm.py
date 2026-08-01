#!/usr/bin/env python3
"""Wrapper that registers KiroProvider before litellm initializes, then runs the proxy."""
import os
import sys

CUSTOM_DIR = os.path.dirname(os.path.abspath(__file__))
if CUSTOM_DIR not in sys.path:
    sys.path.insert(0, CUSTOM_DIR)

# Register custom provider before litellm reads the config
import litellm
from kiro.provider import KiroProvider

litellm.custom_provider_map = [
    {"provider": "kiro", "custom_handler": KiroProvider()},
]
print("[Kiro] Custom provider registered before proxy startup", file=sys.stderr)

# Run the proxy CLI in this same process
from litellm.proxy.proxy_cli import run_server

run_server()
