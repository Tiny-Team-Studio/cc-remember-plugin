"""Tests for path resolution across different install layouts.

Tests the current inline path resolution in save-session.sh and
run-consolidation.sh, proving where it breaks. Then tests the fix
(resolve-paths.sh) once it exists.

Install layouts tested:
  1. Local:       $PROJECT/.claude/remember/scripts/save-session.sh
  2. Marketplace: ~/.claude/plugins/cache/org/remember/0.1.0/scripts/save-session.sh
  3. Symlinked:   Local layout with symlinked scripts/ directory
  4. Spaces:      Local layout with spaces in the project path
"""

import os
import stat
import subprocess
import tempfile

import pytest


def _create_local_install(base: str) -> tuple[str, str]:
    """Create a local install layout and return (project_dir, plugin_dir).

    Layout:
        base/my-project/
        base/my-project/.claude/remember/scripts/save-session.sh
        base/my-project/.claude/remember/pipeline/llm.py
        base/my-project/.remember/tmp/
        base/my-project/.remember/logs/
    """
    project = os.path.join(base, "my-project")
    plugin = os.path.join(project, ".claude", "remember")
    scripts = os.path.join(plugin, "scripts")
    os.makedirs(scripts)
    os.makedirs(os.path.join(plugin, "pipeline"))
    os.makedirs(os.path.join(project, ".remember", "tmp"))
    os.makedirs(os.path.join(project, ".remember", "logs"))

    # Create a marker file so resolve-paths.sh can detect the plugin root
    with open(os.path.join(plugin, "pipeline", "llm.py"), "w") as f:
        f.write("# marker\n")

    return project, plugin


def _create_marketplace_install(base: str) -> tuple[str, str, str]:
    """Create a marketplace install layout and return (project_dir, plugin_dir, cache_dir).

    Layout:
        base/my-project/                                          (project)
        base/my-project/.remember/tmp/
        base/my-project/.remember/logs/
        base/home/.claude/plugins/cache/org/remember/0.1.0/       (plugin)
        base/home/.claude/plugins/cache/org/remember/0.1.0/scripts/
        base/home/.claude/plugins/cache/org/remember/0.1.0/pipeline/llm.py
    """
    project = os.path.join(base, "my-project")
    cache_base = os.path.join(base, "home", ".claude", "plugins", "cache")
    plugin = os.path.join(cache_base, "claude-plugins-official", "remember", "0.1.0")
    scripts = os.path.join(plugin, "scripts")
    os.makedirs(scripts)
    os.makedirs(os.path.join(plugin, "pipeline"))
    os.makedirs(os.path.join(project, ".remember", "tmp"))
    os.makedirs(os.path.join(project, ".remember", "logs"))

    with open(os.path.join(plugin, "pipeline", "llm.py"), "w") as f:
        f.write("# marker\n")

    return project, plugin, cache_base


def _write_test_script(plugin_dir: str, filename: str, content: str) -> str:
    """Write a test script into the plugin's scripts/ dir and make it executable."""
    path = os.path.join(plugin_dir, "scripts", filename)
    with open(path, "w") as f:
        f.write(content)
    os.chmod(path, os.stat(path).st_mode | stat.S_IEXEC)
    return path


# ─── Test the CURRENT inline resolution (proving the bug) ────────────────────

# This is the pattern used in save-session.sh line 57 and run-consolidation.sh line 38:
#   PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../../.." && pwd)}"
#   PIPELINE_DIR="${CLAUDE_PLUGIN_ROOT:-${PROJECT_DIR}/.claude/remember}"
CURRENT_RESOLUTION_SCRIPT = """\
#!/bin/bash
set -e
PROJECT_DIR="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "$0")/../../.." && pwd)}"
PIPELINE_DIR="${CLAUDE_PLUGIN_ROOT:-${PROJECT_DIR}/.claude/remember}"
echo "PROJECT_DIR=$PROJECT_DIR"
echo "PIPELINE_DIR=$PIPELINE_DIR"
"""


class TestCurrentResolutionLocal:
    """Current inline resolution with a local install layout."""

    def test_local_without_env_vars(self, tmp_path):
        """Local install without env vars — should work (path traversal is correct)."""
        project, plugin = _create_local_install(str(tmp_path))
        script = _write_test_script(plugin, "test-resolve.sh", CURRENT_RESOLUTION_SCRIPT)

        result = subprocess.run(
            ["bash", script],
            capture_output=True, text=True,
            env={**os.environ, "PATH": os.environ["PATH"]},
        )
        assert result.returncode == 0
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin

    def test_local_with_env_vars(self, tmp_path):
        """Local install with env vars — should work (env vars take priority)."""
        project, plugin = _create_local_install(str(tmp_path))
        script = _write_test_script(plugin, "test-resolve.sh", CURRENT_RESOLUTION_SCRIPT)

        env = {**os.environ, "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_ROOT": plugin}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin


class TestCurrentResolutionMarketplace:
    """Current inline resolution with a marketplace install layout — proves the bug."""

    def test_marketplace_without_env_vars_is_wrong(self, tmp_path):
        """Marketplace install WITHOUT env vars — path traversal gives WRONG result.

        This is the core of issue #9: ../../.. from
        ~/.claude/plugins/cache/org/remember/0.1.0/scripts/ goes to
        ~/.claude/plugins/cache/org — NOT the project dir.
        """
        project, plugin, _ = _create_marketplace_install(str(tmp_path))
        script = _write_test_script(plugin, "test-resolve.sh", CURRENT_RESOLUTION_SCRIPT)

        # Deliberately NOT setting CLAUDE_PROJECT_DIR or CLAUDE_PLUGIN_ROOT
        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0

        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)

        # THIS IS THE BUG: PROJECT_DIR resolves to the wrong location
        assert resolved["PROJECT_DIR"] != project, (
            "If this passes, the bug is fixed and this test needs updating"
        )
        # It resolves to cache/org instead of the project
        assert "cache" in resolved["PROJECT_DIR"]

    def test_marketplace_with_env_vars_works(self, tmp_path):
        """Marketplace install WITH env vars — should work."""
        project, plugin, _ = _create_marketplace_install(str(tmp_path))
        script = _write_test_script(plugin, "test-resolve.sh", CURRENT_RESOLUTION_SCRIPT)

        env = {**os.environ, "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_ROOT": plugin}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin


class TestCurrentResolutionSpaces:
    """Current inline resolution with spaces in the path."""

    def test_local_with_spaces_without_env_vars(self, tmp_path):
        """Local install with spaces in path — should work (quotes are correct)."""
        base = os.path.join(str(tmp_path), "my projects", "work stuff")
        os.makedirs(base)
        project, plugin = _create_local_install(base)
        script = _write_test_script(plugin, "test-resolve.sh", CURRENT_RESOLUTION_SCRIPT)

        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project


# ─── Test resolve-paths.sh (the fix) ─────────────────────────────────────────

RESOLVE_PATHS_SH = os.path.join(
    os.path.dirname(__file__), "..", "scripts", "resolve-paths.sh"
)

# Wrapper that sources resolve-paths.sh and prints the results
RESOLVE_WRAPPER = """\
#!/bin/bash
source "{resolve_paths}" 2>&1
echo "PROJECT_DIR=$PROJECT_DIR"
echo "PIPELINE_DIR=$PIPELINE_DIR"
"""


def _has_resolve_paths() -> bool:
    """Check if resolve-paths.sh exists (tests skip if not yet created)."""
    return os.path.isfile(RESOLVE_PATHS_SH)


@pytest.mark.skipif(not _has_resolve_paths(), reason="resolve-paths.sh not yet created")
class TestResolvePathsLocal:
    """resolve-paths.sh with a local install layout."""

    def test_local_without_env_vars(self, tmp_path):
        """Should resolve from script location when in local layout."""
        project, plugin = _create_local_install(str(tmp_path))
        wrapper = RESOLVE_WRAPPER.format(resolve_paths=RESOLVE_PATHS_SH)
        # Copy resolve-paths.sh into the test plugin's scripts dir
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin

    def test_local_with_env_vars(self, tmp_path):
        """Env vars should take priority over path traversal."""
        project, plugin = _create_local_install(str(tmp_path))
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {**os.environ, "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_ROOT": plugin}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin


@pytest.mark.skipif(not _has_resolve_paths(), reason="resolve-paths.sh not yet created")
class TestResolvePathsMarketplace:
    """resolve-paths.sh with a marketplace install layout."""

    def test_marketplace_with_env_vars(self, tmp_path):
        """Marketplace with env vars — the normal working case."""
        project, plugin, _ = _create_marketplace_install(str(tmp_path))
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {**os.environ, "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_ROOT": plugin}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin

    def test_marketplace_without_env_vars_fails_loud(self, tmp_path):
        """Marketplace WITHOUT env vars — should FAIL with a clear error, not silently compute wrong paths."""
        project, plugin, _ = _create_marketplace_install(str(tmp_path))
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        # Should fail — marketplace install without env vars cannot resolve project dir
        assert result.returncode != 0, (
            "Should fail when marketplace install has no CLAUDE_PROJECT_DIR"
        )
        assert "FATAL" in result.stderr or "FATAL" in result.stdout


@pytest.mark.skipif(not _has_resolve_paths(), reason="resolve-paths.sh not yet created")
class TestResolvePathsSpaces:
    """resolve-paths.sh with spaces in paths."""

    def test_spaces_in_project_path(self, tmp_path):
        """Paths with spaces should resolve correctly."""
        base = os.path.join(str(tmp_path), "my projects", "work stuff")
        os.makedirs(base)
        project, plugin = _create_local_install(base)
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project

    def test_spaces_in_env_var_paths(self, tmp_path):
        """Env vars with spaces should work too."""
        base = os.path.join(str(tmp_path), "path with spaces")
        os.makedirs(base)
        project, plugin = _create_local_install(base)
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {**os.environ, "CLAUDE_PROJECT_DIR": project, "CLAUDE_PLUGIN_ROOT": plugin}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        assert resolved["PROJECT_DIR"] == project
        assert resolved["PIPELINE_DIR"] == plugin


@pytest.mark.skipif(not _has_resolve_paths(), reason="resolve-paths.sh not yet created")
class TestResolvePathsSymlink:
    """resolve-paths.sh with symlinked plugin directory."""

    def test_symlinked_plugin_dir(self, tmp_path):
        """When plugin dir is symlinked, resolve through the symlink."""
        # Create the real plugin somewhere else
        real_plugin = os.path.join(str(tmp_path), "real-plugin")
        os.makedirs(os.path.join(real_plugin, "scripts"))
        os.makedirs(os.path.join(real_plugin, "pipeline"))
        with open(os.path.join(real_plugin, "pipeline", "llm.py"), "w") as f:
            f.write("# marker\n")

        # Create project with symlinked .claude/remember -> real_plugin
        project = os.path.join(str(tmp_path), "my-project")
        os.makedirs(os.path.join(project, ".claude"))
        os.makedirs(os.path.join(project, ".remember", "tmp"))
        os.makedirs(os.path.join(project, ".remember", "logs"))
        os.symlink(real_plugin, os.path.join(project, ".claude", "remember"))

        plugin = os.path.join(project, ".claude", "remember")
        import shutil
        shutil.copy(RESOLVE_PATHS_SH, os.path.join(plugin, "scripts", "resolve-paths.sh"))
        script = _write_test_script(plugin, "test-wrapper.sh",
            '#!/bin/bash\nsource "$(dirname "$0")/resolve-paths.sh" 2>&1\n'
            'echo "PROJECT_DIR=$PROJECT_DIR"\n'
            'echo "PIPELINE_DIR=$PIPELINE_DIR"\n'
        )

        env = {k: v for k, v in os.environ.items()
               if k not in ("CLAUDE_PROJECT_DIR", "CLAUDE_PLUGIN_ROOT")}
        result = subprocess.run(["bash", script], capture_output=True, text=True, env=env)
        assert result.returncode == 0, f"stderr: {result.stderr}"
        lines = result.stdout.strip().split("\n")
        resolved = dict(line.split("=", 1) for line in lines)
        # The resolved paths should point to the real locations
        assert os.path.isdir(resolved["PROJECT_DIR"])
        assert os.path.isdir(resolved["PIPELINE_DIR"])
        assert os.path.isfile(os.path.join(resolved["PIPELINE_DIR"], "pipeline", "llm.py"))
