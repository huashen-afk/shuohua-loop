#!/usr/bin/env python3
"""
流程：① 终端按【空格】→ 进入预启动；② 【任意窗口】按【g】→ 开始循环（输入 shuohua1 → 回车，每 1 秒一次）。

模拟输入走 macOS 自带 osascript（System Events）；预启动与「空格停止」均需 pynput（pip install pynput）。

预启动阶段可在终端按【Esc】取消。运行中停止：任意窗口【空格】；或终端【Esc】/【Ctrl+C】。
主循环默认限时 30 秒，到时间自动结束（见 RUN_DURATION_SEC）。

权限（建议都勾选，改完后请完全退出终端 Cmd+Q 再打开）：
  · 系统设置 → 隐私与安全性 → 辅助功能：
      - 打开「终端」或「Cursor」（与你实际运行脚本的应用一致）
      - 再点「+」，在弹窗中按 Cmd+Shift+G（前往文件夹），输入 /usr/bin，添加「osascript」并打开开关
        （报错 1002「osascript 不允许发送按键」就是缺这一项）
      - 预启动（按 g）与「任意处空格」停止需允许运行环境监听键盘（pynput，通常与终端/Python 同一项）
  · 若曾拒绝过「控制 System Events」，可到隐私与安全性 → 自动化 里检查
"""

from __future__ import annotations

import select
import subprocess
import sys
import termios
import threading
import time
import tty

# 主循环最长运行秒数，到点自动退出。
RUN_DURATION_SEC = 30.0
# 每次输入后的等待（秒）。
LOOP_INTERVAL = 0.05
SLEEP_POLL_STEP = 0.05


def applescript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def keystroke_via_system_events(text: str) -> tuple[bool, str]:
    """通过 System Events 向前台应用发送按键。成功返回 (True, '')。"""
    t = applescript_escape(text)
    script = (
        'tell application "System Events"\n'
        f'    keystroke "{t}"\n'
        "    key code 36\n"
        "end tell"
    )
    r = subprocess.run(
        ["/usr/bin/osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=10,
    )
    if r.returncode == 0:
        return True, ""
    err = (r.stderr or r.stdout or "").strip() or f"exit {r.returncode}"
    return False, err


def sleep_until(stop: threading.Event, end_monotonic: float) -> None:
    """睡到 end_monotonic 或 stop 被置位（分段睡眠以便响应空格/Esc）。"""
    while not stop.is_set() and time.monotonic() < end_monotonic:
        remaining = end_monotonic - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(SLEEP_POLL_STEP, remaining))


def wait_space_or_esc_in_terminal() -> bool:
    """在终端内等待：空格=进入预启动 True；Esc=退出 False。"""
    fd = sys.stdin.fileno()
    if not sys.stdin.isatty():
        print("请在 Terminal.app 里直接运行本脚本（不要重定向 stdin）。")
        return False
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while True:
            ch = sys.stdin.read(1)
            if ch == " ":
                return True
            if ch == "\x1b":
                dr, _, _ = select.select([sys.stdin], [], [], 0.05)
                if dr:
                    while True:
                        r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                        if not r2:
                            break
                        sys.stdin.read(1)
                return False
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def stdin_stop_watcher(stop: threading.Event) -> None:
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        while not stop.is_set():
            r, _, _ = select.select([sys.stdin], [], [], 0.2)
            if not r:
                continue
            ch = sys.stdin.read(1)
            if ch != "\x1b":
                continue
            dr, _, _ = select.select([sys.stdin], [], [], 0.05)
            if dr:
                while True:
                    r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                    if not r2:
                        break
                    sys.stdin.read(1)
            stop.set()
            return
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def prestart_wait_global_g() -> bool:
    """
    预启动：全局检测到按下小写 g 返回 True；终端 Esc 取消返回 False；未安装 pynput 返回 False。
    """
    proceed = threading.Event()
    cancel = threading.Event()
    abort = threading.Event()

    def esc_thread() -> None:
        fd = sys.stdin.fileno()
        if not sys.stdin.isatty():
            return
        old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while not proceed.is_set() and not abort.is_set():
                r, _, _ = select.select([sys.stdin], [], [], 0.2)
                if not r:
                    continue
                ch = sys.stdin.read(1)
                if ch != "\x1b":
                    continue
                dr, _, _ = select.select([sys.stdin], [], [], 0.05)
                if dr:
                    while True:
                        r2, _, _ = select.select([sys.stdin], [], [], 0.01)
                        if not r2:
                            break
                        sys.stdin.read(1)
                cancel.set()
                return
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)

    t_esc = threading.Thread(target=esc_thread, daemon=True)
    t_esc.start()

    try:
        from pynput import keyboard
    except ImportError:
        print(
            "预启动阶段需要 pynput 以全局检测【g】，请执行 pip install pynput",
            file=sys.stderr,
        )
        abort.set()
        t_esc.join(timeout=2.0)
        return False

    def on_press(key: keyboard.Key | keyboard.KeyCode | None) -> bool | None:
        if cancel.is_set():
            return False
        if isinstance(key, keyboard.KeyCode) and key.char == "g":
            proceed.set()
            return False
        return None

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        while not proceed.is_set() and not cancel.is_set():
            time.sleep(0.05)
    finally:
        listener.stop()
        listener.join(timeout=2.0)

    abort.set()
    t_esc.join(timeout=1.0)
    return proceed.is_set() and not cancel.is_set()


def global_space_stop_watcher(stop: threading.Event) -> None:
    """任意前台按键：检测到空格则 stop.set()，并结束监听线程。"""
    try:
        from pynput import keyboard
    except ImportError:
        print(
            "未安装 pynput：无法在任意窗口用空格停止，请执行 pip install pynput；"
            "仍可用终端内 Esc / Ctrl+C。",
            file=sys.stderr,
        )
        stop.wait()
        return

    def on_press(key: keyboard.Key | keyboard.KeyCode | None) -> bool | None:
        if key == keyboard.Key.space:
            stop.set()
            return False
        return None

    listener = keyboard.Listener(on_press=on_press)
    listener.start()
    try:
        stop.wait()
    finally:
        listener.stop()
        listener.join(timeout=2.0)


def main() -> None:
    print("Python:", sys.executable)
    print("① 请保持焦点在本终端，按【空格】进入预启动（预启动前按【Esc】可退出）。")
    if not wait_space_or_esc_in_terminal():
        print("已退出。")
        return

    print("② 【预启动】请切到目标窗口准备；在【任意窗口】按小写【g】开始执行；终端按【Esc】取消。")
    if not prestart_wait_global_g():
        print("已取消或未就绪（若未安装 pynput请先 pip install）。")
        return

    stop = threading.Event()
    watcher = threading.Thread(target=stdin_stop_watcher, args=(stop,), daemon=True)
    watcher.start()
    space_watcher = threading.Thread(target=global_space_stop_watcher, args=(stop,), daemon=True)
    space_watcher.start()

    text = "shuohua1"
    run_end = time.monotonic() + RUN_DURATION_SEC
    print(
        f"已开始。最长运行 {RUN_DURATION_SEC:.0f} 秒后自动结束。"
        "请先切到【要输入的目标窗口】；"
        "在【任意窗口】按【空格】可提前停止，或切回终端按【Esc】/【Ctrl+C】。",
    )
    try:
        while not stop.is_set():
            if time.monotonic() >= run_end:
                print("已到限定时间，自动停止。", flush=True)
                stop.set()
                break
            ok, err = keystroke_via_system_events(text)
            if not ok:
                print("模拟输入失败：", err, file=sys.stderr)
                if "1002" in err or "不允许发送按键" in err:
                    print(
                        "\n说明：系统把「发按键」算在 osascript 头上。"
                        "请到「辅助功能」里添加 /usr/bin/osascript 并打开开关"
                        "（同时保留「终端」或 Cursor）。改完请完全退出终端再运行。\n",
                        file=sys.stderr,
                    )
                else:
                    print(
                        "\n请检查：辅助功能已授权「终端」或 Cursor；"
                        "自动化里允许控制「系统事件」。\n",
                        file=sys.stderr,
                    )
                break
            slice_end = min(run_end, time.monotonic() + LOOP_INTERVAL)
            sleep_until(stop, slice_end)
    except KeyboardInterrupt:
        stop.set()
    watcher.join(timeout=1.0)
    space_watcher.join(timeout=2.0)
    print("已停止。")


if __name__ == "__main__":
    main()
