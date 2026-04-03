EXTENSION_YAML_TEMPLATE = """\
name: {name}
version: 0.1.0
description: A btcli extension
entry_point: main.py
dependencies: []
"""

MAIN_PY_TEMPLATE = """\
#!/usr/bin/env python3
\"\"\"Entry point for the {name} btcli extension.\"\"\"


def main():
    print("Hello from {name}!")


if __name__ == "__main__":
    main()
"""

TEST_TEMPLATE = """\
from pathlib import Path
import subprocess
import sys


def test_{safe_name}_runs():
    entry = Path(__file__).parent.parent / "main.py"
    result = subprocess.run(
        [sys.executable, str(entry)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
"""
