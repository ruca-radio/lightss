"""Validate the JavaScript produced by the Python HTML templates."""

import re
import shutil
import subprocess

import pytest

import light_gui


@pytest.mark.parametrize("player_enabled", [False, True])
def test_rendered_main_page_scripts_parse(player_enabled):
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is required to validate generated JavaScript")

    html = light_gui.render_html(player_enabled=player_enabled)
    scripts = [
        script
        for script in re.findall(r"<script\b[^>]*>(.*?)</script>", html, re.DOTALL)
        if script.strip()
    ]
    assert scripts, "The main page must contain executable JavaScript"
    for index, script in enumerate(scripts):
        result = subprocess.run(
            [node, "--check"],
            input=script,
            text=True,
            capture_output=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Script {index}: {result.stderr}"
