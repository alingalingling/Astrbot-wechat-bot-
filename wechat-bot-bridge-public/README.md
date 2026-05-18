# WeChat Bot Bridge — QQ & WeChat 双端互通

让你的 **AstrBot QQ 机器人** 与 **微信小号** 互通。同一个 LLM + 同一套记忆，用户在微信上发消息 = 跟 QQ 机器人聊天，反之亦然。

## 特性

- **微信小号本身就是机器人**，无需添加机器人好友
- **微信群聊仅 @回复**，不主动发言
- **共享记忆**：QQ 聊过的微信知道，微信聊过的 QQ 知道（AstrBot LivingMemory）
- **Web 控制面板**：浏览器管理启动/停止/暂停/恢复，实时状态和日志
- **焦点抑制**：发送消息时短暂激活微信窗口，发完自动归还焦点
- **微信号（wxid）稳定标识**：防止对方改名后上下文丢失
- **图片视觉识别**：支持接收图片并触发 LLM 视觉模型描述
- **支持文字、图片、表情包**

## 架构

```
Windows 桌面
├── WeChat 3.9.x          ← 小号登录
└── Python 桥接脚本
    ├── wxauto             ← 监听/发送微信消息
    └── requests           ← 调用 AstrBot API

Docker / 服务器（同一局域网或本机）
└── AstrBot (端口 6185)
    ├── /api/v1/chat       ← Open API，处理 LLM+记忆
    └── LivingMemory       ← 记忆存储（跨平台共享）
```

## 前置条件

| 依赖 | 说明 |
|------|------|
| Windows 系统 | 需要桌面微信 + Python 运行环境 |
| WeChat 3.9.x | wxauto **只支持 3.9.x** 版本，4.0+ 无效 |
| Python 3.9+ | 运行桥接脚本 |
| AstrBot | 已部署运行的 AstrBot 实例（Docker 或本地） |
| Ollama | 用于 embedding 模型（如 nomic-embed-text） |

## 快速开始

### 1. 获取微信 3.9.x

从 [tom-snow/wechat-windows-versions](https://github.com/tom-snow/wechat-windows-versions) 下载 3.9.x 版本（推荐 3.9.10.19）。

> **防自动更新**：WeChat 3.9.x 会自动升级到 4.x，装完后请做以下操作：
> 1. 登录后：左下角菜单 → 设置 → 通用设置 → 取消勾选"有更新时自动升级微信"
> 2. 重命名微信安装目录下的 `WeChatUpdate.exe` 为 `WeChatUpdate.exe.bak`
> 3. 注册表禁用：`reg add "HKEY_CURRENT_USER\Software\Tencent\WeChat" /v "NeedUpdateType" /t reg_dword /d "0" /f`

### 2. 安装依赖

```bash
pip install wxauto>=3.9 requests>=2.28 pymem
```

如果 pip 安装慢，可以临时换源：
```bash
pip install wxauto requests pymem -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> **wxauto 安装说明**：wxauto v3.9+ 可能需要从 GitHub 直接安装：
> ```bash
> pip install git+https://github.com/cluic/wxauto.git
> ```

### 3. 配置 AstrBot

确保你的 AstrBot 实例正常运行，并且：

- Open API 端口已映射（默认 6185）
- 已创建 API 密钥（scope=chat）
- `unique_session = true`（跨平台上下文共享）
- 已移除 `group_chat` 插件（群聊由桥接脚本控制）
- LivingMemory 插件正常运行

### 4. 配置桥接脚本

编辑 `bridge.py`，修改以下配置：

```python
ASTRBOT_URL = "http://localhost:6185/api/v1/chat"    # AstrBot API 地址
API_KEY = "your_astrobot_api_key_here"                # AstrBot API 密钥
BOT_NICKNAMES = ["你的微信昵称"]                       # 机器人微信昵称（群聊 @ 检测用）
```

## 使用

### 微信版本补丁（每次重启微信后必做）

微信 3.9.x 已被服务器端封禁，登录时会提示"版本过低"。使用内存补丁绕过：

```bash
python wechat_patcher.py
```

**扫码登录顺序（关键！）：**
1. 手机扫码
2. **不要点确认**
3. **再扫一次**
4. **点确认登录**

### 启动桥接

**方式一：Web 控制面板**
```bash
python bridge.py
# 浏览器打开 http://127.0.0.1:8765
# 点击"启动"按钮
```

**方式二：批处理文件**
```bash
# 启动控制面板
start_bot.bat

# 停止桥接
stop_bot.bat
```

### 验证

给微信小号发私聊消息，检查是否回复。查看 `bridge.log` 排查问题。

> 注意：第一次启动时，桥接脚本会自动读取好友列表构建微信号映射。如果对方改过微信昵称，上下文仍然关联到同一个 wxid，不会丢失。

## 项目文件

| 文件 | 说明 |
|------|------|
| `bridge.py` | 主桥接脚本（含 Web 控制面板） |
| `wechat_patcher.py` | 微信版本过低内存补丁 |
| `requirements.txt` | Python 依赖 |
| `start_bot.bat` | 启动控制面板+自动打开浏览器 |
| `stop_bot.bat` | 停止桥接脚本 |
| `start_wechat.bat` | 启动微信+自动打补丁 |

## 消息流程

### 私聊
1. wxauto 轮询到新消息 → 判断私聊
2. `POST http://<astrbot-ip>:6185/api/v1/chat` 发送消息
3. AstrBot 处理：查记忆 → LLM → 存记忆 → 返回回复
4. 激活微信窗口 → 切换到对应聊天 → 粘贴并发送回复

### 群聊 @回复
1. 轮询到新群消息，检查是否含 `@` + 已知昵称
2. 未被 @ → 跳过
3. 被 @ → 同私聊流程，session_id 使用 `wx_group_群名_发送者昵称`

## 技术要点

### DirectUI 发送机制

微信 WeChat 3.9.x 使用腾讯自研 **DirectUI** 框架，导致标准 UI 自动化方法失效：

| 方案 | 结果 | 原因 |
|------|------|------|
| `wxauto.SendMsg()`（UIA ValuePattern） | ❌ 静默失败 | DirectUI ValuePattern stub |
| `AttachThreadInput` + `keybd_event(Ctrl+V)` | ❌ 不粘贴 | 焦点不在编辑框 |
| `editbox.Click()` + `SendKeys('{Ctrl}v')` + `SendKeys('{Enter}')` | ✅ 可靠 | Click 让 DirectUI 内部焦点移到编辑框 |

### 新消息检测

`GetAllNewMessage()` 内部依赖 `IsRedPixel`（截屏检测红色角标）。窗口被遮挡时截屏会拍到覆盖窗口，无法检测红点。解决方案：每 3 轮轮询做一次 UIA 直读会话列表，对未读会话手动切换读取。

## 常见问题

### Embedding 连接失败

AstrBot 容器内通过 Docker bridge 网络连接 Ollama：
```
http://172.17.0.2:11434/v1
```
不能在 embedding 配置中使用 `host.docker.internal`（除非 Ollama 端口映射到宿主机）。

### 记忆存储失败后不重试

如果 `pending_summary.retry_count >= 3`，LivingMemory 插件不再重试。可通过 SQLite 手动重置：

```bash
# 进入 AstrBot 容器后执行
python -c "
import sqlite3, json
conn = sqlite3.connect('/AstrBot/data/plugin_data/astrbot_plugin_livingmemory/conversations.db')
cur = conn.cursor()
cur.execute('SELECT session_id, metadata FROM sessions')
for r in cur.fetchall():
    meta = json.loads(r[1])
    if meta.get('pending_summary'):
        meta['pending_summary']['retry_count'] = 0
        cur.execute('UPDATE sessions SET metadata=? WHERE session_id=?', (json.dumps(meta), r[0]))
conn.commit()
conn.close()
"
```

### 微信启动相关

- 每次重启微信后必须重新运行 `wechat_patcher.py`
- 扫码必须按"扫码 → 不确认 → 再扫一次 → 确认"的顺序操作
- 如果微信自动更新到 4.x，需要卸载重装 3.9.x 并禁用更新

### 群聊不回复

检查 `BOT_NICKNAMES` 配置是否正确。桥接脚本检查消息中是否含 `@` + 任一已知昵称。

## AstrBot 部署参考

- 容器名：`astrbot-final`
- Open API 端口：6185（映射到宿主机）
- QQ 适配器：aiocqhttp + napcat（OneBot v11）
- 主 LLM：通过 api.deepseek.com 或其他 OpenAI 兼容 API
- Embedding 模型：`nomic-embed-text`（Ollama 提供）
- 记忆数据库：SQLite（conversations.db）+ FAISS 向量索引

## 许可证

MIT
