"""最小 pytest 兼容运行器：在没有 pytest 的环境中执行 tests/ 下的断言函数。

以真实包名 ``astrbot_plugin_qq_session_manager`` 导入插件，模拟 AstrBot 的加载方式，
这样包内相对导入（如 ``..core.exceptions``）才能被正确解析。
"""
from __future__ import annotations

import importlib.util
import inspect
import sys
import tempfile
import traceback
from pathlib import Path

PLUGINS_ROOT = Path(__file__).resolve().parent.parent.parent
PLUGIN_NAME = "astrbot_plugin_qq_session_manager"
PLUGIN_ROOT = PLUGINS_ROOT / PLUGIN_NAME

sys.path.insert(0, str(PLUGINS_ROOT))
sys.path.insert(0, str(PLUGIN_ROOT))


def _load(path: Path):
    spec = importlib.util.spec_from_file_location(f"{PLUGIN_NAME}_tests.{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _TmpPath:
    def __init__(self, base: Path):
        self._base = base

    def __truediv__(self, other):
        return self._base / other

    def mkdir(self, *args, **kwargs):
        self._base.mkdir(parents=True, exist_ok=True)
        return self._base


def main() -> int:
    failures: list[str] = []
    total = 0
    for test_file in sorted((PLUGIN_ROOT / "tests").glob("test_*.py")):
        module = _load(test_file)
        for name, fn in sorted(vars(module).items()):
            if not name.startswith("test_") or not inspect.isfunction(fn):
                continue
            params = inspect.signature(fn).parameters
            total += 1
            with tempfile.TemporaryDirectory() as tmp:
                kwargs = {}
                if "tmp_path" in params:
                    kwargs["tmp_path"] = _TmpPath(Path(tmp))
                try:
                    fn(**kwargs)
                except Exception:
                    failures.append(f"{test_file.name}::{name}\n{traceback.format_exc()}")
    print(f"collected {total} tests")
    for failure in failures:
        print("FAILED " + failure)
    print(f"failed {len(failures)} / {total}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
