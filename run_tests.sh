#!/bin/bash
cd /root/fast-mcp-telegram
python3 -m pytest tests/unit/auth/ -v --tb=short 2>&1 | tail -60
