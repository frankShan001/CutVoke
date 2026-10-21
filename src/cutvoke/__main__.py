"""支持 python -m cutvoke 调用 CLI。"""

from cutvoke.interfaces.cli import main
import sys

if __name__ == "__main__":
    sys.exit(main())