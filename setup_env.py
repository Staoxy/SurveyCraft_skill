"""SurveyCraft 环境引导：依赖自检 / 可选创建 venv。

用法：
    python setup_env.py                # 自检当前解释器
    python setup_env.py --create-venv  # 创建 .venv 并安装 requirements.txt
"""
import argparse
import importlib
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
REQUIRED = {
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "docx": "python-docx",
    "yaml": "PyYAML",
    "jsonschema": "jsonschema",
    "matplotlib": "matplotlib",
    "pytest": "pytest",
}


def self_check() -> int:
    ok = True
    print(f"Python {sys.version.split()[0]} @ {sys.executable}")
    for module, pkg in REQUIRED.items():
        try:
            mod = importlib.import_module(module)
            ver = getattr(mod, "__version__", "OK")
            print(f"  [OK] {pkg:<12} {ver}")
        except ImportError:
            ok = False
            print(f"  [MISSING] {pkg}  (pip install {pkg})")
    ok = _check_cjk_font() and ok
    print("环境自检通过" if ok else "环境不完整，请安装缺失依赖后重试")
    return 0 if ok else 1


def _check_cjk_font() -> bool:
    try:
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib import font_manager
        names = {f.name for f in font_manager.fontManager.ttflist}
        for cand in ("Microsoft YaHei", "SimHei", "PingFang SC", "Noto Sans CJK SC"):
            if cand in names:
                print(f"  [OK] matplotlib 中文字体: {cand}")
                return True
        print("  [WARN] 未找到中文字体（微软雅黑/黑体等），图表中文可能乱码")
        return False
    except Exception as exc:  # pragma: no cover
        print(f"  [WARN] matplotlib 字体检查失败: {exc}")
        return True


def create_venv() -> int:
    venv_dir = ROOT / ".venv"
    if not venv_dir.exists():
        print(f"创建虚拟环境 {venv_dir} ...")
        venv.EnvBuilder(with_pip=True).create(venv_dir)
    py = venv_dir / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    print("安装依赖 ...")
    subprocess.check_call([str(py), "-m", "pip", "install", "-r", str(ROOT / "requirements.txt")])
    print(f"完成。使用: {py}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="SurveyCraft 环境引导")
    parser.add_argument("--create-venv", action="store_true", help="创建 .venv 并安装依赖")
    args = parser.parse_args()
    sys.exit(create_venv() if args.create_venv else self_check())
