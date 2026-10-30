"""
Doodle cursor (Windows 10+; PyQt5 + numpy-free)

A transparent click-through overlay that follows the mouse. The doodle sways
left/right with a colored trail, has blinking eyes that follow your motion,
droops (and fades sleepy) when the mouse sits still, snores little Z's, throws
sparks when you click, and gets dizzy if you shake the mouse side to side.

    Ctrl+Shift+Q  quit          Ctrl+Shift+H  show / hide the real cursor
    Ctrl+Shift+T  next color theme
    Ctrl+Shift+Up / Down  bigger / smaller
    --restore-cursor   put the Windows cursors back if something crashed

Settings: ~/.doodle_cursor.json (created on first run). Tray icon has Quit too.
"""
import sys


def restore_system_cursor():
    """Restore Windows cursors. Safe to call anywhere, imports no GUI."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.user32.SystemParametersInfoW(0x0057, 0, None, 0)  # SPI_SETCURSORS
    except Exception as exc:
        print("Could not restore the system cursor:", exc)


if __name__ == "__main__" and "--restore-cursor" in sys.argv:
    restore_system_cursor()
    print("System cursor restored.")
    raise SystemExit(0)

import atexit
import signal
import time
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QPointF
from PyQt5.QtGui import (QColor, QCursor, QFont, QIcon, QLinearGradient, QPainter,
                         QPainterPath, QPen, QPixmap, QPolygonF)
from PyQt5.QtWidgets import (QAction, QActionGroup, QApplication, QMenu,
                             QSystemTrayIcon, QWidget)

from doodle_core import (THEME_NAMES, THEMES, Doodle, load_config, mix, palette,
                         save_config, shape, star_points)

CONFIG_PATH = Path.home() / ".doodle_cursor.json"


# ---------------------------------------------------------------- Windows helpers
def key_down(vk):
    if sys.platform != "win32":
        return False
    import ctypes
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def hide_system_cursor():
    if sys.platform != "win32":
        return
    try:
        import ctypes
        u = ctypes.windll.user32
        u.CreateCursor.restype = ctypes.c_void_p
        u.CreateCursor.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int,
                                   ctypes.c_void_p, ctypes.c_void_p]
        u.SetSystemCursor.argtypes = [ctypes.c_void_p, ctypes.c_uint]
        atexit.register(restore_system_cursor)       # always give the cursor back
        for cid in (32512, 32513, 32514, 32515, 32516, 32642, 32643, 32644,
                    32645, 32646, 32648, 32649, 32650, 32651):
            and_mask = (ctypes.c_ubyte * 128)(*([0xFF] * 128))
            xor_mask = (ctypes.c_ubyte * 128)()
            handle = u.CreateCursor(None, 0, 0, 32, 32, and_mask, xor_mask)
            if handle:
                u.SetSystemCursor(handle, cid)
    except Exception as exc:
        restore_system_cursor()
        print("Could not hide the system cursor:", exc)


# ---------------------------------------------------------------- overlay
class Overlay(QWidget):
    def __init__(self, cfg):
        super().__init__()
        self.cfg = cfg
        self.doodle = Doodle(cfg)
        self.frames = []
        self.particles = []
        self.sparks = []
        self.stars = []
        self.face = (1.0, 0.0, 0.0, "normal")
        self.win_w = self.win_h = self.margin = 0
        self.theme_actions = {}      # filled in by build_tray
        self.t = 0.0
        self.origin = (0, 0)
        self.cursor_hidden = False
        self._keys = {}

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                            | Qt.Tool | Qt.WindowTransparentForInput)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.apply_size()

        self.last_time = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.PreciseTimer)
        self.timer.timeout.connect(self.tick)
        self.timer.start(max(1, 1000 // cfg["fps"]))

        if cfg["hide_cursor"]:
            self.set_cursor_hidden(True)

    # -- real cursor
    def set_cursor_hidden(self, hidden):
        if hidden:
            hide_system_cursor()
        else:
            restore_system_cursor()
        self.cursor_hidden = hidden

    def toggle_cursor(self):
        self.set_cursor_hidden(not self.cursor_hidden)

    # -- size: the window grows with the doodle
    def apply_size(self):
        length = self.cfg["length"]
        self.margin = int(120 + length * 0.1)
        self.win_w = int(length * 3 + 160)
        self.win_h = int(self.margin + length * 1.6 + 100)
        self.resize(self.win_w, self.win_h)

    def change_size(self, factor):
        old = self.cfg["length"]
        new = max(40.0, min(200.0, old * factor))
        ratio = new / old
        self.cfg["length"] = new
        self.cfg["width"] = max(6.0, min(30.0, self.cfg["width"] * ratio))
        save_config(CONFIG_PATH, self.cfg)
        self.apply_size()

    # -- theme
    def set_theme(self, name):
        self.cfg["theme"] = name
        save_config(CONFIG_PATH, self.cfg)
        if name in self.theme_actions:
            self.theme_actions[name].setChecked(True)

    def cycle_theme(self):
        i = THEME_NAMES.index(self.cfg["theme"]) if self.cfg["theme"] in THEME_NAMES else 0
        self.set_theme(THEME_NAMES[(i + 1) % len(THEME_NAMES)])

    # -- hotkeys: Ctrl+Shift+<key>, fire once per press
    def hotkey(self, name, vk):
        down = key_down(0x11) and key_down(0x10) and key_down(vk)
        fired = down and not self._keys.get(name, False)
        self._keys[name] = down
        return fired

    # -- frame
    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last_time, 0.05)
        self.last_time = now

        if self.hotkey("quit", 0x51):
            QApplication.quit()
            return
        if self.hotkey("cursor", 0x48):
            self.toggle_cursor()
        if self.hotkey("theme", 0x54):
            self.cycle_theme()
        if self.hotkey("bigger", 0x26):
            self.change_size(1.12)
        if self.hotkey("smaller", 0x28):
            self.change_size(1 / 1.12)
        self.t = now

        p = QCursor.pos()
        mouse = (p.x(), p.y())
        self.doodle.step(dt, mouse, key_down(0x01), now)
        self.frames = self.doodle.frames()
        self.particles = self.doodle.particles()
        self.sparks = self.doodle.click_sparks()
        self.stars = self.doodle.dizzy_stars()
        self.face = self.doodle.face()
        self.origin = (mouse[0] - self.win_w // 2, mouse[1] - self.margin)
        self.move(*self.origin)
        if not self.isVisible():
            self.show()
        self.update()

    def paintEvent(self, _event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        ox, oy = self.origin
        for bx, by, bend, droop, direction, scale, alpha, layer in self.frames:
            self.draw_one(painter, bx - ox, by - oy, bend, droop, direction,
                          scale, alpha, layer)
        self.draw_zzz(painter, ox, oy)
        self.draw_sparks(painter, ox, oy)
        self.draw_stars(painter, ox, oy)

    def colors(self, layer, droop):
        return palette(self.cfg["theme"], self.t, layer, droop, self.cfg["sleepy_tint"])

    def draw_one(self, painter, bx, by, bend, droop, direction, scale, alpha, layer):
        polygon, circles, cap, eyes = shape(self.cfg["length"], self.cfg["width"],
                                      bend, droop, direction, scale)
        path = QPainterPath()
        path.addPolygon(QPolygonF([QPointF(bx + x, by + y) for x, y in polygon]))
        path.closeSubpath()
        for cx, cy, r in circles:
            path.addEllipse(QPointF(bx + cx, by + cy), r, r)
        path = path.simplified()                  # merge body + lobes into one outline
        fill, shade, line = self.colors(layer, droop)

        if alpha >= 255:                          # the real doodle: shaded gradient fill
            rect = path.boundingRect()
            grad = QLinearGradient(rect.topLeft(), rect.bottomRight())
            grad.setColorAt(0.0, QColor(*mix(fill, (255, 255, 255), 0.45)))
            grad.setColorAt(0.45, QColor(*fill))
            grad.setColorAt(1.0, QColor(*shade))
            painter.setPen(QPen(QColor(*line), 3, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(grad)
            painter.drawPath(path)
            (x1, y1), (x2, y2) = cap
            painter.setPen(QPen(QColor(*line), 2, Qt.SolidLine, Qt.RoundCap))
            painter.drawLine(QPointF(bx + x1, by + y1), QPointF(bx + x2, by + y2))
            if self.cfg["eyes"]:
                self.draw_eyes(painter, bx, by, eyes, line)
        else:                                     # ghost: tinted, see-through
            painter.setPen(QPen(QColor(*line, alpha), 2, Qt.SolidLine,
                                Qt.RoundCap, Qt.RoundJoin))
            painter.setBrush(QColor(*shade, alpha // 2))
            painter.drawPath(path)

    def draw_eyes(self, painter, bx, by, eyes, line):
        eye_open, gx, gy, mode = self.face
        for ex, ey, r in eyes:
            c = QPointF(bx + ex, by + ey)
            if mode == "dizzy":                       # X eyes
                painter.setPen(QPen(QColor(*line), 2, Qt.SolidLine, Qt.RoundCap))
                k = r * 0.8
                painter.drawLine(QPointF(c.x() - k, c.y() - k), QPointF(c.x() + k, c.y() + k))
                painter.drawLine(QPointF(c.x() - k, c.y() + k), QPointF(c.x() + k, c.y() - k))
            elif eye_open < 0.15:                     # closed / mid-blink
                painter.setPen(QPen(QColor(*line), 2, Qt.SolidLine, Qt.RoundCap))
                painter.drawLine(QPointF(c.x() - r * 0.8, c.y()), QPointF(c.x() + r * 0.8, c.y()))
            else:
                painter.setPen(QPen(QColor(*line), 1.5))
                painter.setBrush(QColor(255, 255, 255))
                painter.drawEllipse(c, r, r * max(0.25, eye_open))
                pupil = r * (0.28 if mode == "surprised" else 0.5)
                painter.setPen(Qt.NoPen)
                painter.setBrush(QColor(20, 20, 20))
                painter.drawEllipse(QPointF(c.x() + gx * r * 0.45, c.y() + gy * r * 0.45 * eye_open),
                                    pupil, pupil * max(0.4, eye_open))

    def draw_sparks(self, painter, ox, oy):
        if not self.sparks:
            return
        fill, shade, _line = self.colors(0, 0.0)
        colors = (fill, shade, (255, 255, 255))
        painter.setPen(Qt.NoPen)
        for x, y, alpha, size, k in self.sparks:
            painter.setBrush(QColor(*colors[k % 3], max(0, min(255, alpha))))
            painter.drawEllipse(QPointF(x - ox, y - oy), size, size)

    def draw_stars(self, painter, ox, oy):
        if not self.stars:
            return
        _fill, _shade, line = self.colors(0, 0.0)
        painter.setPen(QPen(QColor(*line), 1.5))
        painter.setBrush(QColor(255, 225, 90))
        for x, y, size in self.stars:
            painter.drawPolygon(QPolygonF([QPointF(px - ox, py - oy)
                                           for px, py in star_points(x, y, size)]))

    def draw_zzz(self, painter, ox, oy):
        if not self.particles:
            return
        _fill, _shade, line = self.colors(0, 0.0)
        font = QFont("Segoe UI")
        font.setBold(True)
        font.setItalic(True)
        for x, y, alpha, size in self.particles:
            font.setPixelSize(int(size))
            painter.setFont(font)
            painter.setPen(QColor(*line, max(0, min(255, alpha))))
            painter.drawText(QPointF(x - ox, y - oy), "Z")


# ---------------------------------------------------------------- tray + main
def build_tray(app, overlay):
    if not QSystemTrayIcon.isSystemTrayAvailable():
        return None
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    fill, _shade, line = THEMES["peach"]
    p.setPen(QPen(QColor(*line), 4))
    p.setBrush(QColor(*fill))
    p.drawRoundedRect(22, 6, 20, 40, 10, 10)
    p.drawEllipse(12, 40, 20, 18)
    p.drawEllipse(32, 40, 20, 18)
    p.end()

    tray = QSystemTrayIcon(QIcon(pm), app)
    tray.setToolTip("Doodle cursor")
    menu = QMenu()
    show_real = QAction("Show real cursor", menu)
    show_real.setCheckable(True)
    show_real.triggered.connect(lambda _=False: overlay.toggle_cursor())
    menu.addAction(show_real)
    theme_menu = menu.addMenu("Theme  (Ctrl+Shift+T)")
    group = QActionGroup(theme_menu)
    for name in THEME_NAMES:
        action = QAction(name.capitalize(), theme_menu)
        action.setCheckable(True)
        action.setChecked(name == overlay.cfg["theme"])
        action.triggered.connect(lambda _=False, n=name: overlay.set_theme(n))
        group.addAction(action)
        theme_menu.addAction(action)
        overlay.theme_actions[name] = action
    tray._group = group
    quit_action = QAction("Quit", menu)
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    tray.setContextMenu(menu)
    tray.show()
    tray._menu = menu          # keep references alive
    return tray


def main():
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)

    cfg = load_config(CONFIG_PATH)
    overlay = Overlay(cfg)
    tray = build_tray(app, overlay)

    app.aboutToQuit.connect(restore_system_cursor)
    signal.signal(signal.SIGINT, lambda *a: app.quit())   # tick timer lets Python see it
    code = app.exec_()
    restore_system_cursor()
    return code


if __name__ == "__main__":
    sys.exit(main())