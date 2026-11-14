package com.coco.agentandroid

import android.graphics.Rect
import android.view.accessibility.AccessibilityNodeInfo
import org.json.JSONArray
import org.json.JSONObject

/**
 * 把无障碍服务的节点树翻译成结构化 JSON。
 *
 * 字段名与 PC 端 agent_android/snapshot.py 完全一致，两端可以互换使用。
 * 关键点：id 按深度优先顺序稳定编号，PC 端一句 `tap n12` 就能引用任意控件。
 */
object UiTreeBuilder {

    /** 与 build() 相同的 DFS 顺序展开成列表，供按 id / 文本查找 */
    fun flatten(root: AccessibilityNodeInfo?): List<AccessibilityNodeInfo> {
        if (root == null) return emptyList()
        val result = ArrayList<AccessibilityNodeInfo>()
        fun walk(node: AccessibilityNodeInfo) {
            result.add(node)
            for (i in 0 until node.childCount) {
                val child = node.getChild(i) ?: continue
                walk(child)
            }
        }
        walk(root)
        return result
    }

    fun build(root: AccessibilityNodeInfo?, full: Boolean = false): JSONObject {
        val all = flatten(root)
        val array = JSONArray()
        for ((index, node) in all.withIndex()) {
            val bounds = Rect()
            node.getBoundsInScreen(bounds)
            val item = JSONObject().apply {
                put("id", "n$index")
                put("index", index)
                put("text", node.text?.toString() ?: "")
                put("content_desc", node.contentDescription?.toString() ?: "")
                put("resource_id", node.viewIdResourceName ?: "")
                put("class", node.className?.toString()?.substringAfterLast('.') ?: "")
                put("package", node.packageName?.toString() ?: "")
                put("bounds", JSONArray().apply {
                    put(bounds.left); put(bounds.top); put(bounds.right); put(bounds.bottom)
                })
                put("center", JSONArray().apply {
                    put((bounds.left + bounds.right) / 2)
                    put((bounds.top + bounds.bottom) / 2)
                })
                put("clickable", node.isClickable)
                put("long_clickable", node.isLongClickable)
                put("scrollable", node.isScrollable)
                put("editable", node.isEditable)
                put("enabled", node.isEnabled)
                put("checked", node.isChecked)
                put("focused", node.isFocused)
            }
            // 默认过滤纯布局容器，减少 Agent 的 token 消耗
            if (full || isMeaningful(node)) array.put(item)
        }
        return JSONObject()
            .put("source", "accessibility")
            .put("node_count", array.length())
            .put("total_node_count", all.size)
            .put("package", root?.packageName?.toString() ?: "")
            .put("nodes", array)
            .put("usage", "用 element.id 作为 tap/input 的 target；每次操作后重新 snapshot")
    }

    private fun isMeaningful(node: AccessibilityNodeInfo): Boolean =
        !node.text.isNullOrEmpty() ||
            !node.contentDescription.isNullOrEmpty() ||
            !node.viewIdResourceName.isNullOrEmpty() ||
            node.isClickable || node.isScrollable || node.isEditable || node.isChecked

    /**
     * 按 n12 / text=登录 / id=btn_login / 裸文本 查找节点。
     * 返回时自动上溯到最近的可点击祖先（Compose 的文字节点本身通常不可点击）。
     */
    fun find(root: AccessibilityNodeInfo?, target: String): AccessibilityNodeInfo? {
        if (target.isBlank()) return null
        val all = flatten(root)
        if (all.isEmpty()) return null
        val byId = Regex("n(\\d+)").matchEntire(target)
        val found: AccessibilityNodeInfo? = if (byId != null) {
            all.getOrNull(byId.groupValues[1].toIntOrNull() ?: -1)
        } else {
            val (key, value) = target.partitionBefore('=')
            // 注意：过滤只影响输出，id 始终按完整 DFS 编号，所以过滤后 id 会不连续
            val candidates = when (key) {
                "text" -> all.filter { it.text?.toString() == value || it.contentDescription?.toString() == value }
                "desc" -> all.filter { it.contentDescription?.toString() == value }
                "id" -> all.filter { it.viewIdResourceName?.endsWith("/$value") == true || it.viewIdResourceName == value }
                else -> {
                    val exact = all.filter { it.text?.toString() == target || it.contentDescription?.toString() == target }
                    exact.ifEmpty {
                        all.filter {
                            it.text?.toString()?.contains(target) == true ||
                                it.contentDescription?.toString()?.contains(target) == true
                        }
                    }
                }
            }
            candidates.minWithOrNull(
                compareBy<AccessibilityNodeInfo> { node -> if (node.isClickable) 0 else 1 }
                    .thenBy { node ->
                        val rect = Rect()
                        node.getBoundsInScreen(rect)
                        rect.top
                    }
            )
        }
        return found?.let { clickableAncestor(it) }
    }

    private fun clickableAncestor(node: AccessibilityNodeInfo): AccessibilityNodeInfo {
        var current: AccessibilityNodeInfo? = node
        repeat(8) {
            val cur = current ?: return node
            if (cur.isClickable || cur.isEditable) return cur
            current = cur.parent
        }
        return node
    }

    /** "text=登录" -> ("text", "登录")；没有等号时把整串当作裸文本 */
    private fun String.partitionBefore(delimiter: Char): Pair<String, String> {
        val idx = indexOf(delimiter)
        if (idx < 0) return "" to this
        return substring(0, idx).lowercase() to substring(idx + 1)
    }
}
