"""Present complete transparent Windows frames without a color-key redraw."""
from __future__ import annotations

import ctypes as C
from ctypes import wintypes as W
from PIL import Image, ImageChops


class BitmapInfo(C.Structure):
    _fields_ = [("size", W.DWORD), ("width", W.LONG), ("height", W.LONG),
                ("planes", W.WORD), ("bits", W.WORD), ("compression", W.DWORD),
                ("image_bytes", W.DWORD), ("xppm", W.LONG), ("yppm", W.LONG),
                ("colors", W.DWORD), ("important", W.DWORD), ("palette", W.DWORD)]


class Blend(C.Structure):
    _fields_ = [("operation", W.BYTE), ("flags", W.BYTE),
                ("opacity", W.BYTE), ("alpha", W.BYTE)]


def transparent_frame(rgb, size, origin, key):
    """Preserve the existing binary silhouette; transparent pixels have zero RGB."""
    rgb = rgb.convert("RGB")
    diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, key))
    r, g, b = diff.split()
    mask = ImageChops.lighter(ImageChops.lighter(r, g), b).point(lambda v: 255 if v else 0)
    sprite = Image.new("RGBA", rgb.size)
    sprite.paste(rgb, (0, 0), mask)
    frame = Image.new("RGBA", size)
    frame.paste(sprite, (round(origin[0]), round(origin[1])))
    return frame


class WindowsSurface:
    """One owned DIB/DC; UpdateLayeredWindow commits pixels, size and position."""
    def __init__(self, window):
        self.user = C.WinDLL("user32", use_last_error=True)
        self.gdi = C.WinDLL("gdi32", use_last_error=True)
        self.user.GetAncestor.argtypes = [W.HWND, W.UINT]
        self.user.GetAncestor.restype = W.HWND
        self.user.GetWindowLongW.argtypes = [W.HWND, C.c_int]
        self.user.GetWindowLongW.restype = W.LONG
        self.user.SetWindowLongW.argtypes = [W.HWND, C.c_int, W.LONG]
        self.user.SetWindowLongW.restype = W.LONG
        self.user.GetWindowRect.argtypes = [W.HWND, C.POINTER(W.RECT)]
        self.user.GetWindowRect.restype = W.BOOL
        self.user.UpdateLayeredWindow.argtypes = [W.HWND, W.HDC, C.POINTER(W.POINT),
            C.POINTER(W.SIZE), W.HDC, C.POINTER(W.POINT), W.DWORD,
            C.POINTER(Blend), W.DWORD]
        self.user.UpdateLayeredWindow.restype = W.BOOL
        self.gdi.CreateCompatibleDC.argtypes = [W.HDC]
        self.gdi.CreateCompatibleDC.restype = W.HDC
        self.gdi.CreateDIBSection.argtypes = [W.HDC, C.POINTER(BitmapInfo), W.UINT,
            C.POINTER(C.c_void_p), W.HANDLE, W.DWORD]
        self.gdi.CreateDIBSection.restype = W.HBITMAP
        self.gdi.SelectObject.argtypes = [W.HDC, W.HANDLE]
        self.gdi.SelectObject.restype = W.HANDLE
        self.gdi.DeleteObject.argtypes = [W.HANDLE]
        self.gdi.DeleteDC.argtypes = [W.HDC]
        # SetLayeredWindowAttributes (Tk's color key) and UpdateLayeredWindow
        # cannot be mixed. Clear Tk's key and reset the layered style once.
        window.attributes("-transparentcolor", "")
        # Tk may rebuild its native wrapper while mapping/changing attributes.
        # Acquire the final HWND only after those idle geometry tasks finish.
        window.update_idletasks()
        self.hwnd = self.user.GetAncestor(window.winfo_id(), 2)
        style = self.user.GetWindowLongW(self.hwnd, -20)
        self.user.SetWindowLongW(self.hwnd, -20, style & ~0x80000)
        self.user.SetWindowLongW(self.hwnd, -20, style | 0x80000)
        self.dc = self.gdi.CreateCompatibleDC(None)
        if not self.dc:
            raise C.WinError(C.get_last_error())
        self.bitmap = self.old_bitmap = None
        self.bits = C.c_void_p()
        self.size = None

    def _allocate(self, size):
        if size == self.size:
            return
        w, h = size
        info = BitmapInfo(size=40, width=w, height=-h, planes=1, bits=32)
        bits = C.c_void_p()
        bitmap = self.gdi.CreateDIBSection(self.dc, C.byref(info), 0, C.byref(bits), None, 0)
        if not bitmap:
            raise C.WinError(C.get_last_error())
        previous = self.gdi.SelectObject(self.dc, bitmap)
        if self.bitmap:
            self.gdi.DeleteObject(self.bitmap)
        else:
            self.old_bitmap = previous
        self.bitmap, self.bits, self.size = bitmap, bits, size

    def present(self, frame, position=None):
        self._allocate(frame.size)
        pixels = frame.tobytes("raw", "BGRA")
        C.memmove(self.bits, pixels, len(pixels))
        if position is None:
            rect = W.RECT()
            if not self.user.GetWindowRect(self.hwnd, C.byref(rect)):
                raise C.WinError(C.get_last_error())
            position = rect.left, rect.top
        dest = W.POINT(round(position[0]), round(position[1]))
        size, source, blend = W.SIZE(*frame.size), W.POINT(0, 0), Blend(0, 0, 255, 1)
        if not self.user.UpdateLayeredWindow(self.hwnd, None, C.byref(dest), C.byref(size),
                self.dc, C.byref(source), 0, C.byref(blend), 2):
            raise C.WinError(C.get_last_error())

    def close(self):
        if self.dc:
            if self.bitmap:
                self.gdi.SelectObject(self.dc, self.old_bitmap)
                self.gdi.DeleteObject(self.bitmap)
                self.bitmap = None
            self.gdi.DeleteDC(self.dc)
            self.dc = None
