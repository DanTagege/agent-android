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
    {"repeat": {"times": 3, "steps": [{"swipe": "up"}, {"wait": 0.5}]}},
    {"if": {"text": "允许", "then": [{"tap": "允许"}], "else": [{"wait": 0.5}]}},
    {"while": {"text": "加载中", "max": 10, "steps": [{"wait": 1}]}},
    {"assert": {"text": ["欢迎", "首页"]}}
  ]
}
```

支持的步骤：launch / tap / input / swipe / key / scroll_to / wait /
assert / screenshot / snapshot / repeat / if / while。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from . import actions, bridge, snapshot
from .util import AgentAndroidError, log

ATOMIC_STEPS = (
    "launch", "tap", "input", "swipe", "key", "scroll_to",
    "wait", "assert", "screenshot", "snapshot",
)
CONTROL_STEPS = ("repeat", "if", "while")
STEP_KEYS = ATOMIC_STEPS + CONTROL_STEPS

MAX_DEPTH = 5  # 嵌套上限，防止写错导致无限递归


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
    _validate_steps(flow["steps"], depth=0)
    return flow


def _validate_steps(steps: list[Any], *, depth: int) -> None:
    if depth > MAX_DEPTH:
        raise AgentAndroidError(
            "流程嵌套太深", f"最多 {MAX_DEPTH} 层", code="flow_invalid_shape",
        )
    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict) or len(step) != 1:
            raise AgentAndroidError(
                f"第 {index} 步格式不对",
                '每一步必须是只含一个键的对象，例如 {"tap": "text=登录"}',
                code="flow_invalid_step", retryable=False,
            )
        key = next(iter(step))
        if key not in STEP_KEYS:
            raise AgentAndroidError(
                f"第 {index} 步是未知动作: {key}",
                f"可用: {', '.join(STEP_KEYS)}",
                code="flow_unknown_step", retryable=False,
            )
        value = step[key]
        if key in CONTROL_STEPS:
            if not isinstance(value, dict):
                raise AgentAndroidError(
                    f"第 {index} 步的 {key} 需要一个对象", code="flow_invalid_step",
                )
            for branch in ("steps", "then", "else"):
                if isinstance(value.get(branch), list):
                    _validate_steps(value[branch], depth=depth + 1)


def _do_input(step: dict[str, Any], device: str) -> dict[str, Any]:
    """输入。含非 ASCII 时自动改走伴侣 App（adb input text 不支持中文）。"""
    text = str(step.get("text", ""))
    target = step.get("target")
    if not text:
        raise AgentAndroidError("input 步骤缺少 text", code="flow_invalid_step")

    if all(ord(ch) < 128 for ch in text):
        return actions.input_text(text, device=device, target=target,
                                  clear=bool(step.get("clear", False)))

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


def _text_present(device: str, needle: str) -> bool:
    data = snapshot.take(device)
    return any(needle in (n.get("text") or "") for n in data["nodes"])


class _RunState:
    def __init__(self, *, policy: str, resume: set[int], dry_run: bool) -> None:
        self.policy = policy
        self.resume = resume
        self.dry_run = dry_run
        self.records: list[dict[str, Any]] = []
        self.failed = False

    def next_index(self) -> int:
        return len(self.records) + 1


def _lint_target(key: str, value: Any, device: str) -> dict[str, Any]:
    """dry-run 用：只检查 target 在当前界面能否解析，不执行动作。"""
    target = value if isinstance(value, str) else (
        value.get("target") if isinstance(value, dict) else None
    )
    if not target:
        return {"action": key, "ok": True, "detail": {"skipped": "该步骤没有 target"}}
    try:
        element = snapshot.find_element(str(target), device)
        return {"action": key, "ok": True,
                "detail": {"target": str(target), "resolved": element["id"],
                           "text": element.get("text", ""), "dry_run": True}}
    except AgentAndroidError as exc:
        return {"action": key, "ok": False,
                "detail": {"target": str(target), "error": exc.message,
                           "hint": exc.hint, "error_code": exc.code,
                           "dry_run": True}}


def _run_steps(steps: list[Any], device: str, state: _RunState,
               depth: int = 0) -> None:
    for step in steps:
        if state.failed and state.policy == "stop":
            return

        key = next(iter(step))
        value = step[key]

        if key in CONTROL_STEPS:
            _run_control(key, value, device, state, depth)
            continue

        index = state.next_index()
        if index in state.resume:
            state.records.append({
                "step": index, "action": key, "ok": True, "cost_s": 0.0,
                "detail": {"skipped": "上次已通过（--resume）"},
            })
            continue

        started = time.time()
        if state.dry_run and key in ("tap", "scroll_to", "input"):
            # 只解析 target，不真的点/输
            linted = _lint_target(key, value, device)
            ok, detail = linted["ok"], linted["detail"]
        elif state.dry_run and key in ("assert", "swipe", "key", "screenshot"):
            # 这些要么依赖真实执行结果，要么会改变界面，dry-run 下一律跳过
            ok, detail = True, {"skipped": f"dry-run 不执行 {key}"}
        else:
            try:
                detail = _exec_atomic(key, value, device)
                ok = True
            except AgentAndroidError as exc:
                ok, detail = False, {
                    "error": exc.message, "error_code": exc.code,
                    "hint": exc.hint, "actions": exc.actions,
                }
            except Exception as exc:  # 单步异常不能让整轮消失
                ok, detail = False, {"error": f"{type(exc).__name__}: {exc}"}

        state.records.append({
            "step": index, "action": key, "ok": ok,
            "cost_s": round(time.time() - started, 2), "detail": detail,
        })
        if not ok:
            state.failed = True


def _run_control(key: str, value: dict[str, Any], device: str,
                 state: _RunState, depth: int) -> None:
    if depth >= MAX_DEPTH:
        state.records.append({
            "step": state.next_index(), "action": key, "ok": False, "cost_s": 0.0,
            "detail": {"error": f"嵌套超过 {MAX_DEPTH} 层"},
        })
        state.failed = True
        return

    if key == "repeat":
        times = max(1, int(value.get("times", 1)))
        inner = value.get("steps", [])
        for round_index in range(times):
            log(f"repeat 第 {round_index + 1}/{times} 轮")
            _run_steps(inner, device, state, depth + 1)
            if state.failed and state.policy == "stop":
                return

    elif key == "if":
        needle = str(value.get("text", ""))
        branch = "then" if needle and _text_present(device, needle) else "else"
        log(f"if「{needle}」→ 走 {branch} 分支")
        _run_steps(value.get(branch, []), device, state, depth + 1)

    elif key == "while":
        needle = str(value.get("text", ""))
        limit = max(1, int(value.get("max", 10)))
        inner = value.get("steps", [])
        for _ in range(limit):
            if not needle or not _text_present(device, needle):
                return
            _run_steps(inner, device, state, depth + 1)
            if state.failed and state.policy == "stop":
                return


def _exec_atomic(key: str, value: Any, device: str) -> dict[str, Any]:
    if key == "wait":
        time.sleep(float(value))
        return {"slept": float(value)}
    if key == "launch":
        return actions.launch(str(value), device=device)
    if key == "tap":
        return actions.tap(str(value), device=device)
    if key == "input":
        return _do_input(value if isinstance(value, dict) else {"text": value}, device)
    if key == "swipe":
        return actions.swipe(str(value), device=device)
    if key == "key":
        return actions.key(str(value), device=device)
    if key == "scroll_to":
        return actions.scroll_to(str(value), device=device)
    if key == "screenshot":
        return actions.screenshot(str(value), device=device)
    if key == "snapshot":
        data = snapshot.take(device)
        return {"package": data["package"], "nodes": data["node_count"]}
    if key == "assert":
        return _do_assert(value if isinstance(value, dict) else {"text": value}, device)
    raise AgentAndroidError(f"未实现的步骤: {key}", code="flow_unknown_step")


def run_flow(flow: dict[str, Any], device: str, *,
             on_failure: str | None = None,
             resume: set[int] | None = None,
             dry_run: bool = False) -> dict[str, Any]:
    """逐步执行流程，返回含每步耗时与结果的报告。

    resume:  上次报告里已通过的步骤序号，这些步骤直接跳过。
    dry_run: 只校验 target 能否解析，不真的点击/输入。
    """
    policy = on_failure or flow.get("on_failure", "stop")
    state = _RunState(policy=policy, resume=resume or set(), dry_run=dry_run)
    started = time.time()

    _run_steps(flow["steps"], device, state)

    steps = state.records
    failed = [s for s in steps if not s["ok"]]
    return {
        "flow": flow.get("name", "未命名流程"),
        "device": device,
        "dry_run": dry_run,
        "total": len(flow["steps"]),
        "executed": len(steps),
        "passed": sum(1 for s in steps if s["ok"]),
        "failed": len(failed),
        "ok": not failed,
        "cost_s": round(time.time() - started, 1),
        "steps": steps,
    }


def run_flow_file(path: str, device: str, *, on_failure: str | None = None,
                  report: str | None = None, resume: str | None = None,
                  dry_run: bool = False) -> dict[str, Any]:
    flow = load_flow(path)

    passed: set[int] = set()
    if resume:
        previous = json.loads(Path(resume).expanduser().read_text(encoding="utf-8"))
        passed = {int(s["step"]) for s in previous.get("steps", []) if s.get("ok")}

    result = run_flow(flow, device, on_failure=on_failure,
                      resume=passed, dry_run=dry_run)

    if report:
        target = Path(report).expanduser().resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        result["report_path"] = str(target)

    result["summary"] = (
        ("[dry-run] " if dry_run else "")
        + f"{result['passed']}/{result['executed']} 步通过"
        + ("" if result["ok"] else "，失败项: " + ", ".join(
            f"#{s['step']} {s['action']}" for s in result["steps"] if not s["ok"]
        ))
    )
    return result
