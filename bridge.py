"""
微信 ↔ AstrBot 桥接脚本（带 Web 控制面板）
通过 wxauto 监听微信消息，调用 AstrBot Open API 获取回复，自动发回微信。
"""
import json
import os
import re
import time
import logging
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from wxauto import WeChat
import requests
import win32gui, win32con, win32process, win32api


def _activate_wechat_foreground(hwnd):
    """使用 AttachThreadInput 可靠激活微信窗口到前台"""
    if not hwnd:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_SHOWWINDOW | win32con.SWP_NOSIZE | win32con.SWP_NOMOVE)
        win32gui.SetWindowPos(hwnd, win32con.HWND_NOTOPMOST, 0, 0, 0, 0,
                              win32con.SWP_SHOWWINDOW | win32con.SWP_NOSIZE | win32con.SWP_NOMOVE)
        wechat_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
        current_tid = win32api.GetCurrentThreadId()
        win32process.AttachThreadInput(current_tid, wechat_tid, True)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetFocus(hwnd)
        win32process.AttachThreadInput(current_tid, wechat_tid, False)
        return True
    except Exception as e:
        log.error(f"激活微信窗口失败: {e}")
        return False


# 安静版 _show：不让微信抢焦点，但保持窗口可达
def _quiet_show(self):
    self.HWND = win32gui.FindWindow("WeChatMainWndForPC", None)
    if self.HWND:
        win32gui.ShowWindow(self.HWND, 4)  # SW_SHOWNOACTIVATE


_wx_original_show = None  # 保存原始 _show 供发送消息时临时恢复

# ============ 配置 ============
ASTRBOT_URL = "http://localhost:6185/api/v1/chat"
API_KEY = "your_astrobot_api_key_here"  # 从 AstrBot 控制面板获取
POLL_INTERVAL = 10
WEB_PORT = 8765
ASTRBOT_ATTACHMENTS = "/path/to/astrbot/attachments"  # AstrBot 附件目录（发送图片时需要）
BOT_NICKNAMES = ["你的微信昵称"]  # 机器人微信昵称（用于群聊 @ 检测）
# =============================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.FileHandler("bridge.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("bridge")

# 状态控制
paused = threading.Event()
paused.clear()  # set = 已暂停, clear = 运行中
running = False
run_lock = threading.Lock()
wechat_instance = None
wxid_map = {}  # 昵称/备注 → 微信号
nick_map = {}  # 微信号 → 原始昵称
_seen_msg_ids = {}  # {chat_name: {msg_content, ...}}，同聊天同内容永久去重


def build_contact_map(wx):
    """从微信读取好友列表，构建 昵称→微信号 和 微信号→原始昵称 映射"""
    try:
        details = wx.GetFriendDetails()
        name_to_wxid = {}
        wxid_to_nick = {}
        for d in details:
            nick = d.get("昵称", "")
            wxid = d.get("微信号", "")
            remark = d.get("备注", "")
            if wxid:
                wxid_to_nick[wxid] = nick
                if nick:
                    name_to_wxid[nick] = wxid
                if remark:
                    name_to_wxid[remark] = wxid
        # 自身也记一下
        name_to_wxid[wx.nickname] = "self"
        wxid_to_nick["self"] = wx.nickname
        log.info(f"好友列表加载完成，共 {len(details)} 人")
        return name_to_wxid, wxid_to_nick
    except Exception as e:
        log.error(f"获取好友列表失败: {e}")
        return {}, {}


# ============ AstrBot API ============

def ensure_str(text) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        return text.decode("utf-8", errors="replace")
    return str(text)


def call_astrbot(session_id: str, message: str, username: str = "wechat") -> tuple:
    """调用 AstrBot API 并返回 (回复文本, 图片路径)。"""
    payload = {
        "username": username,
        "session_id": session_id,
        "message": message,
    }
    for attempt in range(3):
        try:
            resp = requests.post(
                ASTRBOT_URL, json=payload,
                headers={
                    "Authorization": f"Bearer {API_KEY}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                timeout=120,
            )
            resp.raise_for_status()
            break
        except requests.exceptions.RequestException as e:
            log.warning(f"AstrBot API 请求失败（尝试 {attempt+1}/3）: {e}")
            if attempt == 2:
                return (None, None)
            time.sleep(1)

    full_text = ""
    image_path = None
    has_data = False
    body = resp.content.decode("utf-8")
    for line in body.splitlines():
        line = line.strip()
        if not line.startswith("data: "):
            continue
        has_data = True
        try:
            data = json.loads(line[6:])
        except json.JSONDecodeError:
            continue
        t = data.get("type")
        text = ensure_str(data.get("data"))
        if t == "plain":
            # 跳过函数调用/结果的 JSON 跟踪信息
            if text.startswith('{"id": "call_'):
                continue
            full_text += text
        elif t == "complete" and text:
            full_text = text
        elif t == "end":
            break
        elif t == "error":
            log.error(f"AstrBot 返回错误: {text}")
            return (None, None)
        elif t == "image":
            filename = text.replace("[IMAGE]", "", 1) if text else ""
            if filename:
                img_path = os.path.join(ASTRBOT_ATTACHMENTS, filename)
                if os.path.exists(img_path):
                    image_path = img_path

    if not has_data:
        log.warning(f"AstrBot 非 SSE 响应 (HTTP {resp.status_code}): {body[:300]}")
        return (None, None)

    result = full_text.strip() if full_text else None
    return (ensure_str(result) if result else None, image_path)


# ============ 桥接核心 ============


def _clipboard_and_enter(wx, who, hwnd):
    """点击编辑框→UIA粘贴→UIA Enter（DirectUI 需要先 Click 聚焦）"""
    time.sleep(0.3)
    try:
        editbox = wx.ChatBox.EditControl()
        editbox.Click()
        time.sleep(0.2)
        editbox.SendKeys('{Ctrl}v')
        time.sleep(0.1)
        editbox.SendKeys('{Enter}')
    except Exception as e:
        log.warning(f"  UIA 粘贴+Enter 失败: {e}")
        # Fallback: AttachThreadInput + 物理按键
        if hwnd:
            try:
                wechat_tid = win32process.GetWindowThreadProcessId(hwnd)[0]
                current_tid = win32api.GetCurrentThreadId()
                win32process.AttachThreadInput(current_tid, wechat_tid, True)
                win32gui.SetForegroundWindow(hwnd)
                win32api.keybd_event(win32con.VK_CONTROL, 0, 0, 0)
                win32api.keybd_event(ord('V'), 0, 0, 0)
                time.sleep(0.05)
                win32api.keybd_event(ord('V'), 0, win32con.KEYEVENTF_KEYUP, 0)
                win32api.keybd_event(win32con.VK_CONTROL, 0, win32con.KEYEVENTF_KEYUP, 0)
                time.sleep(0.2)
                win32api.keybd_event(win32con.VK_RETURN, 0, 0, 0)
                time.sleep(0.05)
                win32api.keybd_event(win32con.VK_RETURN, 0, win32con.KEYEVENTF_KEYUP, 0)
                win32process.AttachThreadInput(current_tid, wechat_tid, False)
            except Exception as e2:
                log.warning(f"  物理按键 Fallback 也失败: {e2}")


def _send_msg_enter(wx, msg, who, hwnd):
    """发送文本：设置剪贴板→粘贴→物理Enter"""
    from wxauto import SetClipboardText
    try:
        if who not in wx.CurrentChat():
            wx.ChatWith(who)
            time.sleep(0.3)
    except Exception:
        wx.ChatWith(who)
        time.sleep(0.3)
    SetClipboardText(msg)
    time.sleep(0.1)
    _clipboard_and_enter(wx, who, hwnd)


def _send_image_enter(wx, img_path, who, hwnd):
    """发送图片：复制文件到剪贴板→粘贴→物理Enter"""
    from wxauto import SetClipboardFiles
    if not os.path.exists(img_path):
        log.warning(f"  图片文件不存在: {img_path}")
        return
    try:
        if who not in wx.CurrentChat():
            wx.ChatWith(who)
            time.sleep(0.3)
    except Exception:
        wx.ChatWith(who)
        time.sleep(0.3)
    SetClipboardFiles([img_path])
    time.sleep(0.3)
    _clipboard_and_enter(wx, who, hwnd)


def handle_message(wx: WeChat, chat_name: str, msg) -> None:
    msg_type = ensure_str(getattr(msg, "type", "sys"))
    msg_content = ensure_str(getattr(msg, "content", ""))

    if msg_type in ("sys", "self"):
        return

    # wxauto 群聊消息会携带 sender 属性（发言者在群内的昵称）
    sender = ensure_str(getattr(msg, "sender", ""))
    is_group = bool(sender) and sender != chat_name

    if is_group:
        # 只在被 @ 时回复（检查所有已知昵称）
        if not any(f"@{n}" in msg_content for n in BOT_NICKNAMES):
            log.info(f"[{chat_name}] 未 @ 机器人，跳过: {msg_content[:40]}")
            return

        # 去掉 wxauto 附加的群成员数后缀，如 "牛郎店（微信超级版） (3)" → "牛郎店（微信超级版）"
        base_name = re.sub(r'\s*\(\d+\)\s*$', '', chat_name).strip()
        username = sender
        # 每个群成员独立 session，避免 AstrBot 的 creator 冲突
        session_id = f"wx_group_{base_name}_{sender}"
        chat_name_for_send = base_name
    else:
        # 用微信号（wxid）作稳定标识，防止对方改名后上下文丢失
        stable_id = wxid_map.get(chat_name, chat_name)
        original_nick = nick_map.get(stable_id, chat_name)
        username = f"{original_nick}({stable_id})" if stable_id != chat_name else chat_name
        session_id = f"wx_{stable_id}"
        chat_name_for_send = chat_name
    log.info(f"[{chat_name}]: {msg_content[:60]}")

    if "[图片]" in msg_content:
        message = "[图片]"
    else:
        message = msg_content

    # 加上平台前缀，让 LLM 知道消息来源
    if is_group:
        message = f"{sender}在微信群中说: {message}"
    else:
        message = f"[微信私聊] {message}"

    reply, reply_image = call_astrbot(session_id, message, username=username)
    if not reply and not reply_image:
        log.warning(f"  AstrBot 未返回回复，跳过")
        return

    if reply:
        log.info(f"  -> 回复: {reply[:80]}")
    if reply_image:
        log.info(f"  -> 图片: {os.path.basename(reply_image)}")

    # 激活微信窗口并发送回复（带重试）
    hwnd = wx.HWND or win32gui.FindWindow("WeChatMainWndForPC", None)
    max_retries = 3
    prev_foreground = win32gui.GetForegroundWindow()  # 记住用户当前窗口

    if reply:
        sent = False
        for attempt in range(max_retries):
            if hwnd:
                ok = _activate_wechat_foreground(hwnd)
                if not ok:
                    log.warning(f"  激活微信窗口失败（尝试 {attempt+1}/{max_retries}）")
                    time.sleep(1.0)
                    continue
                time.sleep(1.0)
            try:
                if _wx_original_show:
                    WeChat._show = _wx_original_show
                _send_msg_enter(wx, reply, chat_name_for_send, hwnd)
                sent = True
                log.info(f"  消息发送成功")
                break
            except Exception as e:
                log.warning(f"  消息发送尝试 {attempt+1}/{max_retries} 失败: {e}")
                time.sleep(0.5)
        if not sent:
            log.error(f"  消息所有发送尝试均失败")

    # 发送图片（如果有）
    if reply_image and os.path.exists(reply_image):
        log.info(f"  发送图片: {os.path.basename(reply_image)}")
        img_sent = False
        for attempt in range(max_retries):
            if hwnd:
                ok = _activate_wechat_foreground(hwnd)
                if not ok:
                    time.sleep(1.0)
                    continue
                time.sleep(0.5)
            try:
                if _wx_original_show:
                    WeChat._show = _wx_original_show
                _send_image_enter(wx, reply_image, chat_name_for_send, hwnd)
                img_sent = True
                log.info(f"  图片发送成功")
                break
            except Exception as e:
                log.warning(f"  图片发送尝试 {attempt+1}/{max_retries} 失败: {e}")
                time.sleep(0.5)
        if not img_sent:
            log.error(f"  图片所有发送尝试均失败")

    # 恢复安静模式，并归还焦点
    if _wx_original_show:
        WeChat._show = _quiet_show
    try:
        win32gui.SetWindowPos(hwnd, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                              win32con.SWP_NOSIZE | win32con.SWP_NOMOVE | win32con.SWP_NOACTIVATE)
        if prev_foreground and prev_foreground != hwnd:
            win32gui.SetForegroundWindow(prev_foreground)
    except Exception:
        pass


def bridge_loop():
    global running, wechat_instance
    import ctypes
    ctypes.windll.ole32.CoInitialize(None)  # 新线程需要初始化 COM
    log.info("正在连接微信...")

    # 猴子补丁：禁用 wxauto 的 _show() 抢焦点（在 init 调用 _show 之前打上）
    global _wx_original_show
    _wx_original_show = WeChat._show
    WeChat._show = _quiet_show

    try:
        wx = WeChat()
        wechat_instance = wx
    except Exception as e:
        log.error(f"微信初始化失败: {e}")
        running = False
        return

    log.info(f"登录账号: {wx.nickname}")
    global wxid_map, nick_map
    wxid_map, nick_map = build_contact_map(wx)
    running = True
    cycle_count = 0

    while running:
        try:
            if paused.is_set():
                # 暂停中，等一会再检查
                time.sleep(1)
                continue

            new_msgs = wx.GetAllNewMessage()
            cycle_count += 1

            # 每 5 轮刷新一次昵称（用户可能改了微信名）
            if cycle_count % 5 == 0:
                try:
                    fresh = wx.A_MyIcon.Name
                    if fresh and fresh != wx.nickname:
                        log.info(f"昵称已更新: {wx.nickname} → {fresh}")
                        wx.nickname = fresh
                except Exception:
                    pass

            # GetAllNewMessage 内部靠 IsRedPixel 截图检测红点，
            # 窗口被遮挡时截图会拍到覆盖窗口，导致永远检测不到新消息。
            # 每 3 轮做一次 UIA 直读：刷新会话列表，检查有未读的会话。
            if not new_msgs and cycle_count % 3 == 0:
                try:
                    sessions_unread = wx.GetSessionList(reset=True, newmessage=True)
                    if sessions_unread:
                        for us, _ in sessions_unread.items():
                            try:
                                wx.ChatWith(us)
                                time.sleep(0.3)
                                for m in wx.GetAllMessage():
                                    mc = ensure_str(getattr(m, 'content', ''))
                                    if not mc:
                                        continue
                                    dk = (us, mc)
                                    if dk not in _seen_msg_ids:
                                        _seen_msg_ids[dk] = True
                                        if len(_seen_msg_ids) > 10000:
                                            _seen_msg_ids.clear()
                                        handle_message(wx, us, m)
                            except Exception:
                                continue
                except Exception as e:
                    log.debug(f'manual session scan failed: {e}')

            if new_msgs:
                for chat_name, msg_list in new_msgs.items():
                    for msg in msg_list:
                        # 源头去重：同一内容跳过，不进入 handle_message
                        msg_content = ensure_str(getattr(msg, "content", ""))
                        dedup_chat = re.sub(r'\s*\(\d+\)\s*$', '', chat_name).strip()
                        dedup_key = (dedup_chat, msg_content)
                        if dedup_key in _seen_msg_ids:
                            continue
                        _seen_msg_ids[dedup_key] = True
                        if len(_seen_msg_ids) > 10000:
                            _seen_msg_ids.clear()
                        handle_message(wx, chat_name, msg)
                time.sleep(1.0)  # 等发送彻底完成后再移走窗口
                # 操作完后把微信放到其他窗口后面，避免遮住用户当前工作
            try:
                win32gui.SetWindowPos(wx.HWND, win32con.HWND_BOTTOM, 0, 0, 0, 0,
                                      win32con.SWP_NOSIZE | win32con.SWP_NOMOVE | win32con.SWP_NOACTIVATE)
            except Exception:
                pass
            time.sleep(POLL_INTERVAL)
        except KeyboardInterrupt:
            break
        except Exception as e:
            log.error(f"运行异常: {e}")
            time.sleep(POLL_INTERVAL * 2)

    running = False
    ctypes.windll.ole32.CoUninitialize()


# ============ Web 控制面板 ============

PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>微信机器人控制面板</title>
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { font-family: -apple-system, sans-serif; background: #f5f5f5; display: flex; justify-content: center; align-items: center; min-height: 100vh; }
  .card { background: #fff; border-radius: 16px; padding: 40px; box-shadow: 0 4px 24px rgba(0,0,0,0.1); text-align: center; min-width: 320px; }
  h1 { font-size: 24px; margin-bottom: 8px; color: #333; }
  .subtitle { color: #999; font-size: 14px; margin-bottom: 32px; }
  .status { font-size: 18px; margin-bottom: 24px; padding: 12px; border-radius: 8px; }
  .status.running { background: #e8f5e9; color: #2e7d32; }
  .status.paused { background: #fff3e0; color: #e65100; }
  .status.offline { background: #fbe9e7; color: #c62828; }
  .btn { display: inline-block; padding: 14px 48px; border: none; border-radius: 8px; font-size: 16px; cursor: pointer; margin: 8px; transition: opacity .2s; }
  .btn:hover { opacity: .85; }
  .btn:disabled { opacity: .4; cursor: default; }
  .btn-primary { background: #4CAF50; color: #fff; }
  .btn-danger { background: #f44336; color: #fff; }
  .btn-secondary { background: #ff9800; color: #fff; }
  .log { margin-top: 24px; text-align: left; max-height: 200px; overflow-y: auto; background: #fafafa; border-radius: 8px; padding: 12px; font-size: 12px; color: #666; }
</style>
</head>
<body>
<div class="card">
  <h1>🤖 微信机器人</h1>
  <p class="subtitle" id="account">微信机器人</p>
  <div class="status" id="status">加载中...</div>
  <div>
    <button class="btn btn-primary" id="btnStart" onclick="action('start')">启动</button>
    <button class="btn btn-danger" id="btnStop" onclick="action('stop')" disabled>停止</button>
    <button class="btn btn-secondary" id="btnPause" onclick="action('pause')" disabled>暂停</button>
    <button class="btn btn-primary" id="btnResume" onclick="action('resume')" style="display:none" disabled>恢复</button>
  </div>
  <div class="log" id="log">等待连接...</div>
</div>
<script>
async function refresh() {
  const r = await fetch('/status');
  const s = await r.json();
  const statusEl = document.getElementById('status');
  const btnStart = document.getElementById('btnStart');
  const btnStop = document.getElementById('btnStop');
  const btnPause = document.getElementById('btnPause');
  const btnResume = document.getElementById('btnResume');

  if (!s.running) {
    statusEl.className = 'status offline';
    statusEl.textContent = '未运行';
    btnStart.disabled = false;
    btnStop.disabled = true;
    btnPause.disabled = true;
    btnResume.style.display = 'none';
    document.getElementById('log').textContent = s.log || '无日志';
    return;
  }

  btnStart.disabled = true;
  btnStop.disabled = false;

  if (s.paused) {
    statusEl.className = 'status paused';
    statusEl.textContent = '已暂停';
    btnPause.style.display = 'none';
    btnResume.style.display = 'inline-block';
    btnResume.disabled = false;
  } else {
    statusEl.className = 'status running';
    statusEl.textContent = '运行中';
    btnPause.style.display = 'inline-block';
    btnPause.disabled = false;
    btnResume.style.display = 'none';
  }

  document.getElementById('log').textContent = s.log || '无日志';
}

async function action(cmd) {
  await fetch('/' + cmd, { method: 'POST' });
  setTimeout(refresh, 500);
}

refresh();
setInterval(refresh, 3000);
</script>
</body>
</html>"""


class WebHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/status":
            self.send_json({
                "running": running,
                "paused": paused.is_set(),
                "log": open("bridge.log", encoding="utf-8", errors="replace").read().splitlines()[-10:] if running else [],
            })
        else:
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(PAGE.encode("utf-8"))

    def do_POST(self):
        if self.path == "/start":
            _start_bridge()
            self.send_json({"ok": True})
        elif self.path == "/stop":
            _stop_bridge()
            self.send_json({"ok": True})
        elif self.path == "/pause":
            paused.set()
            log.info("[Web] 已暂停")
            self.send_json({"ok": True})
        elif self.path == "/resume":
            paused.clear()
            log.info("[Web] 已恢复")
            self.send_json({"ok": True})
        else:
            self.send_json({"ok": False}, 404)

    def send_json(self, data, code=200):
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def log_message(self, fmt, *args):
        pass  # 不打印 HTTP 日志


bridge_thread = None


def _start_bridge():
    global running, bridge_thread
    with run_lock:
        if running:
            return
        running = True
    paused.clear()
    bridge_thread = threading.Thread(target=bridge_loop, daemon=True)
    bridge_thread.start()


def _stop_bridge():
    global running
    with run_lock:
        running = False


def start_web():
    server = HTTPServer(("127.0.0.1", WEB_PORT), WebHandler)
    log.info(f"Web 控制面板: http://127.0.0.1:{WEB_PORT}")
    server.serve_forever()


if __name__ == "__main__":
    log.info("启动 Web 控制面板...")
    start_web()
