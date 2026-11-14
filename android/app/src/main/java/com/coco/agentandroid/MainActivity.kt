package com.coco.agentandroid

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.provider.Settings
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * 设备端控制台：显示服务状态、adb forward 命令和最近操作日志。
 * 真正的「给 AI 用」的能力在 BridgeAccessibilityService 里，这里只是让人
 * 能一眼看出服务有没有起来、以及怎么连。
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent {
            MaterialTheme(
                colorScheme = lightColorScheme(primary = Color(0xFF534AB7))
            ) {
                BridgeScreen()
            }
        }
    }
}

@androidx.compose.runtime.Composable
fun BridgeScreen() {
    val context = LocalContext.current
    // 点「刷新状态」时触发重组，重新读取系统里的无障碍开关
    val tick = remember { mutableStateOf(0) }
    val connected = remember(tick.value) {
        isAccessibilityEnabled(context) && BridgeState.service != null
    }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp)
    ) {
        Spacer(Modifier.height(8.dp))
        Text(stringResource(R.string.title), fontSize = 24.sp)
        Text(
            stringResource(R.string.subtitle),
            fontSize = 13.sp,
            color = MaterialTheme.colorScheme.onSurfaceVariant
        )

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text(
                    if (connected) stringResource(R.string.status_connected)
                    else stringResource(R.string.status_disconnected),
                    fontSize = 16.sp,
                    color = if (connected) Color(0xFF0F6E56) else Color(0xFFA32D2D)
                )
                Spacer(Modifier.height(6.dp))
                Text("HTTP  127.0.0.1:${BridgeState.PORT}", fontFamily = FontFamily.Monospace, fontSize = 12.sp)
                Text(
                    "server: ${if (BridgeState.serverRunning.value) "running" else "stopped"}",
                    fontFamily = FontFamily.Monospace, fontSize = 12.sp
                )
                // 令牌要显眼：PC 端不带它会被 401，用户得能一眼抄走
                Text(
                    "token  ${BridgeState.token.value}",
                    fontFamily = FontFamily.Monospace, fontSize = 12.sp,
                    color = Color(0xFF534AB7)
                )
            }
        }

        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Button(onClick = {
                context.startActivity(Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS))
            }) { Text(stringResource(R.string.open_settings)) }
            OutlinedButton(onClick = { tick.value++ }) {
                Text(stringResource(R.string.refresh))
            }
        }

        // 自测输入框：用来验证「中文输入」这条链路是否真的通了
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text("伴侣能力自测", fontSize = 14.sp)
                Spacer(Modifier.height(8.dp))
                val testText = remember { androidx.compose.runtime.mutableStateOf("") }
                OutlinedTextField(
                    value = testText.value,
                    onValueChange = { testText.value = it },
                    label = { Text("在这里输入中文，再用 /snapshot 核对") },
                    modifier = Modifier.fillMaxWidth()
                )
            }
        }

        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                Text(stringResource(R.string.forward_hint), fontSize = 12.sp)
                Spacer(Modifier.height(8.dp))
                Text(
                    "adb forward tcp:${BridgeState.PORT} tcp:${BridgeState.PORT}",
                    fontFamily = FontFamily.Monospace, fontSize = 12.sp
                )
                Text(
                    "curl -H \"X-Agent-Token: ${BridgeState.token.value}\" \\\n" +
                        "  127.0.0.1:${BridgeState.PORT}/snapshot",
                    fontFamily = FontFamily.Monospace, fontSize = 11.sp
                )
                Text(
                    "curl -X POST 127.0.0.1:${BridgeState.PORT}/action \\\n" +
                        "  -d '{\"action\":\"tap\",\"target\":\"text=登录\"}'",
                    fontFamily = FontFamily.Monospace, fontSize = 11.sp
                )
                Spacer(Modifier.height(8.dp))
                Text(stringResource(R.string.safety_hint), fontSize = 11.sp, color = Color(0xFFA32D2D))
            }
        }

        Text(stringResource(R.string.log_title), fontSize = 16.sp)
        Card(modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.padding(16.dp)) {
                if (BridgeState.logs.isEmpty()) {
                    Text(stringResource(R.string.log_empty), fontSize = 12.sp)
                } else {
                    BridgeState.logs.take(12).forEach { line ->
                        Text(line, fontFamily = FontFamily.Monospace, fontSize = 11.sp)
                    }
                }
            }
        }
    }
}

/** 读取系统设置，判断本应用的无障碍服务是否已被用户开启 */
private fun isAccessibilityEnabled(context: Context): Boolean {
    val resolver = context.contentResolver
    val enabled = Settings.Secure.getInt(resolver, Settings.Secure.ACCESSIBILITY_ENABLED, 0)
    if (enabled != 1) return false
    val services = Settings.Secure.getString(
        resolver, Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES
    ) ?: return false
    return services.contains(context.packageName)
}
