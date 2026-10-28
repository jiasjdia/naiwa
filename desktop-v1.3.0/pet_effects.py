"""Short-lived, click-through split effects, independent of pet physics."""
from functools import lru_cache
from pathlib import Path
import math
import sys
import time
import tkinter as tk

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageTk

BURST_DURATION = 0.60
BURST_FPS = 30
TEXTURE = Path(__file__).resolve().parent / "assets" / "effects" / "split_burst.png"


@lru_cache(maxsize=1)
def burst_texture():
    with Image.open(TEXTURE) as source:
        sparks = source.convert("RGBA")
    # Keep the sparks, but redraw the ring at screen resolution so its glow
    # and stroke do not become wider when a large pet splits.
    center = sparks.width / 2
    radius = sparks.width * 0.365
    ImageDraw.Draw(sparks).ellipse((center-radius, center-radius,
                                  center+radius, center+radius), fill=(0, 0, 0, 0))
    return sparks


def burst_ring(size, radius):
    """Antialiased ring with a fixed screen-space stroke and restrained glow."""
    factor = 2
    # Antialias just the ring's bounds, not the entire expansion canvas.
    side = min(size, math.ceil(radius * 2 + 10))
    ring = Image.new("RGBA", (side * factor, side * factor))
    draw = ImageDraw.Draw(ring)
    center = (side - 1) * factor / 2
    for width, color in ((5, (255, 200, 25, 28)),
                         (3, (255, 213, 35, 65)),
                         (1.5, (255, 230, 65, 255))):
        outer = (radius + width / 2) * factor
        draw.ellipse((center-outer, center-outer, center+outer, center+outer),
                     outline=color, width=max(1, round(width * factor)))
    ring = ring.resize((side, side), Image.Resampling.BILINEAR)
    frame = Image.new("RGBA", (size, size))
    frame.alpha_composite(ring, ((size - side) // 2, (size - side) // 2))
    return frame


def burst_size(height):
    return max(32, math.ceil(height * 2.8))


def burst_frame(height, elapsed):
    """A growing gold ring and varied sparks; size follows the mother's body."""
    size = burst_size(height)
    frame = Image.new("RGBA", (size, size))
    u = max(0.0, min(1.0, elapsed / BURST_DURATION))
    if elapsed < 0 or u >= 1:
        return frame
    scale = 0.18 + 0.82 * (1 - (1 - u) ** 2.2)
    side = max(1, round(size * scale))
    sprite = burst_texture().resize((side, side), Image.Resampling.BILINEAR)
    opacity = min(1.0, elapsed / 0.035) * (1 - max(0, (u - 0.30) / 0.70)) ** 1.3
    frame.alpha_composite(sprite, ((size - side) // 2, (size - side) // 2))
    frame.alpha_composite(burst_ring(size, side * 0.34))
    frame.putalpha(frame.getchannel("A").point(lambda a: round(a * opacity)))
    return frame


def premultiply(frame):
    """UpdateLayeredWindow expects premultiplied colors for smooth transparency."""
    r, g, b, a = frame.split()
    return Image.merge("RGBA", (ImageChops.multiply(r, a), ImageChops.multiply(g, a),
                                ImageChops.multiply(b, a), a))


def combo_frame(text, split=False, scale=1.0):
    """Warm compact speech bubble; render at double resolution for crisp type."""
    font = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 28)
    width = max(160, math.ceil(font.getlength(text) / 2) + 30)
    image = Image.new("RGBA", (width * 2, 100))
    draw = ImageDraw.Draw(image)
    fill = "#fff4cc" if not split else "#ffe1a3"
    draw.rounded_rectangle((2, 2, width * 2 - 3, 81), radius=24,
                           fill=fill, outline="#e6b84f", width=2)
    middle = width
    draw.polygon(((middle-10, 80), (middle, 96), (middle+10, 80)), fill=fill)
    draw.text((width, 40), text, font=font, anchor="mm", fill="#704518")
    return image.resize((max(1, round(width * scale)), max(1, round(50 * scale))),
                        Image.Resampling.LANCZOS)


class ComboBubble:
    """Mouse-transparent feedback following one pet; no extra physics object."""
    def __init__(self, pet):
        self.pet = pet
        self.closed = False
        self.after = self.surface = None
        self.until = 0
        self.frame = None
        self.window = tk.Toplevel(pet.group.root)
        self.window.withdraw()
        self.window.overrideredirect(True)
        self.window.attributes("-topmost", True)
        self.window.configure(bg="#ff00ff")
        if sys.platform == "win32":
            from pet_surface import WindowsSurface
            self.surface = WindowsSurface(self.window)
            style = self.surface.user.GetWindowLongW(self.surface.hwnd, -20)
            self.surface.user.SetWindowLongW(self.surface.hwnd, -20,
                                            style | 0x20 | 0x08000000 | 0x80)
        else:
            self.window.attributes("-transparentcolor", "#ff00ff")
            self.label = tk.Label(self.window, bg="#ff00ff", bd=0)
            self.label.pack()

    def show(self, text, split=False):
        self.text, self.split = text, split
        self._frame_key = None
        self.until = time.monotonic() + 1.15
        if self.after is None:
            self._tick()

    def _tick(self):
        self.after = None
        remaining = self.until - time.monotonic()
        if self.closed:
            return
        if remaining <= 0 or getattr(self.pet, "_closed", False):
            self.close()
            return
        box = self.pet.visible_box()
        # Half-size feedback, still proportional to intermediate growth sizes.
        scale = getattr(self.pet, "global_scale", 0.6) / 0.6 * 0.5
        key = round(50 * scale)
        if key != self._frame_key:
            self.frame = combo_frame(self.text, self.split, scale)
            self._frame_key = key
        width, height = self.frame.size
        x = round(self.pet.root.winfo_x() + (box[0] + box[2]) / 2 - width / 2)
        y = round(self.pet.root.winfo_y() + box[1] - height - 10 * scale)
        # Keep feedback readable even when the pet touches a screen edge.
        from pet_group import work_area
        left, top, right, bottom = work_area(self.pet.foot_position())
        x = max(left, min(right - width, x))
        y = max(top, min(bottom - height, y))
        frame = self.frame.copy()
        opacity = min(1, remaining / 0.25)
        frame.putalpha(frame.getchannel("A").point(lambda a: round(a * opacity)))
        if self.surface is not None:
            self.surface.present(premultiply(frame), (x, y))
        else:
            self.window.geometry(f"{width}x{height}{x:+d}{y:+d}")
            self.photo = ImageTk.PhotoImage(frame, master=self.window)
            self.label.configure(image=self.photo)
        self.window.deiconify()
        self.after = self.window.after(33, self._tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.after is not None:
            self.window.after_cancel(self.after)
            self.after = None
        if self.surface is not None:
            self.surface.close()
            self.surface = None
        self.window.destroy()


class SplitBurst:
    def __init__(self, owner, center, height, on_close):
        self.closed = False
        self.after = self.surface = self.photo = None
        self.on_close = on_close
        self.height = height
        self.size = burst_size(height)
        self.position = (round(center[0] - self.size / 2), round(center[1] - self.size / 2))
        self.started = time.monotonic()
        self.window = tk.Toplevel(owner)
        self.window.withdraw()
        try:
            self.window.title("奶小蛙分裂光圈")
            self.window.overrideredirect(True)
            self.window.attributes("-topmost", True)
            self.window.configure(bg="#ff00ff")
            self.window.geometry(f"{self.size}x{self.size}{self.position[0]:+d}{self.position[1]:+d}")
            if sys.platform == "win32":
                from pet_surface import WindowsSurface
                self.surface = WindowsSurface(self.window)
                style = self.surface.user.GetWindowLongW(self.surface.hwnd, -20)
                # Transparent to mouse input, no activation, no taskbar entry.
                self.surface.user.SetWindowLongW(self.surface.hwnd, -20,
                                                style | 0x20 | 0x08000000 | 0x80)
                self.surface.present(Image.new("RGBA", (self.size, self.size)), self.position)
            else:
                self.window.attributes("-transparentcolor", "#ff00ff")
                self.canvas = tk.Canvas(self.window, width=self.size, height=self.size,
                                        bg="#ff00ff", highlightthickness=0, bd=0)
                self.canvas.pack()
                self.image_id = self.canvas.create_image(0, 0, anchor="nw")
            self.window.deiconify()
            self._tick()
        except Exception:
            self.close()
            raise

    def _tick(self):
        self.after = None
        if self.closed:
            return
        elapsed = time.monotonic() - self.started
        if elapsed >= BURST_DURATION:
            self.close()
            return
        frame = burst_frame(self.height, elapsed)
        if self.surface is not None:
            self.surface.present(premultiply(frame), self.position)
        else:
            flat = Image.new("RGB", frame.size, "#ff00ff")
            flat.paste(frame, mask=frame.getchannel("A"))
            self.photo = ImageTk.PhotoImage(flat, master=self.window)
            self.canvas.itemconfigure(self.image_id, image=self.photo)
        self.after = self.window.after(round(1000 / BURST_FPS), self._tick)

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.after is not None:
            self.window.after_cancel(self.after)
            self.after = None
        if self.surface is not None:
            self.surface.close()
            self.surface = None
        self.photo = None
        self.window.destroy()
        self.on_close(self)
