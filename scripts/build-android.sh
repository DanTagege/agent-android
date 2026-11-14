#!/usr/bin/env bash
# 编译设备端伴侣 App（Android / Kotlin / Compose）。
#
# 需要 JDK 17+ 与 Android SDK，两者都会先探测再使用：
#   1) 优先用已有的 JAVA_HOME / ANDROID_HOME
#   2) 否则在常见安装位置里找
#   3) 都找不到就给出明确提示，而不是让 Gradle 抛一屏术语
set -euo pipefail

java_major() {
    [ -x "$1/bin/java" ] || return 1
    "$1/bin/java" -version 2>&1 \
        | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p' \
        | head -1
}

find_jdk() {
    local candidates=() found
    # 自定义位置优先：JDK 装在任意目录时用 AGENT_ANDROID_JDK 指过来
    [ -n "${AGENT_ANDROID_JDK:-}" ] && candidates+=("$AGENT_ANDROID_JDK")
    [ -n "${JAVA_HOME:-}" ] && candidates+=("$JAVA_HOME")
    candidates+=(
        "${HOME:-/root}/.jdks"/*
        "/c/Program Files/Eclipse Adoptium"/*
        "/c/Program Files/Microsoft"/jdk-*
        "/c/Program Files/Java"/*
        "/usr/lib/jvm"/*
    )
    for found in "${candidates[@]}"; do
        [ -d "$found" ] || continue
        if [ "$(java_major "$found" || echo 0)" -ge 17 ] 2>/dev/null; then
            echo "$found"
            return 0
        fi
    done
    return 1
}

find_sdk() {
    local candidates=() found
    [ -n "${ANDROID_HOME:-}" ] && candidates+=("$ANDROID_HOME")
    [ -n "${ANDROID_SDK_ROOT:-}" ] && candidates+=("$ANDROID_SDK_ROOT")
    candidates+=(
        "${LOCALAPPDATA:-}/Android/Sdk"
        "${HOME:-/root}/Android/Sdk"
        "${HOME:-/root}/Library/Android/sdk"
        "/d/AndroidDev/android-sdk"
    )
    for found in "${candidates[@]}"; do
        [ -d "$found/platform-tools" ] && { echo "$found"; return 0; }
    done
    return 1
}

if ! JDK="$(find_jdk)"; then
    cat >&2 <<'EOF'
找不到 JDK 17+。

AGP 8.2 要求 Java 17，而当前 JAVA_HOME 指向的是别的版本。
装一个 JDK 17（Temurin 或 Microsoft OpenJDK）后重试，
或用环境变量指过去（两者都可以）：

  export AGENT_ANDROID_JDK="D:/somewhere/jdk-17"
  export JAVA_HOME="D:/somewhere/jdk-17"
EOF
    exit 1
fi
export JAVA_HOME="$JDK"

if ! SDK="$(find_sdk)"; then
    echo "找不到 Android SDK（需要 platform-tools）。设置 ANDROID_HOME 后重试。" >&2
    exit 1
fi
export ANDROID_HOME="$SDK"
export ANDROID_SDK_ROOT="$SDK"
export PATH="$JAVA_HOME/bin:$PATH"

echo "JDK: $JAVA_HOME"
echo "SDK: $ANDROID_HOME"

cd "$(dirname "$0")/../android"
sh ./gradlew --no-daemon --console=plain "$@"

echo ""
echo "APK: $(pwd)/app/build/outputs/apk/debug/app-debug.apk"
