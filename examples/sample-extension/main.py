#!/usr/bin/env python3
"""A sample btcli extension that demonstrates the extensions framework.

This extension prints a greeting and basic system info to show how an
extension entry point works.
"""

import platform
import sys


def main():
    print("=== Sample btcli Extension ===")
    print(f"Python: {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}")
    print(f"Platform: {platform.system()} {platform.machine()}")
    print("Extension loaded successfully!")


if __name__ == "__main__":
    main()
