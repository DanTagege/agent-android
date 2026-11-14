#!/usr/bin/env bash
# Windows + Git Bash 下一键编译设备端伴侣 App。
# 复用本机已缓存的 Gradle，跳过 130MB 发行版下载；同时避开常见的沙箱构建坑。
set -e

export JAVA_HOME="D:/AndroidDev/jdk/jdk-17.0.20.1+1"
export ANDROID_HOME="D:/AndroidDev/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME"
export PATH="$JAVA_HOME/bin:$PATH"

# JAVA_TOOL_OPTIONS 会被 launcher 和 daemon 同时继承，比 -D 命令行参数可靠
export JAVA_TOOL_OPTIONS="-Dorg.gradle.native=false"
# Windows 版 Java 不认 /tmp 这类 POSIX 路径，两个变量都要设
export TMP="C:/Users/Quanzhenglin/AppData/Local/Temp"
export TEMP="$TMP"

GRADLE="$HOME/.gradle/wrapper/dists/gradle-8.2.1-bin/5hap6b9n41hkg4jeh2au2pllh/gradle-8.2.1/bin/gradle"
PROJECT_DIR="$(cd "$(dirname "$0")/../android" && pwd)"

cd "$PROJECT_DIR"
"$GRADLE" --no-daemon --console=plain --no-watch-fs "$@"

echo ""
echo "APK: $PROJECT_DIR/app/build/outputs/apk/debug/app-debug.apk"
