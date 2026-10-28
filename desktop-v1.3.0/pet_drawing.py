"""Desktop shape placement for pet clones."""
import math
import sys
import tkinter as tk
from tkinter import ttk
from PIL import ImageGrab, ImageTk

from pet_physics import TIERS


def desktop_backdrop(area, window):
    """ImageGrab uses physical pixels; Tk may use DPI-virtualized coordinates."""
    bounds = area
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes
        user = ctypes.WinDLL("user32", use_last_error=True)
        user.GetAncestor.argtypes = (wintypes.HWND, wintypes.UINT)
        user.GetAncestor.restype = wintypes.HWND
        user.LogicalToPhysicalPointForPerMonitorDPI.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
        hwnd = user.GetAncestor(window.winfo_id(), 2)
        a, b = wintypes.POINT(*area[:2]), wintypes.POINT(*area[2:])
        if (user.LogicalToPhysicalPointForPerMonitorDPI(hwnd, ctypes.byref(a))
                and user.LogicalToPhysicalPointForPerMonitorDPI(hwnd, ctypes.byref(b))):
            bounds = (a.x, a.y, b.x, b.y)
    return ImageGrab.grab(bbox=bounds, all_screens=True).resize(
        (area[2]-area[0], area[3]-area[1]))


def shape_points(shape, start, end, spacing):
    """Evenly spaced centers; rectangles/circles are outlines, not fills."""
    spacing = max(1, spacing)
    x, y = start
    ex, ey = end
    if shape == "point":
        return [start]
    if shape == "line":
        distance = math.dist(start, end)
        if distance < spacing / 2:
            return [start]
        segments = max(1, round(distance / spacing))
        return [(x + (ex-x)*i/segments, y + (ey-y)*i/segments)
                for i in range(segments+1)]
    if shape == "rectangle":
        if abs(ex-x) < 1 or abs(ey-y) < 1:
            return shape_points("line", start, end, spacing)
        corners = [(x, y), (ex, y), (ex, ey), (x, ey)]
        points = []
        for a, b in zip(corners, corners[1:] + corners[:1]):
            segments = max(1, round(math.dist(a, b) / spacing))
            points.extend((a[0]+(b[0]-a[0])*i/segments,
                           a[1]+(b[1]-a[1])*i/segments) for i in range(segments))
        return points
    if shape == "circle":
        radius = math.dist(start, end)
        if radius < spacing / 2:
            return [start]
        count = max(3, round(2*math.pi*radius/spacing))
        return [(x+radius*math.cos(2*math.pi*i/count),
                 y+radius*math.sin(2*math.pi*i/count)) for i in range(count)]
    raise ValueError(shape)


class PetDrawing:
    def __init__(self, group, source):
        self.group, self.source = group, source
        self.overlay = self.after = None
        self.closed = False
        self.busy = False
        self.window = tk.Toplevel(group.root)
        self.window.title("分身绘图")
        self.window.attributes("-topmost", True)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.close)
        self.window.bind("<Escape>", lambda event: self.close())
        body = ttk.Frame(self.window, padding=18)
        body.pack()
        ttk.Label(body, text="分身绘图", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(body, text="先选尺寸和图形，再到桌面拖拽绘制。", padding=(0, 8)).pack(anchor="w")
        self.scale = tk.DoubleVar(self.window, value=min(TIERS, key=lambda s: abs(s-source.global_scale)))
        sizes = ttk.LabelFrame(body, text="分身尺寸", padding=10)
        sizes.pack(fill="x", pady=6)
        for value in reversed(TIERS):
            ttk.Radiobutton(sizes, text=f"{value:.1f}", variable=self.scale, value=value).pack(side="left", padx=9)
        self.shape = tk.StringVar(self.window, value="point")
        shapes = ttk.LabelFrame(body, text="排列图形", padding=10)
        shapes.pack(fill="x", pady=6)
        for name, value in (("点", "point"), ("线", "line"), ("矩形", "rectangle"), ("圆", "circle")):
            ttk.Radiobutton(shapes, text=name, variable=self.shape, value=value).pack(side="left", padx=8)
        ttk.Label(body, text="矩形和圆沿轮廓排列；圆从圆心向外拖。\n松手后拖动调整位置，确认后生成。", padding=(0, 8)).pack(anchor="w")
        self.start_button = ttk.Button(body, text="开始绘制", command=self.begin)
        self.start_button.pack(fill="x", pady=(6, 0))
        from pet_group import work_area, place_window
        self.window.update_idletasks()
        left, top, right, bottom = work_area(self.window.winfo_pointerxy())
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        place_window(self.window, (left+right-width)//2, (top+bottom-height)//2)
        self.window.focus_force()

    def begin(self):
        from pet_group import work_area, place_window
        if self.closed or self.busy or self.group.closed or self.group.reunifying:
            return
        self.busy = True
        self.start_button.configure(state="disabled", text="正在绘制 / 生成，Esc 取消")
        self.area = work_area(self.window.winfo_pointerxy())
        self.window.withdraw()
        self.window.update_idletasks()
        self.selected_scale, self.selected_shape = self.scale.get(), self.shape.get()
        if self.source not in self.group.pets:
            self.source = self.group.primary
        box = self.source.visible_box()
        ratio = self.selected_scale / self.source.global_scale
        self.body_size = ((box[2]-box[0])*ratio, (box[3]-box[1])*ratio)
        self.spacing = max(self.body_size)*1.08
        self.overlay = tk.Toplevel(self.group.root)
        self.overlay.withdraw()
        self.overlay.overrideredirect(True)
        self.overlay.attributes("-topmost", True)
        left, top, right, bottom = self.area
        self.canvas = tk.Canvas(self.overlay, width=right-left, height=bottom-top,
                                highlightthickness=0, cursor="crosshair", bg="#27333d")
        self.canvas.pack()
        place_window(self.overlay, left, top, (right-left, bottom-top))
        # A frozen desktop backdrop captures clicks even over transparent desktop
        # regions; previews remain fully opaque and legible.
        self.backdrop = ImageTk.PhotoImage(desktop_backdrop(self.area, self.overlay), master=self.overlay)
        self.canvas.create_image(0, 0, image=self.backdrop, anchor="nw")
        self.canvas.create_rectangle(12, 12, 580, 58, fill="#fff4cc", outline="#e6b84f")
        self.hint = self.canvas.create_text(26, 35, anchor="w", fill="#704518",
                     font=("Microsoft YaHei UI", 12), text="点一下或拖拽绘制，松手后可移动位置。Esc / 右键取消")
        self.start = None
        self.adjusting = False
        self.drag_origin = None
        self.placed_points = []
        toolbar = tk.Frame(self.canvas, bg="#fff4cc", padx=12, pady=8)
        self.confirm_button = ttk.Button(toolbar, text="确认生成  Enter", command=self.confirm, state="disabled")
        self.confirm_button.pack(side="left", padx=4)
        ttk.Button(toolbar, text="重新绘制  R", command=self.redraw).pack(side="left", padx=4)
        ttk.Button(toolbar, text="取消  Esc", command=self.close).pack(side="left", padx=4)
        self.canvas.create_window((right-left)/2, bottom-top-20, window=toolbar, anchor="s")
        self.canvas.bind("<ButtonPress-1>", self.press)
        self.canvas.bind("<B1-Motion>", self.motion)
        self.canvas.bind("<ButtonRelease-1>", self.release)
        self.overlay.bind("<Escape>", lambda event: self.close())
        self.overlay.bind("<Button-3>", lambda event: self.close())
        self.overlay.bind("<Return>", lambda event: self.confirm())
        self.overlay.bind("<r>", lambda event: self.redraw())
        self.overlay.bind("<R>", lambda event: self.redraw())
        self.overlay.deiconify()
        place_window(self.overlay, left, top, (right-left, bottom-top))
        self.overlay.focus_force()
        self.overlay.grab_set()

    def points(self, end):
        left, top, right, bottom = self.area
        halfw, halfh = (value/2 for value in self.body_size)
        if self.selected_shape == "point":
            return [(max(halfw, min(right-left-halfw, self.start[0])),
                     max(halfh, min(bottom-top-halfh, self.start[1])))]
        # Omit off-screen centers instead of clamping a whole edge into a pile.
        return [(x, y) for x, y in shape_points(self.selected_shape, self.start, end, self.spacing)
                if halfw <= x <= right-left-halfw and halfh <= y <= bottom-top-halfh]

    def press(self, event):
        if self.adjusting:
            self.drag_origin = (event.x, event.y)
            self.drag_points = list(self.placed_points)
            return
        self.start = (event.x, event.y)
        self.motion(event)

    def motion(self, event):
        if self.adjusting:
            if self.drag_origin is None or not self.drag_points:
                return
            dx, dy = event.x-self.drag_origin[0], event.y-self.drag_origin[1]
            w, h = self.body_size
            left, top, right, bottom = self.area
            dx = max(w/2-min(x for x, y in self.drag_points),
                     min(right-left-w/2-max(x for x, y in self.drag_points), dx))
            dy = max(h/2-min(y for x, y in self.drag_points),
                     min(bottom-top-h/2-max(y for x, y in self.drag_points), dy))
            self.placed_points = [(x+dx, y+dy) for x, y in self.drag_points]
            self._paint_points(self.placed_points)
            return
        if self.start is None:
            return
        self._paint_points(self.points((event.x, event.y)))

    def _paint_points(self, points):
        self.canvas.delete("preview")
        w, h = self.body_size
        for x, y in points:
            self.canvas.create_oval(x-w/2, y-h/2, x+w/2, y+h/2,
                                    fill="#fff4cc", outline="#e6b84f", width=2,
                                    stipple="gray50", tags="preview")
            self.canvas.create_text(x, y, text="+", fill="#704518", tags="preview")
        tip = "拖动调整位置，Enter 确认生成" if self.adjusting else "松手后可拖动调整位置"
        self.canvas.itemconfigure(self.hint, text=f"预览 {len(points)} 只 · 尺寸 {self.selected_scale:.1f} · {tip}")
        self.canvas.tag_lower("preview", self.hint)

    def release(self, event):
        if self.adjusting:
            self.motion(event)
            self.drag_origin = None
            return
        if self.start is None:
            return
        self.placed_points = self.points((event.x, event.y))
        self.adjusting = True
        self.canvas.configure(cursor="fleur")
        self.confirm_button.configure(state="normal" if self.placed_points else "disabled")
        self._paint_points(self.placed_points)

    def redraw(self):
        self.start = None
        self.adjusting = False
        self.drag_origin = None
        self.placed_points = []
        self.canvas.delete("preview")
        self.canvas.configure(cursor="crosshair")
        self.confirm_button.configure(state="disabled")
        self.canvas.itemconfigure(self.hint, text="重新点击或拖拽绘制，松手后可移动位置。Esc / 右键取消")

    def confirm(self):
        if not self.adjusting or not self.placed_points or self.overlay is None:
            return
        self.pending = iter(self.placed_points)
        self.overlay.grab_release()
        self.overlay.destroy()
        self.overlay = None
        self.backdrop = None
        self.window.deiconify()
        self.window.title("正在生成分身 · Esc 停止")
        self._spawn_next()

    def _spawn_next(self):
        self.after = None
        if self.closed or self.group.closed or self.group.reunifying:
            self.close()
            return
        point = next(self.pending, None)
        if point is None:
            self.close()
            return
        try:
            self.group.spawn(point=(point[0]+self.area[0], point[1]+self.area[1]), scale=self.selected_scale)
        except Exception:
            self.close()
            raise
        # Yield to playback and physics between creations, with no count cap.
        self.after = self.group.root.after(10, self._spawn_next)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.after is not None:
            self.group.root.after_cancel(self.after)
            self.after = None
        if self.overlay is not None:
            self.overlay.grab_release()
            self.overlay.destroy()
            self.overlay = None
        self.window.destroy()
        self.backdrop = None
        if getattr(self.group, "drawing", None) is self:
            self.group.drawing = None
