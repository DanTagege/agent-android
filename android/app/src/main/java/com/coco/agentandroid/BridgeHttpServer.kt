package com.coco.agentandroid

import org.json.JSONArray
import org.json.JSONObject
import java.io.BufferedOutputStream
import java.io.ByteArrayOutputStream
import java.io.InputStream
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.net.Socket

/**
 * 极简 HTTP/1.1 服务，只依赖 JDK 标准库，不引入第三方网络框架。
 *
 * 安全：仅绑定 127.0.0.1。PC 端通过 `adb forward` 访问，
 * 等价于把手机控制权交给本机进程 —— 绝不要改成 0.0.0.0。
 *
 * 路由：
 *   GET  /health      服务状态
 *   GET  /snapshot    当前屏幕 UI 元素表
 *   GET  /logs        最近操作日志
 *   POST /action      执行 tap / input / swipe / key / snapshot
 */
class BridgeHttpServer(
    private val port: Int,
    private val service: BridgeAccessibilityService
) : Thread("agent-android-http") {

    @Volatile
    private var running = true
    private var serverSocket: ServerSocket? = null

    override fun run() {
        try {
            serverSocket = openSocket()
            BridgeState.serverRunning.value = true
            BridgeState.log("HTTP 服务已启动 127.0.0.1:$port")
            while (running) {
                // accept() 阻塞；每个连接开一个线程处理，避免慢客户端卡住整个服务
                val client = serverSocket?.accept() ?: break
                Thread { handle(client) }.start()
            }
        } catch (e: Exception) {
            BridgeState.log("HTTP 服务异常: ${e.message}")
        } finally {
            BridgeState.serverRunning.value = false
            runCatching { serverSocket?.close() }
            BridgeState.log("HTTP 服务已停止")
        }
    }

    fun stopServer() {
        running = false
        runCatching { serverSocket?.close() }
    }

    /** 按字节读一行。HTTP 请求头是 ASCII，按字节读不会碰到多字节字符问题。 */
    private fun readLine(input: InputStream): String? {
        val buffer = ByteArrayOutputStream()
        var b = input.read()
        if (b == -1) return null
        while (b != -1 && b != '\n'.code) {
            if (b != '\r'.code) buffer.write(b)
            b = input.read()
        }
        return buffer.toString("UTF-8")
    }

    /**
     * 按 Content-Length 读满字节再整体解码。
     * 不能用字符流读：Content-Length 是字节数，而一个中文字符占 3 字节，
     * 按字符数读会一直等不到足够的字符，直到 socket 超时（中文输入必现）。
     */
    private fun readBody(input: InputStream, length: Int): String {
        val data = ByteArray(length)
        var read = 0
        while (read < length) {
            val n = input.read(data, read, length - read)
            if (n < 0) break
            read += n
        }
        return String(data, 0, read, Charsets.UTF_8)
    }

    /** 绑定 127.0.0.1；reuseAddress + 重试，避免关掉服务再打开时端口还在 TIME_WAIT。 */
    private fun openSocket(): ServerSocket {
        var lastError: Exception? = null
        repeat(3) { attempt ->
            try {
                return ServerSocket().apply {
                    reuseAddress = true
                    bind(InetSocketAddress(InetAddress.getByName("127.0.0.1"), port), 8)
                }
            } catch (e: Exception) {
                lastError = e
                Thread.sleep(300L * (attempt + 1))
            }
        }
        throw lastError ?: IllegalStateException("无法绑定端口 $port")
    }

    private fun handle(socket: Socket) {
        try {
            socket.use { s ->
                s.soTimeout = 10_000
                val input = s.getInputStream()
                val requestLine = readLine(input) ?: return
                val parts = requestLine.split(" ")
                val method = parts.getOrNull(0) ?: ""
                val path = parts.getOrNull(1) ?: "/"

                // 逐行读请求头，拿到 Content-Length 后才能准确读取 POST body
                val headers = HashMap<String, String>()
                while (true) {
                    val line = readLine(input) ?: break
                    if (line.isEmpty()) break
                    val idx = line.indexOf(':')
                    if (idx > 0) {
                        headers[line.substring(0, idx).trim().lowercase()] = line.substring(idx + 1).trim()
                    }
                }
                // 限制请求体大小：恶意 Content-Length 会直接分配巨大数组把进程撑爆
                val length = (headers["content-length"]?.toIntOrNull() ?: 0)
                    .coerceIn(0, 1_048_576)
                val body = if (length > 0) readBody(input, length) else ""

                // /health 免令牌，方便探活；其余接口都必须带 X-Agent-Token。
                // 不加这一层的话，设备上任意应用都能读屏幕、模拟点击。
                val pathOnly = path.substringBefore('?')
                val tokenOk = headers["x-agent-token"] == BridgeState.token.value
                val (status, payload) = if (pathOnly != "/health" && !tokenOk) {
                    "401 Unauthorized" to error(
                        "未授权",
                        "请求头带 X-Agent-Token，值见 App 界面（或设置 AGENT_ANDROID_TOKEN）"
                    )
                } else {
                    route(method, path, body)
                }
                val bytes = payload.toString().toByteArray(Charsets.UTF_8)
                val out = BufferedOutputStream(s.getOutputStream())
                val head = "HTTP/1.1 $status\r\n" +
                    "Content-Type: application/json; charset=utf-8\r\n" +
                    "Content-Length: ${bytes.size}\r\n" +
                    "Connection: close\r\n\r\n"
                out.write(head.toByteArray(Charsets.UTF_8))
                out.write(bytes)
                out.flush()
            }
        } catch (e: Exception) {
            BridgeState.log("处理请求失败: ${e.message}")
        }
    }

    private fun route(method: String, rawPath: String, body: String): Pair<String, JSONObject> {
        val path = rawPath.substringBefore('?')
        return try {
            when {
                method == "GET" && path == "/health" -> "200 OK" to ok(
                    "service" to "connected",
                    "port" to port,
                    "server_running" to BridgeState.serverRunning.value
                )
                method == "GET" && path == "/snapshot" -> {
                    BridgeState.log("GET /snapshot")
                    "200 OK" to service.snapshot(full = rawPath.contains("full=1"))
                }
                method == "GET" && path == "/logs" ->
                    "200 OK" to ok("logs" to JSONArray(BridgeState.logs))
                method == "POST" && path == "/action" -> {
                    val action = JSONObject(body).optString("action")
                    BridgeState.log("POST /action $action")
                    "200 OK" to service.perform(JSONObject(body))
                }
                else -> "404 Not Found" to error("未知路径", "可用: /health /snapshot /logs /action")
            }
        } catch (e: Exception) {
            BridgeState.log("动作失败: ${e.message}")
            "400 Bad Request" to error(e.message ?: "执行失败", "检查请求体字段是否完整")
        }
    }

    private fun ok(vararg pairs: Pair<String, Any>): JSONObject = JSONObject().apply {
        put("ok", true)
        pairs.forEach { put(it.first, it.second) }
    }

    private fun error(message: String, hint: String = ""): JSONObject = JSONObject()
        .put("ok", false)
        .put("error", message)
        .put("hint", hint)
}
