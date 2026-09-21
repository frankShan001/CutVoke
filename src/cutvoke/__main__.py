"""支持 python -m cutvoke 调用 CLI。"""

from .main import main
import sys

if __name__ == "__main__":
    sys.exit(main())