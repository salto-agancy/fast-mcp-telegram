#!/usr/bin/env python3
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "pytest", "tests/unit/auth/", "-x", "-q", "--tb=short"],
    cwd="/root/fast-mcp-telegram",
    capture_output=True, text=True, timeout=180
)
print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
sys.exit(result.returncode)
