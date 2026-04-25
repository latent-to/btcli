import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from bittensor_cli.src.commands.extensions.manifest import (
    ExtensionManifest,
    get_extensions_dir,
    get_installed_extensions,
    get_extension_by_name,
)
from bittensor_cli.src.commands.extensions.ext_commands import (
    _check_git,
    ext_create,
    ext_list,
    ext_remove,
)


@pytest.fixture
def tmp_extensions_dir(tmp_path):
    """Override the extensions directory to a temporary path."""
    ext_dir = tmp_path / "extensions"
    ext_dir.mkdir()
    with patch(
        "bittensor_cli.src.commands.extensions.manifest.EXTENSIONS_DIR", ext_dir
    ):
        yield ext_dir


@pytest.fixture
def sample_extension(tmp_extensions_dir):
    """Create a sample extension directory with a valid manifest."""
    ext_path = tmp_extensions_dir / "sample-ext"
    ext_path.mkdir()
    (ext_path / "extension.yaml").write_text(
        "name: sample-ext\n"
        "version: 1.0.0\n"
        "description: A sample extension\n"
        "entry_point: main.py\n"
    )
    (ext_path / "main.py").write_text('print("hello")\n')
    return ext_path


class TestExtensionManifest:
    def test_from_yaml_valid(self, sample_extension):
        manifest = ExtensionManifest.from_yaml(sample_extension)
        assert manifest.name == "sample-ext"
        assert manifest.version == "1.0.0"
        assert manifest.description == "A sample extension"
        assert manifest.entry_point == "main.py"
        assert manifest.dependencies == []

    def test_from_yaml_missing_file(self, tmp_extensions_dir):
        empty_dir = tmp_extensions_dir / "empty"
        empty_dir.mkdir()
        with pytest.raises(FileNotFoundError, match="No extension.yaml"):
            ExtensionManifest.from_yaml(empty_dir)

    def test_from_yaml_missing_required_fields(self, tmp_extensions_dir):
        ext_path = tmp_extensions_dir / "bad-ext"
        ext_path.mkdir()
        (ext_path / "extension.yaml").write_text("name: bad-ext\n")
        with pytest.raises(ValueError, match="missing required fields"):
            ExtensionManifest.from_yaml(ext_path)

    def test_from_yaml_with_optional_fields(self, tmp_extensions_dir):
        ext_path = tmp_extensions_dir / "full-ext"
        ext_path.mkdir()
        (ext_path / "extension.yaml").write_text(
            "name: full-ext\n"
            "version: 2.0.0\n"
            "description: Full extension\n"
            "entry_point: run.py\n"
            "dependencies:\n"
            "  - requests\n"
            "  - numpy\n"
            "author: test-author\n"
            "repository: https://github.com/test/repo\n"
        )
        manifest = ExtensionManifest.from_yaml(ext_path)
        assert manifest.dependencies == ["requests", "numpy"]
        assert manifest.author == "test-author"
        assert manifest.repository == "https://github.com/test/repo"

    def test_to_yaml_roundtrip(self, tmp_extensions_dir):
        ext_path = tmp_extensions_dir / "roundtrip"
        ext_path.mkdir()
        manifest = ExtensionManifest(
            name="roundtrip",
            version="1.0.0",
            description="Roundtrip test",
            entry_point="main.py",
        )
        manifest.to_yaml(ext_path)
        loaded = ExtensionManifest.from_yaml(ext_path)
        assert loaded.name == manifest.name
        assert loaded.version == manifest.version
        assert loaded.description == manifest.description
        assert loaded.entry_point == manifest.entry_point


class TestGetExtensionsDir:
    def test_creates_directory(self, tmp_path):
        ext_dir = tmp_path / "new_extensions"
        with patch(
            "bittensor_cli.src.commands.extensions.manifest.EXTENSIONS_DIR", ext_dir
        ):
            result = get_extensions_dir()
        assert result == ext_dir
        assert ext_dir.exists()

    def test_returns_existing_directory(self, tmp_extensions_dir):
        result = get_extensions_dir()
        assert result == tmp_extensions_dir
        assert result.exists()


class TestGetInstalledExtensions:
    def test_empty_directory(self, tmp_extensions_dir):
        result = get_installed_extensions()
        assert result == []

    def test_finds_valid_extensions(self, sample_extension, tmp_extensions_dir):
        result = get_installed_extensions()
        assert len(result) == 1
        path, manifest = result[0]
        assert path == sample_extension
        assert manifest.name == "sample-ext"

    def test_skips_invalid_extensions(self, tmp_extensions_dir):
        bad_dir = tmp_extensions_dir / "bad"
        bad_dir.mkdir()
        (bad_dir / "extension.yaml").write_text("name: bad\n")
        result = get_installed_extensions()
        assert result == []

    def test_skips_files(self, tmp_extensions_dir):
        (tmp_extensions_dir / "not-a-dir.txt").write_text("hello")
        result = get_installed_extensions()
        assert result == []


class TestGetExtensionByName:
    def test_find_by_manifest_name(self, sample_extension, tmp_extensions_dir):
        path, manifest = get_extension_by_name("sample-ext")
        assert manifest.name == "sample-ext"

    def test_not_found(self, tmp_extensions_dir):
        with pytest.raises(FileNotFoundError, match="not found"):
            get_extension_by_name("nonexistent")


class TestExtCreate:
    def test_creates_boilerplate(self, tmp_extensions_dir):
        ext_create("my-plugin")
        target = tmp_extensions_dir / "my-plugin"
        assert target.exists()
        assert (target / "extension.yaml").exists()
        assert (target / "main.py").exists()
        assert (target / "tests" / "test_my_plugin.py").exists()

        manifest = ExtensionManifest.from_yaml(target)
        assert manifest.name == "my-plugin"

    def test_refuses_duplicate(self, sample_extension, tmp_extensions_dir):
        ext_create("sample-ext")
        # Should print error but not crash


class TestExtList:
    def test_no_extensions(self, tmp_extensions_dir, capsys):
        ext_list()

    def test_with_extensions(self, sample_extension, tmp_extensions_dir, capsys):
        ext_list()


class TestExtRemove:
    def test_removes_extension(self, sample_extension, tmp_extensions_dir):
        assert sample_extension.exists()
        ext_remove("sample-ext")
        assert not sample_extension.exists()

    def test_remove_nonexistent(self, tmp_extensions_dir):
        ext_remove("nonexistent")


class TestCheckGit:
    def test_git_available(self):
        import bittensor_cli.src.commands.extensions.ext_commands as mod
        mod._GIT_AVAILABLE = None  # reset cache
        assert _check_git() is True

    def test_git_not_available(self):
        import bittensor_cli.src.commands.extensions.ext_commands as mod
        mod._GIT_AVAILABLE = None  # reset cache
        with patch("shutil.which", return_value=None):
            assert _check_git() is False
        mod._GIT_AVAILABLE = None  # reset cache for other tests


class TestSampleExtension:
    """Integration tests using the sample extension in examples/."""

    SAMPLE_EXT = Path(__file__).parent.parent.parent / "examples" / "sample-extension"

    def test_sample_extension_has_valid_manifest(self):
        manifest = ExtensionManifest.from_yaml(self.SAMPLE_EXT)
        assert manifest.name == "sample-extension"
        assert manifest.version == "0.1.0"
        assert manifest.entry_point == "main.py"

    def test_sample_extension_runs(self):
        entry = self.SAMPLE_EXT / "main.py"
        result = subprocess.run(
            [sys.executable, str(entry)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "Sample btcli Extension" in result.stdout
        assert "Extension loaded successfully!" in result.stdout

    def test_sample_extension_tests_pass(self):
        tests_dir = self.SAMPLE_EXT / "tests"
        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(tests_dir), "-v"],
            capture_output=True,
            text=True,
            cwd=str(self.SAMPLE_EXT),
        )
        assert result.returncode == 0
