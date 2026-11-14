"""支持 `python -m agent_android ...` 方式调用。"""

from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
