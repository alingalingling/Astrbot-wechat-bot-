# WeChat Bot Bridge — CLAUDE.md

项目将 AstrBot QQ 机器人与微信小号互通，共享 LLM + LivingMemory。

## 关键架构

- **Windows 端**：WeChat 3.9.x + wxauto（监听/发送微信消息）+ Python 桥接脚本
- **AstrBot 端**：Open API（端口 6185），LivingMemory 跨平台共享记忆
- 群聊仅 @回复，不主动发言

## 消息发送机制（DirectUI 坑）

WeChat 3.9.x 使用腾讯 DirectUI：

**发送消息正确顺序（关键）：**
1. `wx.ChatWith(who)` — 切换到目标聊天
2. `editbox = wx.ChatBox.EditControl()` — 获取编辑框 UIA 元素
3. `editbox.Click()` — **必须先 Click**，让 DirectUI 内部焦点移到编辑框
4. `editbox.SendKeys('{Ctrl}v')` — 粘贴
5. `editbox.SendKeys('{Enter}')` — 发送

**什么不工作：**
- UIA ValuePattern.SetValue() → 静默失败
- 没有 Click 直接 SendKeys → 键盘焦点不在编辑框
- EnumChildWindows → 0 个子窗口（无标准 HWND）

## 去重策略

`bridge_loop` 中用 `_seen_msg_ids` 字典统一去重（以 `(chat_name, msg_content)` 为 key），`handle_message` 中不再做重复检测。

## 窗口焦点管理

- 猴子补丁 `WeChat._show` 使用 `SW_SHOWNOACTIVATE` 阻止微信抢焦点
- 发送消息时临时恢复原始 `_show`，发送完切回安静模式
- 发送完成后使用 `SetWindowPos(HWND_BOTTOM)` 将微信窗口置底，并归还焦点给用户之前的窗口

## 新消息检测

`GetAllNewMessage()` 依赖截屏检测红点，窗口被遮挡时失效。每 3 轮轮询做 UIA 直读会话列表作为补充。

## 好友识别

启动时通过 `wx.GetFriendDetails()` 读取好友微信号（wxid）做稳定标识，防止对方改名后上下文丢失。
