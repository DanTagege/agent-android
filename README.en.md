# agent-android

> Give your AI coding agent eyes and hands on Android.

[![CI](https://github.com/DanTagege/agent-android/actions/workflows/ci.yml/badge.svg)](https://github.com/DanTagege/agent-android/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.9+](https://img.shields.io/badge/Python-3.9%2B-blue.svg)](pyproject.toml)
[![Platform](https://img.shields.io/badge/platform-Android-3ddc84.svg)](https://www.android.com/)

**English** | [简体中文](README.md)

Your agent can read a browser's DOM. It cannot read a phone screen.

`adb` hands you a screenshot or a wall of XML; the model ends up guessing
coordinates. `agent-android` turns the Android UI hierarchy into a labeled
element table — `id`, text, bounds, clickable — so an agent reads a phone the
way it reads a web page, then acts with one command. The loop is always
**look → act → look again**.

No Appium, no client libraries, no third-party Python packages.

## Install

```bash
git clone https://github.com/DanTagege/agent-android.git
cd agent-android

python -m agent_android setup     # no device? it tells you what to do next
python -m agent_android doctor    # 8 environment checks
```

Requires Python 3.9+ and an Android device or emulator with USB debugging on.
Nothing else.

## 30-second tour

```bash
python -m agent_android snapshot              # structured view of the screen
python -m agent_android tap text=Settings     # tap by text, id, or coordinate
python -m agent_android input "hello"         # ASCII via adb, CJK via companion app
python -m agent_android scroll-to 关于手机     # scroll until the target appears
python -m agent_android screenshot -o shot.png
python -m agent_android crashes               # process + exception + stack
```

Every command prints **one JSON object** to stdout; human logs go to stderr,
so an agent never has to parse prose.

## Commands

| Command | Purpose |
| --- | --- |
| `setup` / `doctor` / `recover` | Get connected, diagnose, self-repair |
| `snapshot [--compact] [--prefer]` | Screen → labeled elements |
| `tap` / `long-press` / `double-tap` / `drag` | Pointing |
| `input` / `swipe` / `scroll-to` / `key` | Editing and navigation |
| `install` / `launch` | App lifecycle |
| `screenshot` / `logcat` / `crashes` | Evidence |
| `notify list\|tap\|shade\|collapse` | Notification shade |
| `run <flow.json>` | Batch flows with assertions |
| `mcp` | MCP server over stdio |
| `serve` | Local HTTP API with token auth |

## Targets

`tap` accepts four forms, so the agent never needs to compute pixels:

```bash
agent-android tap n12              # node id from snapshot
agent-android tap text=Login       # exact text
agent-android tap id=btn_login     # resource-id
agent-android tap 360,900          # raw coordinate
agent-android tap text=OK@2        # second match
```

Text nodes are usually not clickable; the tool walks up to the nearest
clickable ancestor automatically.

## Self-healing

Android is a flaky target. Every error carries a stable `error_code`, a
`retryable` flag, and an `actions` array of commands you can run directly.

| Failure | What happens |
| --- | --- |
| adb serial goes stale | Reconnects and switches to whatever is online, retries once |
| `uiautomator` never idles | Falls back to the accessibility service automatically |
| Companion app killed | Re-enables it, relaunches, refreshes token, retries |
| Screen off | `wake` lights it without bypassing the lock screen |
| Storage full | `doctor` flags it before `dump` fails mysteriously |

## MCP

```bash
agent-android mcp
```

A stdio MCP server exposing 12 tools (`android_snapshot`, `android_tap`,
`android_input`, `android_screenshot`, `android_run_flow`, …). Point your
client at it:

```json
{
  "mcpServers": {
    "agent-android": {
      "command": "python",
      "args": ["-m", "agent_android", "mcp"]
    }
  }
}
```

`android_screenshot` returns image content, so the model can actually look.

## Batch runs

```json
{
  "name": "read device info",
  "steps": [
    {"launch": "com.android.settings"},
    {"wait": 2},
    {"scroll_to": "About phone"},
    {"tap": "About phone"},
    {"assert": {"text": ["Model"]}},
    {"screenshot": "about.png"}
  ]
}
```

```bash
agent-android run examples/settings-info.json --report report.json
```

Ten ready-to-run flows live in [`examples/`](examples).

## Companion app (optional)

A small Kotlin + Compose app under [`android/`](android) that runs an
accessibility service and a loopback-only HTTP server. It unlocks two things
plain adb cannot do:

1. **CJK input** — `adb input text` is ASCII-only.
2. **Snapshots on animated screens** — `uiautomator` waits for idle; the
   accessibility tree does not.

The HTTP server listens on `127.0.0.1` only and requires an `X-Agent-Token`
header. The token is shown in the app and also written to a file the CLI reads.

## Known limitations

- WebView HTML inputs may ignore `ACTION_SET_TEXT` when page JS owns the value;
  type into the address bar instead.
- Some ROMs have click targets that differ from their reported `bounds`.
- `long-press` is not implemented on the companion-app channel (adb path is fine).
- Custom-drawn surfaces (games, Unity) expose no element tree; use screenshots
  plus coordinates.
- CAPTCHAs and lock-screen bypass are intentionally out of scope.

## Not supported

| Case | Why | Alternative |
| --- | --- | --- |
| **iOS / iPhone** | Only the Android adb + accessibility channels are implemented | [idb](https://github.com/facebook/idb), [Maestro](https://maestro.mobile.dev) |
| CAPTCHAs / slider puzzles | Out of scope by policy | Stop the flow and hand it to a human |
| Lock-screen PIN | Never bypasses system security | `wake` only lights the screen |
| Games / Unity / custom-drawn | No element tree to read | `screenshot` + coordinates; `snapshot` returns `likely_custom_drawn: true` |
| The other half of a split screen | Only the focused window is readable | Bring the target window to front first |
| Two agents on one device | They overwrite each other's screen | Add `--lock 30` to serialize |

## Commands that change the device

Read-only by default; these actually mutate state:

| Command | What changes |
| --- | --- |
| `tap` / `long-press` / `double-tap` / `drag` / `swipe` / `input` / `key` | UI state |
| `install` | Installs or overwrites an APK |
| `launch` | Starts the app and brings it to front |
| `recover` | Restarts adb, reconnects emulators, clears the local snapshot cache, **rewrites accessibility settings and relaunches the companion app** |
| First `--bridge` call | Writes `settings put secure enabled_accessibility_services` if the service is off |
| `--lock` | Creates/removes a lock file under `~/.agent_android/` |

## Running in CI

No physical device needed. [`.github/workflows/e2e.yml`](.github/workflows/e2e.yml)
is a complete template built on the official emulator runner:

```yaml
- uses: reactivecircus/android-emulator-runner@v2
  with:
    api-level: 33
    target: google_apis
    script: |
      python -m agent_android doctor
      python -m agent_android run examples/settings-info.json
```

For cloud devices, point `--device` at whatever `adb connect` returned.
With no device at all, `doctor` reports it plainly instead of dumping adb errors.

## Documentation

- [中文 README](README.md) — full manual
- [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) — architecture, measurements, boundaries
- [CHANGELOG.md](CHANGELOG.md)
- [skills/agent-android/SKILL.md](skills/agent-android/SKILL.md) — instructions for coding agents

## License

MIT
