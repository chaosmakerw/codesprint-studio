"""Local launcher; only the Python standard library is required."""
from pathlib import Path
import sys

if sys.version_info < (3, 12):
    print("拾阶需要 Python 3.12 或更高版本，请安装后重新启动。", file=sys.stderr)
    raise SystemExit(1)

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))
from projectstudy.server import main

if __name__ == "__main__":
    launch_args = sys.argv[1:]
    if "--no-open" in launch_args:
        launch_args = [arg for arg in launch_args if arg not in {"--no-open", "--open"}]
    raise SystemExit(main(launch_args))
