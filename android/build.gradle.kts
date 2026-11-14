// 顶层构建文件：只声明插件版本，不在这里应用插件。
// 版本组合经过实机编译验证：Gradle 8.2.1 / AGP 8.2.1 / Kotlin 1.9.24 /
// Compose Compiler 1.5.14 / Compose BOM 2024.06.00 / compileSdk 34。
plugins {
    id("com.android.application") version "8.2.1" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}
