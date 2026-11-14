"""agent-android: 让 AI 编程助手通过 adb 观察并操作 Android 设备。

设计目标与 agent-browser / agent-desktop 一致：
  1. 每条命令都输出机器可读的 JSON，方便 Agent 直接解析；
  2. 把屏幕翻译成「带标签的元素表」，而不是让 Agent 看图猜坐标；
  3. 默认只在本机 127.0.0.1 提供服务，不暴露到公网。

本包只依赖 Python 标准库，第三方依赖为零。
"""

__version__ = "0.1.0"
__all__ = [
    "adb", "snapshot", "actions", "cli", "server",
    "emulators", "bridge", "diagnostics", "onboarding",
]

from .util import AgentAndroidError  # noqa: F401  统一异常类型，方便外部捕获
