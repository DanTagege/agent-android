# 发布构建若开启混淆，需保留无障碍服务与反射用到的类。
# 当前 release 默认 isMinifyEnabled = false，这里先留一份可直接启用的规则。
-keep class com.coco.agentandroid.BridgeAccessibilityService { *; }
-keep class com.coco.agentandroid.BridgeHttpServer { *; }
-keepattributes *Annotation*
