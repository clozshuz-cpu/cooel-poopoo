"""Pure logic for the doodle cursor: config, motion and shape geometry. No Qt, no OpenGL."""
import colorsys
import json
import math
import random
from collections import deque
from pathlib import Path

DEFAULT_CONFIG = {
    "schema_version": 2,
    "length": 90.0,        # body length in px (40..200)
    "width": 13.0,         # body half-width in px (6..30)
    "fps": 60,             # 15..120
    "follow": 0.20,        # how quickly the doodle catches up to the mouse (0.05..1)
    "max_bend": 25.0,      # max sway in degrees (0..45)
    "trail_ghosts": 3,     # ghost copies while moving (0..3)
    "idle_seconds": 2.0,   # stationary time before it droops (0.5..60)
    "hide_cursor": True,   # hide the real system cursor on start
    "theme": "peach",      # see THEMES
    "sleepy_tint": True,   # color fades to cool gray while drooping
    "zzz": True,           # floating Z's after sleeping a while
    "sleep_seconds": 4.0,  # extra seconds of drooping before the Z's start (1..60)
    "eyes": True,          # googly eyes that blink, follow motion, and close when sleepy
    "sparkles": True,      # sparks pop out where you click
    "dizzy": True,         # shake the mouse side to side fast and it gets dizzy
}

# (fill, shade, outline); "rainbow" is computed over time
THEMES = {
    "peach":   ((255, 205, 175), (232, 150, 130), (110, 50, 50)),
    "pink":    ((255, 175, 205), (228, 110, 160), (100, 30, 60)),
    "mint":    ((175, 240, 205), (100, 200, 150), (20, 80, 60)),
    "sky":     ((175, 212, 255), (110, 160, 230), (20, 50, 100)),
    "lava":    ((255, 160, 95), (220, 70, 40), (80, 20, 10)),
    "mono":    ((255, 255, 255), (200, 200, 208), (0, 0, 0)),
    "rainbow": None,
}
THEME_NAMES = tuple(THEMES)
SLEEPY = (170, 182, 220)

BEND_GAIN = 0.8
BEND_K, BEND_C = 150.0, 10.0       # sway spring (stiffness, damping)
PERK_K, PERK_C = 220.0, 14.0       # perk-up spring (gives the overshoot)
DROOP_ANGLE = 1.75                 # radians of hook when drooped
DROOP_EASE = 3.0
TRAIL_LAGS = (4, 8, 12)            # frames behind, one per ghost
TRAIL_ALPHA = (140, 90, 50)
DROOP_GHOSTS = ((7, 3, 110), (14, 6, 70))   # dx, dy, alpha


def _clamp(value, lower, upper):
    return max(lower, min(upper, value))


def validate_config(data, defaults=DEFAULT_CONFIG):
    """Coerce and clamp JSON data. Returns (config, needs_save)."""
    config = dict(defaults)
    needs_save = not isinstance(data, dict)
    if isinstance(data, dict):
        for key, default in defaults.items():
            if key not in data:
                needs_save = True
                continue
            value = data[key]
            if isinstance(default, bool):
                valid = type(value) is bool
            elif isinstance(default, float):
                valid = type(value) in (int, float) and math.isfinite(float(value))
            elif isinstance(default, str):
                valid = type(value) is str
            else:
                valid = type(value) is int
            if valid:
                config[key] = value
            else:
                needs_save = True
    before = dict(config)
    config["length"] = float(_clamp(config["length"], 40.0, 200.0))
    config["width"] = float(_clamp(config["width"], 6.0, 30.0))
    config["fps"] = int(_clamp(config["fps"], 15, 120))
    config["follow"] = float(_clamp(config["follow"], 0.05, 1.0))
    config["max_bend"] = float(_clamp(config["max_bend"], 0.0, 45.0))
    config["trail_ghosts"] = int(_clamp(config["trail_ghosts"], 0, 3))
    config["idle_seconds"] = float(_clamp(config["idle_seconds"], 0.5, 60.0))
    config["hide_cursor"] = bool(config["hide_cursor"])
    config["sleepy_tint"] = bool(config["sleepy_tint"])
    config["zzz"] = bool(config["zzz"])
    for key in ("eyes", "sparkles", "dizzy"):
        config[key] = bool(config[key])
    config["sleep_seconds"] = float(_clamp(config["sleep_seconds"], 1.0, 60.0))
    if config["theme"] not in THEMES:
        config["theme"] = defaults["theme"]
    config["schema_version"] = defaults["schema_version"]
    needs_save = needs_save or before != config
    return config, needs_save


def load_config(path):
    try:
        with Path(path).open("r", encoding="utf-8") as source:
            data = json.load(source)
    except (OSError, ValueError):
        data = None
    config, needs_save = validate_config(data)
    if needs_save:
        save_config(path, config)
    return config


def save_config(path, config):
    try:
        with Path(path).open("w", encoding="utf-8") as target:
            json.dump(config, target, indent=2)
    except OSError:
        pass


# ---------------------------------------------------------------- color
def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _hsv(h, sat, val):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, sat, val)
    return (int(r * 255), int(g * 255), int(b * 255))


def palette(theme, t, layer, droop, sleepy):
    """Return (fill, shade, outline) RGB tuples for one layer (0 = main doodle, 1+ = ghosts)."""
    if theme == "rainbow":
        h = t * 0.25 - layer * 0.07
        fill, shade, line = _hsv(h, 0.45, 1.0), _hsv(h, 0.75, 0.85), _hsv(h, 0.85, 0.30)
    else:
        fill, shade, line = THEMES.get(theme) or THEMES["peach"]
    if sleepy and droop > 0:
        k = min(1.0, droop) * 0.55
        fill, shade = mix(fill, SLEEPY, k), mix(shade, SLEEPY, k)
    return fill, shade, line


# ---------------------------------------------------------------- geometry
def _bezier(p0, p1, p2, n=24):
    points = []
    for i in range(n + 1):
        t = i / n
        a, b, c = (1 - t) ** 2, 2 * (1 - t) * t, t * t
        points.append((a * p0[0] + b * p1[0] + c * p2[0],
                       a * p0[1] + b * p1[1] + c * p2[1]))
    return points


def _spine(length, bend_deg, droop, direction):
    """Centre line from the base (0, 0) up to the tip. y is negative upward."""
    length *= 1 - 0.15 * max(droop, 0.0)
    bend = math.radians(bend_deg)
    theta = bend + (direction * DROOP_ANGLE - bend) * droop
    p1 = (math.sin(bend * 0.5) * length * 0.5,
          -math.cos(bend * 0.5) * length * 0.55 * (1 - 0.3 * droop))
    p2 = (math.sin(theta) * length, -math.cos(theta) * length)
    return _bezier((0.0, 0.0), p1, p2)


def _outline(points, half):
    """Body outline, slightly pinched in the middle like a hand-drawn doodle."""
    n = len(points)
    left, right = [], []
    for i, (x, y) in enumerate(points):
        w = half * (1.0 - 0.16 * math.sin(math.pi * i / (n - 1)))
        a = points[max(i - 1, 0)]
        b = points[min(i + 1, len(points) - 1)]
        dx, dy = b[0] - a[0], b[1] - a[1]
        d = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / d, dx / d
        left.append((x + nx * w, y + ny * w))
        right.append((x - nx * w, y - ny * w))
    return left + right[::-1]


def shape(length, width, bend_deg, droop, direction, scale):
    """Return (polygon, circles, cap_line, eyes) relative to the base point."""
    points = _spine(length * scale, bend_deg, droop, direction)
    half = width * scale
    polygon = _outline(points, half)
    base, tip = points[0], points[-1]
    circles = [(base[0] - half * 0.75, base[1] + half * 0.3, half * 0.95),
               (base[0] + half * 0.75, base[1] + half * 0.3, half * 0.95),
               (tip[0], tip[1], half)]
    i = int(len(points) * 0.88)
    a, b = points[i - 1], points[min(i + 1, len(points) - 1)]
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / d, dx / d
    cx, cy = points[i]
    cap = ((cx + nx * half, cy + ny * half), (cx - nx * half, cy - ny * half))
    j = int(len(points) * 0.55)
    a, b = points[j - 1], points[j + 1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    d = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / d, dx / d
    px, py = points[j]
    eyes = [(px + nx * half * 0.42, py + ny * half * 0.42, half * 0.34),
            (px - nx * half * 0.42, py - ny * half * 0.42, half * 0.34)]
    return polygon, circles, cap, eyes


def star_points(cx, cy, r):
    """Five-point star polygon, used for the dizzy stars."""
    pts = []
    for i in range(10):
        radius = r if i % 2 == 0 else r * 0.45
        angle = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
    return pts


# ---------------------------------------------------------------- motion
class Doodle:
    """Follows a mouse position: eased follow, sway spring, trail, idle droop."""

    def __init__(self, config):
        self.cfg = config
        self.pos = None
        self.last = None
        self.last_move = 0.0
        self.direction = -1
        self.bend = self.bend_v = 0.0
        self.droop = self.droop_v = 0.0
        self.was_idle = False
        self.scale = 1.0
        self.speed = 0.0
        self.bob = 0.0
        self.history = deque(maxlen=max(TRAIL_LAGS) + 2)
        self.zs = []                 # floating Z's: [born, life, dx, dy]
        self.last_z = 0.0
        self.now = 0.0
        self.pressed = False
        self.was_pressed = False
        self.sparks = []             # [born, life, x, y, vx, vy]
        self.gaze = [0.0, 0.0]
        self.next_blink = 0.0
        self.blink_until = 0.0
        self.last_sign = 0
        self.reversals = deque()
        self.dizzy_until = 0.0

    def step(self, dt, mouse, pressed, now):
        cfg = self.cfg
        mx, my = mouse
        if self.pos is None:
            self.pos = [float(mx), float(my)]
            self.last = (mx, my)
            self.last_move = now
        lx, ly = self.last
        if math.hypot(mx - lx, my - ly) > 0.5:
            self.last_move = now
            if abs(mx - lx) > 1:
                self.direction = 1 if mx > lx else -1
        dx = mx - lx
        if abs(dx) > 3:                      # count fast left/right reversals
            sign = 1 if dx > 0 else -1
            if self.last_sign and sign != self.last_sign:
                self.reversals.append(now)
            self.last_sign = sign
        while self.reversals and now - self.reversals[0] > 1.0:
            self.reversals.popleft()
        if cfg["dizzy"] and len(self.reversals) >= 7:
            self.dizzy_until = now + 1.6
            self.reversals.clear()
        self.last = (mx, my)

        lag_x, lag_y = mx - self.pos[0], my - self.pos[1]
        k = 1 - (1 - cfg["follow"]) ** (dt * 60)
        self.pos[0] += lag_x * k
        self.pos[1] += lag_y * k
        self.speed = math.hypot(lag_x, lag_y)

        target = _clamp(lag_x * BEND_GAIN, -cfg["max_bend"], cfg["max_bend"])
        self.bend_v += (BEND_K * (target - self.bend) - BEND_C * self.bend_v) * dt
        self.bend += self.bend_v * dt

        idle = (now - self.last_move) > cfg["idle_seconds"]
        if idle != self.was_idle:
            self.droop_v = 0.0
            self.was_idle = idle
        if idle:
            self.droop += (1.0 - self.droop) * min(1.0, dt * DROOP_EASE)
        else:
            self.droop_v += (PERK_K * (0.0 - self.droop) - PERK_C * self.droop_v) * dt
            self.droop += self.droop_v * dt
        self.droop = _clamp(self.droop, -0.15, 1.0)

        want = 1.1 if pressed else 1.0
        self.scale += (want - self.scale) * min(1.0, dt * 20)
        self.bob = math.sin(now * 2.0) * 3.0 * max(self.droop, 0.0)

        self.now = now
        self.pressed = pressed
        if pressed and not self.was_pressed and cfg["sparkles"]:
            for k in range(12):              # burst where the click happened
                angle = random.uniform(0, math.tau)
                speed = random.uniform(90, 220)
                self.sparks.append([now, 0.65, float(mx), float(my),
                                    math.cos(angle) * speed,
                                    math.sin(angle) * speed - 60])
        self.was_pressed = pressed
        self.sparks = [sp for sp in self.sparks if now - sp[0] < sp[1]]

        self.gaze[0] += (_clamp(lag_x / 25.0, -1, 1) - self.gaze[0]) * min(1.0, dt * 12)
        self.gaze[1] += (_clamp(lag_y / 25.0, -1, 1) - self.gaze[1]) * min(1.0, dt * 12)
        if now >= self.next_blink:
            self.blink_until = now + 0.14
            self.next_blink = now + random.uniform(2.5, 6.0)

        idle_for = now - self.last_move
        if not idle:
            self.zs.clear()
        elif (cfg["zzz"] and idle_for > cfg["idle_seconds"] + cfg["sleep_seconds"]
              and now - self.last_z > 1.3):
            self.last_z = now
            reach = cfg["length"] * self.scale
            self.zs.append([now, 2.6, self.direction * reach * 0.84, -reach * 0.12])
        self.zs = [z for z in self.zs if now - z[0] < z[1]]

        base = self._base()
        self.history.append((base[0], base[1], self.bend, self.droop, self.direction))

    def _base(self):
        return (self.pos[0],
                self.pos[1] + self.cfg["length"] * self.scale + self.bob)

    def frames(self):
        """Draw list of (base_x, base_y, bend, droop, direction, scale, alpha, layer); last is the real one; layer 0 = main, 1+ = ghosts."""
        out = []
        if self.pos is None:
            return out
        strength = min(1.0, self.speed / 40.0)
        if strength > 0.05:
            ghosts = list(zip(TRAIL_LAGS, TRAIL_ALPHA))[:self.cfg["trail_ghosts"]]
            for layer, (lag, alpha) in enumerate(ghosts, 1):
                if len(self.history) > lag:
                    bx, by, bend, droop, direction = self.history[-1 - lag]
                    out.append((bx, by, bend, droop, direction, self.scale,
                                int(alpha * strength), layer))
        bx, by = self._base()
        if self.droop > 0.3:
            for layer, (dx, dy, alpha) in enumerate(DROOP_GHOSTS, 1):
                out.append((bx + self.direction * dx, by + dy, self.bend, self.droop,
                            self.direction, self.scale, int(alpha * self.droop), layer))
        out.append((bx, by, self.bend, self.droop, self.direction, self.scale, 255, 0))
        return out

    def particles(self):
        """Floating Z's as (x, y, alpha, size) in screen coordinates."""
        if self.pos is None:
            return []
        bx, by = self._base()
        out = []
        for born, life, dx, dy in self.zs:
            age = self.now - born
            out.append((bx + dx + math.sin(age * 2.5) * 6, by + dy - age * 28,
                        int(255 * (1 - age / life)), 12 + age * 6))
        return out

    def face(self):
        """(eye_open 0..1, gaze_x, gaze_y, mode) where mode is normal/surprised/dizzy."""
        if self.now < self.dizzy_until:
            return (1.0, 0.0, 0.0, "dizzy")
        eye_open = _clamp(1.0 - self.droop * 1.7, 0.0, 1.0)
        if self.now < self.blink_until:
            eye_open = 0.0
        return (eye_open, self.gaze[0], self.gaze[1],
                "surprised" if self.pressed else "normal")

    def click_sparks(self):
        """Sparks as (x, y, alpha, size, k) in screen coordinates."""
        out = []
        for k, (born, life, x, y, vx, vy) in enumerate(self.sparks):
            age = self.now - born
            fade = max(0.0, 1 - age / life)
            out.append((x + vx * age, y + vy * age + 190 * age * age,
                        int(255 * fade), 2 + 5 * fade, k))
        return out

    def dizzy_stars(self):
        """Three orbiting stars above the tip as (x, y, size); empty unless dizzy."""
        if self.pos is None or self.now >= self.dizzy_until:
            return []
        cx, cy = self.pos[0], self.pos[1] - 20
        return [(cx + math.cos(self.now * 6 + k * 2.094) * 30,
                 cy + math.sin(self.now * 6 + k * 2.094) * 9, 7) for k in range(3)]


if __name__ == "__main__":      # quick self-check, no GUI needed
    cfg, _ = validate_config({"fps": 9999, "follow": "x"})
    assert cfg["fps"] == 120 and cfg["follow"] == 0.20
    d = Doodle(cfg)
    t = 0.0
    for i in range(60):          # sweep right
        t += 1 / 60
        d.step(1 / 60, (100 + i * 8, 100), False, t)
    assert d.bend > 5 and d.droop < 0.2, (d.bend, d.droop)
    for i in range(240):         # stand still 4 s
        t += 1 / 60
        d.step(1 / 60, (580, 100), False, t)
    assert d.droop > 0.9, d.droop
    for i in range(480):         # keep sleeping 8 more seconds -> Z's
        t += 1 / 60
        d.step(1 / 60, (580, 100), False, t)
    assert d.particles(), "expected floating Z's"
    d.step(1 / 60, (700, 100), False, t + 0.02)
    assert not d.particles(), "Z's should vanish when the mouse moves"
    fill, shade, line = palette("rainbow", 1.0, 0, 0.0, True)
    assert all(0 <= c <= 255 for c in fill + shade + line)
    assert validate_config({"theme": "nope"})[0]["theme"] == "peach"
    poly, circles, cap, eyes = shape(90.0, 13.0, 0.0, 0.0, 1, 1.0)
    assert len(eyes) == 2 and eyes[0][2] > 2
    d2 = Doodle(validate_config({})[0])
    t = 100.0
    for i in range(30):          # zigzag fast -> dizzy
        t += 1 / 60
        d2.step(1 / 60, (200 + (12 if i % 2 else -12), 100), False, t)
    assert d2.face()[3] == "dizzy" and len(d2.dizzy_stars()) == 3
    d3 = Doodle(validate_config({})[0])
    d3.step(1 / 60, (50, 50), False, 200.0)
    d3.step(1 / 60, (50, 50), True, 200.02)      # click
    assert len(d3.click_sparks()) == 12
    d3.step(1 / 60, (50, 50), False, 201.0)
    assert not d3.click_sparks(), "sparks should fade out"
    assert d.face()[0] < 0.2, "eyes closed while sleeping"
    print("core ok: bend/droop/zzz/colors/eyes/dizzy/sparks behave")