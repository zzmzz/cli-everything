"""E2E tests for {{ site_name }} CLI."""

import json
import os
import subprocess
import sys

import pytest


def _resolve_cli(name):
    """Resolve installed CLI command; falls back to python -m for dev.

    Set env CLI_ANYTHING_FORCE_INSTALLED=1 to require the installed command.
    """
    import shutil

    force = os.environ.get("CLI_ANYTHING_FORCE_INSTALLED", "").strip() == "1"
    path = shutil.which(name)
    if path:
        print(f"[_resolve_cli] Using installed command: {path}")
        return [path]
    if force:
        raise RuntimeError(f"{name} not found in PATH. Install with: pip install -e .")
    module = "cli_anything.{{ site_name }}.{{ site_name }}_cli"
    print(f"[_resolve_cli] Falling back to: {sys.executable} -m {module}")
    return [sys.executable, "-m", module]


class TestCLISubprocess:
    """Test the CLI as a subprocess."""

    CLI_BASE = _resolve_cli("cli-anything-{{ site_name }}")

    def _run(self, args, check=True):
        return subprocess.run(
            self.CLI_BASE + args,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_help(self):
        result = self._run(["--help"])
        assert result.returncode == 0
        assert "{{ site_name }}" in result.stdout.lower() or "Usage" in result.stdout

    def test_json_flag(self):
        result = self._run(["--help"])
        assert "--json" in result.stdout

    {% for domain in domains %}
    def test_{{ domain.name }}_help(self):
        result = self._run(["{{ domain.name }}", "--help"])
        assert result.returncode == 0

    {% endfor %}


{% if has_live_tests %}
@pytest.mark.skipif(
    not os.environ.get("{{ env_prefix }}_COOKIE") and not os.environ.get("{{ env_prefix }}_TOKEN"),
    reason="No auth credentials in environment",
)
class TestLiveAPI:
    """E2E tests against the real API (requires valid credentials)."""

    {% for test in live_tests %}
    def test_{{ test.name }}(self):
        """{{ test.description }}"""
        {{ test.code }}

    {% endfor %}
{% endif %}
