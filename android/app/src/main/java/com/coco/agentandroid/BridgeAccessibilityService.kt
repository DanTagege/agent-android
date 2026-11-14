package com.coco.agentandroid

import android.accessibilityservice.AccessibilityService
import android.accessibilityservice.GestureDescription
import android.graphics.Path
import android.graphics.Rect
import android.os.Build
import android.os.Bundle
import android.util.DisplayMetrics
import android.view.WindowManager
import android.view.accessibility.AccessibilityEvent
import android.view.accessibility.AccessibilityNodeInfo
import java.io.File
import org.json.JSONObject

/**
 * 设备端能力提供者。
 *
 * 相比 PC 端 adb 方案，无障碍服务的两个关键优势：
 *   1. 可以直接 ACTION_SET_TEXT，支持中文输入（adb input text 不支持）；
 *   2. UI 层级来自当前实时窗口，比 uiautomator dump 更不容易拿到过期数据。
 *
 * 服务连接后自动在 127.0.0.1 启动 HTTP 服务，PC 端用 adb forward 访问。
 */
class BridgeAccessibilityService : AccessibilityService() {

    private var httpServer: BridgeHttpServer? = null

    override fun onServiceConnected() {
        super.onServiceConnected()
        BridgeState.service = this
        BridgeState.log("无障碍服务已连接")
        // 令牌同时写到外部目录和内部目录：logcat 的 main buffer 会滚动，
        // PC 端过一会儿就读不到了。文件是持久化的，两种路径互为兜底。
        runCatching {
            File(getExternalFilesDir(null) ?: filesDir, "token.txt")
                .writeText(BridgeState.token.value)
        }
        runCatching { File(filesDir, "token.txt").writeText(BridgeState.token.value) }
        httpServer?.stopServer()
        httpServer = BridgeHttpServer(BridgeState.PORT, this).apply { start() }
    }

    // 事件只用来感知窗口变化；实际快照由 Agent 主动拉取，避免无谓的推送开销
    override fun onAccessibilityEvent(event: AccessibilityEvent?) = Unit

    override fun onInterrupt() = Unit

    override fun onDestroy() {
        httpServer?.stopServer()
        httpServer = null
        BridgeState.service = null
        BridgeState.log("服务已停止")
        super.onDestroy()
    }

    /** 当前屏幕的元素表 */
    fun snapshot(full: Boolean = false): JSONObject =
        UiTreeBuilder.build(rootInActiveWindow, full)

    /** 执行一条动作，返回结果 JSON（字段名与 PC 端 CLI 对齐） */
    fun perform(body: JSONObject): JSONObject {
        val action = body.optString("action")
        val target = body.optString("target", "")
        val result = when (action) {
            "snapshot" -> snapshot(body.optBoolean("full", false))
            "tap" -> tap(target)
            "long_press" -> tap(target, longPress = true)
            "input" -> inputText(
                body.optString("text"),
                target,
                body.optBoolean("clear", false)
            )
            "swipe" -> swipe(body.optString("direction", "up"))
            "key" -> key(body.optString("name", "back"))
            else -> throw IllegalArgumentException("未知动作: $action")
        }
        return result.put("action", action).put("device", "local")
    }

    private fun tap(target: String, longPress: Boolean = false): JSONObject {
        // 空 target 会匹配到一堆无文字的布局节点，导致随机点击，必须先挡住
        require(target.isNotBlank()) { "target 不能为空，例如 text=登录 / n12 / 360,900" }
        // 坐标形式 "360,900"：直接派发一个短促手势
        val coord = Regex("(\\d+)\\s*,\\s*(\\d+)").matchEntire(target)
        if (coord != null) {
            val x = coord.groupValues[1].toInt()
            val y = coord.groupValues[2].toInt()
            gesture(x.toFloat(), y.toFloat(), x.toFloat(), y.toFloat(),
                if (longPress) 800L else 60L)
            return ok("target" to target, "at" to "$x,$y")
        }

        val node = UiTreeBuilder.find(rootInActiveWindow, target)
            ?: throw IllegalArgumentException("找不到元素: $target")
        val action = if (longPress) AccessibilityNodeInfo.ACTION_LONG_CLICK
                     else AccessibilityNodeInfo.ACTION_CLICK
        // 控件自身能响应就优先用语义点击；失败再退化为按坐标派发手势
        if (!node.performAction(action)) {
            val rect = Rect()
            node.getBoundsInScreen(rect)
            gesture(rect.centerX().toFloat(), rect.centerY().toFloat(),
                rect.centerX().toFloat(), rect.centerY().toFloat(),
                if (longPress) 800L else 60L)
        }
        return ok("target" to target, "text" to (node.text?.toString() ?: ""))
    }

    /** 用 ACTION_SET_TEXT 输入，支持中文、emoji 等 adb 无法投递的字符 */
    private fun inputText(text: String, target: String, clear: Boolean): JSONObject {
        val root = rootInActiveWindow
        val node = if (target.isNotEmpty()) {
            UiTreeBuilder.find(root, target)
        } else {
            UiTreeBuilder.flatten(root).firstOrNull { it.isEditable && it.isFocused }
                ?: UiTreeBuilder.flatten(root).firstOrNull { it.isEditable }
        } ?: throw IllegalArgumentException("找不到可输入的控件")

        val args = Bundle().apply {
            putCharSequence(AccessibilityNodeInfo.ACTION_ARGUMENT_SET_TEXT_CHARSEQUENCE, text)
        }
        node.performAction(AccessibilityNodeInfo.ACTION_FOCUS)
        val done = node.performAction(AccessibilityNodeInfo.ACTION_SET_TEXT, args)
        if (!done) throw IllegalArgumentException("输入失败：控件不接受 ACTION_SET_TEXT")
        // ACTION_SET_TEXT 本身就是「设置完整文本」，天然等价于覆盖；
        // clear 字段回显出来，让调用方知道语义已满足，不用猜。
        return ok("text" to text, "target" to target, "clear" to clear)
    }

    private fun swipe(direction: String): JSONObject {
        val (w, h) = screenSize()
        val cx = w / 2f
        val cy = h / 2f
        val points: List<Float> = when (direction) {
            "up" -> listOf(cx, h * 0.75f, cx, h * 0.25f)
            "down" -> listOf(cx, h * 0.25f, cx, h * 0.75f)
            "left" -> listOf(w * 0.8f, cy, w * 0.2f, cy)
            "right" -> listOf(w * 0.2f, cy, w * 0.8f, cy)
            else -> {
                // 与 CLI 对齐：也支持 "100,800 100,200" 这种自定义坐标
                val nums = Regex("\\d+").findAll(direction).map { it.value.toFloat() }.toList()
                require(nums.size == 4) {
                    "方向只支持 up/down/left/right，或 \"x1,y1 x2,y2\""
                }
                nums
            }
        }
        gesture(points[0], points[1], points[2], points[3], 400L)
        return ok("direction" to direction)
    }

    /** 取真实屏幕尺寸。displayMetrics 在部分 ROM 上已扣除导航栏，会让手势落点偏移。 */
    private fun screenSize(): Pair<Float, Float> {
        val wm = getSystemService(WindowManager::class.java)
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) {
            val bounds = wm.currentWindowMetrics.bounds
            bounds.width().toFloat() to bounds.height().toFloat()
        } else {
            val metrics = DisplayMetrics()
            @Suppress("DEPRECATION")
            wm.defaultDisplay.getRealMetrics(metrics)
            metrics.widthPixels.toFloat() to metrics.heightPixels.toFloat()
        }
    }

    private fun key(name: String): JSONObject {
        val global = when (name.lowercase()) {
            "back" -> GLOBAL_ACTION_BACK
            "home" -> GLOBAL_ACTION_HOME
            "recents" -> GLOBAL_ACTION_RECENTS
            else -> throw IllegalArgumentException("不支持的按键: $name")
        }
        performGlobalAction(global)
        return ok("name" to name)
    }

    /** dispatchGesture 需要配置 android:canPerformGestures="true" */
    private fun gesture(x1: Float, y1: Float, x2: Float, y2: Float, duration: Long) {
        val path = Path().apply {
            moveTo(x1, y1)
            // 单点点击时起止点相同，部分 ROM 不会识别成点击；给 1px 位移更稳
            if (x1 == x2 && y1 == y2) lineTo(x1 + 1f, y1 + 1f) else lineTo(x2, y2)
        }
        val stroke = GestureDescription.StrokeDescription(path, 0, duration)
        val description = GestureDescription.Builder().addStroke(stroke).build()
        // 返回 false 表示系统拒绝了这次手势（上一条还没结束、锁屏、或配置缺少
        // canPerformGestures）。不检查就会给 Agent「假成功」，整条任务链会偏航。
        val accepted = dispatchGesture(description, null, null)
        if (!accepted) {
            throw IllegalStateException(
                "手势被系统拒绝：可能上一条手势未结束、屏幕已锁定，或无障碍配置缺少 canPerformGestures"
            )
        }
    }

    private fun ok(vararg pairs: Pair<String, Any>): JSONObject = JSONObject().apply {
        put("ok", true)
        pairs.forEach { put(it.first, it.second) }
    }
}
