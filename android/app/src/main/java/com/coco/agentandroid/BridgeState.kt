package com.coco.agentandroid

import android.util.Log
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.snapshots.Snapshot
import java.time.LocalTime
import java.time.format.DateTimeFormatter

/**
 * 全局单例状态：服务引用、访问令牌、端口，以及可在界面上实时显示的操作日志。
 * 用 Compose 的 SnapshotState 保存，写入后界面会自动重组，无需手动刷新。
 */
object BridgeState {
    /** 设备端本地端口，PC 上用 `adb forward tcp:8377 tcp:8377` 映射 */
    const val PORT = 8377

    /**
     * 访问令牌：每次进程启动随机生成，显示在界面上。
     *
     * 这个 HTTP 接口等价于远程控制手机，而 Android 上任意应用都能连
     * 127.0.0.1 —— 没有令牌的话，设备上任何一个带 INTERNET 权限的 App
     * 都能读取屏幕内容、模拟点击。请求需带 `X-Agent-Token`。
     */
    val token = mutableStateOf(randomToken())

    /** 用 Compose 状态而不是 @Volatile：服务启动/停止后界面会自动更新 */
    val serverRunning = mutableStateOf(false)

    val logs = mutableStateListOf<String>()

    @Volatile
    var service: BridgeAccessibilityService? = null

    private val formatter = DateTimeFormatter.ofPattern("HH:mm:ss")

    init {
        // 令牌也打一份到 logcat，PC 端 `agent-android --bridge` 会自动读取，
        // 用户就不用盯着屏幕手抄。第三方应用读不到别的应用日志，边界是安全的。
        Log.i("AgentBridge", "TOKEN=${token.value}")
    }

    private fun randomToken(): String {
        // 去掉容易混淆的 0/O/1/I，方便用户手抄到命令行
        val alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        return (1..6).map { alphabet.random() }.joinToString("")
    }

    fun log(message: String) {
        val stamp = LocalTime.now().format(formatter)
        // 多个 HTTP 工作线程会同时写日志：synchronized 防止快照冲突，
        // withMutableSnapshot 保证 Compose 读到一致状态。
        synchronized(this) {
            Snapshot.withMutableSnapshot {
                logs.add(0, "[$stamp] $message")
                // 只保留最近 200 条，避免长时间运行后内存无限增长
                while (logs.size > 200) logs.removeAt(logs.lastIndex)
            }
        }
    }
}
