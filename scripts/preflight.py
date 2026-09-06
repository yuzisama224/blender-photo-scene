"""Report the local runtime required by the photo-scene workflow."""

import argparse
import importlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def inspect(blender=None):
    candidate = blender or os.environ.get("BLENDER_EXECUTABLE") or shutil.which("blender")
    if not candidate and sys.platform == "darwin":
        candidate = "/Applications/Blender.app/Contents/MacOS/Blender"
    issues = []
    version = None
    if candidate and Path(candidate).is_file():
        try:
            result = subprocess.run([candidate, "--version"], capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and result.stdout.startswith("Blender "):
                version = result.stdout.splitlines()[0]
            else:
                issues.append({"code": "blender_launch_failed", "message": "Blender 版本查询失败", "returncode": result.returncode})
        except (OSError, subprocess.TimeoutExpired) as error:
            issues.append({"code": "blender_launch_failed", "message": str(error)})
    else:
        issues.append({"code": "blender_missing", "message": "用 --blender 或 BLENDER_EXECUTABLE 指定 Blender 可执行文件"})
    packages = {}
    for module in ("numpy", "cv2", "yaml"):
        try:
            packages[module] = importlib.import_module(module).__version__
        except ImportError:
            packages[module] = None
            issues.append({"code": "python_dependency_missing", "message": module})
    return {
        "pass": not issues,
        "python": sys.executable,
        "blender": candidate,
        "blender_version": version,
        "python_packages": packages,
        "issues": issues,
        "mcp": {"status": "requires_agent_check", "action": "发现 Blender MCP 工具并执行只读场景查询，工具实际成功后才确认连接"},
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = inspect(args.blender)
    result = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        destination = Path(args.output)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(result + "\n", encoding="utf-8")
    print(result)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
