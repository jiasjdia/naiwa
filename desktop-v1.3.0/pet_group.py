"""Shared desktop-pet resources, window placement, and group operations."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import copy
import ctypes
from ctypes import wintypes
import sys
import time
import random
import math
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageChops
from pet_physics import PhysicsWorld, TIERS, GROWTH_INCREMENT, tier_of

CLONE_SCALE = min(TIERS)
REUNIFY_SEC = 0.45
SPLIT_DURATION_SEC = 0.75
SPLIT_SHAKE_HZ = 18
SPLIT_SHAKE_START_HZ = 10
SPLIT_MAX_ANGLE = 20
LAUGH_SPREAD_RADIUS = 1.5
LAUGH_SPREAD_DELAY = 0.7
LAUGH_SPREAD_COOLDOWN = 3.0

USAGE_TEXT = """欢迎饲养奶小蛙！

点击与大笑
待机时双击可见身体，从第121帧开始播放6秒及对应音频一次，启动双击计入本轮连击，双击后停手长大2个小档，从第一下起累计6下及以上分裂。只有右键“捧腹大笑”和“全部大笑”播放完整动画一次。键盘及自动表演播放第54～381帧，自动表演静音。所有大笑均不循环；停止大笑只停止动作与声音，物理运动继续。
笑声传递：以正在笑的桌宠可见身高的1.5倍为半径，按身体中心距离判断。待机桌宠持续在范围内0.7秒后，从第121帧开始笑6秒，也能继续传递，并继承笑声的有声或静音状态。已在笑的不会重播；笑完或停止后冷却3秒，再重新累计靠近时间。拖动、按住、菜单操作及分裂期间不会被感染。

成长与分裂
每次有效点击身体都会以脚底为锚点向下压扁、横向鼓起并弹簧回弹，连击延续当前惯性；大笑画面和音频继续播放。大笑期间按轮记录连击，相邻点击间隔不超过0.3秒算同一轮。停点0.3秒后统一结算：1～5下只成长，3下一次长3个小档、5下一次长5个小档；达到第6下立即分裂，本轮不先长大。成长模拟用力吹一口气：迅速撑开，约0.16秒膨胀到目标体型的120%，再用约0.24秒轻轻收回目标，整次约0.4秒，只回弹一次。0.4→0.6→0.8→1.0之间各有5个均匀小档，多轮累计可跨越大档，最大到1.0，不因累计次数自动分裂。桌宠上方的浅金色气泡实时显示本轮连击及实际可增加的成长值，第6下切换为分裂提示；达到体型上限时显示对应提示，气泡跟随桌宠且不阻挡鼠标，短暂停留后淡出。成长跨大笑保留，待机需再次双击后继续。右键大小菜单只列四大档，手动调大小清零成长。
分裂时以下半身左右两点连成的固定长度横杆带动整个身体刚性倾斜，并附带少量左右位移及残影，不做局部挤压或扭曲；横杆位于距脚底约三分之一身高处，两端向中线左右各偏四分之一身宽，每秒10次逐渐加速至18次，摆角幅度从0度增至20度，幅度与速度采用二次曲线更早提升，形成达到极限后裂开的效果，约0.75秒后母体替换为左右两只比当前大小小的第一个大档宠物（1.0→0.8、0.8→0.6、0.6→0.4，最小仍为0.4），以抛物线飞出。摇晃期间点击无效，连续7下也只分裂一次。大小档和小档均需单轮6下才分裂；最大1.0档的1～5下仍会膨胀溢出并回弹至1.0，6下分成两只0.8档。
分裂摇晃期间保持触发时的压缩形状，分裂完成后两只分身继承压缩状态与弹簧惯性，再各自回弹。炸开瞬间出现金黄色扩散光圈，周围带有大小不同的星芒和亮点，约0.6秒后淡出；光圈和星点按母体可见身高同比缩放，不拦截鼠标。分身继承出生时母体的大笑进度、播放区间与有声或静音状态，只播放剩余部分。笑完不取消分裂；若出生时已经笑完则直接待机。

按压、拖动与抛掷
待机轻点会局部Q弹，按住约1.5秒蓄满。按住移动可拎起；快速甩动后松手按最近约120毫秒的方向与速度抛出（初速度为鼠标速度的1.8倍），越快初速度越大。轻拿轻放维持放下Q弹和悬停。
快速抛出优先于合体。轻放时与同档宠物重叠仍可合体升一档；不同档与最大档不能合体。抛掷及分裂宠物受重力、边界反弹和身体碰撞影响，每块屏幕独立，任务栏上方为地面。

菜单与分身
右键哪只哪只成为本体；大小、单只大笑及停止控制本体，全部大笑及停止控制全体。复制分身生成0.4档待机宠物。分身数量不设上限。右键“分身绘图…”可先选尺寸，再用点、线、矩形或圆排列分身：点一下生成一只，线和矩形拖出起终点，圆从圆心向外拖；矩形和圆沿轮廓排列，间距随体型计算。桌面预览显示位置和数量，松手保留预览，可拖动整组调整位置，点击“确认生成”或Enter后逐只生成；“重新绘制”或R重画，Esc或右键取消绘制，生成期间Esc停止剩余生成。归一约0.45秒收回所有分身。
快速抛掷会在屏幕边缘回弹，越界时自动纠正。若找不到桌宠，再次双击pet.pyw可显示现有桌宠，并把屏幕外的桌宠拉回可见范围，保留分身和成长进度。

快捷键与保存
Ctrl+Alt+1全体大笑，Ctrl+Alt+C复制，Ctrl+Alt+V在鼠标处粘贴，Ctrl+Alt+Q退出；有焦点时1大笑，Q或Esc退出。各只空闲3～4分钟独立静音大笑。
成长只在本次运行保留；退出重启只出现一只，保存本体基础大档，不保存成长小档、分身和位置。
"""


def work_area(point, fallback=(1920, 1080)):
    """Nearest monitor's work area in the same screen coordinates as Tk."""
    if sys.platform == "win32":
        class MonitorInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                        ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.MonitorFromPoint.argtypes = (wintypes.POINT, wintypes.DWORD)
        user.MonitorFromPoint.restype = wintypes.HANDLE
        user.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MonitorInfo))
        monitor = user.MonitorFromPoint(wintypes.POINT(round(point[0]), round(point[1])), 2)
        info = MonitorInfo(cbSize=ctypes.sizeof(MonitorInfo))
        if monitor and user.GetMonitorInfoW(monitor, ctypes.byref(info)):
            r = info.rcWork
            return r.left, r.top, r.right, r.bottom
    return 0, 0, *fallback


def constrain_origin(x, y, box, area):
    """Keep visible content in a monitor's work area, ignoring transparent pads."""
    left, top, right, bottom = area
    bx0, by0, bx1, by1 = box
    x = min(max(x, left - bx0), max(left - bx0, right - bx1))
    y = min(max(y, top - by0), max(top - by0, bottom - by1))
    return round(x), round(y)


def place_window(window, x, y, size=None, *, flush=True):
    """Move to absolute coordinates; Tk's negative geometry means right-relative."""
    x, y = round(x), round(y)
    native_batch = (not flush and sys.platform == "win32" and isinstance(window, tk.Misc)
                    and window.winfo_ismapped())
    prefix = f"{size[0]}x{size[1]}" if size is not None else ""
    # A deferred negative Tk geometry would overwrite SetWindowPos on the next
    # idle flush. Mapped physics windows therefore move natively in both axes.
    if not native_batch:
        window.geometry(f"{prefix}{x:+d}{y:+d}")
    if flush:
        window.update_idletasks()
    if sys.platform == "win32" and isinstance(window, tk.Misc) and (native_batch or x < 0 or y < 0):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        user.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND, ctypes.c_int,
                                     ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        hwnd = user.GetAncestor(window.winfo_id(), 2)
        flags = 0x0004 | 0x0010 | (0x0001 if size is None else 0)
        w, h = size or (0, 0)
        if not user.SetWindowPos(hwnd, None, x, y, w, h, flags):
            raise ctypes.WinError(ctypes.get_last_error())
        if flush:
            window.update_idletasks()


def place_windows(moves):
    """Commit a physics frame together, without one native redraw per pet."""
    moves = list(moves)
    if not moves:
        return
    if sys.platform == "win32" and all(isinstance(w, tk.Misc) and w.winfo_ismapped() for w, _, _ in moves):
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        user.BeginDeferWindowPos.argtypes = (ctypes.c_int,)
        user.BeginDeferWindowPos.restype = wintypes.HANDLE
        user.DeferWindowPos.argtypes = (wintypes.HANDLE, wintypes.HWND, wintypes.HWND,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT)
        user.DeferWindowPos.restype = wintypes.HANDLE
        user.EndDeferWindowPos.argtypes = (wintypes.HANDLE,)
        user.EndDeferWindowPos.restype = wintypes.BOOL
        batch = user.BeginDeferWindowPos(len(moves))
        for window, x, y in moves:
            if not batch:
                break
            hwnd = user.GetAncestor(window.winfo_id(), 2)
            batch = user.DeferWindowPos(batch, hwnd, None, round(x), round(y), 0, 0, 0x0015)
        if batch and user.EndDeferWindowPos(batch):
            return
    for window, x, y in moves:
        place_window(window, x, y, flush=False)


class SharedCache:
    def __init__(self, budget):
        self.budget = budget
        self.bytes = 0
        self.items = OrderedDict()

    def get(self, key):
        entry = self.items.get(key)
        if entry is None:
            return None
        self.items.move_to_end(key)
        return entry[0]

    def put(self, key, value, nbytes):
        previous = self.items.pop(key, None)
        if previous is not None:
            self.bytes -= previous[1]
        # Oversized entries are used by the caller without evicting the entire cache.
        if nbytes > self.budget:
            return value
        self.items[key] = (value, nbytes)
        self.bytes += nbytes
        while self.bytes > self.budget:
            _, (_, weight) = self.items.popitem(last=False)
            self.bytes -= weight
        return value

    def clear(self):
        self.items.clear()
        self.bytes = 0


class FrameCacheView(Mapping):
    """Legacy frame-cache inspection for a pet's current size, without ownership."""
    def __init__(self, pet):
        self.pet = pet

    def _entries(self):
        for key, (value, weight) in self.pet.group.cache.items.items():
            if key[0] == "photo":
                _, cid, index, width, height = key
                clip = self.pet.clips[cid]
                if (width, height) == (clip.fw, clip.fh):
                    yield (cid, index), value, weight

    def __iter__(self):
        return (key for key, _, _ in self._entries())

    def __len__(self):
        return sum(1 for _ in self._entries())

    def __getitem__(self, key):
        cid, index = key
        clip = self.pet.clips[cid]
        photo = self.pet.group.cache.get(("photo", cid, index, clip.fw, clip.fh))
        if photo is None:
            raise KeyError(key)
        return photo

    @property
    def bytes(self):
        return sum(weight for _, _, weight in self._entries())


class PetVoice:
    """A pet releases its own voice or its membership in the shared soundtrack."""
    def __init__(self, group, pet):
        self.group, self.pet = group, pet

    @property
    def output_latency_ms(self):
        return getattr(self.group.audio, "output_latency_ms", 0)

    def play(self, clip_id, start_ms=None, *, end_ms=None, loops=0):
        self.stop()
        player = self.group.audio
        if hasattr(player, "play_voice"):
            if end_ms is not None or loops:
                base = getattr(player, "offset_ms", {}).get(clip_id, 0)
                return player.play_voice(self.pet, clip_id, start_ms=base + (start_ms or 0),
                                         end_ms=None if end_ms is None else base + end_ms, loops=loops)
            if start_ms is None:
                return player.play_voice(self.pet, clip_id)
            base = getattr(player, "offset_ms", {}).get(clip_id, 0)
            return player.play_voice(self.pet, clip_id, start_ms=base + start_ms)
        return player.play(clip_id) if start_ms is None else player.play(clip_id, start_ms=start_ms)

    def stop(self):
        group = self.group
        if self.pet in group.audio_members:
            group.audio_members.remove(self.pet)
            if not group.audio_members:
                group.audio.stop()
        if hasattr(group.audio, "stop_voice"):
            group.audio.stop_voice(self.pet)
        elif not group.audio_members:
            group.audio.stop()

    def quit(self):
        self.stop()
        if hasattr(self.group.audio, "release_voice"):
            self.group.audio.release_voice(self.pet)


class PetGroup:
    def __init__(self, manifest, settings, audio, cache_budget):
        self.root = tk.Tk()
        self.root.withdraw()
        self.manifest, self.settings, self.audio = manifest, settings, audio
        self.cache = SharedCache(cache_budget)
        self.pets = []  # bottom to top, refreshed from native stacking before a merge
        self.primary = None
        self.copy_ready = False
        self.audio_members = set()
        self._laugh_exposure = {}
        self.split_effects = set()
        self.runtime = None
        self.closed = False
        self.reunifying = False
        self._reunify_after = None
        self._eye_source = None
        self.help_window = None
        self.drawing = None
        self.physics = PhysicsWorld()
        self.pending_bursts = []
        self._physics_after = None
        self._physics_started = False
        self._physics_timer = None
        self._physics_last = time.monotonic()
        try:
            self.audio.prewarm("laugh")
        except Exception as error:
            from pet_app import log
            log(f"音频预热异常(不影响画面): {error}")

    def register(self, pet):
        self.pets.append(pet)
        if self.primary is None:
            self.primary = pet
        self.set_hover(pet)
        if not self._physics_started:
            self._physics_started = True
            self._physics_after = self.root.after(16, self._physics_tick)

    def cancel_emission(self, pet):
        self.pending_bursts = [entry for entry in getattr(self, "pending_bursts", []) if entry[1] is not pet]
        if getattr(pet, "_splitting", False):
            pet._splitting = False
            pet.canvas.delete("split_ghost")
            pet._split_ghost_photos = []
            pet._split_ghost_layers = []
            pet._split_pose_history = None
            if not getattr(pet, "_closed", False):
                pet._resume_tap(time.monotonic())
                place_window(pet.root, *pet._split_origin)
                pet.draw()

    def throw(self, pet, velocity):
        self.physics.add(pet, pet.visible_box(), (pet.root.winfo_x(), pet.root.winfo_y()),
                         work_area(pet.foot_position()), dynamic=True,
                         velocity=tuple(component * 1.8 for component in velocity), speed_multiplier=1.8)
        self._sync_physics_timer()

    def set_hover(self, pet):
        if not hasattr(self, "physics") or self.closed:
            return
        origin = (pet.root.winfo_x(), pet.root.winfo_y())
        self.physics.add(pet, pet.visible_box(), origin, work_area(pet.foot_position()))

    def begin_drag(self, pet):
        self.cancel_emission(pet)
        if hasattr(self, "physics"):
            self.physics.remove(pet)

    def resize_physics(self, pet):
        if not hasattr(self, "physics"):
            return
        entry = self.physics.particles.get(pet)
        dynamic = self.physics.is_dynamic(pet)
        velocity = tuple(entry.body.velocity) if entry is not None else (0, 0)
        self.physics.add(pet, pet.visible_box(), (pet.root.winfo_x(), pet.root.winfo_y()),
                         work_area(pet.foot_position()), dynamic=dynamic, velocity=velocity,
                         speed_multiplier=entry.speed_multiplier if entry is not None else 1)

    def request_split(self, source, now=None):
        if (self.closed or self.reunifying or source not in self.pets
                or getattr(source, "_splitting", False)):
            return 0
        now = time.monotonic() if now is None else now
        base = getattr(source, "_growth_base", source.global_scale)
        logical_scale = base + getattr(source, "_growth_step", 0) * GROWTH_INCREMENT
        if getattr(source, "_growth_anim", None) is not None:
            source._growth_anim = None
            source.set_scale(logical_scale, growing=True)
        source._freeze_tap(now)
        source._splitting = True
        source._split_origin = (source.root.winfo_x(), source.root.winfo_y())
        source._split_began = now
        source._split_ghost_photos = []
        source._split_pose_history = None
        scale = next((value for value in TIERS if value < logical_scale - 1e-9), min(TIERS))
        self.pending_bursts.append((now + SPLIT_DURATION_SEC, source, scale, 0, source._action_serial))
        if hasattr(self, "physics"):
            # Hold an airborne parent still while charging.
            self.set_hover(source)
            self._sync_physics_timer()
        return 2

    def _sync_physics_timer(self):
        active = bool(self.pending_bursts) or any(
            self.physics.is_dynamic(pet) and not entry.body.is_sleeping
            for pet, entry in self.physics.particles.items())
        if active and self._physics_timer is None:
            from pet_app import _begin_jelly_timer
            self._physics_timer = _begin_jelly_timer()
        elif not active and self._physics_timer is not None:
            self._physics_timer.timeEndPeriod(1)
            self._physics_timer = None

    def _emit_due(self, now):
        for due, source, scale, _, serial in tuple(self.pending_bursts):
            if source not in self.pets:
                self.cancel_emission(source)
                continue
            progress = min(1, max(0, (now - source._split_began) / SPLIT_DURATION_SEC))
            source.draw_split_ghosts(progress)
            if now < due:
                continue
            box = source.visible_box()
            tap = getattr(source, "_tap_spring", None)
            compression = tap.compression if tap is not None and tap.active else 0
            height = (box[3] - box[1]) * (1 - compression)
            center = (source.root.winfo_x() + (box[0] + box[2]) / 2,
                      source.root.winfo_y() + box[3] - height / 2)
            # Construct both children before removing the parent so a failed
            # construction can roll back without losing the source pet.
            children = []
            try:
                for direction in (-1, 1):
                    child = self.spawn(source=source, scale=scale, emitted=True, direction=direction)
                    if child is None:
                        raise RuntimeError("当前无法创建分身")
                    children.append(child)
                was_primary = self.primary is source
                self.destroy_pet(source)
                if was_primary:
                    self.set_primary(children[0])
                self._show_split_effect(center, height)
            except Exception as error:
                for child in children:
                    self.destroy_pet(child)
                self.cancel_emission(source)
                from pet_app import log
                log(f"分裂失败，保留母体及成长进度: {error}")

    def _show_split_effect(self, center, height):
        if not isinstance(self.root, tk.Misc):
            return
        try:
            from pet_effects import SplitBurst
            effect = SplitBurst(self.root, center, height, self.split_effects.discard)
            if not effect.closed:
                self.split_effects.add(effect)
        except Exception as error:
            from pet_app import log
            log(f"分裂光圈暂时不可用: {error}")

    def _spread_laughter(self, now):
        """Continuous nearby exposure; take a snapshot before starting new voices."""
        if self.closed or self.reunifying:
            self._laugh_exposure = {}
            return
        donors = []
        for pet in tuple(self.pets):
            if getattr(pet, "_closed", False) or pet.mode != "playing" or pet.cur_clip != "laugh":
                continue
            if pet._action_start is None or now < pet._action_start or pet.animation_snapshot(now) is None:
                continue
            box = pet.visible_box()
            center = (pet.root.winfo_x() + (box[0] + box[2]) / 2,
                      pet.root.winfo_y() + (box[1] + box[3]) / 2)
            donors.append((pet, center, LAUGH_SPREAD_RADIUS * (box[3] - box[1])))
        exposures = {}
        previous = getattr(self, "_laugh_exposure", {})
        for pet in tuple(self.pets):
            if (getattr(pet, "_closed", False) or pet.mode != "idle" or pet.dragging
                    or pet.menu_open or getattr(pet, "_press_valid", False)
                    or getattr(pet, "_splitting", False)
                    or now < getattr(pet, "_laugh_cooldown_until", 0)):
                continue
            if not donors:
                continue
            box = pet.visible_box()
            center = (pet.root.winfo_x() + (box[0] + box[2]) / 2,
                      pet.root.winfo_y() + (box[1] + box[3]) / 2)
            nearby = [(math.hypot(center[0] - point[0], center[1] - point[1]), source)
                      for source, point, radius in donors
                      if math.hypot(center[0] - point[0], center[1] - point[1]) <= radius]
            if not nearby:
                continue
            since = previous.get(pet, now)
            if now - since >= LAUGH_SPREAD_DELAY - 1e-9:
                source = min(nearby, key=lambda entry: entry[0])[1]
                pet.play_short_laugh(with_audio=source._action_audible)
            else:
                exposures[pet] = since
        self._laugh_exposure = exposures

    def _physics_tick(self):
        self._physics_after = None
        if self.closed:
            return
        now = time.monotonic()
        elapsed, self._physics_last = now - self._physics_last, now
        if not self.reunifying:
            self._emit_due(now)
            for pet in tuple(self.pets):
                self.physics.sync_hover(pet, (pet.root.winfo_x(), pet.root.winfo_y()))
            self.physics.advance(elapsed)
            place_windows((pet.root, x, y) for pet, (x, y) in self.physics.positions()
                          if pet in self.pets and not pet._closed)
            self.root.update_idletasks()
        self._spread_laughter(now)
        if not self.closed:
            self._sync_physics_timer()
            rate = 60  # Rigid shaking is rendered at 60 Hz; physics still uses fixed 120 Hz steps.
            delay = max(1, math.ceil((now + 1 / rate - time.monotonic()) * 1000))
            self._physics_after = self.root.after(delay, self._physics_tick)

    def show_help(self):
        """One non-modal help window, owned by the group rather than a pet."""
        if self.closed:
            return
        if self.help_window is not None and self.help_window.winfo_exists():
            self.help_window.deiconify()
            self.help_window.lift()
            self.help_window.focus_force()
            return

        window = self.help_window = tk.Toplevel(self.root)
        window.withdraw()
        window.title("奶小蛙使用说明")
        window.attributes("-topmost", True)

        def close_help(event=None):
            self.help_window = None
            window.destroy()
            return "break"

        window.protocol("WM_DELETE_WINDOW", close_help)
        window.bind("<Escape>", close_help)
        content = ttk.Frame(window, padding=16)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="桌宠使用说明", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w", pady=(0, 12))
        text = ScrolledText(content, wrap="word", font=("Microsoft YaHei UI", 10),
                            padx=12, pady=10, relief="flat", borderwidth=0)
        text.insert("1.0", USAGE_TEXT)
        text.configure(state="disabled")
        text.pack(fill="both", expand=True)
        footer = ttk.Frame(content)
        footer.pack(fill="x", pady=(12, 0))
        ttk.Label(footer, text="Esc 关闭本说明窗口").pack(side="left")
        ttk.Button(footer, text="关闭", command=close_help).pack(side="right")

        left, top, right, bottom = work_area(self.root.winfo_pointerxy())
        width, height = min(640, right - left - 32), min(680, bottom - top - 48)
        window.minsize(min(420, width), min(300, height))
        window.deiconify()
        place_window(window, (left + right - width) // 2, (top + bottom - height) // 2, (width, height))
        window.focus_force()

    def eyes(self, size):
        from pet_eyes import EyeFollow
        key = ("eyes", *size)
        prototype = self.cache.get(key)
        if prototype is None:
            if self._eye_source is None:
                self._eye_source = EyeFollow(size)
                prototype = self._eye_source
            else:
                prototype = copy.copy(self._eye_source)
                prototype.size = tuple(size)
                prototype._prepare()
            weight = sum(im.width * im.height * len(im.getbands())
                         for im in (prototype.static, prototype.mask, prototype.pupil_sprite))
            self.cache.put(key, prototype, weight)
        eyes = copy.copy(prototype)
        eyes.reset()
        return eyes

    def set_primary(self, pet):
        if self.closed or self.reunifying or pet not in self.pets:
            return
        self.primary = pet
        pet._save_scale(pet.global_scale)
        pet._write_geometry_file()

    def copy(self):
        if not self.closed and not self.reunifying:
            self.copy_ready = True

    def duplicate(self, source):
        self.copy()
        return self.spawn(source=source)

    def draw_clones(self, source):
        if self.closed or self.reunifying:
            return
        if self.drawing is not None:
            self.drawing.close()
        from pet_drawing import PetDrawing
        self.drawing = PetDrawing(self, source)

    def paste(self):
        if self.copy_ready:
            return self.spawn(point=self.root.winfo_pointerxy())

    def spawn(self, source=None, point=None, *, scale=CLONE_SCALE, emitted=False, direction=1):
        if self.closed or self.reunifying:
            return None
        from pet_app import PetApp, log
        pet = PetApp.__new__(PetApp)
        try:
            pet.__init__(group=self, scale=scale)
            box = pet.visible_box()
            if source is not None:
                sx, sy = source.root.winfo_x(), source.root.winfo_y()
                sb = source.visible_box()
                point = (sx + (sb[0] + sb[2]) / 2, sy + sb[3])
                area = work_area(point)
                if emitted:
                    x = sx + (sb[0] + sb[2] - box[0] - box[2]) / 2 + direction * (box[2] - box[0]) * 0.52
                    y = sy + (sb[1] + sb[3] - box[1] - box[3]) / 2
                else:
                    x, y = sx + sb[2] + 12 - box[0], sy + sb[3] - box[3]
                    if x + box[2] > area[2]:
                        x = sx + sb[0] - 12 - box[2]
            else:
                point = point or self.root.winfo_pointerxy()
                area = work_area(point)
                x, y = point[0] - (box[0] + box[2]) / 2, point[1] - (box[1] + box[3]) / 2
            x, y = constrain_origin(x, y, box, area)
            if emitted:
                pet.inherit_animation(source)
            pet.draw()
            place_window(pet.root, x, y)
            if emitted:
                self.physics.add(pet, box, (x, y), area, dynamic=True, source=source,
                                 velocity=self.physics.launch_velocity(area, direction))
            else:
                self.set_hover(pet)
            pet.tick()
            log(f"新增分身: {len(self.pets)}只, 尺寸={pet.global_scale}")
            return pet
        except Exception:
            if hasattr(pet, "root"):
                self.destroy_pet(pet)
            raise

    def play_all(self, clip_id, with_audio=True, *, full=False):
        if self.closed or self.reunifying or not self.primary or clip_id not in self.primary.clips:
            return
        now = time.monotonic()
        for pet in tuple(self.pets):
            pet.play(clip_id, with_audio=with_audio, started_at=now, full=full)

    def stop_all_laughs(self):
        if self.closed or self.reunifying:
            return
        for pet in tuple(self.pets):
            pet.stop_laugh()

    def dispatch(self):
        if self.runtime is None or self.closed:
            return
        for action in self.runtime.pending():
            if action == "quit":
                self.quit()
                return
            if action == "show":
                self.show_all()
            elif action == "copy":
                self.copy()
            elif action == "paste":
                self.paste()
            else:
                self.play_all(action)

    def show_all(self):
        """Launching the existing instance also brings lost windows back on screen."""
        for pet in tuple(self.pets):
            x, y = pet.root.winfo_x(), pet.root.winfo_y()
            nx, ny = constrain_origin(x, y, pet.visible_box(), work_area(pet.foot_position()))
            if abs(nx - x) > 1 or abs(ny - y) > 1:
                self.cancel_emission(pet)
                place_window(pet.root, nx, ny)
                self.set_hover(pet)
                if pet.mode == "thrown":
                    pet._to_idle()
            pet.root.deiconify()
            pet.root.lift()

    @staticmethod
    def overlap(a, b):
        ma, ax, ay = a.screen_mask()
        mb, bx, by = b.screen_mask()
        left, top = max(ax, bx), max(ay, by)
        right, bottom = min(ax + ma.width, bx + mb.width), min(ay + ma.height, by + mb.height)
        if right <= left or bottom <= top:
            return 0
        am = ma.crop((left - ax, top - ay, right - ax, bottom - ay))
        bm = mb.crop((left - bx, top - by, right - bx, bottom - by))
        return ImageChops.multiply(am, bm).histogram()[255]

    def try_merge(self, source):
        if self.closed or self.reunifying or source not in self.pets:
            return False
        # Tk reports its toplevels in native bottom-to-top stacking order.
        order = list(self.root.tk.call("wm", "stackorder", self.root))
        ranks = {str(window): i for i, window in enumerate(order)}
        candidates = [(self.overlap(source, p), ranks.get(str(p.root), i), p)
                      for i, p in enumerate(self.pets) if p is not source and not getattr(p, "_splitting", False)]
        if not candidates:
            return False
        area, _, target = max(candidates, key=lambda item: item[:2])
        from pet_app import log
        tier = tier_of(source.global_scale)
        if not area or tier == 1 or tier != tier_of(target.global_scale):
            return False
        scale = TIERS[tier - 2]
        anchor = target.foot_position()
        was_primary = self.primary in (source, target)
        target._to_idle()
        target.set_scale(scale, anchor=anchor, clamp=False)
        if was_primary:
            self.primary = target
            target._save_scale(scale)
        self.destroy_pet(source)
        self.set_hover(target)
        target.land()
        target._write_geometry_file()
        log(f"合体完成: 尺寸={scale}, 剩余 {len(self.pets)} 只")
        return True

    def destroy_pet(self, pet):
        getattr(self, "_laugh_exposure", {}).pop(pet, None)
        self.cancel_emission(pet)
        if hasattr(self, "physics"):
            self.physics.remove(pet)
        if pet in self.pets:
            self.pets.remove(pet)
        pet._close_window()

    def reunify(self):
        if self.closed or self.reunifying or len(self.pets) < 2:
            return
        if getattr(self, "drawing", None) is not None:
            self.drawing.close()
        self.reunifying = True
        self._laugh_exposure = {}
        for pet in tuple(self.pets):
            self.cancel_emission(pet)
        self.physics.clear()
        target = self.primary
        target._to_idle()
        destination = target.foot_position()
        starts = []
        for pet in tuple(self.pets):
            if pet is target:
                continue
            pet._to_idle()
            pet.pickup_for_return()
            starts.append((pet, pet.root.winfo_x(), pet.root.winfo_y(), pet.foot_position()))
        began = time.monotonic()

        def advance():
            self._reunify_after = None
            if self.closed:
                return
            fraction = min(1.0, max(0.0, (time.monotonic() - began) / REUNIFY_SEC))
            eased = 1 - (1 - fraction) ** 3
            for pet, x, y, foot in starts:
                place_window(pet.root, x + (destination[0] - foot[0]) * eased,
                             y + (destination[1] - foot[1]) * eased)
            if fraction >= 1:
                for pet, *_ in starts:
                    self.destroy_pet(pet)
                self.reunifying = False
                self.set_hover(target)
                target.land()
                target._write_geometry_file()
            else:
                self._reunify_after = self.root.after(10, advance)
        advance()

    def quit(self):
        if self.closed:
            return
        self.closed = True
        if getattr(self, "drawing", None) is not None:
            self.drawing.close()
        self.pending_bursts.clear()
        if self._physics_after is not None:
            self.root.after_cancel(self._physics_after)
            self._physics_after = None
        if self._physics_timer is not None:
            self._physics_timer.timeEndPeriod(1)
            self._physics_timer = None
        self.physics.clear()
        if self.help_window is not None:
            self.help_window.destroy()
            self.help_window = None
        if self.primary is not None:
            self.primary._save_scale(self.primary.global_scale)
        if self._reunify_after is not None:
            self.root.after_cancel(self._reunify_after)
            self._reunify_after = None
        for effect in tuple(getattr(self, "split_effects", ())):
            effect.close()
        for pet in tuple(self.pets):
            self.destroy_pet(pet)
        self.audio.quit()
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None
        self._eye_source = None
        self.cache.clear()
        self.root.destroy()
