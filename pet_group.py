"""Shared desktop-pet resources, window placement, and group operations."""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Mapping
import copy
import ctypes
from ctypes import wintypes
import sys
import time
import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText

from PIL import Image, ImageChops

MAX_PETS = 20
CLONE_SCALE = 0.4
REUNIFY_SEC = 0.45

USAGE_TEXT = """欢迎广大奶家人饲养奶蛙，本产品专为奶家人设计，下为使用说明：
1.本体与菜单
右键哪一只，哪一只就成为本体，关闭菜单也会保留。菜单中的“捧腹大笑”让所有奶蛙一起执行；“大小”只调整当前本体。

2.点击、长按与拖动
奶家人的身体总是Q弹的，单击可见身体会局部回弹，长按约 1.5 秒蓄满后松开，回弹更明显。按住左键移动即可拎起，松手后放下回弹。

3.连续点击大笑
在 3 秒内有效点击同一只的身体 10 次，奶蛙就会被挑逗，触发捧腹大笑。各只独立计数、独立发声；大笑期间继续单击不会打断，拖起则可以中断。

4.复制与粘贴
右键选择“复制分身”，立即在旁边生成一只，可自由拖动。也可以先按 Ctrl+Alt+C 复制，再用 Ctrl+Alt+V 在鼠标位置反复粘贴。
新分身固定为 0.4，最多同时存在 20 只。达到上限后，合体或归一释放名额即可继续粘贴。

5拖动合体
将一只奶蛙拖到另一只身上，两只就会合体。尺寸直接相加，最高为 1.2；任一只已经达到 1.2 时不能合体。

归一
右键任意一只奶蛙，再选择“归一”。所有分身约在 0.45 秒内回到选择的奶蛙并消失，归一期间暂停其他交互，退出仍立即有效。

快捷键与自动表演
Ctrl+Alt+1：全体捧腹大笑。
Ctrl+Alt+C：复制；Ctrl+Alt+V：在鼠标处粘贴；Ctrl+Alt+Q：退出所有桌宠。
奶蛙有键盘焦点时，也可用 1 播放捧腹大笑，Q 或 Esc 退出。各只空闲 3～4 分钟会独立静音捧腹大笑；手动全体表演只播放一份声音。

退出与重启
菜单“退出”会关闭所有奶蛙。下次启动只出现一只，沿用最后保留奶蛙的大小；
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


def place_window(window, x, y, size=None):
    """Move to absolute coordinates; Tk's negative geometry means right-relative."""
    x, y = round(x), round(y)
    prefix = f"{size[0]}x{size[1]}" if size is not None else ""
    window.geometry(f"{prefix}{x:+d}{y:+d}")
    window.update_idletasks()
    if sys.platform == "win32" and isinstance(window, tk.Misc) and (x < 0 or y < 0):
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
        window.update_idletasks()


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

    def play(self, clip_id):
        self.stop()
        player = self.group.audio
        if hasattr(player, "play_voice"):
            return player.play_voice(self.pet, clip_id)
        return player.play(clip_id)

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
        self.runtime = None
        self.closed = False
        self.reunifying = False
        self._reunify_after = None
        self._eye_source = None
        self.help_window = None
        try:
            self.audio.prewarm("laugh")
        except Exception as error:
            from pet_app import log
            log(f"音频预热异常(不影响画面): {error}")

    def register(self, pet):
        self.pets.append(pet)
        if self.primary is None:
            self.primary = pet

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
        window.title("桌宠使用说明")
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

    def paste(self):
        if self.copy_ready:
            return self.spawn(point=self.root.winfo_pointerxy())

    def spawn(self, source=None, point=None):
        if self.closed or self.reunifying or len(self.pets) >= MAX_PETS:
            return None
        from pet_app import PetApp, log
        pet = PetApp.__new__(PetApp)
        try:
            pet.__init__(group=self, scale=CLONE_SCALE)
            box = pet.visible_box()
            if source is not None:
                sx, sy = source.root.winfo_x(), source.root.winfo_y()
                sb = source.visible_box()
                point = (sx + (sb[0] + sb[2]) / 2, sy + sb[3])
                area = work_area(point)
                x, y = sx + sb[2] + 12 - box[0], sy + sb[3] - box[3]
                if x + box[2] > area[2]:
                    x = sx + sb[0] - 12 - box[2]
            else:
                point = point or self.root.winfo_pointerxy()
                area = work_area(point)
                x, y = point[0] - (box[0] + box[2]) / 2, point[1] - (box[1] + box[3]) / 2
            x, y = constrain_origin(x, y, box, area)
            place_window(pet.root, x, y)
            pet.draw()
            pet.tick()
            log(f"新增分身: {len(self.pets)}/{MAX_PETS}, 尺寸={pet.global_scale}")
            return pet
        except Exception:
            if hasattr(pet, "root"):
                self.destroy_pet(pet)
            raise

    def play_all(self, clip_id, with_audio=True):
        if self.closed or self.reunifying or not self.primary or clip_id not in self.primary.clips:
            return
        for pet in tuple(self.pets):
            pet.play(clip_id, with_audio=False)
        success = self.audio.play(clip_id) if with_audio else False
        if success:
            self.audio_members = set(self.pets)
        now = time.monotonic()
        for pet in self.pets:
            pet.last_tick = now
            pet.acc = -float(getattr(self.audio, "output_latency_ms", 0)) if success else 0.0

    def dispatch(self):
        if self.runtime is None or self.closed:
            return
        for action in self.runtime.pending():
            if action == "quit":
                self.quit()
                return
            if action == "show":
                for pet in self.pets:
                    pet.root.deiconify()
                    pet.root.lift()
            elif action == "copy":
                self.copy()
            elif action == "paste":
                self.paste()
            else:
                self.play_all(action)

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
                      for i, p in enumerate(self.pets) if p is not source]
        if not candidates:
            return False
        area, _, target = max(candidates, key=lambda item: item[:2])
        from pet_app import SCALE_MAX, log
        if not area or max(source.global_scale, target.global_scale) >= SCALE_MAX - 1e-9:
            return False
        scale = min(SCALE_MAX, round(source.global_scale + target.global_scale, 4))
        anchor = target.foot_position()
        was_primary = self.primary in (source, target)
        target._to_idle()
        target.set_scale(scale, anchor=anchor, clamp=False)
        if was_primary:
            self.primary = target
            target._save_scale(scale)
        self.destroy_pet(source)
        target.land()
        target._write_geometry_file()
        log(f"合体完成: 尺寸={scale}, 剩余 {len(self.pets)} 只")
        return True

    def destroy_pet(self, pet):
        if pet in self.pets:
            self.pets.remove(pet)
        pet._close_window()

    def reunify(self):
        if self.closed or self.reunifying or len(self.pets) < 2:
            return
        self.reunifying = True
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
                target.land()
                target._write_geometry_file()
            else:
                self._reunify_after = self.root.after(10, advance)
        advance()

    def quit(self):
        if self.closed:
            return
        self.closed = True
        if self.help_window is not None:
            self.help_window.destroy()
            self.help_window = None
        if self.primary is not None:
            self.primary._save_scale(self.primary.global_scale)
        if self._reunify_after is not None:
            self.root.after_cancel(self._reunify_after)
            self._reunify_after = None
        for pet in tuple(self.pets):
            self.destroy_pet(pet)
        self.audio.quit()
        if self.runtime is not None:
            self.runtime.close()
            self.runtime = None
        self._eye_source = None
        self.cache.clear()
        self.root.destroy()
