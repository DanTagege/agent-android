"""手工构建 wheel —— 零依赖，用于验证打包内容。

正常发布应该用 `python -m build`，但那需要 setuptools。这个脚本用标准库
直接拼出符合 PEP 427 的 wheel，可以 pip install 验证，也方便在离线环境
检查「哪些文件会被打进包里」。

用法：
    python scripts/build_wheel.py
    pip install --no-index --target /tmp/whl dist/*.whl
"""

from __future__ import annotations

import base64
import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "agent-android"
MODULE = "agent_android"
VERSION = "0.1.0"
DIST = f"{MODULE}-{VERSION}"
WHEEL_NAME = f"{DIST}-py3-none-any.whl"


def _digest(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    return "sha256=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode()


def _metadata() -> str:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return f"""Metadata-Version: 2.1
Name: {NAME}
Version: {VERSION}
Summary: Let AI coding agents see and control Android phones/emulators through adb
License: MIT
Requires-Python: >=3.9
Description-Content-Type: text/markdown
Classifier: Development Status :: 3 - Alpha
Classifier: Environment :: Console
Classifier: Intended Audience :: Developers
Classifier: License :: OSI Approved :: MIT License
Classifier: Programming Language :: Python :: 3
Classifier: Topic :: Software Development :: Testing
Project-URL: Source, https://github.com/DanTagege/agent-android

{readme}
"""


def build() -> Path:
    dist_dir = ROOT / "dist"
    dist_dir.mkdir(exist_ok=True)
    target = dist_dir / WHEEL_NAME

    files: list[tuple[str, bytes]] = []

    for path in sorted((ROOT / MODULE).glob("*.py")):
        files.append((f"{MODULE}/{path.name}", path.read_bytes()))

    # py.typed 必须一起装进去，否则 mypy / pyright 读不到类型注解
    marker = ROOT / MODULE / "py.typed"
    if marker.is_file():
        files.append((f"{MODULE}/py.typed", marker.read_bytes()))

    info = f"{DIST}.dist-info"
    files.append((f"{info}/METADATA", _metadata().encode("utf-8")))
    files.append((f"{info}/WHEEL", (
        "Wheel-Version: 1.0\n"
        "Generator: agent-android manual builder\n"
        "Root-Is-Purelib: true\n"
        "Tag: py3-none-any\n"
    ).encode("utf-8")))
    files.append((f"{info}/entry_points.txt", (
        "[console_scripts]\n"
        "agent-android = agent_android.cli:main\n"
    ).encode("utf-8")))
    files.append((f"{info}/licenses/LICENSE", (ROOT / "LICENSE").read_bytes()))

    record_lines = [f"{name},{_digest(data)},{len(data)}" for name, data in files]
    record_lines.append(f"{info}/RECORD,,")

    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in files:
            archive.writestr(name, data)
        archive.writestr(f"{info}/RECORD", "\n".join(record_lines) + "\n")

    return target


if __name__ == "__main__":
    path = build()
    print(f"生成 {path}（{path.stat().st_size // 1024} KB）")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    print(f"包含 {len(names)} 个文件：")
    for name in names:
        print("  " + name)
