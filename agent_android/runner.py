"""流程 runner：把一组操作写成 JSON，一次跑完并给出逐步报告。

为什么需要它：12 步冒烟脚本只能验证「项目本身没坏」，验证不了「你的 App
改了一行有没有坏」。把操作写成流程，就能一条命令做完回归。

流程格式（JSON，零第三方依赖）：

```json
{
  "name": "登录流程",
  "on_failure": "stop",
  "steps": [
    {"launch": "com.example.app"},
    {"wait": 1.5},
    {"tap": "text=登录"},
    {"input": {"text": "hello", "target": "text=用户名"}},
    {"swipe": "up"},
    {"assert": {"text": ["欢迎", "首页"]}}
  ]
}
```

支持的步骤：launch / tap / input / swipe / key / scroll_to / wait /
assert / screenshot / snapshot。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import actions, adb, bridge, snapshot
from .util import AgentAndroidError

STEP_KEYS = (
    "launch", "tap", "input", "swipe", "key", "scroll_to",
    "wait", "assert", "screenshot", "snapshot",
)


def load_flow(path: str | Path) -> dict[str, Any]:
    """读取并校验流程文件。错误信息要能直接指出哪一步写错了。"""
    file = Path(path).expanduser().resolve()
    if not file.is_file():
        raise AgentAndroidError(
            f"流程文件不存在: {file}",
            "示例见 examples/search.json",
            code="flow_not_found", retryable=False,
        )
    try:
        flow = json.loads(file.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise AgentAndroidError(
            f"流程文件不是合法 JSON: {exc}",
            "注意 JSON 不支持注释和末尾多余逗号",
            code="flow_invalid_json", retryable=False,
        ) from exc

    if not isinstance(flow, dict) or not isinstance(flow.get("steps"), list):
        raise AgentAndroidError(
            "流程缺少 steps 数组",
            '结构示例: {"name": "...", "steps": [{"tap": "text=登录"}]}',
            code="flow_invalid_shape", retryable=False,
        )
    if not flow["steps"]:
        raise AgentAndroidError("流程里没有任何步骤", code="flow_empty", retryable=False)

    for index, step in enumerate(flow["steps"]):
        if not isinstance(step, dict) or len(step) != 1:
            raise AgentAndroidError(
                f"第 {index + 1} 步格式不对",
                "每一步必须是只含一个键的对象，例如 {\"tap\": \"text=登录\"}",
                code="flow_invalid_step", retryable=False,
            )
        key = next(iter(step))
        if key not in STEP_KEYS:
            raise AgentAndroidError(
                f"第 {index + 1} 步是未知动作: {key}",
                f"可用: {', '.join(STEP_KEYS)}",
                code="flow_unknown_step", retryable=False,
            )
    return flow


def _do_input(step: dict[str, Any], device: str) -> dict[str, Any]:
    """输入。含非 ASCII 时自动改走伴侣 App（adb input text 不支持中文）。"""
    text = str(step.get("text", ""))
    target = step.get("target")
    if not text:
        raise AgentAndroidError("input 步骤缺少 text", code="flow_invalid_step")

    if all(ord(ch) < 128 for ch in text):
        return actions.input_text(text, device=device, target=target,
                                  clear=bool(step.get("clear", False)))

    # 中文：先按坐标聚焦（CLI 的编号），再用无障碍写入焦点
    if target:
        actions.tap(str(target), device=device)
        time.sleep(0.5)
    token = bridge.read_token(device)
    return bridge.action(device, {"action": "input", "text": text}, token=token)


def _do_assert(cond: dict[str, Any], device: str) -> dict[str, Any]:
    """断言。一次 snapshot 同时检查多个条件，避免重复抓屏。"""
    data = snapshot.take(device)
    texts = [n["text"] for n in data["nodes"] if n["text"]]
    problems: list[str] = []

    wanted = cond.get("text")
    if wanted:
        needles = [wanted] if isinstance(wanted, str) else list(wanted)
        for needle in needles:
            if not any(str(needle) in t for t in texts):
                problems.append(f"界面没有「{needle}」")

    if "package" in cond and data["package"] != cond["package"]:
        problems.append(f"前台包名是 {data['package']}，不是 {cond['package']}")

    minimum = cond.get("node_count_min")
    if minimum is not None and data["node_count"] < int(minimum):
        problems.append(f"节点数 {data['node_count']} < {minimum}")

    if problems:
        raise AgentAndroidError(
            "；".join(problems),
            f"当前界面文字: {texts[:6]}",
            code="assert_failed", retryable=False,
            actions=["agent-android snapshot  # 看实际界面"],
        )
    return {"action": "assert", "passed": True, "checked": list(cond)}


def run_flow(flow: dict[str, Any], device: str, *,
             on_failure: str | None = None) -> dict[str, Any]:
    """逐步执行流程，返回含每步耗时与结果的报告。"""
    policy = on_failure or flow.get("on_failure", "stop")
    steps: list[dict[str, Any]] = []
    started = time.time()

    for index, step in enumerate(flow["steps"], start=1):
        key = next(iter(step))
        value = step[key]
        step_started = time.time()
        detail: dict[str, Any] = {}

        try:
            if key == "wait":
                time.sleep(float(value))
                detail = {"slept": float(value)}
            elif key == "launch":
                detail = actions.launch(str(value), device=device)
            elif key == "tap":
                detail = actions.tap(str(value), device=device)
            elif key == "input":
                detail = _do_input(value if isinstance(value, dict) else {"text": value}, device)
            elif key == "swipe":
                detail = actions.swipe(str(value), device=device)
            elif key == "key":
                detail = actions.key(str(value), device=device)
            elif key == "scroll_to":
                detail = actions.scroll_to(str(value), device=device)
            elif key == "screenshot":
                detail = actions.screenshot(str(value), device=device)
            elif key == "snapshot":
                data = snapshot.take(device)
                detail = {"package": data["package"], "nodes": data["node_count"]}
            elif key == "assert":
                detail = _do_assert(value if isinstance(value, dict) else {"text": value}, device)
            ok = True
        except AgentAndroidError as exc:
            ok, detail = False, {
                "error": exc.message, "error_code": exc.code,
                "hint": exc.hint, "actions": exc.actions,
            }
        except Exception as exc:  # 单步异常不能让整轮消失
            ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}

        record = {
            "step": index,
            "action": key,
            "ok": ok,
            "cost_s": round(time.time() - step_started, 2),
            "detail": detail,
        }
        steps.append(record)
        if not ok and policy == "stop":
            break

    passed = sum(1 for s in steps if s["ok"])
    failed = [s for s in steps if not s["ok"]]
    return {
        "flow": flow.get("name", "未命名流程"),
        "device": device,
        "total": len(flow["steps"]),
        "executed": len(steps),
        "passed": passed,
        "failed": len(failed),
        "ok": not failed and len(steps) == len(flow["steps"]),
        "cost_s": round(time.time() - started, 1),
        "steps": steps,
    }


def run_flow_file(path: str, device: str, *, on_failure: str | None = None,
                  report: str | None = None) -> dict[str, Any]:
    flow = load_flow(path)
    result = run_flow(flow, device, on_failure=on_failure)
    if report:
        target = Path(report).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        result["report_path"] = str(target)
    # 附一眼能看懂的结论，Agent 不用自己数
    result["summary"] = (
        f"{result['passed']}/{result['total']} 步通过"
        + ("" if result["ok"] else "，失败项: " + ", ".join(
            f"#{s['step']} {s['action']}" for s in result["steps"] if not s["ok"]
        ))
    )
    return result
