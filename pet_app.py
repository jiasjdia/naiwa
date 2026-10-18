#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
pet_app.py - Python/tkinter desktop pet.

The Windows window uses a transparent color key. Frames, body deformation,
eye tracking, and audio are controlled by this one Python implementation.

行为(与需求一一对应):
  * 常态 = 大笑第 1 帧静止
  * 待机时全身可按压,1.5秒蓄满后松手局部Q弹;移动>=4px转拖动
  * 待机时拎起略微拉长,松手后脚底原地固定、全身压扁回弹约0.9秒
  * 滚动3秒内有效点击10次,触发完整有声大笑;继续轻点不会掐掉笑声
  * 大笑中2秒点击8次喷出更小分身,弹跳碰撞并堆积,拖过后悬停
  * 右键任一只设为本体,单只/全体大笑与停止分开,大小只作用于本体
  * 四档0.8/0.7/0.6/0.5,最多40只,同档合体升级,归一收回分身
  * 交互触发动作(右键菜单 / 键盘 1、2、3 / 连点身体)播放音频
  * 空闲 180~240 秒随机演一个动作(**只做动作,不出声**)
  * 点洋红(透明)处:鼠标穿透给桌面(由 Windows 抠色自动完成,这里只需不响应)

用法: pythonw pet_app.py      (无控制台)
      python  pet_app.py      (带控制台,便于看日志)
"""

from __future__ import annotations

import json
import math
import random
import sys
import time
import tkinter as tk
from collections import deque
from pathlib import Path

from PIL import Image, ImageTk
from pet_audio import AudioPlayer
from pet_eyes import EyeFollow
from pet_group import (PetGroup, PetVoice, FrameCacheView, MAX_PETS,
                       work_area, constrain_origin, place_window)
from pet_physics import TIERS, snap_scale, tier_of

from pet_paths import ROOT, DATA_ROOT
MANIFEST = ROOT / "assets" / "manifest.json"
SETTINGS = DATA_ROOT / "pet_settings.json"

CHROMA = (255, 0, 255)
CHROMA_HEX = "#ff00ff"
# 抠色阈值:Windows 的 -transparentcolor 只认"完全等于该颜色"的像素,
# 而素材边缘是抗锯齿的半透明像素,合成后会变成"洋红+角色色"的中间色 ——
# 那些像素既不是纯洋红(不会被抠掉),也不是纯角色色,于是显示成一圈紫边。
# 所以 alpha 必须二值化:>= 阈值的算角色(用原色),< 阈值的算透明(用纯洋红)。
ALPHA_CUTOFF = 110
RANDOM_MIN_MS = 180 * 1000      # 3 分钟
RANDOM_MAX_MS = 240 * 1000      # 4 分钟
DRAG_THRESHOLD_PX = 4           # 位移小于它算"点击"
TICK_MS = 10                    # 主循环间隔(再按 fps 决定何时换帧)
FRAME_CACHE_BYTES = 220 * 1024 * 1024   # 帧位图内存上限(超出丢最久未用的)
# 整体显示缩放:窗口尺寸 = 素材窗口尺寸 × displayScale。
#
# 默认值直接取 manifest 的 globalScale —— 那不是随便一个数,而是
# "逻辑像素 -> 屏幕像素"的比例:素材把宠物归一化到 TARGET_PET_HEIGHT 逻辑像素,
# 乘上它正好落到目标屏幕高度。等于它时帧文件是 1:1 贴图(最清晰、也不放大)。
# 想调大小就改它:0.2091 是"宠物 280px",乘 0.5 就是 140px。
DEFAULT_DISPLAY_SCALE = None    # None = 用 manifest.globalScale

# 大小从四档0.5到一档0.8;历史自由尺寸会就近归档。
SCALE_PRESETS = tuple(reversed(TIERS))
SCALE_MIN = min(SCALE_PRESETS)
SCALE_MAX = max(SCALE_PRESETS)
# 「变小/变大」微调一档的步长(和预设档位对齐,方便微调后正好落到某个预设上)
SCALE_STEP = 0.1

JELLY_FRAME_SEC = 1.0 / 60.0
JELLY_DURATION_SEC = 0.70
BODY_PAD = 32                    # 素材基准像素;只增加透明余量,不放大角色
BODY_LAND_SEC = 0.90
LAUGH_CLICKS = 10
LAUGH_WINDOW_SEC = 3.0
SPLIT_CLICKS = 8
SPLIT_WINDOW_SEC = 2.0
EMITTED_LOOP_FIRST_FRAME = 54  # Human-facing, inclusive; internal indices are zero-based.
CHARGE_SEC = 1.5

LOG_PATH = DATA_ROOT / "_preview" / "pet.log"


def load_settings() -> dict:
    """用户可调参数。没有文件就用默认值,并且写一份出来方便改。"""
    defaults = {"displayScale": DEFAULT_DISPLAY_SCALE, "fps": None,
                "randomMinSec": 180, "randomMaxSec": 240}
    packaged_defaults = ROOT / "pet_settings.json"
    if SETTINGS != packaged_defaults and packaged_defaults.is_file():
        try:
            data = json.loads(packaged_defaults.read_text(encoding="utf-8"))
            defaults.update({k: v for k, v in data.items() if v is not None})
        except (OSError, ValueError, AttributeError):
            pass
    if SETTINGS.exists():
        try:
            data = json.loads(SETTINGS.read_text(encoding="utf-8"))
            defaults.update({k: v for k, v in data.items() if v is not None})
        except Exception as e:
            log(f"pet_settings.json 解析失败,用默认值: {e}")
    else:
        try:
            SETTINGS.parent.mkdir(parents=True, exist_ok=True)
            SETTINGS.write_text(json.dumps(defaults, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return defaults


def log(*parts) -> None:
    msg = " ".join(str(p) for p in parts)
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    if sys.stdout is not None:
        print(line, flush=True)
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _opaque_mask(rgba: Image.Image) -> Image.Image:
    """显示和鼠标命中共用同一个二值透明阈值。"""
    return rgba.getchannel("A").point(lambda a: 255 if a >= ALPHA_CUTOFF else 0)


def _chroma_frame(rgba: Image.Image) -> tuple[Image.Image, Image.Image]:
    # 必须在 RGBA 变形完成之后抠色,不能重采样已经混入洋红的 RGB。
    mask = _opaque_mask(rgba)
    flat = Image.new("RGB", rgba.size, CHROMA)
    flat.paste(rgba.convert("RGB"), (0, 0), mask)
    return flat, mask


def _begin_jelly_timer():
    """Windows 动画期间临时请求 1ms 精度,结束时必须配对 timeEndPeriod。

    https://learn.microsoft.com/windows/win32/api/timeapi/nf-timeapi-timebeginperiod
    """
    if sys.platform == "win32":
        try:
            import ctypes
            timer = ctypes.WinDLL("winmm")
            if timer.timeBeginPeriod(1) == 0:
                return timer
        except OSError:
            pass
    return None


class _LocalJelly:
    """全身多点局部弹簧。一次从原始 RGBA 采样,绝不反复变形上一帧。"""

    DECAY, FREQUENCY = 8.0, 24.0
    LIMIT = 3.0                         # 相对各受力部位的短按峰值

    def __init__(self, rgba: Image.Image):
        self.rgba = rgba.copy()
        self.sx, self.sy = rgba.width / 320.0, rgba.height / 305.0
        self.mask = _opaque_mask(rgba)
        self.bbox = self.mask.getbbox() or (0, 0, rgba.width, rgba.height)
        self.body_width = self.bbox[2] - self.bbox[0]
        self.pad = round(BODY_PAD * self.sy)
        self.origin = (0, -self.pad)
        self.size = (rgba.width, rgba.height + 2 * self.pad)
        self.fields = []
        self.held = None
        self.last_poke = 0.0
        self._mesh = None
        self._patch_runs = []
        self._state = None
        step = max(3, round(8 * min(self.sx, self.sy)))
        self.xs = list(range(0, rgba.width, step)) + [rgba.width]
        self.ys = list(range(-self.pad, rgba.height + self.pad, step)) + [rgba.height + self.pad]
        self._nodes = [(x, y) for y in self.ys for x in self.xs]
        self._points = self._nodes
        self._cells = []
        self._node_cells = [[] for _ in self._nodes]
        stride = len(self.xs)
        for j in range(len(self.ys) - 1):
            for i in range(stride - 1):
                n = j * stride + i
                box = (self.xs[i], self.ys[j] + self.pad,
                       self.xs[i + 1], self.ys[j + 1] + self.pad)
                corners = (n, n + stride, n + stride + 1, n + 1)
                for corner in corners:
                    self._node_cells[corner].append(len(self._cells))
                self._cells.append((box, corners))
        self._reference_thickness = self._thickness(146 * self.sx, 180 * self.sy)

    @property
    def active(self):
        return self.held is not None or bool(self.fields)

    @staticmethod
    def _smooth(t):
        t = max(0.0, min(1.0, t))
        return t * t * (3 - 2 * t)

    def _thickness(self, x, y):
        """取穿过点击点的连续不透明弦长,让细耳朵/腿的形变同比缩小。"""
        x = max(0, min(self.rgba.width - 1, round(x)))
        y = max(0, min(self.rgba.height - 1, round(y)))
        pixels = self.mask.load()
        spans = []
        for dx, dy, limit in ((1, 0, self.rgba.width), (0, 1, self.rgba.height)):
            span = 1
            for sign in (-1, 1):
                for n in range(1, limit):
                    u, v = x + sign * dx * n, y + sign * dy * n
                    if not (0 <= u < self.rgba.width and 0 <= v < self.rgba.height) or not pixels[u, v]:
                        break
                    span += 1
            spans.append(span)
        return max(1, min(spans))

    def _field(self, point, now):
        x, y = point
        factor = max(0.12, min(1.0, self._thickness(x, y) / self._reference_thickness))
        amplitude = 0.12 * self.body_width * factor
        rx, ry = 110 * self.sx * factor, 72 * self.sy * factor
        # MESH 是目标到源的反向采样:向外取样才能让可见纹理向点击处聚拢。
        # r*(1-r²)^3 在 r=1/sqrt(7) 达峰,归一化后沿用短按的位移标定。
        radial_peak = (6 / 7) ** 3 / math.sqrt(7)
        gain = amplitude / (max(rx, ry) * radial_peak)
        nodes = []
        for i, (u, v) in enumerate(self._nodes):
            r2 = ((u - x) / rx) ** 2 + ((v - y) / ry) ** 2
            if r2 < 1:
                weight = (1 - r2) ** 3
                nodes.append((i, (u - x) * gain * weight,
                              (v - y) * gain * weight, weight))
        return _JellyField(point, nodes, now)

    def begin_press(self, point, now):
        self.advance(now)
        self.cancel_press(now)
        self.held = self._field(point, now)

    @classmethod
    def _peak(cls, q, velocity):
        d, w = cls.DECAY, cls.FREQUENCY
        b = (velocity + d * q) / w
        theta = math.atan(w / d)
        t = ((theta - math.atan2(q, b)) % (2 * math.pi)) / w
        return math.hypot(q, b) * math.exp(-d * t) * math.sin(theta)

    def release(self, now):
        if self.held is None:
            return
        self.advance(now)
        field, self.held = self.held, None
        charge = self._smooth((now - field.started) / CHARGE_SEC)
        # 仅给本次输入分配剩余弹力额度,不缩放或重置旧场的位移。
        envelope = [0.0] * len(self._nodes)
        same_point = 0.0
        for old in self.fields:
            bound = max(abs(old.q), self._peak(old.q, old.velocity), self._peak(-old.q, -old.velocity))
            if old.center == field.center:
                same_point += bound
            for i, _, _, weight in old.nodes:
                envelope[i] += weight * bound
        available = min(((self.LIMIT - envelope[i]) / weight
                         for i, _, _, weight in field.nodes), default=self.LIMIT)
        available = min(available, self.LIMIT - same_point)
        target = min(1 + charge, max(field.q, available, 0.0))
        # 根据松开时已有的收缩校准峰值,而不是把速度简单乘二。
        lo, hi = 0.0, 100.0
        for _ in range(28):
            mid = (lo + hi) / 2
            if self._peak(field.q, mid) < target:
                lo = mid
            else:
                hi = mid
        field.velocity = (lo + hi) / 2
        field.released = field.last_time = self.last_poke = now
        if field.q > 1e-8 or field.velocity > 1e-8:
            self.fields.append(field)

    def cancel_press(self, now):
        if self.held is not None:
            # 转拖拽时不让已显示的收缩骤然消失;只释放已有变形,不注入点击弹力。
            field, self.held = self.held, None
            field.released = field.last_time = now
            self.fields.append(field)

    def advance(self, now):
        d, w = self.DECAY, self.FREQUENCY
        self.fields = [f for f in self.fields if now - f.released < JELLY_DURATION_SEC]
        for field in self.fields:
            dt = max(0.0, now - field.last_time)
            e, c, s = math.exp(-d * dt), math.cos(w * dt), math.sin(w * dt)
            q, v = field.q, field.velocity
            field.q = e * (q * c + (v + d * q) / w * s)
            field.velocity = e * (v * c - (d * v + (d * d + w * w) * q) / w * s)
            field.last_time = now
        if self.held is not None:
            progress = self._smooth((now - self.held.started) / CHARGE_SEC)
            # 只减半按住时的回缩;release() 仍按原目标独立校准回弹幅度。
            self.held.q = 0.5 * progress * (1 + progress)

    @staticmethod
    def _area(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    def _compile(self):
        fields = self.fields + ([self.held] if self.held is not None else [])
        state = tuple((id(f), f.q, f.velocity) for f in fields)
        if state == self._state:
            return
        self._state = state
        offsets = {}
        for field in fields:
            bound = max(abs(field.q), self._peak(field.q, field.velocity),
                        self._peak(-field.q, -field.velocity))
            for i, dx, dy, weight in field.nodes:
                offset = offsets.setdefault(i, [0.0, 0.0, 0.0])
                offset[0] += dx * field.q
                offset[1] += dy * field.q
                offset[2] += weight * bound
        offsets = {i: (dx * min(1.0, self.LIMIT / max(1e-9, bound)),
                       dy * min(1.0, self.LIMIT / max(1e-9, bound)))
                   for i, (dx, dy, bound) in offsets.items() if abs(dx) + abs(dy) > 1e-9}
        affected = sorted({cell for i in offsets for cell in self._node_cells[i]})
        # 共享节点防接缝;限制 Jacobian 防止耳尖等细部翻折。
        gain = 1.0
        while True:
            points = self._nodes.copy()
            for i, (dx, dy) in offsets.items():
                x, y = self._nodes[i]
                points[i] = (x + gain * dx, y + gain * dy)
            safe = True
            for cell in affected:
                box, (a, b, c, d) = self._cells[cell]
                minimum = (box[2] - box[0]) * (box[3] - box[1]) * 0.25
                if (self._area(points[a], points[c], points[b]) < minimum
                        or self._area(points[a], points[d], points[c]) < minimum):
                    safe = False
                    break
            if safe:
                break
            gain *= 0.8
        self._points = points
        self._mesh = []
        self._patch_runs = []
        for cell in affected:
            box, corners = self._cells[cell]
            self._mesh.append((box, tuple(value for n in corners for value in points[n])))
            if self._patch_runs and self._patch_runs[-1][1::2] == box[1::2] and self._patch_runs[-1][2] == box[0]:
                left, top, _, bottom = self._patch_runs[-1]
                self._patch_runs[-1] = (left, top, box[2], bottom)
            else:
                self._patch_runs.append(box)

    def inverse(self, x, y):
        """使用刚刚显示的网格进行反查,而非用下一个动画帧猜测点击位置。"""
        if self._mesh is None or not (0 <= x < self.size[0] and -self.pad <= y < self.size[1] - self.pad):
            return x, y
        from bisect import bisect_right
        i, j = bisect_right(self.xs, x) - 1, bisect_right(self.ys, y) - 1
        tx = (x - self.xs[i]) / (self.xs[i + 1] - self.xs[i])
        ty = (y - self.ys[j]) / (self.ys[j + 1] - self.ys[j])
        n, stride = j * len(self.xs) + i, len(self.xs)
        a, b, c, d = [self._points[k] for k in (n, n + stride, n + stride + 1, n + 1)]
        return tuple((1 - tx) * ((1 - ty) * a[k] + ty * b[k])
                     + tx * ((1 - ty) * d[k] + ty * c[k]) for k in (0, 1))

    def render_rgba(self, rgba=None):
        self._compile()
        source = self.rgba if rgba is None else rgba
        result = Image.new("RGBA", self.size)
        result.paste(source, (0, self.pad))
        # 不重采样无形变区:RGBA 的预乘/反预乘也会改变原有透明像素。
        if self._mesh:
            patch = source.transform(self.size, Image.Transform.MESH, self._mesh, Image.Resampling.BICUBIC)
            for box in self._patch_runs:
                result.paste(patch.crop(box), box[:2])
        return result


class _JellyField:
    """一个固定在素材坐标中的局部受力,强度以本部位短按峰值为单位。"""

    def __init__(self, center, nodes, now):
        self.center, self.nodes = center, nodes
        self.started = self.last_time = now
        self.released = now
        self.q = self.velocity = 0.0


class _BodyJelly:
    """原图上的全身仿射形变。抓取点固定,放下后改用当时的脚底作锚点。"""

    DECAY, FREQUENCY = 7.0, 20.0
    PICKUP_SEC, HANDOFF_SEC = 0.12, 0.08

    def __init__(self, rgba: Image.Image):
        self.rgba = rgba.copy()
        bbox = _opaque_mask(rgba).getbbox() or (0, 0, rgba.width, rgba.height)
        self.foot = ((bbox[0] + bbox[2]) / 2.0, float(bbox[3]))
        self.height = float(bbox[3] - bbox[1])
        self.pose = (1.0, 1.0, 0.0, 0.0)  # sx, sy, tx, ty;相对素材内容坐标
        self.phase = "done"
        self.compression = self.velocity = 0.0
        self.floor = self.foot
        self.started = self.last_time = 0.0

    @property
    def moving(self) -> bool:
        return self.phase in ("pickup", "landing", "handoff")

    def forward(self, x: float, y: float) -> tuple[float, float]:
        sx, sy, tx, ty = self.pose
        return sx * x + tx, sy * y + ty

    def inverse(self, x: float, y: float) -> tuple[float, float]:
        sx, sy, tx, ty = self.pose
        return (x - tx) / sx, (y - ty) / sy

    def pickup(self, x: float, y: float, now: float) -> None:
        # 从正在显示的形变继续,包括上一轮还未收住的落地形变。
        self.grip = self.inverse(x, y)
        self.pin = (x, y)
        self.start_sy = self.pose[1]
        self.phase = "pickup"
        self.started = self.last_time = now

    def _anchored_pose(self, sy: float, source, target) -> None:
        sx = 1.0 / math.sqrt(sy)
        self.pose = (sx, sy, target[0] - sx * source[0], target[1] - sy * source[1])

    @classmethod
    def _peak(cls, position: float, velocity: float) -> float:
        d, w = cls.DECAY, cls.FREQUENCY
        b = (velocity + d * position) / w
        phase = math.atan2(position, b)
        theta = math.atan(w / d)
        t = ((theta - phase) % (2 * math.pi)) / w
        return math.hypot(position, b) * math.exp(-d * t) * math.sin(theta)

    def release(self, speed: float, now: float) -> None:
        self.floor = self.forward(*self.foot)
        self.compression = 1.0 - self.pose[1]
        # speed 已按角色可见身高归一化,各大小档位的手感一致。
        self.strength = 0.10 + 0.12 * min(1.0, max(0.0, speed) / 2.5)
        target = max(self.strength, self.compression)
        lo, hi = 0.0, 20.0
        for _ in range(28):
            mid = (lo + hi) / 2
            if self._peak(self.compression, mid) < target:
                lo = mid
            else:
                hi = mid
        self.velocity = (lo + hi) / 2
        self.phase = "landing"
        self.started = self.last_time = now

    def handoff(self, now: float) -> None:
        self.floor = self.forward(*self.foot)
        self.start_sy = self.pose[1]
        self.phase = "handoff"
        self.started = self.last_time = now

    def advance(self, now: float) -> None:
        if self.phase in ("pickup", "handoff"):
            duration = self.PICKUP_SEC if self.phase == "pickup" else self.HANDOFF_SEC
            t = min(1.0, max(0.0, (now - self.started) / duration))
            ease = t * t * (3.0 - 2.0 * t)
            target_sy = 1.08 if self.phase == "pickup" else 1.0
            sy = self.start_sy + (target_sy - self.start_sy) * ease
            if self.phase == "pickup":
                self._anchored_pose(sy, self.grip, self.pin)
                if t >= 1:
                    self.phase = "held"
            else:
                self._anchored_pose(sy, self.foot, self.floor)
                if t >= 1:
                    self.phase = "done"
        elif self.phase == "landing":
            if now - self.started >= BODY_LAND_SEC:
                self.compression = self.velocity = 0.0
                self._anchored_pose(1.0, self.foot, self.floor)
                self.phase = "done"
            else:
                dt = max(0.0, now - self.last_time)
                d, w = self.DECAY, self.FREQUENCY
                e, c, s = math.exp(-d * dt), math.cos(w * dt), math.sin(w * dt)
                q, v = self.compression, self.velocity
                self.compression = e * (q * c + (v + d * q) / w * s)
                self.velocity = e * (v * c - (d * v + (d * d + w * w) * q) / w * s)
                self._anchored_pose(1.0 - self.compression, self.foot, self.floor)
        self.last_time = now

    def render_rgba(self, rgba: Image.Image, size, origin, source_origin=(0, 0)) -> Image.Image:
        sx, sy, tx, ty = self.pose
        return rgba.transform(size, Image.Transform.AFFINE,
                              (1 / sx, 0, -(tx + origin[0]) / sx - source_origin[0],
                               0, 1 / sy, -(ty + origin[1]) / sy - source_origin[1]), Image.Resampling.BICUBIC)


class Clip:
    def __init__(self, meta: dict, assets: Path, scale: float):
        self.id = meta["id"]
        self.label = meta["label"]
        self.frames = int(meta["frames"])
        self.dir = meta["dir"]
        self.pad = max(4, len(str(self.frames)))
        self.paths = [assets / self.dir / f"f{i + 1:0{self.pad}d}.webp" for i in range(self.frames)]
        st = meta["window"]["store"]
        # 三个动作使用相同内容原点,上下透明边距只给全身拉长留空间。
        self.src_w = max(1, int(st["w"]))     # 帧文件原始像素(用于坐标换算)
        self.src_h = max(1, int(st["h"]))
        self.fw = max(1, int(round(st["w"] * scale)))
        self.fh = max(1, int(round(st["h"] * scale)))
        self.fx = 0
        self.fy = round(BODY_PAD * scale)


class PetApp:
    def __init__(self, group=None, scale=None) -> None:
        self.group = group
        if not MANIFEST.exists():
            raise SystemExit(f"找不到素材清单: {MANIFEST}")
        m = group.manifest if group is not None else json.loads(MANIFEST.read_text(encoding="utf-8"))
        self.manifest = m
        self.assets = ROOT / "assets"
        self.settings = group.settings if group is not None else load_settings()
        self.fps = float(self.settings.get("fps") or m.get("fps") or 25)
        self.frame_ms = 1000.0 / self.fps
        self.canvas_w = int(m["canvas"]["w"])
        self.canvas_h = int(m["canvas"]["h"])
        # displayScale 缺省时用 manifest.globalScale:这才是"素材设计好的显示比例",
        # 用别的值会让窗口和帧文件对不上(帧要么被放大发虚,要么被缩小浪费)。
        gs = self.settings.get("displayScale")
        initial_scale = scale if scale is not None else gs or m.get("globalScale") or 1.0
        self.global_scale = snap_scale(initial_scale)
        # 角色内容尺寸 = 素材给的"窗口显示尺寸" × displayScale;窗口另加透明边距。
        # 素材已经保证"三个动作的帧文件一样大、且第一帧身体在帧内的位置完全相同",
        # 所以三个动作始终使用同样的透明边距和内容原点,切换时锚点重合。
        # 取 clips[0].window.store(帧文件实际像素,320x305);这是窗口尺寸的"基准",
        # 改大小时乘新的 scale 就得到新窗口尺寸。
        st0 = m["clips"][0]["window"]["store"]
        self.base_w = max(1, int(st0["w"]))
        self.base_h = max(1, int(st0["h"]))
        self.win_w = max(1, int(round(self.base_w * self.global_scale)))
        self.pad_y = round(BODY_PAD * self.global_scale)
        self.win_h = max(1, int(round(self.base_h * self.global_scale))) + 2 * self.pad_y
        self.origin = (0.0, 0.0)
        self.random_min_ms = float(self.settings["randomMinSec"]) * 1000
        self.random_max_ms = float(self.settings["randomMaxSec"]) * 1000
        if self.group is None:
            self.group = PetGroup(m, self.settings, AudioPlayer(), FRAME_CACHE_BYTES)
        self.audio = PetVoice(self.group, self)
        # The active clips drive the menu, focused keys, and automatic performances.
        self.clips = {c["id"]: Clip(c, self.assets, self.global_scale)
                      for c in m["clips"] if c["id"] == "laugh"}
        self.order = list(self.clips)
        self.idle_clip = m.get("idleClip") or self.order[0]
        self.idle_frame = int(m.get("idleFrame") or 1) - 1

        # ---- 状态 ----
        self.mode = "idle"                 # idle | playing
        self.cur_clip = self.idle_clip
        self.cur_index = self.idle_frame
        self.acc = 0.0
        self.deadline = time.monotonic() + random.uniform(self.random_min_ms, self.random_max_ms) / 1000.0
        self.last_tick = time.monotonic()
        self.menu_open = False
        self.dragging = False
        self.drag_moved = False
        self._press_valid = False
        self._press_local = (0, 0)
        self._clicks = deque(maxlen=LAUGH_CLICKS)
        self._click_laugh_active = False
        self._split_clicks = deque(maxlen=SPLIT_CLICKS)
        self._action_start = None
        self._action_serial = 0
        self._action_audible = False
        self._emitted = False
        self._loop_start = None
        self._press_idle = False
        self._drag_body_eligible = False
        self._drag_samples = deque(maxlen=128)
        self._body: _BodyJelly | None = None
        self._idle_remaining: float | None = None
        self._jelly: _LocalJelly | None = None
        self._jelly_photo: ImageTk.PhotoImage | None = None
        self._jelly_mask: bytes | None = None
        self._mask_origin = (0, 0)
        self._mask_size = (0, 0)
        self._jelly_next_frame = 0.0
        self._jelly_timer = None
        self._tick_after: str | None = None
        self._focus_after = None
        self._closed = False
        self._runtime = None
        self._eyes = None
        self._eye_photo = None
        self._eye_next_frame = 0.0
        self._idle_rgba = None

        # ---- 帧缓存(按显示尺寸直接缓存 PhotoImage,LRU 淘汰) ----
        self.cache = FrameCacheView(self)
        self._display_photo = None
        # 命中判定用的 alpha 缓存(只在帧变化时重算)
        self._alpha_key: tuple | None = None
        self._alpha: bytes | None = None
        self._alpha_size = (0, 0)        # alpha 缓冲的像素尺寸(帧文件分辨率)
        self._alpha_display = (1, 1)     # 该缓冲对应的显示尺寸(用于坐标换算)

        self._build_window()
        self._setup_eyes()
        self.group.register(self)
        log(f"启动: 窗口 {self.win_w}x{self.win_h} 画布 {self.canvas_w}x{self.canvas_h} "
            f"fps={self.fps} 待机={self.idle_clip}#{self.idle_frame + 1} "
            f"动作={[self.clips[c].label for c in self.order]}")
        # 把"每个动作第一帧在窗口里的落点"打出来。
        # 切换动作如果看起来还在偏移,一眼就能看出是哪个动作的几何没对齐
        # (第一帧锚点必须三个动作完全一致)。
        for cid in self.order:
            clip = self.clips[cid]
            bb = self._frame_alpha_bbox(cid, 0)
            if bb is None:
                continue
            # 换算到"窗口坐标"(= 屏幕上的窗口内坐标),这样和 displayScale 无关,
            # 调大调小都看得出三个动作是否对齐。
            x0, y0, x1, y1 = bb
            kx = clip.fw / clip.src_w
            ky = clip.fh / clip.src_h
            cx = clip.fx + (x0 + x1) / 2.0 * kx
            bot = clip.fy + (y1 + 1) * ky
            log(f"  第一帧几何 {cid:<7} 帧={clip.src_w}x{clip.src_h}→窗口 {clip.fw}x{clip.fh} "
                f"落点=({clip.fx},{clip.fy}) -> 窗口内 中心x={cx:.1f} 底边y={bot:.1f}")

    # ------------------------------------------------------------------ 窗口
    def _build_window(self) -> None:
        self.root = tk.Toplevel(self.group.root)
        self.root.title("奶小蛙")
        icon = ROOT / "assets/app.ico"
        if icon.is_file():
            self.group.root.iconbitmap(default=str(icon))
        self.root.overrideredirect(True)                  # 无边框
        self.root.attributes("-topmost", True)            # 置顶
        try:
            self.root.attributes("-transparentcolor", CHROMA_HEX)   # Windows 抠色透明
        except tk.TclError as e:
            log("警告: 本平台不支持 -transparentcolor,宠物会带一块背景:", e)
        self.root.config(bg=CHROMA_HEX)

        x, y = self._initial_position()
        self.root.geometry(f"{self.win_w}x{self.win_h}+{x}+{y}")

        self.canvas = tk.Canvas(self.root, width=self.win_w, height=self.win_h,
                                highlightthickness=0, bd=0, bg=CHROMA_HEX)
        self.canvas.pack(fill="both", expand=True)
        self.image_id = self.canvas.create_image(0, 0, anchor="nw")

        # 菜单用 tkinter 自绘(原生 Menu 在 overrideredirect 窗口上弹出位置会飘)
        self.menu = tk.Menu(self.root, tearoff=0)
        for cid in self.order:
            self.menu.add_command(label=self.clips[cid].label,
                                  command=lambda c=cid: self.play(c, with_audio=True))
        self.menu.add_command(label="全部大笑", command=lambda: self.play_all("laugh", with_audio=True))
        self.menu.add_command(label="停止大笑", command=self.stop_laugh)
        self.menu.add_command(label="停止全部大笑", command=self.group.stop_all_laughs)
        self.menu.add_separator()
        self.menu.add_command(label="复制分身", command=lambda: self.group.duplicate(self))
        self.menu.add_command(label="归一", command=self.group.reunify)
        self.menu.add_separator()

        # ---- 大小子菜单 ----
        # 预设档用 radiobutton:tk 会在当前档位前面画一个选中标记,一眼看出现在是哪档。
        # 变量值刻意用"档位序号"而不是 scale 本身 —— scale 是浮点数,字符串比较容易对不上。
        self.scale_var = tk.IntVar(master=self.root, value=self._preset_index())
        self.size_menu = tk.Menu(self.menu, tearoff=0)
        self.size_menu.add_command(label="变小  −", command=lambda: self.group.primary.step_scale(-1))
        self.size_menu.add_command(label="变大  ＋", command=lambda: self.group.primary.step_scale(+1))
        self.size_menu.add_separator()
        for i, s in enumerate(SCALE_PRESETS):
            h = int(round(self.base_h * s))
            self.size_menu.add_radiobutton(
                label=f"{tier_of(s)}档  {s:.1f}   （{h}px 高）", value=i,
                variable=self.scale_var, command=lambda i=i: self.group.primary.set_scale(SCALE_PRESETS[i]))
        self.menu.add_cascade(label="大小", menu=self.size_menu)

        self.menu.add_command(label="使用说明", command=self.group.show_help)
        self.menu.add_separator()
        self.menu.add_command(label="退出", command=self.quit)

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_motion)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right)
        self.root.bind("<FocusOut>", self._on_focus_out)
        self.root.bind("<Escape>", lambda e: self.quit())

        # 兜底快捷键(不需要焦点也行? 需要窗口有焦点;这里保留,配合右键菜单)
        for i, cid in enumerate(self.order, start=1):
            self.root.bind(f"<Key-{i}>", lambda e, c=cid: self.play_all(c, with_audio=True))
        self.root.bind("<Key-q>", lambda e: self.quit())

        self.root.protocol("WM_DELETE_WINDOW", self.quit)

    def _initial_position(self) -> tuple[int, int]:
        """默认放在任务栏上方的右下角(最不容易挡到东西的位置)。"""
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        # 用 winfo_screenheight 有时会把任务栏算进去,留 100px 余量更稳
        return max(0, sw - self.win_w - 40), max(0, sh - self.win_h - 100 + self.pad_y)

    # ------------------------------------------------------------------ 大小
    def _preset_index(self) -> int:
        """当前 scale 最接近哪个预设档(用于菜单里的选中标记)。"""
        cur = self.global_scale
        return min(range(len(SCALE_PRESETS)),
                   key=lambda i: abs(SCALE_PRESETS[i] - cur))

    @property
    def cache_bytes(self):
        return self.cache.bytes

    def visible_box(self):
        clip = self.clips[self.idle_clip]
        rgba = self._read_rgba(self.idle_clip, self.idle_frame)
        box = _opaque_mask(rgba).getbbox() if rgba is not None else None
        x0, y0, x1, y1 = box or (0, 0, clip.fw, clip.fh)
        return x0 + clip.fx, y0 + clip.fy, x1 + clip.fx, y1 + clip.fy

    def foot_position(self):
        box = self.visible_box()
        return self.root.winfo_x() + (box[0] + box[2]) / 2, self.root.winfo_y() + box[3]

    def screen_mask(self):
        """The displayed binary silhouette, including the current body deformation."""
        if self._jelly_mask is not None:
            mask = Image.frombytes("L", self._mask_size, self._jelly_mask)
            ox, oy = self._mask_origin
        else:
            clip = self.clips[self.cur_clip]
            rgba = self._read_rgba(self.cur_clip, self.cur_index)
            mask = _opaque_mask(rgba) if rgba is not None else Image.new("L", (clip.fw, clip.fh))
            ox, oy = clip.fx, clip.fy
        ox, oy = round(ox), round(oy)
        left, top = max(0, -ox), max(0, -oy)
        right, bottom = min(mask.width, self.win_w - ox), min(mask.height, self.win_h - oy)
        if right <= left or bottom <= top:
            return Image.new("L", (1, 1)), self.root.winfo_x(), self.root.winfo_y()
        mask = mask.crop((left, top, right, bottom))
        return mask, self.root.winfo_x() + ox + left, self.root.winfo_y() + oy + top

    def _write_geometry_file(self) -> None:
        """把窗口实际几何写给自动化截图验证用(缩放后也要刷新,否则它会按旧尺寸找)。"""
        if getattr(self, "group", None) is not None and self.group.primary is not self:
            return
        try:
            geo = {"x": self.root.winfo_x(), "y": self.root.winfo_y(),
                   "w": self.win_w, "h": self.win_h, "scale": self.global_scale,
                   "screen": [self.root.winfo_screenwidth(), self.root.winfo_screenheight()]}
            (DATA_ROOT / "_preview").mkdir(parents=True, exist_ok=True)
            (DATA_ROOT / "_preview" / "pet_geometry.json").write_text(
                json.dumps(geo, ensure_ascii=False), encoding="utf-8")
        except Exception as e:
            log(f"写几何信息失败: {e}")

    def set_scale(self, scale: float, *, anchor=None, clamp=True) -> None:
        """立刻改变宠物大小(不用重启)。

        更新动作、画布及命中尺寸,以脚底中心为锚点;共享缓存按尺寸区分。
        只有当前本体写回设置。合体可指定目标锚点并保持准确落点。
        """
        if getattr(self, "group", None) is not None and self.group.reunifying:
            return
        scale = snap_scale(scale)
        if abs(scale - self.global_scale) < 1e-9:
            return
        anchor = self.foot_position() if anchor is None else anchor
        self._drop_jelly()
        if getattr(self, "group", None) is not None:
            self.group.cancel_emission(self)
        if hasattr(self, "_split_clicks"):
            self._split_clicks.clear()
        old = self.global_scale
        self.global_scale = scale

        new_w = max(1, int(round(self.base_w * scale)))
        new_h = max(1, int(round(self.base_h * scale))) + 2 * round(BODY_PAD * scale)
        self.pad_y = round(BODY_PAD * scale)

        # 1) 三个动作一起改尺寸,保证切换动作时仍然严丝合缝
        for clip in self.clips.values():
            clip.fw = max(1, int(round(clip.src_w * scale)))
            clip.fh = max(1, int(round(clip.src_h * scale)))
            clip.fy = self.pad_y
        # 2) 窗口 / 画布
        self.win_w, self.win_h = new_w, new_h
        self.canvas.config(width=new_w, height=new_h)
        box = self.visible_box()
        nx, ny = anchor[0] - (box[0] + box[2]) / 2, anchor[1] - box[3]
        if clamp:
            nx, ny = constrain_origin(nx, ny, box, work_area(anchor))
        place_window(self.root, nx, ny, (new_w, new_h))
        # Shared frame keys include the displayed dimensions; other pets retain their frames.
        # 4) alpha 缓存失效,下次右键时按新尺寸重算
        self._alpha_key = None
        self._alpha = None
        self._setup_eyes()

        log(f"尺寸 {old:.2f} → {scale:.2f}(宠物高 {self.win_h}px,窗口 "
            f"{new_w}x{new_h},位置 {nx},{ny})")
        # 5) 写回配置
        self._save_scale(scale)
        self.draw()
        self._write_geometry_file()
        if getattr(self, "group", None) is not None:
            self.group.resize_physics(self)

    def _save_scale(self, scale: float) -> None:
        """把新尺寸写回 pet_settings.json(保留文件里其他键,不乱格式化)。"""
        if getattr(self, "group", None) is not None and self.group.primary is not self:
            return
        try:
            data: dict = {}
            if SETTINGS.exists():
                try:
                    data = json.loads(SETTINGS.read_text(encoding="utf-8"))
                except Exception:
                    data = {}
            if not isinstance(data, dict):
                data = {}
            data["displayScale"] = round(scale, 4)
            self.settings["displayScale"] = data["displayScale"]
            SETTINGS.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                                encoding="utf-8")
        except Exception as e:
            log(f"保存尺寸失败(不影响本次使用): {e}")

    def step_scale(self, direction: int) -> None:
        """变小/变大各一档(±SCALE_STEP),到边界就停。"""
        target = round(self.global_scale + direction * SCALE_STEP, 4)
        target = max(SCALE_MIN, min(SCALE_MAX, target))
        if abs(target - self.global_scale) < 1e-9:
            log(f"已经到{'最小' if direction < 0 else '最大'}尺寸({self.global_scale:.2f})")
            return
        self.set_scale(target)
        self.scale_var.set(self._preset_index())

    # ------------------------------------------------------------------ 帧
    def _setup_eyes(self) -> None:
        self._eyes = None
        self._eye_photo = None
        self._idle_rgba = None
        self._eye_next_frame = 0.0
        try:
            clip = self.clips[self.idle_clip]
            self._eyes = self.group.eyes((clip.fw, clip.fh))
            self._idle_rgba = self._read_rgba(self.idle_clip, self.idle_frame)
        except Exception as error:
            log(f"眼部素材不可用,使用原待机图: {error}")

    def _update_eyes(self, now: float) -> bool:
        eyes = getattr(self, "_eyes", None)
        if eyes is None or self.mode != "idle" or self.menu_open:
            return False
        if now < self._eye_next_frame:
            return False
        self._eye_next_frame = max(self._eye_next_frame + EyeFollow.INTERVAL, now)
        try:
            px, py = self.root.winfo_pointerxy()
            if px == -1 and py == -1:
                return False
            point = self._content_point(px - self.root.winfo_rootx(), py - self.root.winfo_rooty())
            return eyes.advance(point, now)
        except tk.TclError:
            return False

    def _read_rgba(self, clip_id: str, index: int) -> "Image.Image | None":
        """按显示大小读取原始 RGBA,局部变形必须在抠色之前进行。"""
        clip = self.clips[clip_id]
        index = max(0, min(index, clip.frames - 1))
        group = getattr(self, "group", None)
        key = ("rgba", clip_id, index, clip.fw, clip.fh)
        if group is not None:
            hit = group.cache.get(key)
            if hit is not None:
                return hit
        try:
            with Image.open(clip.paths[index]) as im:
                im = im.convert("RGBA")
                if (im.width, im.height) != (clip.fw, clip.fh):
                    im = im.resize((clip.fw, clip.fh), Image.LANCZOS)
                return group.cache.put(key, im, clip.fw * clip.fh * 4) if group is not None else im
        except Exception as e:
            log(f"读帧失败 {clip_id}#{index + 1}: {e}")
            return None

    def _load_frame(self, clip_id: str, index: int) -> "Image.Image | None":
        """透明处用纯洋红,不透明处保留原色,避免混色紫边。"""
        rgba = self._read_rgba(clip_id, index)
        return _chroma_frame(rgba)[0] if rgba is not None else None

    def _frame_image(self, clip_id: str, index: int) -> ImageTk.PhotoImage | None:
        """取一帧的 PhotoImage(按显示尺寸缓存,LRU 淘汰)。"""
        clip = self.clips[clip_id]
        index = max(0, min(index, clip.frames - 1))
        key = ("photo", clip_id, index, clip.fw, clip.fh)
        hit = self.group.cache.get(key)
        if hit is not None:
            return hit

        flat = self._load_frame(clip_id, index)
        if flat is None:
            return None
        photo = ImageTk.PhotoImage(flat, master=self.group.root)
        nbytes = clip.fw * clip.fh * 4
        return self.group.cache.put(key, photo, nbytes)

    def _frame_alpha_bbox(self, clip_id: str, index: int):
        """某一帧里角色的包围盒(帧文件坐标)。用于对齐自检。

        阈值用 8(和素材流水线的 alpha>16 口径一致),不能用抠色阈值 ALPHA_CUTOFF=110 ——
        角色边缘有一圈 alpha 8~64 的淡边,用高阈值量出来的中心会偏几个像素,
        看起来像"没对齐",其实是量法不一致。
        """
        clip = self.clips[clip_id]
        try:
            with Image.open(clip.paths[index]) as im:
                a = im.convert("RGBA").getchannel("A")
                return a.point(lambda v: 255 if v >= 8 else 0).getbbox()
        except Exception:
            return None

    def draw(self) -> None:
        clip = self.clips[self.cur_clip]
        origin = (clip.fx, clip.fy)
        local_active = self._jelly is not None and self._jelly.active
        eyes = getattr(self, "_eyes", None) if self.mode == "idle" else None
        deformed = local_active or self._body is not None
        if self.mode == "idle" and (deformed or eyes is not None):
            rgba = (self._jelly.rgba if local_active else self._body.rgba
                    if self._body is not None else self._idle_rgba)
            if rgba is None:
                return
            if eyes is not None:
                rgba = eyes.render_rgba(rgba)
            source_origin = (0, 0)
            if local_active:
                rgba = self._jelly.render_rgba(rgba)
                source_origin = self._jelly.origin
            if self._body is not None:
                rgba = self._body.render_rgba(rgba, (self.win_w, self.win_h), origin, source_origin)
                origin = (0, 0)
            else:
                origin = (origin[0] + source_origin[0], origin[1] + source_origin[1])
            flat, mask = _chroma_frame(rgba)
            # Tk 不持有 Python 引用;只保留当前变形帧,不放入普通帧的 LRU。
            photo = ImageTk.PhotoImage(flat, master=self.root)
            self._jelly_photo = photo if deformed else None
            self._eye_photo = photo if eyes is not None else None
            self._jelly_mask = mask.tobytes() if deformed else None
            self._mask_origin, self._mask_size = origin, mask.size
        else:
            self._eye_photo = None
            self._jelly_photo = None
            self._jelly_mask = None
            photo = self._frame_image(self.cur_clip, self.cur_index)
        if photo is None:
            return
        self._display_photo = photo
        # 普通帧使用统一内容原点,全身变形输出已包含窗口透明边距。
        self.canvas.itemconfigure(self.image_id, image=photo)
        self.canvas.coords(self.image_id, *origin)

    def _content_point(self, x: float, y: float) -> tuple[float, float]:
        clip = self.clips[self.idle_clip]
        point = (x - clip.fx, y - clip.fy)
        if self._body is not None:
            point = self._body.inverse(*point)
        return self._jelly.inverse(*point) if self._jelly is not None and self._jelly.active else point

    def _complete_click(self, now: float) -> None:
        while self._clicks and now - self._clicks[0] > LAUGH_WINDOW_SEC:
            self._clicks.popleft()
        self._clicks.append(now)
        if len(self._clicks) >= LAUGH_CLICKS:
            log(f"点击身体 {LAUGH_WINDOW_SEC:g} 秒内达到 {LAUGH_CLICKS} 次,触发有声大笑")
            self.play("laugh", with_audio=True)
            self._click_laugh_active = True
            return
        if self._body is not None:
            self._body.handoff(now)
            self._resume_idle(now)
        self._jelly.release(now)
        self._resume_idle(now)
        self._wake_animation(now)

    def _complete_split_click(self, now):
        if not hasattr(self, "_split_clicks"):
            self._split_clicks = deque(maxlen=SPLIT_CLICKS)
        while self._split_clicks and now - self._split_clicks[0] > SPLIT_WINDOW_SEC:
            self._split_clicks.popleft()
        self._split_clicks.append(now)
        if len(self._split_clicks) == SPLIT_CLICKS:
            self._split_clicks.clear()
            group = getattr(self, "group", None)
            if group is not None:
                group.request_split(self, now)

    def _wake_animation(self, now: float) -> None:
        self._jelly_next_frame = now
        if self._jelly_timer is None:
            self._jelly_timer = _begin_jelly_timer()

    def _animation_active(self) -> bool:
        return ((self._jelly is not None and self._jelly.active)
                or (self._body is not None and self._body.moving))

    def _pause_idle(self, now: float) -> None:
        if self._idle_remaining is None:
            self._idle_remaining = max(0.0, self.deadline - now)

    def _resume_idle(self, now: float) -> None:
        if getattr(self, "_idle_remaining", None) is not None:
            self.deadline = now + self._idle_remaining
            self._idle_remaining = None

    def _rebase_body(self) -> None:
        """将可见脚底附近的平移归入窗口位置;画面世界坐标和鼠标抓取点不变。

        拎起变长或重抓压扁的角色时,不能让图片平移在透明边距里不断累积。
        """
        body = self._body
        if body is None:
            return
        foot = body.forward(*body.foot)
        dx, dy = round(foot[0] - body.foot[0]), round(foot[1] - body.foot[1])
        if not (dx or dy):
            return
        sx, sy, tx, ty = body.pose
        body.pose = (sx, sy, tx - dx, ty - dy)
        body.floor = (body.floor[0] - dx, body.floor[1] - dy)
        if hasattr(body, "pin"):
            body.pin = (body.pin[0] - dx, body.pin[1] - dy)
        x, y = self.root.winfo_x() + dx, self.root.winfo_y() + dy
        place_window(self.root, x, y)
        if self.dragging:
            self._win_x += dx
            self._win_y += dy
            self._press_local = (self._press_local[0] - dx, self._press_local[1] - dy)

    def _finish_body(self) -> None:
        if getattr(self, "_body", None) is not None:
            body = self._body
            floor = body.forward(*body.foot)
            body._anchored_pose(1.0, body.foot, floor)
            self._rebase_body()
        self._body = None
        if not getattr(self, "_press_valid", False):
            self._resume_idle(time.monotonic())

    def _drop_jelly(self) -> None:
        """切动作/拖动/菜单/缩放时复位;也取消跨状态的按下候选。"""
        was_drag = getattr(self, "dragging", False) and getattr(self, "drag_moved", False)
        timer = getattr(self, "_jelly_timer", None)
        if timer is not None:
            timer.timeEndPeriod(1)
        self._jelly_timer = None
        self._jelly = None
        self._jelly_photo = None
        self._jelly_mask = None
        self._press_valid = False
        self.dragging = False
        self._drag_body_eligible = False
        if getattr(self, "_clicks", None) is not None:
            self._clicks.clear()
        self._finish_body()
        group = getattr(self, "group", None)
        if (was_drag and not getattr(self, "_closed", False) and group is not None
                and not group.closed and not group.reunifying):
            group.set_hover(self)

    # ------------------------------------------------------------------ 逻辑
    def play_all(self, clip_id: str, with_audio: bool = True) -> None:
        if getattr(self, "group", None) is not None:
            self.group.play_all(clip_id, with_audio=with_audio)
        else:
            self.play(clip_id, with_audio=with_audio)

    def land(self) -> None:
        """Start the existing foot-anchored drop deformation after merge/return."""
        rgba = self._read_rgba(self.idle_clip, self.idle_frame)
        if rgba is None:
            return
        now = time.monotonic()
        self._body = _BodyJelly(rgba)
        self._body.release(0.0, now)
        self._wake_animation(now)
        self.draw()

    def pickup_for_return(self) -> None:
        rgba = self._read_rgba(self.idle_clip, self.idle_frame)
        if rgba is None:
            return
        now = time.monotonic()
        self._body = _BodyJelly(rgba)
        self._body.pickup(*self._body.foot, now)
        self._pause_idle(now)
        self._wake_animation(now)

    def play(self, clip_id: str, with_audio: bool = False, *, started_at=None) -> None:
        """普通桌宠完整播放;喷射分身从第54帧开始无限循环大笑。

        with_audio 只有**交互触发**才为 True(右键菜单、键盘 1、连续点击身体):
          自动随机动作是"宠物自己演的",不出声才不会在旁边说话/看视频时突然响一下。
        捧腹大笑音轨在启动时预热,所以手动点击依然是 ~1 帧延迟出声。
        """
        if clip_id not in self.clips:
            return
        group = getattr(self, "group", None)
        if group is not None and (group.closed or group.reunifying):
            return
        if group is not None:
            group.cancel_emission(self)
        if hasattr(self, "_split_clicks"):
            self._split_clicks.clear()
        self._action_serial = getattr(self, "_action_serial", 0) + 1
        self._action_audible = False
        self._click_laugh_active = False
        self._drop_jelly()
        self.mode = "playing"
        self.cur_clip = clip_id
        self._loop_start = (min(EMITTED_LOOP_FIRST_FRAME - 1, self.clips[clip_id].frames - 1)
                            if getattr(self, "_emitted", False) and clip_id == "laugh" else None)
        self.cur_index = self._loop_start if self._loop_start is not None else 0
        self.acc = 0.0
        log(f"播放 {self.clips[clip_id].label} (打断当前动作)"
            + ("" if with_audio else " — 自动播放,不出声"))
        if with_audio:
            if self._loop_start is not None:
                success = self.audio.play(clip_id, start_ms=self._loop_start * self.frame_ms,
                                          end_ms=self.clips[clip_id].frames * self.frame_ms, loops=-1)
            else:
                success = self.audio.play(clip_id)
            if success:
                self._action_audible = True
                # Hold frame zero while this device's queued audio reaches the output.
                self.acc = -float(getattr(self.audio, "output_latency_ms", 0.0))
        else:
            self.audio.stop()         # 自动动作静音;顺带掐掉上一段手动动作的余音
        self.last_tick = time.monotonic() if started_at is None else started_at
        self._action_start = self.last_tick - self.acc / 1000
        self.draw()

    def animation_snapshot(self, now=None):
        now = time.monotonic() if now is None else now
        if self.mode != "playing" or self._action_start is None:
            return None
        if (getattr(self, "_loop_start", None) is None
                and (now - self._action_start) * 1000 >= self.clips[self.cur_clip].frames * self.frame_ms):
            return None
        return self.cur_clip, self._action_start, self._action_audible

    def inherit_animation(self, source):
        """Start an independent loop, inheriting only the source's audible state."""
        now = time.monotonic()
        snapshot = source.animation_snapshot(now)
        if snapshot is None:
            return
        self._emitted = True
        self.play(snapshot[0], with_audio=snapshot[2], started_at=now)

    def stop_laugh(self):
        group = getattr(self, "group", None)
        if group is not None and (group.closed or group.reunifying):
            return
        if self.mode == "playing" and self.cur_clip == "laugh":
            self._to_idle()

    def _to_idle(self) -> None:
        """动作结束、显式停止或拖动时回到待机。"""
        self._click_laugh_active = False
        group = getattr(self, "group", None)
        if group is not None:
            group.cancel_emission(self)
        if hasattr(self, "_split_clicks"):
            self._split_clicks.clear()
        self._action_start = None
        self._action_audible = False
        self._loop_start = None
        self._drop_jelly()
        self.mode = "idle"
        self.cur_clip = self.idle_clip
        self.cur_index = self.idle_frame
        self.acc = 0.0
        if getattr(self, "_eyes", None) is not None:
            self._eyes.reset()
            self._eye_next_frame = 0.0
        self.audio.stop()             # 动作播完就停音(音频通常比画面略长一点)
        self.deadline = time.monotonic() + random.uniform(self.random_min_ms, self.random_max_ms) / 1000.0
        log(f"回到待机(下次随机动作 {self.deadline - time.monotonic():.0f}s 后)")
        self.draw()

    def tick(self) -> None:
        self._tick_after = None
        if getattr(self, "_closed", False):
            return
        group = getattr(self, "group", None)
        if group is not None and group.primary is self:
            group.dispatch()
            if self._closed:
                return
        runtime = getattr(self, "_runtime", None) if group is None else None
        if runtime is not None:
            for action in runtime.pending():
                if action == "quit":
                    self.quit()
                    return
                if action == "show":
                    self.root.deiconify()
                    self.root.lift()
                elif action in self.clips:
                    self.play(action, with_audio=True)
        now = time.monotonic()
        dt = now - self.last_tick
        self.last_tick = now
        if dt > 0.25:            # 局部形变限制步长;动作使用绝对时钟补帧。
            dt = 0.25

        if self.mode == "playing":
            clip = self.clips[self.cur_clip]
            start = getattr(self, "_action_start", None)
            elapsed_ms = (now - start) * 1000 if start is not None else self.cur_index * self.frame_ms + self.acc + dt * 1000
            elapsed_frames = max(0, int((elapsed_ms + 1e-7) / self.frame_ms))
            self.acc = elapsed_ms - elapsed_frames * self.frame_ms
            loop_start = getattr(self, "_loop_start", None)
            index = (loop_start + elapsed_frames % (clip.frames - loop_start)
                     if loop_start is not None else elapsed_frames)
            if index != self.cur_index:
                self.cur_index = index
                if self.cur_index >= clip.frames:
                    self._to_idle()
                else:
                    self.draw()
        elif (not self.menu_open and self._idle_remaining is None and now >= self.deadline
              and not (group is not None and group.reunifying)):
            pick = random.choice(self.order)
            log(f"空闲到点,随机播放 {self.clips[pick].label}")
            self.play(pick)

        eye_changed = self._update_eyes(now)
        body_drawn = False
        if self.mode == "idle" and self._animation_active():
            if now >= self._jelly_next_frame:
                if self._jelly is not None:
                    self._jelly.advance(now)
                if self._body is not None:
                    self._body.advance(now)
                    self._rebase_body()
                    if self._body is not None and self._body.phase == "done":
                        self._finish_body()
                self.draw()
                body_drawn = True
                if not self._animation_active() and self._jelly_timer is not None:
                    self._jelly_timer.timeEndPeriod(1)
                    self._jelly_timer = None
                self._jelly_next_frame = now + JELLY_FRAME_SEC
        if eye_changed and not body_drawn:
            self.draw()
        delay = TICK_MS
        if self._animation_active():
            # 直接等到下一个变形帧,避免 Windows 把两次短轮询各舍入为一个计时周期。
            delay = max(1, math.ceil((self._jelly_next_frame - time.monotonic()) * 1000))
        self._tick_after = self.root.after(delay, self.tick)

    def quit(self) -> None:
        group = getattr(self, "group", None)
        if group is not None:
            group.quit()
            return
        self._close_window()

    def _close_window(self) -> None:
        if getattr(self, "_closed", False):
            return
        self._closed = True
        log("退出")
        self._drop_jelly()
        if self._tick_after is not None:
            self.root.after_cancel(self._tick_after)
            self._tick_after = None
        if getattr(self, "_focus_after", None) is not None:
            self.root.after_cancel(self._focus_after)
            self._focus_after = None
        self.audio.quit()
        self._display_photo = self._eye_photo = self._jelly_photo = None
        self._eyes = self._idle_rgba = None
        if getattr(self, "_runtime", None) is not None:
            self._runtime.close()
            self._runtime = None
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ 交互
    def _on_press(self, event) -> None:
        group = getattr(self, "group", None)
        if group is not None and group.reunifying:
            return
        self._press_valid = False
        self._press_idle = self.mode == "idle"
        self._drag_body_eligible = False
        self._press_time = time.monotonic()
        # 透明(洋红)区域虽然会被 Windows 抠掉、鼠标能穿到桌面,
        # 但窗口本身在那一块仍可能收到事件 —— 必须按 alpha 命中的才起拖,
        # 否则会出现"点宠物旁边的空白也能把它拽走"的怪行为。
        if self.menu_open or not self._is_opaque(event.x, event.y):
            self.dragging = False
            return
        self.dragging = True
        self.drag_moved = False
        self._press_x = event.x_root
        self._press_y = event.y_root
        self._win_x = self.root.winfo_x()
        self._win_y = self.root.winfo_y()
        self._press_local = (event.x, event.y)
        self._press_valid = True
        if self._press_idle:
            point = self._content_point(event.x, event.y)
            if self._jelly is None:
                rgba = self._read_rgba(self.idle_clip, self.idle_frame)
                if rgba is None:
                    return
                self._jelly = _LocalJelly(rgba)
            self._press_valid = True
            self._jelly.begin_press(point, self._press_time)
            self._pause_idle(self._press_time)
            self._wake_animation(self._press_time)

    def _on_motion(self, event) -> None:
        group = getattr(self, "group", None)
        if group is not None and group.reunifying:
            return
        if not self.dragging:
            return
        dx = event.x_root - self._press_x
        dy = event.y_root - self._press_y
        if not self.drag_moved and abs(dx) < DRAG_THRESHOLD_PX and abs(dy) < DRAG_THRESHOLD_PX:
            return                      # 阈值内不动:轻点不跳位
        if not self.drag_moved:
            # Ordinary performances stop on drag. Emitted loops keep their voice
            # and clock; moving their window must not switch to the idle body.
            if self.mode == "playing" and getattr(self, "_loop_start", None) is None:
                press = (self._press_x, self._press_y, self._press_local,
                         self._win_x, self._win_y, self._press_time)
                self._to_idle()
                (self._press_x, self._press_y, self._press_local,
                 self._win_x, self._win_y, self._press_time) = press
                self.dragging = True
                self._press_idle = True
            if group is not None:
                group.begin_drag(self)
            if hasattr(self, "_split_clicks"):
                self._split_clicks.clear()
            self._clicks.clear()
            self._press_valid = False
            now = time.monotonic()
            if self._jelly is not None:
                self._jelly.cancel_press(now)
            if self._press_idle and self.mode == "idle" and not self.menu_open:
                if self._body is None:
                    rgba = self._read_rgba(self.idle_clip, self.idle_frame)
                    if rgba is not None:
                        self._body = _BodyJelly(rgba)
                if self._body is not None:
                    clip = self.clips[self.idle_clip]
                    self._body.pickup(self._press_local[0] - clip.fx,
                                      self._press_local[1] - clip.fy, now)
                    self._drag_body_eligible = True
                    self._pause_idle(now)
                    self._drag_samples.clear()
                    self._drag_samples.append((max(self._press_time, now - 0.12), self._press_x, self._press_y))
                    self._wake_animation(now)
            else:
                self._resume_idle(now)
            self.draw()
        self.drag_moved = True
        if self._drag_body_eligible:
            self._drag_samples.append((time.monotonic(), event.x_root, event.y_root))
        place_window(self.root, self._win_x + dx, self._win_y + dy)
        # Tk 延后提交 geometry;下一帧脚底重定位前必须得到已经移动的位置。
        self.root.update_idletasks()

    def _release_speed(self, event, now: float) -> float:
        self._drag_samples.append((now, event.x_root, event.y_root))
        samples = list(self._drag_samples)
        cutoff = now - 0.12
        distance = 0.0
        for a, b in zip(samples, samples[1:]):
            if b[0] > cutoff and b[0] > a[0]:
                fraction = min(1.0, (b[0] - cutoff) / (b[0] - a[0]))
                distance += math.hypot(b[1] - a[1], b[2] - a[2]) * fraction
        duration = max(0.001, now - max(cutoff, samples[0][0]))
        return distance / duration / max(1.0, self._body.height)

    def _on_release(self, event) -> None:
        group = getattr(self, "group", None)
        if group is not None and group.reunifying:
            return
        # Tk 偶尔合并 Motion 事件,松开本身仍须应用同一个拖拽阈值。
        if self.dragging and not self.drag_moved:
            self._on_motion(event)
        was_click = (self.dragging and not self.drag_moved
                     and abs(event.x_root - self._press_x) < DRAG_THRESHOLD_PX
                     and abs(event.y_root - self._press_y) < DRAG_THRESHOLD_PX)
        was_valid = self._press_valid
        self._press_valid = False
        if self.dragging and self.drag_moved:
            log(f"拖动到 ({self.root.winfo_x()}, {self.root.winfo_y()})")
            if group is not None and group.try_merge(self):
                return
            if group is not None:
                group.set_hover(self)
            if self._drag_body_eligible and self._body is not None:
                now = time.monotonic()
                self._body.release(self._release_speed(event, now), now)
                self._wake_animation(now)
        self.dragging = False
        self._drag_body_eligible = False
        # 表演期间不再单击停止;大笑中的有效松开进入独立分裂计数。
        if was_click and was_valid and self.mode == "playing":
            if self.cur_clip == "laugh" and not self.menu_open:
                self._complete_split_click(time.monotonic())
        elif was_click and was_valid and self.mode == "idle" and not self.menu_open:
            self._complete_click(time.monotonic())

    def _on_focus_out(self, event=None) -> None:
        if getattr(self, "_closed", False):
            return
        # Tk 在子控件间转移焦点时也先发 FocusOut,等焦点状态稳定后再判定。
        if event is not None:
            if getattr(self, "_focus_after", None) is not None:
                self.root.after_cancel(self._focus_after)
            self._focus_after = self.root.after_idle(self._cancel_unfocused_press)
        else:
            self._cancel_unfocused_press()

    def _cancel_unfocused_press(self) -> None:
        self._focus_after = None
        if getattr(self, "_closed", False):
            return
        focused = self.root.focus_get()
        if focused is not None and focused.winfo_toplevel() == self.root:
            return
        if self.dragging or self._press_valid:
            self._drop_jelly()
            self.draw()

    def _on_right(self, event) -> None:
        group = getattr(self, "group", None)
        if group is not None and group.reunifying:
            return
        # 点在不透明处才弹菜单(洋红区域由窗口抠色穿透,一般收不到事件)
        ok = self._is_opaque(event.x, event.y)
        log(f"右键: 窗口内({event.x},{event.y}) 屏幕({event.x_root},{event.y_root}) 命中角色={ok}")
        if not ok:
            return
        if group is not None:
            group.set_primary(self)
            self.menu.entryconfigure("复制分身", state="disabled" if len(group.pets) + len(group.pending_bursts) >= MAX_PETS else "normal")
            self.menu.entryconfigure("归一", state="normal" if len(group.pets) > 1 else "disabled")
        self._drop_jelly()
        self.draw()
        self.menu_open = True
        try:
            # 打开菜单时刷新选中标记:万一 pet_settings.json 被手改过(或微调到了
            # 非预设档),高亮的那一项才是当前值。
            self.scale_var.set(self._preset_index())
            self.menu.tk_popup(event.x_root, event.y_root)
            log("菜单已弹出")
        except Exception as e:
            log(f"菜单弹出失败: {e}")
        finally:
            try:
                self.menu.grab_release()
            except tk.TclError:
                pass  # The Quit menu command may already have destroyed the window.
            self.menu_open = False
            self._eye_next_frame = 0.0
            if getattr(self, "_eyes", None) is not None:
                self._eyes.last_update = None

    def _is_opaque(self, x: int, y: int) -> bool:
        """判断窗口内某点是否在角色身上。

        缓存当前显示大小的二值蒙版;变形时直接读取画面对应的蒙版。
        """
        clip = self.clips[self.cur_clip]
        if self._jelly_mask is not None:
            fx, fy = round(x) - self._mask_origin[0], round(y) - self._mask_origin[1]
            w, h = self._mask_size
            return 0 <= fx < w and 0 <= fy < h and self._jelly_mask[fy * w + fx] != 0
        # 窗口坐标 -> 帧文件坐标:先减掉帧在窗口里的落点,再按帧的显示尺寸判断
        fx = int(round(x)) - clip.fx
        fy = int(round(y)) - clip.fy
        if fx < 0 or fy < 0 or fx >= clip.fw or fy >= clip.fh:
            return False
        if self._alpha_key != (self.cur_clip, self.cur_index):
            rgba = self._read_rgba(self.cur_clip, self.cur_index)
            self._alpha = _opaque_mask(rgba).tobytes() if rgba is not None else None
            self._alpha_size = self._alpha_display = (clip.fw, clip.fh)
            self._alpha_key = (self.cur_clip, self.cur_index)
        if not self._alpha:
            return False
        return self._alpha[fy * clip.fw + fx] != 0

    def run(self) -> None:
        # 先把待机首帧画出来再进主循环
        photo = self._frame_image(self.idle_clip, self.idle_frame)
        if photo is None:
            raise SystemExit("待机首帧加载失败,检查 assets/ 是否完整")
        self.draw()
        self.root.update_idletasks()
        # 把窗口实际几何写出来:自动化截图验证要用它来精确定位(不靠猜)
        self._write_geometry_file()
        log(f"窗口已显示 位置=({self.root.winfo_x()},{self.root.winfo_y()}) "
            f"尺寸={self.win_w}x{self.win_h} 缩放={self.global_scale:.4f},待机中")
        self.tick()
        self.group.root.mainloop()


def main() -> int:
    from pet_runtime import InstanceGuard
    guard = InstanceGuard()
    if not guard.acquire():
        return 0
    app = None
    try:
        app = PetApp()
        app.group.runtime = guard
        guard.start_hotkeys()
        app.run()
    except SystemExit as e:
        log(e)
        return 1
    except Exception as e:
        import traceback
        log(traceback.format_exc())
        log(f"启动失败: {e}")
        return 1
    finally:
        if app is not None:
            app.quit()
        guard.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
