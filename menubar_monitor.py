#!/usr/bin/env python3
"""MAC Monitor Pro v5 — Design premium + nouvelles fonctionnalités.

v5 :
  • Cards par section (fond arrondi semi-transparent)
  • Séparateurs fins entre sections
  • Polices monospaced pour les chiffres (SF_VALUE, SF_PROC)
  • Heure dans le header
  • Barre de statut CPU sous le header
  • Pression mémoire macOS (memory_pressure)
  • Signal WiFi + SSID (barres animées)
  • Santé batterie (ioreg)
  • GPU usage (ioreg Apple Silicon)
  • Totaux réseau (téléchargé/envoyé depuis démarrage)
  • Toggle CPU/MEM dans Top Processus (clic sur header)
  • Clic section → ouvre l'app système correspondante
  • Alerte température CPU
"""

import colorsys, math, os, re, subprocess, time, psutil, objc, rumps
from collections import deque
from datetime import timedelta

from Foundation import NSMakeRect, NSMakeSize, NSMakePoint, NSTimer, NSRunLoop, NSObject
from AppKit import (
    NSMenuItem, NSMenu, NSView, NSFont, NSColor, NSBezierPath,
    NSAttributedString, NSForegroundColorAttributeName, NSFontAttributeName,
    NSImage, NSRectFill, NSPasteboard, NSAppearance,
)

# ─── Constantes ───────────────────────────────────────────────────────────────
PW, PH            = 300, 734
PAD               = 18
CORNER            = 12
HDR_H             = 46
HIST              = 60
NOTIF_COOLDOWN    = 300
POMODORO_DUR      = 25 * 60
DL_HIGH_THRESH    = 1_000_000
DL_LOW_THRESH     =   100_000
TEMP_ALERT_THRESH = 90  # °C

PLIST_PATH  = os.path.expanduser("~/Library/LaunchAgents/com.macmonitor.app.plist")
PLIST_LABEL = "com.macmonitor.app"

# ─── Palette ──────────────────────────────────────────────────────────────────
def _rgba(r, g, b, a=1.0):
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, a)

C_BG_TOP     = _rgba(0.09, 0.08, 0.13, 0.99)
C_HDR_BOT    = _rgba(0.12, 0.09, 0.18, 1.00)
C_WHITE      = NSColor.whiteColor()
C_GRAY       = _rgba(1, 1, 1, 0.45)
C_DIM        = _rgba(1, 1, 1, 0.11)
C_GREEN      = _rgba(0.19, 0.82, 0.35)
C_ORANGE     = _rgba(1.00, 0.60, 0.04)
C_RED        = _rgba(1.00, 0.27, 0.23)
C_CPU        = _rgba(0.30, 0.62, 1.00)
C_RAM        = _rgba(0.68, 0.38, 1.00)
C_NET        = _rgba(0.18, 0.82, 0.92)
C_DSK        = _rgba(1.00, 0.58, 0.10)
C_BAT        = _rgba(0.19, 0.82, 0.35)
C_MUS        = _rgba(1.00, 0.42, 0.72)
C_TOP        = _rgba(0.85, 0.85, 0.90)
C_BTN_BG     = _rgba(1, 1, 1, 0.07)
C_BTN_HV     = _rgba(1, 1, 1, 0.14)
C_BTN_GRN    = _rgba(0.19, 0.82, 0.35, 0.18)
C_BTN_GRN_HV = _rgba(0.19, 0.82, 0.35, 0.30)
C_BTN_BLU    = _rgba(0.30, 0.62, 1.00, 0.18)
C_BTN_BLU_HV = _rgba(0.30, 0.62, 1.00, 0.30)

def _accent_bar_color(v, accent):
    if v >= 85: return C_RED
    if v >= 60: return C_ORANGE
    return accent

def _b(n):
    for u in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} P"

def SF(size, weight=0.0):
    return NSFont.systemFontOfSize_weight_(size, weight)

SF_TITLE  = SF(13, 0.50)
SF_LABEL  = SF(10, 0.40)
SF_VALUE  = NSFont.monospacedDigitSystemFontOfSize_weight_(22, -0.40)
SF_UNIT   = SF(12, 0.00)
SF_INFO   = SF(11, 0.00)
SF_SMALL  = SF(10, 0.00)
SF_MONO   = NSFont.monospacedDigitSystemFontOfSize_weight_(10, 0.0)
SF_BTN    = SF(12, 0.30)
SF_BTN_S  = SF(10, 0.25)
SF_PROC   = NSFont.monospacedDigitSystemFontOfSize_weight_(10, -0.20)

_S: dict = {}

# ─── Helpers dessin ───────────────────────────────────────────────────────────
def _text(s, x, y, color, font):
    NSAttributedString.alloc().initWithString_attributes_(
        s, {NSForegroundColorAttributeName: color, NSFontAttributeName: font}
    ).drawAtPoint_(NSMakePoint(x, y))

def _text_right(s, rx, y, color, font):
    a = NSAttributedString.alloc().initWithString_attributes_(
        s, {NSForegroundColorAttributeName: color, NSFontAttributeName: font})
    a.drawAtPoint_(NSMakePoint(rx - a.size().width, y))

def _text_center(s, cx, y, color, font):
    a = NSAttributedString.alloc().initWithString_attributes_(
        s, {NSForegroundColorAttributeName: color, NSFontAttributeName: font})
    a.drawAtPoint_(NSMakePoint(cx - a.size().width / 2, y))

def _text_width(s, font):
    return NSAttributedString.alloc().initWithString_attributes_(
        s, {NSFontAttributeName: font}).size().width

def _bar_glow(x, y, w, h, value, accent):
    r = h / 2.0
    C_DIM.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()
    if value > 0:
        fw    = max(r * 2, w * min(value, 100) / 100.0)
        color = _accent_bar_color(value, accent)
        color.colorWithAlphaComponent_(0.22).setFill()
        rg = (h + 6) / 2.0
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x - 1, y - 3, fw + 2, h + 6), rg, rg).fill()
        color.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x, y, fw, h), r, r).fill()

def _sparkline(x, y, w, h, data, color):
    pts = list(data)
    if len(pts) < 2: return
    n = len(pts)
    _rgba(1, 1, 1, 0.04).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), 2, 2).fill()
    def _px(i, v):
        return x + i * w / (n - 1), y + (v / 100.0) * h
    fill = NSBezierPath.bezierPath()
    fill.moveToPoint_(NSMakePoint(x, y))
    for i, v in enumerate(pts):
        fill.lineToPoint_(NSMakePoint(*_px(i, v)))
    fill.lineToPoint_(NSMakePoint(x + w, y))
    fill.closePath()
    color.colorWithAlphaComponent_(0.20).setFill()
    fill.fill()
    line = NSBezierPath.bezierPath()
    line.setLineWidth_(1.4)
    for i, v in enumerate(pts):
        px, py = _px(i, v)
        if i == 0: line.moveToPoint_(NSMakePoint(px, py))
        else:      line.lineToPoint_(NSMakePoint(px, py))
    color.setStroke()
    line.stroke()

def _section_header(x, y, bw, label, accent, value=None, right_text="", right_color=None):
    accent.colorWithAlphaComponent_(0.10).setFill()
    NSBezierPath.fillRect_(NSMakeRect(x - 6, y - 1, (bw + 12) * 0.55, 12))
    accent.colorWithAlphaComponent_(0.04).setFill()
    NSBezierPath.fillRect_(NSMakeRect(x - 6 + (bw + 12) * 0.55, y - 1, (bw + 12) * 0.45, 12))
    lx = x
    if value is not None:
        dot_color = (C_RED if value >= 85 else C_ORANGE if value >= 60 else accent)
        dcx, dcy  = x + 3, y + 5
        dot_color.colorWithAlphaComponent_(0.28).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(dcx - 4, dcy - 4, 10, 10)).fill()
        dot_color.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(dcx - 2.5, dcy - 2.5, 5, 5)).fill()
        lx = x + 13
    _text(label, lx, y, accent.colorWithAlphaComponent_(0.80), SF_LABEL)
    if right_text:
        _text_right(right_text, x + bw, y, right_color or C_GRAY, SF_SMALL)

def _accent_line(x, y_bot, height, accent):
    accent.colorWithAlphaComponent_(0.65).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y_bot, 2.5, height), 1.25, 1.25).fill()

def _card(x, y_sec, w, content_h, accent):
    """Fond carte arrondi dessiné avant le contenu de la section."""
    accent.colorWithAlphaComponent_(0.07).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y_sec - content_h, w, content_h + 14), 7, 7).fill()

def _sep(y, x1, x2):
    _rgba(1, 1, 1, 0.07).setFill()
    NSBezierPath.fillRect_(NSMakeRect(x1, y, x2 - x1, 0.5))

def _wifi_bars(x, y, rssi):
    if rssi == 0: return
    lit = 4 if rssi >= -50 else 3 if rssi >= -60 else 2 if rssi >= -70 else 1
    for i in range(4):
        bar_h = 3.5 + i * 2.5
        col = C_NET if i < lit else _rgba(1, 1, 1, 0.18)
        col.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + i * 5, y, 3.5, bar_h), 0.5, 0.5).fill()

def _draw_btn(x, y, w, h, label, bg_color, txt_color, radius=None):
    r = radius if radius is not None else h / 2.0
    bg_color.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()
    a = NSAttributedString.alloc().initWithString_attributes_(
        label, {NSForegroundColorAttributeName: txt_color, NSFontAttributeName: SF_BTN_S})
    a.drawAtPoint_(NSMakePoint(
        x + (w - a.size().width) / 2,
        y + (h - a.size().height) / 2))
    return NSMakeRect(x, y, w, h)


# ─── Vue du panneau ───────────────────────────────────────────────────────────
class PanelView(NSView):

    _hover_btn: str = ""
    _btn_rects: dict = None
    _sec_rects: dict = None
    _top_mode:  str = "cpu"

    def initWithFrame_(self, frame):
        self = objc.super(PanelView, self).initWithFrame_(frame)
        if self is None: return None
        self.setWantsLayer_(True)
        self._btn_rects = {}
        self._sec_rects = {}
        return self

    def drawRect_(self, _rect):
        s = _S
        if not s: return

        w  = self.bounds().size.width
        h  = self.bounds().size.height
        bw = w - PAD * 2
        sw = bw - 80
        rects  = {}
        srects = {}

        # ── Fond ──────────────────────────────────────────────
        C_BG_TOP.setFill()
        NSRectFill(NSMakeRect(0, 0, w, h))
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, w, h), CORNER, CORNER).fill()
        _rgba(1, 1, 1, 0.025).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, h * 0.65, w, h * 0.35))

        # ── Header ────────────────────────────────────────────
        party = s.get('party', False)
        if party:
            hue = (time.time() * 0.4) % 1.0
            r1, g1, b1 = colorsys.hsv_to_rgb(hue, 0.70, 0.28)
            hdr_color = _rgba(r1, g1, b1, 1.0)
        else:
            hdr_color = C_HDR_BOT

        hdr_path = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, h - HDR_H, w, HDR_H + CORNER), CORNER, CORNER)
        hdr_color.setFill()
        hdr_path.fill()
        _rgba(1, 1, 1, 0.06).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, h - HDR_H * 0.5, w, HDR_H * 0.5))
        _rgba(1, 1, 1, 0.07).setFill()
        NSBezierPath.fillRect_(NSMakeRect(0, h - HDR_H, w, 0.5))

        # Heure
        _text(time.strftime("%H:%M"), PAD, h - HDR_H + 18, _rgba(1, 1, 1, 0.45), SF_SMALL)

        if party:
            _text_center("✦  MAC Monitor Pro  ✦", w / 2, h - HDR_H + 18,
                         _rgba(1.0, 0.95, 0.4, 1.0), SF_TITLE)
        else:
            _text_center("MAC Monitor", w / 2, h - HDR_H + 18, C_WHITE, SF_TITLE)
            _text_right("Pro", w - PAD, h - HDR_H + 18, _rgba(1,1,1,0.35), SF(11, 0.0))

        # Barre de charge CPU sous le header
        cpu_bar = s.get('cpu', 0)
        _rgba(1, 1, 1, 0.06).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(PAD, h - HDR_H - 5, bw, 3), 1.5, 1.5).fill()
        if cpu_bar > 0:
            bc = C_RED if cpu_bar >= 80 else C_ORANGE if cpu_bar >= 60 else C_GREEN
            bc.colorWithAlphaComponent_(0.75).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(PAD, h - HDR_H - 5, max(3, bw * min(cpu_bar, 100) / 100), 3),
                1.5, 1.5).fill()

        y = h - HDR_H - 8

        # ── CPU ───────────────────────────────────────────────
        cpu   = s.get('cpu', 0)
        gpu   = s.get('gpu', -1)
        y_sec = y
        _card(PAD - 8, y_sec, bw + 16, (82 if gpu >= 0 else 66) + 13, C_CPU)
        # Ligne 1 : label + temp
        _section_header(PAD, y, bw, "PROCESSEUR", C_CPU, cpu)
        if s.get('cpu_temp', '—') != "—":
            _text_right(s['cpu_temp'], w - PAD, y, C_CPU.colorWithAlphaComponent_(0.7), SF_SMALL)
        # Ligne 2 : fréquence/cores + bouton gestionnaire
        y -= 13
        _text(s.get('cpu_info', ''), PAD + 2, y, C_GRAY, SF_SMALL)
        act_bg = C_BTN_BLU_HV if self._hover_btn == "act_cpu" else C_BTN_BLU
        rects["act_cpu"] = _draw_btn(w - PAD - 76, y - 2, 76, 13, "→ Activité", act_bg, C_CPU, radius=6)
        y -= 5
        vs = f"{cpu:.1f}"
        _text(vs, PAD, y - 26, _accent_bar_color(cpu, C_CPU), SF_VALUE)
        aw = _text_width(vs, SF_VALUE)
        _text("%", PAD + aw + 2, y - 18, C_GRAY, SF_UNIT)
        _sparkline(PAD + 80, y - 28, sw, 24, s.get('cpu_hist', []), C_CPU)
        y -= 34
        _bar_glow(PAD, y, bw, 7, cpu, C_CPU);  y -= 14
        if gpu >= 0:
            _text("GPU", PAD, y - 11, C_GRAY, SF_SMALL)
            _bar_glow(PAD + 32, y - 9, bw - 32 - 46, 5, gpu, C_CPU.colorWithAlphaComponent_(0.7))
            _text_right(f"{gpu}%", w - PAD, y - 11, C_CPU.colorWithAlphaComponent_(0.8), SF_MONO)
            y -= 16
        _accent_line(PAD - 10, y, y_sec - y, C_CPU)
        _sep(y - 1, PAD, PAD + bw)

        # ── RAM ───────────────────────────────────────────────
        y -= 18
        ram      = s.get('ram', 0)
        mem_pres = s.get('mem_pressure', '—')
        y_sec    = y
        _card(PAD - 8, y_sec, bw + 16, 66, C_RAM)
        pres_col = (C_RED if mem_pres == "Critique" else
                    C_ORANGE if mem_pres == "Avertissement" else C_GREEN)
        _section_header(PAD, y, bw, "MÉMOIRE", C_RAM, ram, s.get('ram_info', ''))
        if mem_pres != "—":
            _text(f"● {mem_pres}", PAD + 78, y,
                  pres_col.colorWithAlphaComponent_(0.85), SF(9, 0.0))
        y -= 18
        vs = f"{ram:.1f}"
        _text(vs, PAD, y - 26, _accent_bar_color(ram, C_RAM), SF_VALUE)
        aw = _text_width(vs, SF_VALUE)
        _text("%", PAD + aw + 2, y - 18, C_GRAY, SF_UNIT)
        _sparkline(PAD + 80, y - 28, sw, 24, s.get('ram_hist', []), C_RAM)
        y -= 34
        _bar_glow(PAD, y, bw, 7, ram, C_RAM);  y -= 13
        _accent_line(PAD - 10, y, y_sec - y, C_RAM)
        _sep(y - 1, PAD, PAD + bw)

        # ── Réseau ────────────────────────────────────────────
        y -= 18
        y_sec     = y
        ip        = s.get('local_ip', '')
        gw        = s.get('gateway', '')
        ping      = s.get('ping', '—')
        wifi_rssi = s.get('wifi_rssi', 0)
        wifi_ssid = s.get('wifi_ssid', '')
        net_hdr   = "  ".join(filter(None, [
            ip,
            f"GW {gw}" if gw else "",
            f"🏓 {ping}" if ping and ping != "—" else "",
        ]))
        _card(PAD - 8, y_sec, bw + 16, 78, C_NET)
        _section_header(PAD, y, bw, "RÉSEAU", C_NET,
                        right_text=net_hdr,
                        right_color=C_NET.colorWithAlphaComponent_(0.75))
        y -= 18
        _text("↓", PAD,       y - 18, C_NET,    SF(16, 0.3))
        _text(s.get('dl_str', '0 B/s'), PAD + 18, y - 18, C_WHITE, SF(13, -0.2))
        _text("↑", w / 2 + 4, y - 18, C_ORANGE, SF(16, 0.3))
        _text(s.get('ul_str', '0 B/s'), w / 2 + 22, y - 18, C_WHITE, SF(13, -0.2))
        y -= 24
        hw = int((bw - 6) / 2)
        _sparkline(PAD,          y - 14, hw, 14, s.get('dl_hist', []), C_NET)
        _sparkline(PAD + hw + 6, y - 14, hw, 14, s.get('ul_hist', []), C_ORANGE)
        y -= 18
        # WiFi bars + totaux
        _wifi_bars(PAD, y - 11, wifi_rssi)
        ssid_disp = wifi_ssid[:14] if wifi_ssid else ""
        if ssid_disp:
            _text(ssid_disp, PAD + 24, y - 11, C_GRAY, SF_SMALL)
        _text_right(f"↓{s.get('net_total_dl','—')}  ↑{s.get('net_total_ul','—')}",
                    w - PAD, y - 11, _rgba(1, 1, 1, 0.30), SF_SMALL)
        y -= 16
        _accent_line(PAD - 10, y, y_sec - y, C_NET)
        _sep(y - 1, PAD, PAD + bw)

        # ── Disque ────────────────────────────────────────────
        y -= 18
        dp    = s.get('disk_pct', 0)
        y_sec = y
        _card(PAD - 8, y_sec, bw + 16, 82, C_DSK)
        _section_header(PAD, y, bw, "STOCKAGE  /", C_DSK, dp)
        y -= 18
        vs = f"{dp:.1f}"
        _text(vs, PAD, y - 26, _accent_bar_color(dp, C_DSK), SF_VALUE)
        aw = _text_width(vs, SF_VALUE)
        _text("%", PAD + aw + 2, y - 18, C_GRAY, SF_UNIT)
        _text_right(s.get('disk_info', ''), w - PAD, y - 18, C_GRAY, SF_SMALL)
        y -= 34
        _bar_glow(PAD, y, bw, 7, dp, C_DSK);  y -= 13
        _text(f"R  {s.get('disk_r','0 B/s')}", PAD, y - 11, C_GREEN, SF_SMALL)
        _text_right(f"W  {s.get('disk_w','0 B/s')}", w - PAD, y - 11, C_ORANGE, SF_SMALL)
        y -= 16
        _accent_line(PAD - 10, y, y_sec - y, C_DSK)
        _sep(y - 1, PAD, PAD + bw)

        # ── Système ───────────────────────────────────────────
        y -= 18
        batt_pct    = s.get('batt_pct')
        batt_health = s.get('batt_health', -1)
        y_sec       = y
        _card(PAD - 8, y_sec, bw + 16, 50 if batt_pct is not None else 34, C_BAT)
        _section_header(PAD, y, bw, "SYSTÈME", C_BAT, None)
        y -= 16
        if batt_pct is not None:
            bp   = batt_pct
            icon = "⚡" if s.get('batt_plug') else "🔋"
            _text(f"{icon}  {bp:.0f}%", PAD, y - 11, C_WHITE, SF_INFO)
            _bar_glow(PAD + 62, y - 8, bw - 62 - 80, 5, bp, C_BAT)
            _text_right(s.get('batt_time', ''), w - PAD, y - 11, C_GRAY, SF_SMALL)
            y -= 18
            _text(f"⏱  {s.get('uptime', '—')}", PAD, y - 11, C_GRAY, SF_SMALL)
            if batt_health > 0:
                hcol = C_GREEN if batt_health >= 80 else C_ORANGE if batt_health >= 60 else C_RED
                _text_right(f"Santé {batt_health}%", w - PAD, y - 11, hcol, SF_SMALL)
            y -= 16
        else:
            _text(f"⏱  {s.get('uptime', '—')}", PAD, y - 11, C_GRAY, SF_SMALL)
            y -= 18
        _accent_line(PAD - 10, y, y_sec - y, C_BAT)
        _sep(y - 1, PAD, PAD + bw)

        # ── Musique ───────────────────────────────────────────
        y -= 18
        music = s.get('music', '')
        y_sec = y
        _card(PAD - 8, y_sec, bw + 16, 44, C_MUS)
        _section_header(PAD, y, bw, "MUSIQUE", C_MUS, None,
                        right_text="♪" if music else "",
                        right_color=C_MUS)
        y -= 16
        if music:
            display = music if len(music) <= 34 else music[:33] + "…"
            _text(display, PAD, y - 2, C_WHITE, SF_SMALL)
        else:
            _text("—", PAD, y - 2, _rgba(1,1,1,0.25), SF_SMALL)
        y -= 16
        _accent_line(PAD - 10, y, y_sec - y, C_MUS)
        _sep(y - 1, PAD, PAD + bw)

        # ── Top Processus ─────────────────────────────────────
        y -= 18
        y_sec    = y
        mode_col = C_RAM if self._top_mode == "mem" else C_CPU
        n_procs  = len(s.get('top_procs', []))
        _card(PAD - 8, y_sec, bw + 16, n_procs * 16 + 8, C_TOP)
        # En-têtes colonnes MEM / CPU
        _section_header(PAD, y, bw, "TOP PROCESSUS", C_TOP, None)
        _text_right("CPU%", w - PAD, y, C_CPU.colorWithAlphaComponent_(0.70), SF(9, 0.3))
        _text_right("MEM%", w - PAD - 36, y, C_RAM.colorWithAlphaComponent_(0.70), SF(9, 0.3))
        # Badge mode actif
        badge = "● MEM" if self._top_mode == "mem" else "● CPU"
        _text(badge, PAD + _text_width("TOP PROCESSUS", SF_LABEL) + 10, y,
              mode_col.colorWithAlphaComponent_(0.65), SF(9, 0.0))
        srects["top"] = NSMakeRect(PAD - 8, y - 2, bw + 16, 14)
        procs = sorted(s.get('top_procs', []),
                       key=lambda p: p['mem'] if self._top_mode == 'mem' else p['cpu'],
                       reverse=True)
        for proc in procs:
            y -= 16
            cp = proc['cpu']; mp = proc['mem']
            col_cpu = C_RED if cp > 50 else C_ORANGE if cp > 20 else C_WHITE
            col_mem = C_RED if mp > 10 else C_ORANGE if mp > 5 else C_RAM.colorWithAlphaComponent_(0.75)
            _text(proc['name'][:20], PAD, y, C_WHITE, SF_PROC)
            _text_right(f"{cp:4.1f}", w - PAD,      y, col_cpu, SF_PROC)
            _text_right(f"{mp:4.1f}", w - PAD - 36, y, col_mem, SF_PROC)
        _accent_line(PAD - 10, y - 4, y_sec - (y - 4), C_TOP)

        # ── Boutons d'action ──────────────────────────────────
        y -= 18
        abh   = 30
        abw   = int((bw - 8) / 3)
        abt_y = y - abh

        caff_active = (_app._caff_proc is not None
                       and _app._caff_proc.poll() is None) if _app else False
        caff_lbl = "💤 Veille OFF" if caff_active else "💤 Veille"
        caff_bg  = (C_BTN_GRN_HV if self._hover_btn == "caff" else
                    C_BTN_GRN    if caff_active else C_BTN_BG)
        caff_col = C_GREEN if caff_active else C_GRAY
        rects["caff"] = _draw_btn(PAD, abt_y, abw, abh, caff_lbl, caff_bg, caff_col)

        pomo_active = (_app._pomo_end > 0) if _app else False
        if pomo_active and _app:
            left     = max(0.0, _app._pomo_end - time.time())
            pomo_lbl = f"⏰ {int(left)//60:02d}:{int(left)%60:02d}"
        else:
            pomo_lbl = "⏰ Pomo"
        pomo_bg  = (C_BTN_BLU_HV if self._hover_btn == "pomo" else
                    C_BTN_BLU    if pomo_active else C_BTN_BG)
        pomo_col = C_CPU if pomo_active else C_GRAY
        rects["pomo"] = _draw_btn(PAD + abw + 4, abt_y, abw, abh,
                                  pomo_lbl, pomo_bg, pomo_col)

        copy_bg = C_BTN_HV if self._hover_btn == "copy" else C_BTN_BG
        rects["copy"] = _draw_btn(PAD + (abw + 4) * 2, abt_y, abw, abh,
                                  "📋 Copier", copy_bg, C_GRAY)
        y = abt_y - 10

        # ── Bouton Quitter ────────────────────────────────────
        qbh   = 34
        qbt_y = y - qbh
        qpath = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(PAD, qbt_y, bw, qbh), qbh / 2, qbh / 2)
        (_rgba(1.0, 0.27, 0.23, 0.30) if self._hover_btn == "quit"
         else _rgba(1.0, 0.27, 0.23, 0.15)).setFill()
        qpath.fill()
        _rgba(1.0, 0.35, 0.30, 0.20).setStroke()
        qpath.setLineWidth_(0.5); qpath.stroke()
        ql = NSAttributedString.alloc().initWithString_attributes_(
            "Quitter MAC Monitor",
            {NSForegroundColorAttributeName: _rgba(1.0, 0.50, 0.48, 1.0),
             NSFontAttributeName: SF_BTN})
        ql.drawAtPoint_(NSMakePoint(
            (w - ql.size().width) / 2,
            qbt_y + (qbh - ql.size().height) / 2))
        rects["quit"] = NSMakeRect(PAD, qbt_y, bw, qbh)

        self._btn_rects = rects
        self._sec_rects = srects

    # ── Interactions ──────────────────────────────────────────
    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        h  = self.bounds().size.height
        if pt.y >= h - HDR_H:
            if _app: _app._party_end = time.time() + 5.0
            return

        # Toggle top procs mode
        r = (self._sec_rects or {}).get("top")
        if r and (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                  r.origin.y <= pt.y <= r.origin.y + r.size.height):
            self._top_mode = "mem" if self._top_mode == "cpu" else "cpu"
            self.setNeedsDisplay_(True)
            return

        # Boutons
        for name, r in (self._btn_rects or {}).items():
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                if name == "quit":
                    rumps.quit_application()
                elif _app:
                    if   name == "caff":    _app.toggle_caff()
                    elif name == "pomo":    _app.toggle_pomo()
                    elif name == "copy":    _app.copy_stats()
                    elif name == "act_cpu": subprocess.Popen(["open", "-a", "Activity Monitor"])
                self.setNeedsDisplay_(True)
                break

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        hv = ""
        for name, r in (self._btn_rects or {}).items():
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                hv = name; break
        if hv != self._hover_btn:
            self._hover_btn = hv
            self.setNeedsDisplay_(True)

    def mouseExited_(self, _event):
        if self._hover_btn:
            self._hover_btn = ""
            self.setNeedsDisplay_(True)

    def acceptsFirstMouse_(self, _event):
        return True

    def refreshDisplay_(self, _timer):
        self.setNeedsDisplay_(True)


# ─── Delegate NSObject ────────────────────────────────────────────────────────
class _StatsDelegate(NSObject):
    _app_ref = None
    def statsRefresh_(self, _timer):
        if self._app_ref: self._app_ref._do_stats()


# ─── Dessin du personnage ─────────────────────────────────────────────────────
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


def draw_character(t: float, state: str, blink: bool,
                   size: int = 22, party: bool = False) -> NSImage:
    if party:
        rc, gc, bc = colorsys.hsv_to_rgb((t * 2) % 1.0, 1.0, 1.0)
        anim = "chill"
    else:
        rc, gc, bc = _STATE_COLORS[state]
        anim = state

    main  = _rgba(rc,       gc,       bc,       1.0)
    dark  = _rgba(rc * 0.5, gc * 0.5, bc * 0.5, 1.0)
    shine = _rgba(1, 1, 1, 0.45)

    img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill()
    NSRectFill(NSMakeRect(0, 0, size, size))

    bob   = (math.sin(t * 6 * math.pi) * 1.8 if anim == "panic"
             else math.sin(t * 2 * math.pi) * (1.1 if anim == "chill" else 0.4))
    y0    = 1.0 + bob
    swing = math.sin(t * 2 * math.pi) * (2.5 if anim == "panic" else 1.5)

    _rrect(7.0 - swing,  y0, 2.5, 3.5, 1.2, dark)
    _rrect(12.5 + swing, y0, 2.5, 3.5, 1.2, dark)

    body_y = y0 + 2.5
    _rrect(5.5, body_y, 11, 6.5, 2.2, main)
    _rrect(6.5, body_y + 4.5, 3.5, 1.2, 0.8, shine)

    arm_s = (math.sin(t * 4 * math.pi) * 1.8 if anim == "busy"
             else math.sin(t * 6 * math.pi) * 2.5 if anim == "panic"
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
        eye_h = 2.6 if anim == "panic" else 2.0
        for ex in (8.0, 12.0):
            _oval(ex, ey, 2.0, eye_h, NSColor.whiteColor())
            _oval(ex + 0.45, ey + 0.35, 1.1, 1.2, NSColor.blackColor())
            _oval(ex + 1.0,  ey + 0.9,  0.5, 0.5, NSColor.whiteColor())

    my = head_y + 1.8
    if anim == "chill":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.0, my + 0.5))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(13.5, my + 0.5), NSMakePoint(9.5, my - 1.0), NSMakePoint(13.0, my - 1.0))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.1); m.stroke()
    elif anim == "busy":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.5, my)); m.lineToPoint_(NSMakePoint(13.0, my))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.0); m.stroke()
    elif anim == "hot":
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
    pulse = 0.8 + math.sin(t * 4 * math.pi) * 0.2
    gc_ball = (_rgba(1.0, 0.3 * pulse, 0.1) if anim == "panic" else
               _rgba(1.0, 0.85 * pulse, 0.1) if anim == "hot" else
               _rgba(0.3, 0.8 * pulse, 1.0) if anim == "busy" else
               _rgba(0.1, pulse, 0.7))
    _oval(tx - 2.0, ty - 2.0, 4.0, 4.0, gc_ball)
    _oval(tx - 0.6, ty + 0.4, 1.0, 1.0, NSColor.whiteColor())

    if anim == "hot":
        sd_y = head_y + 7.5 - ((t * 6) % 7)
        sw = NSBezierPath.bezierPath()
        sw.moveToPoint_(NSMakePoint(19.5, sd_y + 3.0))
        sw.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(18.0, sd_y), NSMakePoint(21.0, sd_y + 2.0), NSMakePoint(21.0, sd_y + 0.8))
        sw.lineToPoint_(NSMakePoint(19.5, sd_y + 3.0))
        _rgba(0.5, 0.82, 1.0, 0.85).setFill(); sw.fill()
    elif anim == "panic":
        for i in range(3):
            sp = NSBezierPath.bezierPath()
            sp.moveToPoint_(NSMakePoint(0, y0 + 3.5 + i * 2.5))
            sp.lineToPoint_(NSMakePoint(1.5 + i * 0.8, y0 + 3.5 + i * 2.5))
            _rgba(1, 0.3, 0.3, 0.7).setStroke(); sp.setLineWidth_(0.7); sp.stroke()
        flk = math.sin(t * 8 * math.pi) * 0.8
        _oval(ax - 1.5 + flk, ty,       2.5, 2.5, _rgba(1.0, 0.55, 0.0, 0.9))
        _oval(ax - 1.0 + flk, ty + 1.0, 2.0, 2.2, _rgba(1.0, 0.9,  0.1, 0.85))
    elif anim == "chill":
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


# ─── Helpers système ──────────────────────────────────────────────────────────
def _get_network_info() -> tuple[str, str]:
    try:
        table = subprocess.run(
            ["netstat", "-rn", "-f", "inet"],
            capture_output=True, text=True, timeout=2
        ).stdout
        gateway = ""; iface = ""
        for line in table.splitlines():
            parts = line.split()
            if len(parts) >= 4 and parts[0] == "default" and parts[-1].startswith("en"):
                if not parts[1].startswith("link#"):
                    gateway = parts[1]; iface = parts[-1]; break
        if not iface:
            for candidate in ("en0", "en1", "en2", "en3"):
                ip = subprocess.run(["ipconfig", "getifaddr", candidate],
                    capture_output=True, text=True, timeout=1).stdout.strip()
                if ip: return ip, gateway
        ip = subprocess.run(["ipconfig", "getifaddr", iface],
            capture_output=True, text=True, timeout=1).stdout.strip()
        return ip, gateway
    except Exception:
        return "", ""


def _get_cpu_temp() -> str:
    try:
        r = subprocess.run(["osx-cpu-temp"], capture_output=True, text=True, timeout=1)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except (FileNotFoundError, Exception):
        pass
    return "—"


def _get_music() -> str:
    script = '''
tell application "System Events"
    set procs to name of every process
    if procs contains "Music" then
        tell application "Music"
            if player state is playing then
                return (name of current track) & " — " & (artist of current track)
            end if
        end tell
    end if
    if procs contains "Spotify" then
        tell application "Spotify"
            if player state is playing then
                return (name of current track) & " — " & (artist of current track)
            end if
        end tell
    end if
end tell
return ""
'''
    try:
        r = subprocess.run(["osascript", "-e", script],
                           capture_output=True, text=True, timeout=2)
        return r.stdout.strip()
    except Exception:
        return ""


def _get_ping() -> str:
    try:
        r = subprocess.run(["ping", "-c", "1", "-t", "1", "8.8.8.8"],
                           capture_output=True, text=True, timeout=2)
        m = re.search(r"time=(\d+\.?\d*)\s*ms", r.stdout)
        if m: return f"{float(m.group(1)):.0f} ms"
    except Exception:
        pass
    return "—"


def _get_mem_pressure() -> str:
    try:
        r = subprocess.run(["memory_pressure"], capture_output=True, text=True, timeout=3)
        for line in r.stdout.splitlines():
            if "System-wide memory free percentage" in line:
                m = re.search(r"(\d+)%", line)
                if m:
                    pct = int(m.group(1))
                    if pct < 15: return "Critique"
                    if pct < 30: return "Avertissement"
                    return "Normal"
    except Exception:
        pass
    return "—"


def _get_wifi_info() -> tuple[str, int]:
    """Retourne (ssid, rssi_dBm). rssi=0 si indisponible."""
    airport = ("/System/Library/PrivateFrameworks/Apple80211.framework"
               "/Versions/Current/Resources/airport")
    try:
        r = subprocess.run([airport, "-I"], capture_output=True, text=True, timeout=2)
        ssid = ""; rssi = 0
        for line in r.stdout.splitlines():
            line = line.strip()
            if line.startswith("SSID:") and not line.startswith("BSSID"):
                ssid = line.split(":", 1)[1].strip()
            elif line.startswith("agrCtlRSSI:"):
                try: rssi = int(line.split(":")[1].strip())
                except ValueError: pass
        return ssid, rssi
    except Exception:
        return "", 0


def _get_batt_health() -> int:
    """Santé batterie en % (0-100) ou -1 si indisponible."""
    try:
        r = subprocess.run(
            ["ioreg", "-l", "-n", "AppleSmartBattery"],
            capture_output=True, text=True, timeout=3
        )
        max_c = re.search(r'"MaxCapacity"\s*=\s*(\d+)', r.stdout)
        des_c = re.search(r'"DesignCapacity"\s*=\s*(\d+)', r.stdout)
        if max_c and des_c:
            return min(100, int(int(max_c.group(1)) / int(des_c.group(1)) * 100))
    except Exception:
        pass
    return -1


def _get_gpu_usage() -> int:
    """GPU % via ioreg (Apple Silicon). Retourne -1 si indisponible."""
    try:
        r = subprocess.run(
            ["ioreg", "-r", "-c", "IOAccelerator", "-d", "2"],
            capture_output=True, text=True, timeout=2
        )
        m = re.search(r'"GPU Activity"\s*=\s*(\d+)', r.stdout)
        if m: return int(m.group(1))
    except Exception:
        pass
    return -1


def _ensure_launchagent():
    if not os.path.exists(PLIST_PATH): return
    result = subprocess.run(["launchctl", "list", PLIST_LABEL],
                            capture_output=True, text=True)
    if result.returncode != 0:
        subprocess.run(["launchctl", "load", "-w", PLIST_PATH], capture_output=True)


def _top_procs(n: int = 3) -> list[dict]:
    procs = []
    for p in psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
        try:
            info = p.info
            procs.append({"name": info["name"] or "?",
                           "cpu":  info["cpu_percent"] or 0.0,
                           "mem":  info["memory_percent"] or 0.0})
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:n]


# ─── Application principale ───────────────────────────────────────────────────
_app        = None
_panel_view = None


class MacMonitorPro(rumps.App):

    def __init__(self):
        global _app
        super().__init__("", quit_button=None)
        _app = self

        self._t     = 0.0
        self._blink = False
        self._state = "chill"
        self._dl    = 0.0
        self._ul    = 0.0
        self._cpu   = 0.0

        self._cpu_hist = deque([0.0] * HIST, maxlen=HIST)
        self._ram_hist = deque([0.0] * HIST, maxlen=HIST)
        self._dl_hist  = deque([0.0] * HIST, maxlen=HIST)
        self._ul_hist  = deque([0.0] * HIST, maxlen=HIST)

        self._prev_disk = psutil.disk_io_counters()
        psutil.cpu_percent()
        self._prev_net  = psutil.net_io_counters()
        self._prev_time = time.time()

        self._last_notif: dict[str, float] = {}

        self._nc        = psutil.cpu_count(logical=False) or 0
        self._nt        = psutil.cpu_count(logical=True)  or 0
        self._boot_time = psutil.boot_time()
        self._local_ip, self._gateway = _get_network_info()
        self._net_last_upd   = time.time()
        self._top_procs_cache: list[dict] = []
        self._top_procs_last = 0.0
        self._disk_pct: float = 0.0
        self._disk_info: str  = ""
        self._disk_last: float = 0.0

        self._caff_proc     = None
        self._pomo_end      = 0.0
        self._party_end     = 0.0
        self._dl_high_since = 0.0

        self._temp_cache  = "—"; self._temp_last  = 0.0
        self._music_cache = "";  self._music_last  = 0.0
        self._ping_cache  = "—"; self._ping_last   = 0.0

        # Nouveaux caches v5
        self._mem_pres_cache = "—"; self._mem_pres_last  = 0.0
        self._wifi_cache     = ("", 0); self._wifi_last  = 0.0
        self._batt_health    = -1;   self._batt_health_last = 0.0
        self._gpu_cache      = -1;   self._gpu_last      = 0.0

        self._setup_done = False
        _ensure_launchagent()

    @rumps.timer(0.05)
    def _late_init(self, timer):
        if self._setup_done: return
        try:
            nsitem = self._nsapp.nsstatusitem
        except AttributeError:
            return

        self._setup_done = True
        timer.stop()

        global _panel_view
        view        = PanelView.alloc().initWithFrame_(NSMakeRect(0, 0, PW, PH))
        _panel_view = view

        panel_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        panel_item.setView_(view)

        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.addItem_(panel_item)
        menu.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        nsitem.setMenu_(menu)
        nsitem.button().setImagePosition_(2)

        rl = NSRunLoop.mainRunLoop()
        t_draw = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, view, "refreshDisplay:", None, True)
        rl.addTimer_forMode_(t_draw, "NSRunLoopCommonModes")

        self._stats_delegate = _StatsDelegate.alloc().init()
        self._stats_delegate._app_ref = self
        t_stats = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self._stats_delegate, "statsRefresh:", None, True)
        rl.addTimer_forMode_(t_stats, "NSRunLoopCommonModes")

        self._live_timers = [t_draw, t_stats]

    @rumps.timer(0.2)
    def _animate(self, _):
        self._t     = (self._t + 0.1) % 1.0
        self._blink = ((self._t % 0.35) < 0.06)

        if not self._setup_done: return

        now   = time.time()
        party = now < self._party_end
        img   = draw_character(self._t, self._state, self._blink, party=party)

        nsitem = self._nsapp.nsstatusitem
        btn    = nsitem.button()
        btn.setImage_(img)

        if self._pomo_end > 0:
            left  = max(0.0, self._pomo_end - now)
            title = f"  ⏰ {int(left)//60:02d}:{int(left)%60:02d}"
            if now >= self._pomo_end:
                self._pomo_end = 0.0
                self._notify("pomo_done", "Pomodoro terminé! 🍅",
                             "25 minutes — prenez une pause ☕")
        else:
            title = f"  {self._cpu:.0f}%  ↓{_b(self._dl)}/s"
        btn.setTitle_(title)

    @rumps.timer(2.0)
    def _update_stats(self, _):
        self._do_stats()

    def _do_stats(self):
        now = time.time()

        cpu  = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        self._cpu = cpu
        self._cpu_hist.append(cpu)

        vm   = psutil.virtual_memory()
        self._ram_hist.append(vm.percent)

        if now - self._disk_last > 30:
            try:
                out = subprocess.run(
                    ["diskutil", "info", "/"], capture_output=True, text=True, timeout=4
                ).stdout
                tot  = float(re.search(r"Container Total Space:\s+([\d.]+) GB", out).group(1))
                fre  = float(re.search(r"Container Free Space:\s+([\d.]+) GB",  out).group(1))
                used = tot - fre
                self._disk_pct  = used / tot * 100
                self._disk_info = f"{used:.1f} G / {tot:.1f} G"
            except Exception:
                _d = psutil.disk_usage("/")
                self._disk_pct  = _d.percent
                self._disk_info = f"{_b(_d.used)} / {_b(_d.total)}"
            self._disk_last = now

        now_disk = psutil.disk_io_counters()
        if self._prev_disk and now_disk:
            dt_io  = max(now - self._prev_time, 0.1)
            disk_r = (now_disk.read_bytes  - self._prev_disk.read_bytes)  / dt_io
            disk_w = (now_disk.write_bytes - self._prev_disk.write_bytes) / dt_io
        else:
            disk_r = disk_w = 0.0
        self._prev_disk = now_disk

        net = psutil.net_io_counters()
        dt  = max(now - self._prev_time, 0.1)
        self._dl = (net.bytes_recv - self._prev_net.bytes_recv) / dt
        self._ul = (net.bytes_sent - self._prev_net.bytes_sent) / dt
        self._prev_net  = net
        self._prev_time = now

        self._dl_hist.append(min(self._dl / 10_000_000 * 100, 100))
        self._ul_hist.append(min(self._ul /  2_000_000 * 100, 100))

        if self._dl > DL_HIGH_THRESH:
            if self._dl_high_since == 0:
                self._dl_high_since = now
        elif self._dl < DL_LOW_THRESH and self._dl_high_since > 0:
            if now - self._dl_high_since > 3:
                self._notify("dl_done", "Téléchargement terminé ✓",
                             "La vitesse de téléchargement est retombée")
            self._dl_high_since = 0

        batt = psutil.sensors_battery()
        up   = str(timedelta(seconds=int(now - self._boot_time)))

        if   cpu >= 80: self._state = "panic"
        elif cpu >= 60: self._state = "hot"
        elif cpu >= 35: self._state = "busy"
        else:           self._state = "chill"

        if now - self._net_last_upd > 30:
            self._local_ip, self._gateway = _get_network_info()
            self._net_last_upd = now

        if now - self._top_procs_last > 8:
            self._top_procs_cache = _top_procs(3)
            self._top_procs_last  = now

        if now - self._temp_last > 30:
            self._temp_cache = _get_cpu_temp()
            self._temp_last  = now

        if now - self._music_last > 8:
            self._music_cache = _get_music()
            self._music_last  = now

        if now - self._ping_last > 20:
            self._ping_cache = _get_ping()
            self._ping_last  = now

        if now - self._mem_pres_last > 15:
            self._mem_pres_cache = _get_mem_pressure()
            self._mem_pres_last  = now

        if now - self._wifi_last > 15:
            self._wifi_cache = _get_wifi_info()
            self._wifi_last  = now

        if now - self._batt_health_last > 300:
            self._batt_health = _get_batt_health()
            self._batt_health_last = now

        if now - self._gpu_last > 3:
            self._gpu_cache = _get_gpu_usage()
            self._gpu_last  = now

        freq_s = f"{freq.current:.0f} MHz" if freq else "—"

        _S.update({
            "cpu":          cpu,
            "cpu_info":     f"{self._nc}C · {self._nt}T · {freq_s}",
            "cpu_temp":     self._temp_cache,
            "cpu_hist":     list(self._cpu_hist),
            "gpu":          self._gpu_cache,
            "ram":          vm.percent,
            "ram_info":     f"{_b(vm.total - vm.available)} / {_b(vm.total)}",
            "ram_hist":     list(self._ram_hist),
            "mem_pressure": self._mem_pres_cache,
            "dl_str":       f"{_b(self._dl)}/s",
            "ul_str":       f"{_b(self._ul)}/s",
            "dl_hist":      list(self._dl_hist),
            "ul_hist":      list(self._ul_hist),
            "net_total_dl": _b(net.bytes_recv),
            "net_total_ul": _b(net.bytes_sent),
            "wifi_ssid":    self._wifi_cache[0],
            "wifi_rssi":    self._wifi_cache[1],
            "disk_pct":     self._disk_pct,
            "disk_info":    self._disk_info,
            "disk_r":       f"{_b(disk_r)}/s",
            "disk_w":       f"{_b(disk_w)}/s",
            "uptime":       f"il y a {up}",
            "local_ip":     self._local_ip,
            "gateway":      self._gateway,
            "ping":         self._ping_cache,
            "music":        self._music_cache,
            "top_procs":    self._top_procs_cache,
            "batt_health":  self._batt_health,
            "party":        time.time() < self._party_end,
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

        self._check_notifications(cpu, vm.percent, batt)

    def toggle_caff(self):
        if self._caff_proc and self._caff_proc.poll() is None:
            self._caff_proc.terminate()
            self._caff_proc = None
            rumps.notification("Anti-veille désactivé", "MAC Monitor", "", sound=False)
        else:
            self._caff_proc = subprocess.Popen(["caffeinate", "-di"])
            rumps.notification("Anti-veille activé 💤", "MAC Monitor",
                               "Le Mac ne se mettra pas en veille", sound=False)

    def toggle_pomo(self):
        if self._pomo_end > 0:
            self._pomo_end = 0.0
            rumps.notification("Pomodoro annulé", "MAC Monitor", "", sound=False)
        else:
            self._pomo_end = time.time() + POMODORO_DUR
            rumps.notification("Pomodoro démarré ⏰", "MAC Monitor",
                               "25 minutes — bonne concentration!", sound=False)

    def copy_stats(self):
        s = _S
        lines = [f"MAC Monitor — {time.strftime('%H:%M:%S')}",
                 f"CPU : {s.get('cpu',0):.1f}%  {s.get('cpu_info','')}"]
        if s.get('cpu_temp') and s['cpu_temp'] != "—":
            lines[-1] += f"  {s['cpu_temp']}"
        if s.get('gpu', -1) >= 0:
            lines[-1] += f"  GPU {s['gpu']}%"
        lines += [
            f"RAM : {s.get('ram',0):.1f}%  {s.get('ram_info','')}  pression: {s.get('mem_pressure','—')}",
            f"Réseau : ↓ {s.get('dl_str','—')}  ↑ {s.get('ul_str','—')}  ping {s.get('ping','—')}",
            f"  Total : ↓{s.get('net_total_dl','—')}  ↑{s.get('net_total_ul','—')}",
            f"Stockage : {s.get('disk_pct',0):.1f}%  {s.get('disk_info','')}",
            f"IP : {s.get('local_ip','—')}  GW : {s.get('gateway','—')}",
        ]
        if s.get('wifi_ssid'):
            lines.append(f"WiFi : {s['wifi_ssid']}  RSSI {s.get('wifi_rssi',0)} dBm")
        if s.get('batt_pct') is not None:
            h_str = f"  santé {s['batt_health']}%" if s.get('batt_health', -1) > 0 else ""
            lines.append(f"Batterie : {s['batt_pct']:.0f}%{h_str}  {s.get('batt_time','')}")
        if s.get('music'):
            lines.append(f"Musique : {s['music']}")
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_("\n".join(lines), "public.utf8-plain-text")
        rumps.notification("Stats copiées 📋", "MAC Monitor", "", sound=False)

    def _notify(self, key: str, title: str, message: str):
        now = time.time()
        if now - self._last_notif.get(key, 0) > NOTIF_COOLDOWN:
            self._last_notif[key] = now
            rumps.notification(title=title, subtitle="MAC Monitor Pro",
                               message=message, sound=False)

    def _check_notifications(self, cpu: float, ram: float, batt):
        if cpu >= 90:
            self._notify("cpu_high", "CPU surchargé 🔥", f"Utilisation CPU : {cpu:.0f}%")
        if ram >= 90:
            self._notify("ram_high", "Mémoire saturée", f"RAM utilisée : {ram:.0f}%")
        if batt and not batt.power_plugged and batt.percent <= 10:
            self._notify("batt_low", "Batterie faible 🔋",
                         f"{batt.percent:.0f}% — branchez votre Mac")
        temp = self._temp_cache
        if temp != "—":
            try:
                t_num = float(re.sub(r"[^\d.]", "", temp.split("°")[0]))
                if t_num >= TEMP_ALERT_THRESH:
                    self._notify("temp_high", "Température CPU élevée 🌡️",
                                 f"{temp} — vérifiez la ventilation")
            except (ValueError, IndexError):
                pass


if __name__ == "__main__":
    MacMonitorPro().run()
