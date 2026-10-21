"""A background renderer must not steal the enclosing MCP protocol input."""
import json
import subprocess
import sys


def test_preview_worker_preserves_parent_protocol_input(tmp_path):
    # Supply real inherited fd-0 bytes to a harness, then run a child that tries
    # to read them. A Popen mock would miss the protocol-corruption failure.
    script = r'''
import json, subprocess, sys, threading
from pathlib import Path
from cutvoke.rendering.preview_prepare import PreviewPreparation
root = Path(sys.argv[1])
reader = "import sys; from pathlib import Path; Path(sys.argv[1]).write_text(sys.stdin.read())"
worker = PreviewPreparation.__new__(PreviewPreparation)
worker._command([sys.executable, "-c", reader, str(root / "child-input")], threading.Event(), root)
print(json.dumps({"parent": sys.stdin.read(), "child": (root / "child-input").read_text()}))
'''
    request = '{"jsonrpc":"2.0","id":1,"method":"tools/call"}\n'
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path)],
                            input=request, capture_output=True, text=True,
                            check=True, timeout=15)
    assert json.loads(result.stdout) == {"parent": request, "child": ""}
