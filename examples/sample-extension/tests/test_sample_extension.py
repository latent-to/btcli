from pathlib import Path
import subprocess
import sys


def test_sample_extension_runs():
    """Verify the sample extension entry point runs without errors."""
    entry = Path(__file__).parent.parent / "main.py"
    result = subprocess.run(
        [sys.executable, str(entry)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Sample btcli Extension" in result.stdout
    assert "Extension loaded successfully!" in result.stdout


def test_sample_extension_prints_python_version():
    """Verify the extension prints Python version info."""
    entry = Path(__file__).parent.parent / "main.py"
    result = subprocess.run(
        [sys.executable, str(entry)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    expected = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    assert expected in result.stdout
