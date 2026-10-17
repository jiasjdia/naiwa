# -*- coding: utf-8 -*-
"""test_scale.py - 尺寸调节功能的自检(会真的开一个 Tk 窗口,约 1 秒)。

验证:窗口/画布/帧尺寸一起变、三个动作尺寸一致、缓存重建、配置写回、
     位置往左上长、上下限钳位、命中判定在新尺寸下仍准确、几何文件刷新。

本测试使用音频替身、临时配置和临时几何输出,不会影响正在运行的桌宠。

用法: python tools\\test_scale.py
"""
import json
import sys
import tkinter as tk
import tempfile
import atexit
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import pet_app  # noqa: E402


class FakeAudio:
    """音频替身:不起进程、不杀别人的进程,只记录调用。"""

    def __init__(self, *a, **kw):
        self.calls = []
        self.enabled = True
        self.media = {}
        self.offset_ms = {}

    def prewarm(self, *a):
        self.calls.append("prewarm")
        return 0

    def available(self, cid):
        return False

    def play(self, *a, **kw):
        self.calls.append("play")
        return False

    def stop(self):
        self.calls.append("stop")

    def quit(self):
        self.calls.append("quit")


pet_app.AudioPlayer = FakeAudio      # 必须在 PetApp() 之前替换

temporary = tempfile.TemporaryDirectory(prefix="pet-scale-test-")
atexit.register(temporary.cleanup)
SETTINGS = Path(temporary.name) / "pet_settings.json"
SETTINGS.write_bytes((ROOT / "pet_settings.json").read_bytes())
pet_app.SETTINGS = SETTINGS
GEOMETRY = Path(temporary.name) / "geometry.json"


def write_test_geometry(self):
    GEOMETRY.write_text(json.dumps({"w": self.win_w, "h": self.win_h,
                                   "scale": self.global_scale}), encoding="utf-8")


pet_app.PetApp._write_geometry_file = write_test_geometry

results = []


def check(label, cond, detail=""):
    results.append((label, cond))
    print(f"  {'PASS' if cond else 'FAIL'}  {label}" + (f"   [{detail}]" if detail and not cond else ""))


print(__doc__)
app = pet_app.PetApp()
app.root.update()
init_scale = app.global_scale
print(f"初始: scale={init_scale:.2f} 宠物高={app.win_h}px 窗口={app.win_w}x{app.win_h} "
      f"位置=({app.root.winfo_x()},{app.root.winfo_y()}) 基准={app.base_w}x{app.base_h}")

print("\n== 1. 预设档位 ==")
check("预设档 = 0.4/0.6/0.8/1.0/1.2",
      pet_app.SCALE_PRESETS == (0.4, 0.6, 0.8, 1.0, 1.2), str(pet_app.SCALE_PRESETS))
check("初始 scale 落在预设区间内", pet_app.SCALE_MIN <= init_scale <= pet_app.SCALE_MAX,
      f"{init_scale}")
check("初始档位序号 = 最接近的预设",
      app._preset_index() == min(range(5), key=lambda i: abs(pet_app.SCALE_PRESETS[i] - init_scale)),
      str(app._preset_index()))
# 下面用"和初始值不同"的档位来测,这样无论你当前调成多大都成立
TARGET = 1.0 if abs(init_scale - 1.0) > 1e-6 else 0.6

print(f"\n== 2. 改到 {TARGET}:窗口/画布/帧一起变 ==")
before_pos = (app.root.winfo_x(), app.root.winfo_y())
app.set_scale(TARGET)
app.root.update()
check(f"scale 更新为 {TARGET}", abs(app.global_scale - TARGET) < 1e-9, f"{app.global_scale}")
exp_w = round(app.base_w * TARGET)
content_h = round(app.base_h * TARGET)
exp_h = content_h + 2 * round(pet_app.BODY_PAD * TARGET)
check("窗口尺寸 = 基准 × 目标档", (app.win_w, app.win_h) == (exp_w, exp_h),
      f"{app.win_w}x{app.win_h} vs {exp_w}x{exp_h}")
check("Tk 窗口实际尺寸已变", (app.root.winfo_width(), app.root.winfo_height()) == (exp_w, exp_h),
      f"{app.root.winfo_width()}x{app.root.winfo_height()}")
check("画布尺寸已变", (int(app.canvas["width"]), int(app.canvas["height"])) == (exp_w, exp_h),
      f"{app.canvas['width']}x{app.canvas['height']}")
check("三个动作内容尺寸一致,并留出上下透明余量",
      all((c.fw, c.fh) == (exp_w, content_h) and c.fy == app.pad_y for c in app.clips.values()),
      str({k: (c.fw, c.fh) for k, c in app.clips.items()}))
check("帧缓存已重建(只剩重画的那一帧,不是旧的整套)",
      len(app.cache) <= 1 and app.cache_bytes <= exp_w * exp_h * 4 + 1,
      f"{len(app.cache)} 项 / {app.cache_bytes}B")
check("alpha 缓存已失效", app._alpha_key is None)
after_pos = (app.root.winfo_x(), app.root.winfo_y())
check("位置往左上长(右下角不动或向右下边界靠)",
      after_pos != before_pos and after_pos[0] <= before_pos[0] and after_pos[1] <= before_pos[1],
      f"{before_pos} -> {after_pos}")

print("\n== 3. 帧真的按新尺寸重载 ==")
app._frame_image("laugh", 0)
photo = app.cache.get(("laugh", 0))
check("缓存里出现新尺寸的帧", photo is not None, str(list(app.cache)[:3]))
check("帧位图宽度 = 窗口宽度(+2 边框容差)",
      photo is not None and abs(photo.width() - exp_w) <= 2,
      f"photo={getattr(photo, 'width', lambda: '?')()}")

print("\n== 4. 配置写回文件 ==")
saved = json.loads(SETTINGS.read_text(encoding="utf-8"))
check(f"displayScale 已写回 {TARGET}", saved.get("displayScale") == TARGET,
      str(saved.get("displayScale")))
check("其他键保留(displayScale/fps/randomMinSec/randomMaxSec)",
      set(saved) >= {"displayScale", "fps", "randomMinSec", "randomMaxSec"}, str(list(saved)))

print("\n== 5. 逐级调小/调大 ==")
app.set_scale(0.6); app.root.update()
for target in (0.5, 0.4, 0.3):
    app.step_scale(-1); app.root.update()
    print(f"    变小 -> {app.global_scale:.2f} (宠物高 {app.win_h}px)")
check("连续变小停在 0.4(下限)", abs(app.global_scale - 0.4) < 1e-9, f"{app.global_scale}")
for _ in range(8):        # 0.4 → 1.2 需要 8 次 +0.1
    app.step_scale(+1); app.root.update()
check("连续变大停在 1.2(上限)", abs(app.global_scale - 1.2) < 1e-9, f"{app.global_scale}")
check("每一步都在预设区间内且窗口 = 基准 × scale",
      (app.win_w, app.win_h) == (round(app.base_w * app.global_scale),
                                 round(app.base_h * app.global_scale) + 2 * round(pet_app.BODY_PAD * app.global_scale)),
      f"{app.win_w}x{app.win_h} scale={app.global_scale}")
check("上限时窗口没超出屏幕",
      app.root.winfo_x() >= 0 and app.root.winfo_y() >= 0,
      f"({app.root.winfo_x()},{app.root.winfo_y()})")

print("\n== 6. 命中判定在新尺寸下仍准确 ==")
app.set_scale(0.6); app.root.update()
app._alpha_key = None
# 窗口正中心:宠物身体应该覆盖 → 命中
cx, cy = app.win_w // 2, app.win_h // 2
hit_center = app._is_opaque(cx, cy)
# 左上角:通常是透明区 → 不命中
hit_corner = app._is_opaque(1, 1)
check("中心点命中角色", hit_center is True, f"center=({cx},{cy})")
check("左上角不命中(透明)", hit_corner is False, "corner=(1,1)")

print("\n== 7. 几何文件已刷新 ==")
geo = json.loads(GEOMETRY.read_text(encoding="utf-8"))
check("pet_geometry.json 的 w/h/scale 与当前一致",
      geo["w"] == app.win_w and geo["h"] == app.win_h and abs(geo["scale"] - app.global_scale) < 1e-9,
      str(geo))

print("\n== 8. 菜单结构(静态检查) ==")
src = (ROOT / "pet_app.py").read_text(encoding="utf-8")
check("有「大小」子菜单", 'add_cascade(label="大小"' in src)
check("预设档用 radiobutton(带选中标记)", "add_radiobutton" in src)
check("有「变小/变大」微调项", "变小" in src and "变大" in src)

# ---- 收尾:销毁窗口 + 还原配置文件(音频是替身,没有进程要清) ----
try:
    app.root.destroy()
except Exception:
    pass
print("\n(测试只写入临时配置)")

failed = [r for r in results if not r[1]]
print(f"\n== 结果 ==\n通过 {len(results) - len(failed)} 项,失败 {len(failed)} 项")
sys.exit(1 if failed else 0)
