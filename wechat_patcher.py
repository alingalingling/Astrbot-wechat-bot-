"""
微信 3.9.x 版本绕过登录补丁
自动修改内存，绕过 "版本过低" 检测
原理：将内存中的 "x64" 替换为 "x32"
"""
import pymem
import pymem.process


def patch_wechat():
    try:
        pm = pymem.Pymem("WeChat.exe")
    except pymem.exception.ProcessNotFound:
        print("[-] WeChat.exe 未运行，请先打开微信（停留在登录界面）")
        return False

    print(f"[+] 已附加到 WeChat.exe (PID: {pm.process_id})")

    matches = pm.pattern_scan_all(b"x64", return_multiple=True)
    ml = list(matches)
    print(f"[*] 找到 {len(ml)} 处 'x64'")

    count = 0
    for addr in ml:
        try:
            pm.write_bytes(addr, b"x32", 3)
            count += 1
        except Exception:
            pass

    print(f"[OK] 已修改 {count} 处 'x64' -> 'x32'")
    print()
    print("请按以下顺序操作：")
    print("  1. 手机扫码")
    print("  2. 手机上不要点确认")
    print("  3. 再扫一次码（刷新）")
    print("  4. 手机上点确认登录")
    return True


def main():
    print("=== 微信版本过低补丁 (WeChat 3.9.x) ===")
    print()
    print("0. 打开微信并停留在登录/扫码界面")
    print("1. 按 Enter 执行补丁")
    print("2. 手机扫码（不要点确认）-> 再扫一次 -> 确认登录")
    print()
    input("准备好后按 Enter 继续...")
    patch_wechat()


if __name__ == "__main__":
    main()
