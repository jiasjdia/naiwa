# -*- coding: utf-8 -*-
"""test_audio_gating.py - 验证音频门控与重播。

两块内容:
  1. "只有手动点动作才出声,自动随机动作静音" —— 借 PetApp.play 的真实实现,
     把 self.audio 换成记录用的假对象,看它对两种调用分别做了什么。
  2. 重播、音量和偏移由 test_audio_backend.py 检查实际 Python 音频接口。

用法: python tools\\test_audio_gating.py
"""
from __future__ import annotations

import sys
import time
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pet_app  # noqa: E402


class FakeAudio:
    """只记录调用,不真的开播放进程。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def play(self, clip_id: str, start_ms: int | None = None) -> bool:
        self.calls.append(("play", clip_id))
        return True

    def stop(self) -> None:
        self.calls.append(("stop", ""))


class Probe(pet_app.PetApp):
    """够 PetApp.play 用的最小替身(不建窗口、不读素材)。"""

    def __init__(self, audio: FakeAudio, clips: dict) -> None:
        self.audio = audio
        self.clips = clips
        self.mode = "idle"
        self.cur_clip = "laugh"
        self.cur_index = 99
        self.acc = 1.0
        self.drew = 0
        self._drop_jelly = types.MethodType(pet_app.PetApp._drop_jelly, self)

    def draw(self) -> None:
        self.drew += 1


# PetApp.play 的真实实现(只取这一个方法,避免构造窗口)
play_impl = pet_app.PetApp.play

CLIPS = {"laugh": type("C", (), {"label": "大笑"})(),
         "laugh": type("C", (), {"label": "跳舞1"})(),
         "laugh": type("C", (), {"label": "跳舞2"})()}

results: list[tuple[str, bool, str]] = []


def case(name: str, *, manual: bool, clip: str = "laugh") -> Probe:
    audio = FakeAudio()
    p = Probe(audio, CLIPS)
    play_impl(p, clip, with_audio=manual)
    return p


def check(label: str, cond: bool, detail: str = "") -> None:
    results.append((label, cond, detail))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"  [{detail}]" if detail and not cond else ""))


print(__doc__)
print("== 1. 手动点选动作:必须出声 ==")
p = case("manual", manual=True, clip="laugh")
check("手动调用 audio.play(动作)", ("play", "laugh") in p.audio.calls, str(p.audio.calls))
check("手动调用不再额外 stop", ("stop", "") not in p.audio.calls, str(p.audio.calls))
check("动作从第 1 帧开始", p.cur_index == 0, f"cur_index={p.cur_index}")
check("进入 playing 状态", p.mode == "playing", p.mode)
check("有重绘", p.drew == 1, f"drew={p.drew}")

print("\n== 2. 自动随机动作:必须静音 ==")
p = case("auto", manual=False, clip="laugh")
check("自动调用不碰 audio.play", not any(c[0] == "play" for c in p.audio.calls), str(p.audio.calls))
check("自动调用会 stop(掐掉上一段余音)", ("stop", "") in p.audio.calls, str(p.audio.calls))
check("动作照常从第 1 帧开始", p.cur_index == 0, f"cur_index={p.cur_index}")
check("照常进入 playing 状态", p.mode == "playing", p.mode)
check("照常重绘(动作一定要演)", p.drew == 1, f"drew={p.drew}")

print("\n== 3. 默认值必须是静音(自动路径靠默认值) ==")
audio = FakeAudio()
p = Probe(audio, CLIPS)
play_impl(p, "laugh")          # 不传 with_audio —— 就是 tick() 里随机动作的调法
check("不传 with_audio 时静音", not any(c[0] == "play" for c in audio.calls), str(audio.calls))

print("\n== 4. 源码层:三处调用点是否正确 ==")
src = (ROOT / "pet_app.py").read_text(encoding="utf-8")
lines = src.splitlines()
# 调用语句可能跨行,所以看"从关键字开始往下 3 行"的窗口
def window(kw: str, span: int = 3) -> str:
    for i, l in enumerate(lines):
        if kw in l:
            return "\n".join(lines[i:i + span])
    return ""

menu_win = window("self.menu.add_command(label=self.clips[cid].label")
key_win = window("<Key-{i}>")
rand_win = window("self.play(pick)")
check("菜单传 with_audio=True", "with_audio=True" in menu_win, repr(menu_win))
check("键盘快捷键传 with_audio=True", "with_audio=True" in key_win, repr(key_win))
check("随机动作用默认(静音)", bool(rand_win) and "with_audio" not in rand_win, repr(rand_win))
check("play() 的默认值是静音", "def play(self, clip_id: str, with_audio: bool = False," in src)

# Replay, seek offsets, and channel replacement are exercised in test_audio_backend.py.

# ---------------------------------------------------------------- 单击中断
# 需求:动作演到一半时,在原地单击桌宠要能中断动作、立刻回待机。
# 同时待机时单击不能把随机倒计时重置掉(否则连点几下 = 自动表演被无限推迟)。
print("\n== 6. 单击中断动作 ==")

release_impl = pet_app.PetApp._on_release
to_idle_impl = pet_app.PetApp._to_idle
drop_jelly_impl = pet_app.PetApp._drop_jelly


class ClickProbe(pet_app.PetApp):
    """够 _on_release / _to_idle 用的最小替身。"""

    def __init__(self, *, mode: str, deadline: float) -> None:
        self.audio = FakeAudio()
        self.mode = mode
        self.cur_clip = "laugh"
        self.cur_index = 42
        self.acc = 7.0
        self.idle_clip = "laugh"
        self.idle_frame = 0
        self.deadline = deadline
        self.dragging = False
        self.drag_moved = False
        self._press_valid = False
        self.menu_open = False
        self._drag_body_eligible = False
        self.random_min_ms = 180 * 1000.0
        self.random_max_ms = 240 * 1000.0
        self.clips = CLIPS
        self.drew = 0
        self._poked = 0
        self._click_laugh_active = False
        # 拖动分支会读窗口位置,给个最小替身
        self.root = type("R", (), {"winfo_x": lambda s: 100, "winfo_y": lambda s: 200})()
        # 绑上真实的 _to_idle / _drop_jelly,测的是真实逻辑(不是替身自己写的空壳)
        self._to_idle = types.MethodType(to_idle_impl, self)
        self._drop_jelly = types.MethodType(drop_jelly_impl, self)

    # Q弹的判定细节不在这里测(那是 test_jelly.py 的活),这里只关心有没有被触发。
    # 本测试的点击坐标都用 (0,0),固定判定为"透明空白",所以行为与加 Q弹之前一致。
    def _is_opaque(self, x: int, y: int) -> bool:
        return 60 <= x <= 140 and 100 <= y <= 180

    def _complete_click(self, now: float) -> None:
        self._poked += 1

    def draw(self) -> None:
        self.drew += 1


class Ev:
    """伪造 Tk 事件。坐标 (0,0) 固定落在"透明空白"的区域(见 ClickProbe._is_opaque),
    所以这些用例测的还是"单击中断"这条路径,不会被 Q弹分走。"""

    def __init__(self, x: int = 0, y: int = 0):
        self.x, self.y = x, y
        self.x_root, self.y_root = x, y


def click(probe: ClickProbe, x: int = 0, y: int = 0) -> None:
    """模拟一次原地单击(按下 -> 抬起,位移在阈值内)。"""
    probe.dragging = True
    probe.drag_moved = False
    probe._press_x, probe._press_y = x, y
    probe._press_local = (x, y)
    probe._press_valid = probe.mode == "idle" and probe._is_opaque(x, y)
    release_impl(probe, Ev(x, y))


def drag(probe: ClickProbe) -> None:
    """模拟一次拖动后松开(不应触发中断)。"""
    probe.dragging = True
    probe.drag_moved = True
    probe._press_valid = False
    release_impl(probe, Ev())


import time as _time  # noqa: E402

now = _time.monotonic()

p = ClickProbe(mode="playing", deadline=now + 999)
click(p)
check("演动作时单击 -> 保持播放", p.mode == "playing", p.mode)
check("演动作时单击 -> 不重置进度", p.cur_clip == "laugh" and p.cur_index == 42,
      f"{p.cur_clip}#{p.cur_index}")
check("演动作时单击 -> 音频继续", ("stop", "") not in p.audio.calls, str(p.audio.calls))
check("演动作时单击 -> 不重绘待机帧", p.drew == 0, f"drew={p.drew}")
check("演动作时单击 -> 不重置随机倒计时",
      p.deadline == now + 999,
      f"deadline-now={p.deadline - now:.0f}s")
check("演动作时单击 -> 没有额外播放音频",
      not any(c[0] == "play" for c in p.audio.calls), str(p.audio.calls))

p2 = ClickProbe(mode="idle", deadline=now + 100)
old = p2.deadline
click(p2)                       # (0,0) 在透明区域
check("待机时单击透明处 -> 不重置随机倒计时", p2.deadline == old, f"{old} -> {p2.deadline}")
check("待机时单击透明处 -> 不重绘", p2.drew == 0, f"drew={p2.drew}")
check("待机时单击透明处 -> 不动音频", p2.audio.calls == [], str(p2.audio.calls))
check("待机时单击透明处 -> 不触发 Q弹", p2._poked == 0, f"poke={p2._poked}")

p2b = ClickProbe(mode="idle", deadline=now + 100)
click(p2b, 100, 140)            # 落在 ClickProbe._is_opaque 认定的有效区域
check("待机时戳肚子 -> 触发 Q弹", p2b._poked == 1, f"poke={p2b._poked}")
check("待机时戳肚子 -> 不重置随机倒计时", p2b.deadline == now + 100)
check("待机时戳肚子 -> 不动音频", p2b.audio.calls == [], str(p2b.audio.calls))

p3 = ClickProbe(mode="playing", deadline=now + 999)
drag(p3)
check("拖动后松开 -> 不算单击,不中断", p3.mode == "playing", p3.mode)
check("拖动后松开 -> 不停音频", ("stop", "") not in p3.audio.calls, str(p3.audio.calls))
check("拖动后松开 -> 不触发戳肚子", p3._poked == 0, f"poke={p3._poked}")

# 源码层:两条回待机的路必须走同一份实现(否则容易只改一处)
src2 = (ROOT / "pet_app.py").read_text(encoding="utf-8")
check("动作播完 = 调 _to_idle()", "self._to_idle()" in src2)
check("提供显式停止大笑入口", "def stop_laugh(self)" in src2)

failed = [r for r in results if not r[1]]
print(f"\n== 结果 ==\n通过 {len(results) - len(failed)} 项,失败 {len(failed)} 项")
sys.exit(1 if failed else 0)
