#!/usr/bin/env python3
import subprocess, sys
files = [
    "tests/unit/auth/test_jwt_verifier.py",
    "tests/unit/auth/test_principal_resolver.py",
    "tests/unit/auth/test_oauth_provider_adapter.py",
    "tests/unit/auth/test_elicitation_state_machine.py",
]
result = subprocess.run(
    [sys.executable, "-m", "pytest"] + files + ["-x", "-q", "--tb=short"],
    cwd="/root/fast-mcp-telegram",
    capture_output=True, text=True, timeout=120
)
print(result.stdout[-3000:] if len(result.stdout) > 3000 else result.stdout)
if result.stderr:
    print("STDERR:", result.stderr[-1000:])
sys.exit(result.returncode)
