#!/usr/bin/env python3
"""MAC Monitor Pro v2 — Animated robot + Apple-style HUD panel.

Nouveautés v2:
  • Sparklines historiques (60 pts) pour CPU et RAM
  • Vitesses disk I/O en temps réel (lecture / écriture)
  • Top 5 processus par CPU dans le panneau
  • Notifications système (CPU > 90 %, RAM > 90 %, batterie faible)
  • IP locale dans le header
  • Bouton Quitter stylisé en rouge
  • Auto-chargement du LaunchAgent au démarrage

États du robot selon CPU :
  chill  < 35%  →  danse, notes de musique
  busy   35-60% →  tape au clavier
  hot    60-80% →  transpire
  panic  > 80%  →  panique, flammes
"""

import math, os, socket, subprocess, time, psutil, objc, rumps
from collections import deque
from datetime import timedelta

from Foundation import NSMakeRect, NSMakeSize, NSMakePoint, NSTimer, NSRunLoop, NSObject
from AppKit import (
    NSMenuItem, NSMenu, NSView, NSFont, NSColor, NSBezierPath,
    NSAttributedString, NSForegroundColorAttributeName, NSFontAttributeName,
    NSImage, NSRectFill,
)

# ─── Constantes ───────────────────────────────────────────────────────────────
PW, PH         = 300, 600
PAD            = 18
CORNER         = 14
HIST           = 60          # points dans les sparklines
NOTIF_COOLDOWN = 300         # secondes entre deux notifications identiques

PLIST_PATH  = os.path.expanduser("~/Library/LaunchAgents/com.macmonitor.app.plist")
PLIST_LABEL = "com.macmonitor.app"

# ─── Palette ──────────────────────────────────────────────────────────────────
def _rgba(r, g, b, a=1.0):
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, a)

C_BG         = _rgba(0.11, 0.11, 0.14, 0.99)
C_SEP        = _rgba(1, 1, 1, 0.08)
C_HDR_BG     = _rgba(1, 1, 1, 0.06)
C_WHITE      = NSColor.whiteColor()
C_GRAY       = _rgba(1, 1, 1, 0.45)
C_DIM        = _rgba(1, 1, 1, 0.14)
C_GREEN      = _rgba(0.19, 0.82, 0.35)
C_ORANGE     = _rgba(1.00, 0.62, 0.04)
C_RED        = _rgba(1.00, 0.27, 0.23)
C_BTN_BG     = _rgba(1, 1, 1, 0.07)
C_BTN_RED    = _rgba(1.00, 0.27, 0.23, 0.12)
C_BTN_RED_HV = _rgba(1.00, 0.27, 0.23, 0.24)

def _bar_color(v):
    if v >= 85: return C_RED
    if v >= 60: return C_ORANGE
    return C_GREEN

def _b(n):
    for u in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} P"

def SF(size, weight=0.0):
    return NSFont.systemFontOfSize_weight_(size, weight)

SF_TITLE = SF(13, 0.40)
SF_LABEL = SF(10, 0.35)
SF_VALUE = SF(22, -0.40)
SF_UNIT  = SF(12,  0.00)
SF_INFO  = SF(11,  0.00)
SF_SMALL = SF(10,  0.00)
SF_BTN   = SF(12,  0.30)
SF_PROC  = SF(10, -0.20)

_S: dict = {}

# ─── Helpers de dessin ────────────────────────────────────────────────────────
def _text(s, x, y, color, font):
    NSAttributedString.alloc().initWithString_attributes_(
        s, {NSForegroundColorAttributeName: color, NSFontAttributeName: font}
    ).drawAtPoint_(NSMakePoint(x, y))

def _text_right(s, right_x, y, color, font):
    astr = NSAttributedString.alloc().initWithString_attributes_(
        s, {NSForegroundColorAttributeName: color, NSFontAttributeName: font}
    )
    astr.drawAtPoint_(NSMakePoint(right_x - astr.size().width, y))

def _bar(x, y, w, h, value):
    r = h / 2.0
    C_DIM.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()
    if value > 0:
        fw = max(r * 2, w * min(value, 100) / 100.0)
        _bar_color(value).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, fw, h), r, r).fill()

def _sparkline(x, y, w, h, data, color):
    """Sparkline avec remplissage translucide sous la courbe."""
    pts = list(data)
    if len(pts) < 2:
        return
    n = len(pts)

    # fond subtil
    _rgba(1, 1, 1, 0.04).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), 2, 2).fill()

    def _px(i, v):
        return x + i * w / (n - 1), y + (v / 100.0) * h

    # zone de remplissage
    fill = NSBezierPath.bezierPath()
    fill.moveToPoint_(NSMakePoint(x, y))
    for i, v in enumerate(pts):
        fill.lineToPoint_(NSMakePoint(*_px(i, v)))
    fill.lineToPoint_(NSMakePoint(x + w, y))
    fill.closePath()
    color.colorWithAlphaComponent_(0.18).setFill()
    fill.fill()

    # trait de la courbe
    line = NSBezierPath.bezierPath()
    line.setLineWidth_(1.2)
    for i, v in enumerate(pts):
        px, py = _px(i, v)
        if i == 0:
            line.moveToPoint_(NSMakePoint(px, py))
        else:
            line.lineToPoint_(NSMakePoint(px, py))
    color.setStroke()
    line.stroke()

def _sep(x, y, w):
    C_SEP.setFill()
    NSBezierPath.fillRect_(NSMakeRect(x, y, w, 0.5))

# ─── Vue du panneau ───────────────────────────────────────────────────────────
class PanelView(NSView):

    _btn_hover: bool = False
    _btn_quit_rect   = None

    def initWithFrame_(self, frame):
        self = objc.super(PanelView, self).initWithFrame_(frame)
        if self is None: return None
        self.setWantsLayer_(True)
        self.layer().setCornerRadius_(CORNER)
        self.layer().setMasksToBounds_(True)
        return self

    def drawRect_(self, _rect):
        s = _S
        if not s:
            return

        w  = self.bounds().size.width
        h  = self.bounds().size.height
        bw = w - PAD * 2
        sw = bw - 80   # largeur des sparklines

        # ── Fond ─────────────────────────────────────────────
        C_BG.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, w, h), CORNER, CORNER).fill()

        # ── Header ───────────────────────────────────────────
        C_HDR_BG.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, h - 44, w, 44), CORNER, CORNER).fill()
        _text("MAC Monitor", PAD, h - 30, C_WHITE, SF_TITLE)
        ip = s.get('local_ip', '')
        if ip:
            _text_right(f" {ip}", w - PAD, h - 30, C_GRAY, SF_SMALL)
        else:
            _text_right("Pro", w - PAD, h - 30, C_GRAY, SF_TITLE)

        y = h - 52

        # ── CPU ──────────────────────────────────────────────
        _sep(PAD, y, bw);  y -= 8
        _text("PROCESSEUR", PAD, y, C_GRAY, SF_LABEL)
        _text_right(s.get('cpu_info', ''), w - PAD, y, C_GRAY, SF_SMALL)
        y -= 18
        cpu = s.get('cpu', 0)
        vs  = f"{cpu:.1f}"
        _text(vs, PAD, y - 26, _bar_color(cpu), SF_VALUE)
        aw  = NSAttributedString.alloc().initWithString_attributes_(
            vs, {NSFontAttributeName: SF_VALUE}).size().width
        _text("%", PAD + aw + 2, y - 18, C_GRAY, SF_UNIT)
        _sparkline(PAD + 80, y - 28, sw, 24, s.get('cpu_hist', []), _bar_color(cpu))
        y -= 34
        _bar(PAD, y, bw, 7, cpu);  y -= 14

        # ── RAM ──────────────────────────────────────────────
        y -= 4;  _sep(PAD, y, bw);  y -= 8
        _text("MÉMOIRE", PAD, y, C_GRAY, SF_LABEL)
        _text_right(s.get('ram_info', ''), w - PAD, y, C_GRAY, SF_SMALL)
        y -= 18
        ram = s.get('ram', 0)
        vs  = f"{ram:.1f}"
        _text(vs, PAD, y - 26, _bar_color(ram), SF_VALUE)
        aw  = NSAttributedString.alloc().initWithString_attributes_(
            vs, {NSFontAttributeName: SF_VALUE}).size().width
        _text("%", PAD + aw + 2, y - 18, C_GRAY, SF_UNIT)
        _sparkline(PAD + 80, y - 28, sw, 24, s.get('ram_hist', []), _bar_color(ram))
        y -= 34
        _bar(PAD, y, bw, 7, ram);  y -= 13
        _text("Swap", PAD, y - 11, C_GRAY, SF_SMALL)
        _bar(PAD + 38, y - 9, bw - 38 - 64, 5, s.get('swap', 0))
        _text_right(s.get('swap_info', ''), w - PAD, y - 11, C_GRAY, SF_SMALL)
        y -= 16

        # ── Réseau ───────────────────────────────────────────
        y -= 4;  _sep(PAD, y, bw);  y -= 8
        _text("RÉSEAU", PAD, y, C_GRAY, SF_LABEL);  y -= 18
        _text("↓", PAD,      y - 18, C_GREEN,  SF(16, 0.3))
        _text(s.get('dl_str', '0.0 B/s'), PAD + 18, y - 18, C_WHITE, SF(13, -0.2))
        _text("↑", w / 2 + 4,  y - 18, C_ORANGE, SF(16, 0.3))
        _text(s.get('ul_str', '0.0 B/s'), w / 2 + 22, y - 18, C_WHITE, SF(13, -0.2))
        y -= 24
        _text(f"Total ↓  {s.get('net_rx','0 B')}", PAD,     y - 11, C_GRAY, SF_SMALL)
        _text_right(f"↑  {s.get('net_tx','0 B')}", w - PAD, y - 11, C_GRAY, SF_SMALL)
        y -= 16

        # ── Disque ───────────────────────────────────────────
        y -= 4;  _sep(PAD, y, bw);  y -= 8
        _text("STOCKAGE  /", PAD, y, C_GRAY, SF_LABEL);  y -= 18
        dp  = s.get('disk_pct', 0)
        vs  = f"{dp:.1f}"
        _text(vs, PAD, y - 26, _bar_color(dp), SF_VALUE)
        aw  = NSAttributedString.alloc().initWithString_attributes_(
            vs, {NSFontAttributeName: SF_VALUE}).size().width
        _text("%", PAD + aw + 2, y - 18, C_GRAY, SF_UNIT)
        _text_right(s.get('disk_info', ''), w - PAD, y - 18, C_GRAY, SF_SMALL)
        y -= 34
        _bar(PAD, y, bw, 7, dp);  y -= 13
        # Vitesses I/O
        _text(f"R  {s.get('disk_r', '0 B/s')}", PAD, y - 11, C_GREEN, SF_SMALL)
        _text_right(f"W  {s.get('disk_w', '0 B/s')}", w - PAD, y - 11, C_ORANGE, SF_SMALL)
        y -= 16

        # ── Batterie + uptime ─────────────────────────────────
        y -= 4;  _sep(PAD, y, bw);  y -= 10
        if s.get('batt_pct') is not None:
            bp   = s['batt_pct']
            icon = "⚡" if s.get('batt_plug') else "🔋"
            _text(f"{icon}  {bp:.0f}%", PAD, y - 11, C_WHITE, SF_INFO)
            _bar(PAD + 62, y - 8, bw - 62 - 80, 5, bp)
            _text_right(s.get('batt_time', ''), w - PAD, y - 11, C_GRAY, SF_SMALL)
            y -= 18
        _text(f"⏱  {s.get('uptime', '—')}", PAD, y - 11, C_GRAY, SF_SMALL)
        y -= 22

        # ── Top 5 Processus ───────────────────────────────────
        y -= 4;  _sep(PAD, y, bw);  y -= 8
        _text("TOP PROCESSUS", PAD, y, C_GRAY, SF_LABEL)
        _text_right("CPU%    MEM%", w - PAD, y, C_GRAY, SF_SMALL)
        for proc in s.get('top_procs', []):
            y -= 15
            cpu_p = proc['cpu']
            mem_p = proc['mem']
            col   = C_RED if cpu_p > 50 else C_ORANGE if cpu_p > 20 else C_WHITE
            _text(proc['name'][:24], PAD, y, C_WHITE, SF_PROC)
            _text_right(f"{cpu_p:5.1f}   {mem_p:5.1f}", w - PAD, y, col, SF_PROC)

        # ── Bouton Quitter ────────────────────────────────────
        y -= 14
        btn_h    = 32
        btn_y    = y - btn_h
        btn_rect = NSMakeRect(PAD, btn_y, bw, btn_h)
        (C_BTN_RED_HV if self._btn_hover else C_BTN_RED).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(btn_rect, 8, 8).fill()
        quit_color = _rgba(1.0, 0.45, 0.45, 1.0)
        lbl = NSAttributedString.alloc().initWithString_attributes_(
            "Quitter MAC Monitor",
            {NSForegroundColorAttributeName: quit_color, NSFontAttributeName: SF_BTN})
        lbl.drawAtPoint_(NSMakePoint(
            (w - lbl.size().width) / 2,
            btn_y + (btn_h - lbl.size().height) / 2))
        self._btn_quit_rect = btn_rect

    # ── Interactions ──────────────────────────────────────────
    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self._btn_quit_rect:
            r = self._btn_quit_rect
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                rumps.quit_application()

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        hv = False
        if self._btn_quit_rect:
            r  = self._btn_quit_rect
            hv = (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                  r.origin.y <= pt.y <= r.origin.y + r.size.height)
        if hv != self._btn_hover:
            self._btn_hover = hv
            self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        if self._btn_hover:
            self._btn_hover = False
            self.setNeedsDisplay_(True)

    def acceptsFirstMouse_(self, _event):
        return True

    def refreshDisplay_(self, _timer):
        """Cible NSTimer en NSRunLoopCommonModes — redessine même menu ouvert."""
        self.setNeedsDisplay_(True)


# ─── Delegate NSObject pour stats live (cible NSTimer valide) ─────────────────
class _StatsDelegate(NSObject):
    _app_ref = None

    def statsRefresh_(self, _timer):
        if self._app_ref:
            self._app_ref._do_stats()


# ─── Dessin du personnage (pixel art) ────────────────────────────────────────
_STATE_COLORS = {
    "chill": (0.18, 0.90, 0.62),
    "busy":  (0.30, 0.62, 1.00),
    "hot":   (1.00, 0.65, 0.12),
    "panic": (1.00, 0.20, 0.20),
}

def _rrect(x, y, w, h, r, color):
    color.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()

def _oval(x, y, w, h, color):
    color.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x, y, w, h)).fill()


def draw_character(t: float, state: str, blink: bool, size: int = 22) -> NSImage:
    rc, gc, bc = _STATE_COLORS[state]
    main  = _rgba(rc,       gc,       bc,       1.0)
    dark  = _rgba(rc * 0.5, gc * 0.5, bc * 0.5, 1.0)
    shine = _rgba(1, 1, 1, 0.45)

    img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill()
    NSRectFill(NSMakeRect(0, 0, size, size))

    bob = (math.sin(t * 6 * math.pi) * 1.8 if state == "panic"
           else math.sin(t * 2 * math.pi) * (1.1 if state == "chill" else 0.4))
    y0    = 1.0 + bob
    swing = math.sin(t * 2 * math.pi) * (2.5 if state == "panic" else 1.5)

    _rrect(7.0 - swing,  y0, 2.5, 3.5, 1.2, dark)
    _rrect(12.5 + swing, y0, 2.5, 3.5, 1.2, dark)

    body_y = y0 + 2.5
    _rrect(5.5, body_y, 11, 6.5, 2.2, main)
    _rrect(6.5, body_y + 4.5, 3.5, 1.2, 0.8, shine)

    arm_s = (math.sin(t * 4 * math.pi) * 1.8 if state == "busy"
             else math.sin(t * 6 * math.pi) * 2.5 if state == "panic"
             else math.sin(t * 2 * math.pi) * 1.2)
    _rrect(2.5,  body_y + 1.0 - arm_s, 2.5, 3.5, 1.2, dark)
    _rrect(17.0, body_y + 1.0 + arm_s, 2.5, 3.5, 1.2, dark)

    head_y = y0 + 8.2
    head_w, head_h, head_x = 11.0, 8.0, (size - 11.0) / 2.0
    hp = NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(head_x, head_y, head_w, head_h))
    main.setFill(); hp.fill()
    dark.setStroke(); hp.setLineWidth_(0.4); hp.stroke()

    ey = head_y + 3.5
    if blink:
        for ex in (8.2, 12.0):
            p = NSBezierPath.bezierPath()
            p.moveToPoint_(NSMakePoint(ex, ey + 1.0))
            p.lineToPoint_(NSMakePoint(ex + 1.8, ey + 1.0))
            NSColor.whiteColor().setStroke(); p.setLineWidth_(0.9); p.stroke()
    else:
        eye_h = 2.6 if state == "panic" else 2.0
        for ex in (8.0, 12.0):
            _oval(ex, ey, 2.0, eye_h, NSColor.whiteColor())
            _oval(ex + 0.45, ey + 0.35, 1.1, 1.2, NSColor.blackColor())
            _oval(ex + 1.0,  ey + 0.9,  0.5, 0.5, NSColor.whiteColor())

    my = head_y + 1.8
    if state == "chill":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.0, my + 0.5))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(13.5, my + 0.5), NSMakePoint(9.5, my - 1.0), NSMakePoint(13.0, my - 1.0))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.1); m.stroke()
    elif state == "busy":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.5, my)); m.lineToPoint_(NSMakePoint(13.0, my))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.0); m.stroke()
    elif state == "hot":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.0, my + 0.5))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(11.2, my - 0.5), NSMakePoint(9.5, my + 0.5), NSMakePoint(10.5, my - 0.5))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(13.5, my + 0.5), NSMakePoint(11.9, my - 0.5), NSMakePoint(13.0, my + 0.5))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.0); m.stroke()
    else:
        _oval(9.8, my - 0.5, 3.0, 2.5, NSColor.blackColor())

    ax, ay = head_x + head_w / 2.0, head_y + head_h - 0.2
    sway   = math.sin(t * 2 * math.pi + 0.7) * 1.6
    tx, ty = ax + sway, ay + 3.5
    al = NSBezierPath.bezierPath()
    al.moveToPoint_(NSMakePoint(ax, ay)); al.lineToPoint_(NSMakePoint(tx, ty))
    dark.setStroke(); al.setLineWidth_(0.9); al.stroke()
    pulse  = 0.8 + math.sin(t * 4 * math.pi) * 0.2
    gc_ball = (_rgba(1.0, 0.3 * pulse, 0.1) if state == "panic" else
               _rgba(1.0, 0.85 * pulse, 0.1) if state == "hot" else
               _rgba(0.3, 0.8 * pulse, 1.0) if state == "busy" else
               _rgba(0.1, pulse, 0.7))
    _oval(tx - 2.0, ty - 2.0, 4.0, 4.0, gc_ball)
    _oval(tx - 0.6, ty + 0.4, 1.0, 1.0, NSColor.whiteColor())

    if state == "hot":
        sd_y = head_y + 7.5 - ((t * 6) % 7)
        sw = NSBezierPath.bezierPath()
        sw.moveToPoint_(NSMakePoint(19.5, sd_y + 3.0))
        sw.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(18.0, sd_y), NSMakePoint(21.0, sd_y + 2.0), NSMakePoint(21.0, sd_y + 0.8))
        sw.lineToPoint_(NSMakePoint(19.5, sd_y + 3.0))
        _rgba(0.5, 0.82, 1.0, 0.85).setFill(); sw.fill()
    elif state == "panic":
        for i in range(3):
            sp = NSBezierPath.bezierPath()
            sp.moveToPoint_(NSMakePoint(0, y0 + 3.5 + i * 2.5))
            sp.lineToPoint_(NSMakePoint(1.5 + i * 0.8, y0 + 3.5 + i * 2.5))
            _rgba(1, 0.3, 0.3, 0.7).setStroke(); sp.setLineWidth_(0.7); sp.stroke()
        flk = math.sin(t * 8 * math.pi) * 0.8
        _oval(ax - 1.5 + flk, ty,       2.5, 2.5, _rgba(1.0, 0.55, 0.0, 0.9))
        _oval(ax - 1.0 + flk, ty + 1.0, 2.0, 2.2, _rgba(1.0, 0.9,  0.1, 0.85))
    elif state == "chill":
        np = (t * 3) % 1.0
        if np < 0.5:
            ny, alpha = head_y + 6 + np * 4, 1.0 - np * 2
            nc = _rgba(rc, gc, bc, alpha)
            _oval(19.5, ny, 1.8, 1.5, nc)
            ln = NSBezierPath.bezierPath()
            ln.moveToPoint_(NSMakePoint(21.3, ny + 1.5))
            ln.lineToPoint_(NSMakePoint(21.3, ny + 4.0))
            nc.setStroke(); ln.setLineWidth_(0.8); ln.stroke()

    img.unlockFocus()
    img.setTemplate_(False)
    return img


# ─── Utilitaires système ──────────────────────────────────────────────────────
def _get_local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return ""


def _ensure_launchagent():
    """Charge le LaunchAgent s'il n'est pas encore actif."""
    if not os.path.exists(PLIST_PATH):
        return
    result = subprocess.run(
        ["launchctl", "list", PLIST_LABEL],
        capture_output=True, text=True
    )
    if result.returncode != 0:
        subprocess.run(["launchctl", "load", "-w", PLIST_PATH],
                       capture_output=True)


def _top_procs(n: int = 5) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            procs.append({
                "name": info["name"] or "?",
                "cpu":  info["cpu_percent"] or 0.0,
                "mem":  info["memory_percent"] or 0.0,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:n]


# ─── Application principale ───────────────────────────────────────────────────
_panel_view = None


class MacMonitorPro(rumps.App):

    def __init__(self):
        super().__init__("", quit_button=None)

        self._t      = 0.0
        self._blink  = False
        self._state  = "chill"
        self._dl     = 0.0
        self._ul     = 0.0
        self._cpu    = 0.0

        # Historiques pour les sparklines
        self._cpu_hist = deque([0.0] * HIST, maxlen=HIST)
        self._ram_hist = deque([0.0] * HIST, maxlen=HIST)

        # Suivi disk I/O
        self._prev_disk = psutil.disk_io_counters()

        # Réseau
        psutil.cpu_percent()
        self._prev_net  = psutil.net_io_counters()
        self._prev_time = time.time()

        # Notifications : dernière heure d'envoi par type
        self._last_notif: dict[str, float] = {}

        # IP locale (rafraîchie toutes les 30 s)
        self._local_ip     = _get_local_ip()
        self._ip_last_upd  = time.time()

        self._setup_done = False

        # Garantir le démarrage automatique
        _ensure_launchagent()

    # ── Initialisation du menu (une seule fois) ────────────────
    @rumps.timer(0.05)
    def _late_init(self, timer):
        if self._setup_done:
            return
        try:
            nsitem = self._nsapp.nsstatusitem
        except AttributeError:
            return

        self._setup_done = True
        timer.stop()

        global _panel_view

        view = PanelView.alloc().initWithFrame_(NSMakeRect(0, 0, PW, PH))
        _panel_view = view

        panel_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        panel_item.setView_(view)

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.addItem_(panel_item)
        nsitem.setMenu_(menu)
        nsitem.button().setImagePosition_(2)   # NSImageLeft

        # ── Timers NSRunLoopCommonModes (fonctionnent menu ouvert) ──
        rl = NSRunLoop.mainRunLoop()

        # Redessin du panneau — cible: PanelView (vrai NSView/NSObject)
        t_draw = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            0.5, view, "refreshDisplay:", None, True)
        rl.addTimer_forMode_(t_draw, "NSRunLoopCommonModes")

        # Stats live — cible: _StatsDelegate (vrai NSObject)
        self._stats_delegate = _StatsDelegate.alloc().init()
        self._stats_delegate._app_ref = self
        t_stats = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.5, self._stats_delegate, "statsRefresh:", None, True)
        rl.addTimer_forMode_(t_stats, "NSRunLoopCommonModes")

        self._live_timers = [t_draw, t_stats]

    # ── Animation 10 fps via rumps (maintient l'icône visible) ─
    @rumps.timer(0.1)
    def _animate(self, _):
        self._t     = (self._t + 0.1) % 1.0
        self._blink = ((self._t % 0.35) < 0.06)

        if not self._setup_done:
            return

        img    = draw_character(self._t, self._state, self._blink)
        nsitem = self._nsapp.nsstatusitem
        btn    = nsitem.button()
        btn.setImage_(img)
        btn.setTitle_(f"  {self._cpu:.0f}%  ↓{_b(self._dl)}/s")

    # ── Stats toutes les 1.5 s ────────────────────────────────
    @rumps.timer(1.5)
    def _update_stats(self, _):
        self._do_stats()

    def _do_stats(self):
        import re as _re

        # CPU
        cpu  = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        nc   = psutil.cpu_count(logical=False) or 0
        nt   = psutil.cpu_count(logical=True)  or 0
        self._cpu = cpu
        self._cpu_hist.append(cpu)

        # RAM
        vm   = psutil.virtual_memory()
        swap = psutil.swap_memory()
        self._ram_hist.append(vm.percent)

        # Disque (espace)
        try:
            _di    = subprocess.run(
                ['diskutil', 'info', '/'], capture_output=True, text=True, timeout=3
            ).stdout
            _tot_g = float(_re.search(r'Container Total Space:\s+([\d.]+) GB', _di).group(1))
            _fre_g = float(_re.search(r'Container Free Space:\s+([\d.]+) GB', _di).group(1))
            _use_g = _tot_g - _fre_g
            disk   = type('D', (), {
                'percent': _use_g / _tot_g * 100,
                'info':    f"{_use_g:.1f} G / {_tot_g:.1f} G",
            })()
        except Exception:
            _d   = psutil.disk_usage("/")
            disk = type('D', (), {
                'percent': _d.percent,
                'info':    f"{_b(_d.total - _d.free)} / {_b(_d.total)}",
            })()

        # Disk I/O speeds
        now_disk = psutil.disk_io_counters()
        now_time = time.time()
        if self._prev_disk and now_disk:
            dt_io = max(now_time - self._prev_time, 0.1)
            disk_r = (now_disk.read_bytes  - self._prev_disk.read_bytes)  / dt_io
            disk_w = (now_disk.write_bytes - self._prev_disk.write_bytes) / dt_io
        else:
            disk_r = disk_w = 0.0
        self._prev_disk = now_disk

        # Réseau
        net = psutil.net_io_counters()
        dt  = max(now_time - self._prev_time, 0.1)
        self._dl = (net.bytes_recv - self._prev_net.bytes_recv) / dt
        self._ul = (net.bytes_sent - self._prev_net.bytes_sent) / dt
        self._prev_net  = net
        self._prev_time = now_time

        # Batterie
        batt = psutil.sensors_battery()
        up   = str(timedelta(seconds=int(time.time() - psutil.boot_time())))

        # État du personnage
        if   cpu >= 80: self._state = "panic"
        elif cpu >= 60: self._state = "hot"
        elif cpu >= 35: self._state = "busy"
        else:           self._state = "chill"

        # IP locale (rafraîchissement toutes les 30 s)
        if time.time() - self._ip_last_upd > 30:
            self._local_ip    = _get_local_ip()
            self._ip_last_upd = time.time()

        freq_s = f"{freq.current:.0f} MHz" if freq else "—"

        _S.update({
            "cpu":       cpu,
            "cpu_info":  f"{nc}C · {nt}T · {freq_s}",
            "cpu_hist":  list(self._cpu_hist),
            "ram":       vm.percent,
            "ram_info":  f"{_b(vm.total - vm.available)} / {_b(vm.total)}",
            "ram_hist":  list(self._ram_hist),
            "swap":      swap.percent,
            "swap_info": f"{_b(swap.used)} / {_b(swap.total)}",
            "dl_str":    f"{_b(self._dl)}/s",
            "ul_str":    f"{_b(self._ul)}/s",
            "net_rx":    _b(net.bytes_recv),
            "net_tx":    _b(net.bytes_sent),
            "disk_pct":  disk.percent,
            "disk_info": disk.info,
            "disk_r":    f"{_b(disk_r)}/s",
            "disk_w":    f"{_b(disk_w)}/s",
            "uptime":    f"il y a {up}",
            "local_ip":  self._local_ip,
            "top_procs": _top_procs(5),
        })

        if batt:
            if batt.secsleft == psutil.POWER_TIME_UNLIMITED: t_s = "∞"
            elif batt.secsleft == psutil.POWER_TIME_UNKNOWN: t_s = "…"
            else: t_s = str(timedelta(seconds=int(batt.secsleft)))
            _S["batt_pct"]  = batt.percent
            _S["batt_plug"] = batt.power_plugged
            _S["batt_time"] = "⚡ branché" if batt.power_plugged else f"{t_s} restant"
        else:
            _S["batt_pct"] = None

        if _panel_view:
            _panel_view.setNeedsDisplay_(True)

        # Notifications système
        self._check_notifications(cpu, vm.percent, batt)

    # ── Notifications ─────────────────────────────────────────
    def _notify(self, key: str, title: str, message: str):
        now = time.time()
        if now - self._last_notif.get(key, 0) > NOTIF_COOLDOWN:
            self._last_notif[key] = now
            rumps.notification(
                title=title,
                subtitle="MAC Monitor Pro",
                message=message,
                sound=False,
            )

    def _check_notifications(self, cpu: float, ram: float, batt):
        if cpu >= 90:
            self._notify(
                "cpu_high",
                "CPU surchargé 🔥",
                f"Utilisation CPU : {cpu:.0f}%",
            )
        if ram >= 90:
            self._notify(
                "ram_high",
                "Mémoire saturée",
                f"RAM utilisée : {ram:.0f}%",
            )
        if batt and not batt.power_plugged and batt.percent <= 10:
            self._notify(
                "batt_low",
                "Batterie faible 🔋",
                f"{batt.percent:.0f}% — branchez votre Mac",
            )


if __name__ == "__main__":
    MacMonitorPro().run()
