import importlib.util
import os
import sys

if __package__ in (None, ""):
    # 以脚本方式直接运行（shims\apm.cmd / apm.ps1）：
    # 包目录名为 code，这里手工构建名为 apm 的常规包上下文，
    # 使包内全部相对导入（from . import ...）正常工作。
    _code_dir = os.path.dirname(os.path.abspath(__file__))
    _init_path = os.path.join(_code_dir, "__init__.py")
    _spec = importlib.util.spec_from_file_location(
        "apm", _init_path, submodule_search_locations=[_code_dir]
    )
    if _spec is None or _spec.loader is None:
        raise ImportError(f"无法加载 apm 包：{_init_path}")
    _apm = importlib.util.module_from_spec(_spec)
    sys.modules["apm"] = _apm
    _spec.loader.exec_module(_apm)

from apm.cli import main

if __name__ == "__main__":
    sys.exit(main())
