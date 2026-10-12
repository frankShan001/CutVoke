"""Source-checkout wrapper for the packaged person-model setup command."""

# Shared tooling paths; supports direct execution from any working directory.
import sys as _tool_sys
from pathlib import Path as _ToolPath
_TOOL_ROOT = next(p for p in _ToolPath(__file__).resolve().parents if (p / "pyproject.toml").is_file())
_tool_sys.path.insert(0, str(_TOOL_ROOT))
from scripts._paths import ARTIFACTS as _TOOL_ARTIFACTS
_tool_sys.path.insert(0, str(_TOOL_ROOT / "src"))

from cutvoke.core.person_model_setup import main, prepare_model

if __name__ == "__main__":
    raise SystemExit(main())
