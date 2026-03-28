#!/usr/bin/env python3
"""MAC Monitor Pro v7 — Interface à onglets, design pro."""

import colorsys, json, math, os, re, subprocess, time, psutil, objc, rumps
from collections import deque
from datetime import datetime, timedelta

from Foundation import NSMakeRect, NSMakeSize, NSMakePoint, NSTimer, NSRunLoop, NSObject
from AppKit import (
    NSMenuItem, NSMenu, NSView, NSFont, NSColor, NSBezierPath,
    NSAttributedString, NSForegroundColorAttributeName, NSFontAttributeName,
    NSImage, NSRectFill, NSPasteboard, NSAppearance,
)

# ─── Dimensions ───────────────────────────────────────────────────────────────
PW          = 320
PH          = 510
PAD         = 20
INNER_W     = PW - PAD * 2          # 280
CORNER      = 14
HDR_H       = 52
TAB_H       = 36
TAB_COUNT   = 4
TAB_W       = INNER_W / TAB_COUNT   # 70

# Zone contenu : de (PH - HDR_H - 8 - TAB_H - 12) à 14
Y_CONTENT   = PH - HDR_H - 8 - TAB_H - 14   # ≈ 396
Y_FLOOR     = 14

HIST        = 60
NOTIF_CD    = 300
POMO_DUR    = 25 * 60
DL_HI       = 1_000_000
DL_LO       =   100_000
TEMP_ALERT  = 90

TABS        = ["sys", "net", "cal", "proc"]
TAB_LABEL   = {"sys": "Système", "net": "Réseau",
               "cal": "Agenda",  "proc": "Process"}

PLIST_PATH  = os.path.expanduser("~/Library/LaunchAgents/com.macmonitor.app.plist")
PLIST_LABEL = "com.macmonitor.app"
DAYS_FR     = ["Lun.", "Mar.", "Mer.", "Jeu.", "Ven.", "Sam.", "Dim."]

# ─── Palette ──────────────────────────────────────────────────────────────────
def _c(r, g, b, a=1.0):
    return NSColor.colorWithRed_green_blue_alpha_(r, g, b, a)

C_BG    = _c(0.07, 0.06, 0.11, 0.99)
C_HDR   = _c(0.10, 0.07, 0.16, 1.00)
C_WHITE = NSColor.whiteColor()
C_GRAY  = _c(1, 1, 1, 0.40)
C_DIM   = _c(1, 1, 1, 0.08)
C_GREEN = _c(0.18, 0.84, 0.40)
C_ORA   = _c(1.00, 0.62, 0.04)
C_RED   = _c(1.00, 0.25, 0.22)

C_SYS   = _c(0.30, 0.62, 1.00)   # bleu
C_NET   = _c(0.18, 0.84, 0.94)   # cyan
C_CAL   = _c(0.55, 0.42, 1.00)   # violet
C_PROC  = _c(0.85, 0.85, 0.92)   # blanc cassé
C_RAM   = _c(0.70, 0.38, 1.00)
C_DSK   = _c(1.00, 0.58, 0.10)
C_BAT   = _c(0.18, 0.84, 0.40)
C_MUS   = _c(1.00, 0.42, 0.72)

TAB_COLOR = {"sys": C_SYS, "net": C_NET, "cal": C_CAL, "proc": C_PROC}

def _bar_col(v, accent):
    return C_RED if v >= 85 else C_ORA if v >= 60 else accent

def _b(n: float) -> str:
    for u in ("B", "K", "M", "G", "T"):
        if abs(n) < 1024: return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} P"

# ─── Polices ──────────────────────────────────────────────────────────────────
def SF(sz, w=0.0): return NSFont.systemFontOfSize_weight_(sz, w)
def MONO(sz, w=0.0): return NSFont.monospacedDigitSystemFontOfSize_weight_(sz, w)

F_TITLE  = SF(13,   0.55)
F_LABEL  = SF(9.5,  0.35)
F_VALUE  = MONO(26, -0.30)
F_UNIT   = SF(12,   0.00)
F_INFO   = SF(11,   0.00)
F_SM     = SF(10,   0.00)
F_MONO   = MONO(10, 0.0)
F_BTN    = SF(11.5, 0.30)
F_BTN_S  = SF(10,   0.25)
F_TAB    = SF(10.5, 0.30)

# ─── Primitives ───────────────────────────────────────────────────────────────
def _attr(s, color, font):
    return NSAttributedString.alloc().initWithString_attributes_(
        s, {NSForegroundColorAttributeName: color, NSFontAttributeName: font})

def _draw(s, x, y, color, font):
    _attr(s, color, font).drawAtPoint_(NSMakePoint(x, y))

def _draw_r(s, rx, y, color, font):
    a = _attr(s, color, font)
    a.drawAtPoint_(NSMakePoint(rx - a.size().width, y))

def _draw_c(s, cx, y, color, font):
    a = _attr(s, color, font)
    a.drawAtPoint_(NSMakePoint(cx - a.size().width / 2, y))

def _tw(s, font):
    return _attr(s, C_WHITE, font).size().width

def _rrect(x, y, w, h, r, color):
    color.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()

def _oval(x, y, w, h, color):
    color.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(x, y, w, h)).fill()

def _line(x1, y1, x2, y2, color, width=0.5):
    p = NSBezierPath.bezierPath()
    p.moveToPoint_(NSMakePoint(x1, y1))
    p.lineToPoint_(NSMakePoint(x2, y2))
    color.setStroke(); p.setLineWidth_(width); p.stroke()

def _bar(x, y, w, h, value, accent):
    """Barre de progression arrondie avec glow."""
    r = h / 2.0
    C_DIM.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()
    if value <= 0: return
    fw    = max(r * 2, w * min(value, 100) / 100.0)
    color = _bar_col(value, accent)
    # glow
    color.colorWithAlphaComponent_(0.18).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x - 1, y - 3, fw + 2, h + 6),
        (h + 6) / 2, (h + 6) / 2).fill()
    color.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, fw, h), r, r).fill()

def _spark(x, y, w, h, data, color):
    pts = list(data)
    if len(pts) < 2: return
    n = len(pts)
    _c(1, 1, 1, 0.04).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), 3, 3).fill()
    def px(i, v): return x + i * w / (n - 1), y + (v / 100) * h
    fill = NSBezierPath.bezierPath()
    fill.moveToPoint_(NSMakePoint(x, y))
    for i, v in enumerate(pts): fill.lineToPoint_(NSMakePoint(*px(i, v)))
    fill.lineToPoint_(NSMakePoint(x + w, y)); fill.closePath()
    color.colorWithAlphaComponent_(0.16).setFill(); fill.fill()
    ln = NSBezierPath.bezierPath(); ln.setLineWidth_(1.5)
    for i, v in enumerate(pts):
        p = NSMakePoint(*px(i, v))
        if i == 0: ln.moveToPoint_(p)
        else:      ln.lineToPoint_(p)
    color.setStroke(); ln.stroke()

def _card(x, y_top, w, h, accent, alpha=0.07):
    accent.colorWithAlphaComponent_(alpha).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y_top - h, w, h), 9, 9).fill()

def _sep(y, x1, x2):
    _c(1, 1, 1, 0.06).setFill()
    NSBezierPath.fillRect_(NSMakeRect(x1, y, x2 - x1, 0.4))

def _section_label(x, y, text, accent):
    """Pastille + label de section."""
    accent.colorWithAlphaComponent_(0.70).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y + 3, 3, 7), 1.5, 1.5).fill()
    _draw(text, x + 8, y, accent.colorWithAlphaComponent_(0.75), F_LABEL)

def _big_value(x, y, value, unit, accent):
    """Grosse valeur numérique + unité."""
    vs = f"{value:.1f}"
    _draw(vs, x, y, _bar_col(value, accent), F_VALUE)
    aw = _tw(vs, F_VALUE)
    _draw(unit, x + aw + 3, y + 8, C_GRAY, F_UNIT)
    return aw

def _btn(x, y, w, h, label, bg, fg, r=None):
    radius = r if r is not None else h / 2
    bg.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), radius, radius).fill()
    a = _attr(label, fg, F_BTN_S)
    a.drawAtPoint_(NSMakePoint(
        x + (w - a.size().width) / 2, y + (h - a.size().height) / 2))
    return NSMakeRect(x, y, w, h)

def _wifi_bars(x, y, rssi):
    lit = 0 if rssi == 0 else (4 if rssi >= -50 else 3 if rssi >= -60
                                else 2 if rssi >= -70 else 1)
    for i in range(4):
        bh  = 3 + i * 2.5
        col = C_NET if i < lit else _c(1, 1, 1, 0.14)
        col.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(x + i * 5.5, y, 4, bh), 0.5, 0.5).fill()


# ─── Vue panneau ──────────────────────────────────────────────────────────────
class PanelView(NSView):

    _tab: str        = "sys"
    _top_mode: str   = "cpu"
    _hover: str      = ""
    _btn_rects: dict = None
    _tab_rects: dict = None
    _copy_flash: float = 0.0   # timestamp jusqu'où afficher "✓ Copié"

    def initWithFrame_(self, frame):
        self = objc.super(PanelView, self).initWithFrame_(frame)
        if self is None: return None
        self.setWantsLayer_(True)
        # Empêche le fond blanc par défaut de la layer (macOS Sonoma)
        self.layer().setBackgroundColor_(
            objc.nil)
        self.layer().setOpaque_(False)
        self._btn_rects = {}
        self._tab_rects = {}
        return self

    # ── drawRect_ ────────────────────────────────────────────
    def drawRect_(self, _):
        s = _S
        if not s: return
        w, h = PW, PH
        bw   = INNER_W
        rects = {}

        # Fond
        C_BG.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, 0, w, h), CORNER, CORNER).fill()
        _c(1, 1, 1, 0.015).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, h * 0.55, w, h * 0.45), CORNER, CORNER).fill()

        # Header
        self._draw_header(w, h, bw, s, rects)

        # Tabs
        tab_rects = {}
        self._draw_tabs(w, h, bw, tab_rects)
        self._tab_rects = tab_rects

        # Contenu
        y = Y_CONTENT
        if   self._tab == "sys":  self._draw_sys(y, bw, s, w, rects)
        elif self._tab == "net":  self._draw_net(y, bw, s, w)
        elif self._tab == "cal":  self._draw_cal(y, bw, s, w)
        elif self._tab == "proc": self._draw_proc(y, bw, s, w, rects)

        self._btn_rects = rects

    # ── Header ───────────────────────────────────────────────
    def _draw_header(self, w, h, bw, s, rects):
        party = s.get('party', False)
        if party:
            hue      = (time.time() * 0.4) % 1.0
            r, g, b  = colorsys.hsv_to_rgb(hue, 0.65, 0.28)
            hdr_c    = _c(r, g, b, 1.0)
        else:
            hdr_c = C_HDR

        # Fond header (arrondi en haut, carré en bas pour jointure propre)
        hdr_c.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(0, h - HDR_H, w, HDR_H + CORNER), CORNER, CORNER).fill()

        # Heure + date
        _draw(time.strftime("%H:%M"), PAD, h - HDR_H + 22,
              _c(1, 1, 1, 0.50), SF(13, 0.3))
        _draw(time.strftime("%a %d %b").capitalize(),
              PAD, h - HDR_H + 8, C_GRAY, F_SM)

        # Titre
        title = "✦ MAC Monitor ✦" if party else "MAC Monitor"
        tcol  = _c(1.0, 0.95, 0.4, 1.0) if party else C_WHITE
        _draw_c(title, w / 2, h - HDR_H + 18, tcol, F_TITLE)
        if not party:
            _draw_r("Pro", w - PAD - 22, h - HDR_H + 18,
                    _c(1, 1, 1, 0.28), F_SM)

        # Bouton × (quitter)
        qx, qy = w - PAD - 17, h - HDR_H + 8
        qbg = _c(1, 1, 1, 0.15) if self._hover == "quit" else _c(1, 1, 1, 0.07)
        qbg.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(qx, qy, 17, 17)).fill()
        _draw_c("×", qx + 8.5, qy + 2, _c(1, 1, 1, 0.55), SF(13, 0.2))
        rects["quit"] = NSMakeRect(qx, qy, 17, 17)

        # Barre CPU sous header
        cpu = s.get('cpu', 0)
        _c(1, 1, 1, 0.05).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(PAD, h - HDR_H - 4, bw, 3), 1.5, 1.5).fill()
        if cpu > 0:
            bc = C_RED if cpu >= 80 else C_ORA if cpu >= 60 else C_GREEN
            bc.colorWithAlphaComponent_(0.70).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(PAD, h - HDR_H - 4,
                           max(3, bw * min(cpu, 100) / 100), 3),
                1.5, 1.5).fill()

    # ── Tab bar ──────────────────────────────────────────────
    def _draw_tabs(self, w, h, bw, tab_rects):
        ty = h - HDR_H - 8 - TAB_H
        # Fond barre
        _c(1, 1, 1, 0.04).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(PAD, ty, bw, TAB_H), 10, 10).fill()
        for i, tab in enumerate(TABS):
            tx  = PAD + i * TAB_W
            col = TAB_COLOR[tab]
            active = (tab == self._tab)
            if active:
                col.colorWithAlphaComponent_(0.20).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(tx + 3, ty + 4, TAB_W - 6, TAB_H - 8),
                    7, 7).fill()
                _draw_c(TAB_LABEL[tab], tx + TAB_W / 2,
                        ty + (TAB_H - 14) / 2, col, F_TAB)
            else:
                _draw_c(TAB_LABEL[tab], tx + TAB_W / 2,
                        ty + (TAB_H - 14) / 2, C_GRAY, F_TAB)
            tab_rects[tab] = NSMakeRect(tx, ty, TAB_W, TAB_H)

    # ── Page 1 : Système ─────────────────────────────────────
    def _draw_sys(self, y, bw, s, w, rects):
        cpu  = s.get('cpu', 0)
        gpu  = s.get('gpu', -1)
        ram  = s.get('ram', 0)
        bpct = s.get('batt_pct')

        # ── CPU Card ──────────────────────────────────
        cpu_card_h = 118 + (18 if gpu >= 0 else 0)
        _card(PAD, y, bw, cpu_card_h, C_SYS)
        _section_label(PAD + 10, y - 14, "PROCESSEUR", C_SYS)
        if s.get('cpu_temp', '—') != '—':
            _draw_r(s['cpu_temp'], w - PAD - 10, y - 14,
                    C_SYS.colorWithAlphaComponent_(0.65), F_SM)

        # Valeur + sparkline
        vy = y - 52
        _big_value(PAD + 10, vy, cpu, "%", C_SYS)
        _spark(PAD + 90, vy + 2, bw - 100, 28,
               s.get('cpu_hist', []), C_SYS)

        # Barre
        _bar(PAD + 10, vy - 14, bw - 20, 7, cpu, C_SYS)

        # Info fréq + bouton activité
        iy = vy - 28
        _draw(s.get('cpu_info', ''), PAD + 10, iy, C_GRAY, F_SM)
        abg = (_c(0.30, 0.62, 1.00, 0.25) if self._hover == "act_cpu"
               else _c(0.30, 0.62, 1.00, 0.13))
        rects["act_cpu"] = _btn(w - PAD - 82, iy - 1, 82, 14,
                                "→ Activité", abg, C_SYS, r=7)

        # GPU
        if gpu >= 0:
            gy = iy - 18
            _draw("GPU", PAD + 10, gy, C_GRAY, F_SM)
            _bar(PAD + 40, gy + 1, bw - 80, 5, gpu,
                 C_SYS.colorWithAlphaComponent_(0.75))
            _draw_r(f"{gpu}%", w - PAD - 10, gy,
                    C_SYS.colorWithAlphaComponent_(0.80), F_MONO)

        y -= cpu_card_h + 22

        # ── RAM Card ──────────────────────────────────
        mem_pres = s.get('mem_pressure', '—')
        pres_col = (C_RED    if mem_pres == "Critique"      else
                    C_ORA    if mem_pres == "Avertissement"  else C_GREEN)
        _card(PAD, y, bw, 98, C_RAM)
        _section_label(PAD + 10, y - 14, "MÉMOIRE", C_RAM)
        if mem_pres != '—':
            pc = pres_col.colorWithAlphaComponent_(0.85)
            _draw_r(f"● {mem_pres}", w - PAD - 10, y - 14, pc, F_SM)

        vy = y - 50
        _big_value(PAD + 10, vy, ram, "%", C_RAM)
        _spark(PAD + 90, vy + 2, bw - 100, 28,
               s.get('ram_hist', []), C_RAM)
        _bar(PAD + 10, vy - 14, bw - 20, 7, ram, C_RAM)
        _draw(s.get('ram_info', ''), PAD + 10, vy - 28, C_GRAY, F_SM)

        y -= 98 + 22

        # ── Batterie / Système Card ────────────────────
        has_b = bpct is not None
        bc_h  = 88 if has_b else 46
        _card(PAD, y, bw, bc_h, C_BAT, alpha=0.06)
        _section_label(PAD + 10, y - 14, "SYSTÈME", C_BAT)
        _draw_r(f"⏱ {s.get('uptime','—')}", w - PAD - 10, y - 14,
                _c(1, 1, 1, 0.28), F_SM)
        if has_b:
            bp   = bpct
            icon = "⚡" if s.get('batt_plug') else "🔋"
            # Ligne 1 : icône + pourcentage coloré
            bp_col = C_GREEN if bp > 50 else C_ORA if bp > 20 else C_RED
            _draw(f"{icon}  {bp:.0f}%", PAD + 10, y - 32, bp_col, F_INFO)
            # Ligne 2 : barre pleine largeur
            _bar(PAD + 10, y - 46, bw - 20, 8, bp, C_BAT)
            # Ligne 3 : temps restant (gauche) + santé (droite)
            batt_t = s.get('batt_time', '')
            _draw(batt_t, PAD + 10, y - 62, C_GRAY, F_SM)
            bh = s.get('batt_health', -1)
            if bh > 0:
                hc = C_GREEN if bh >= 80 else C_ORA if bh >= 60 else C_RED
                _draw_r(f"Santé {bh}%", w - PAD - 10, y - 62, hc, F_SM)
            # Ligne 4 : mini barre santé visuelle
            if bh > 0:
                _bar(PAD + 10, y - 76, bw - 20, 4, bh,
                     C_GREEN if bh >= 80 else C_ORA if bh >= 60 else C_RED)
        else:
            _draw("Pas de batterie détectée", PAD + 10, y - 32,
                  _c(1, 1, 1, 0.25), F_SM)

    # ── Page 2 : Réseau ──────────────────────────────────────
    def _draw_net(self, y, bw, s, w):
        # ── Réseau Card ───────────────────────────────
        ip   = s.get('local_ip', '—')
        gw   = s.get('gateway', '')
        ping = s.get('ping', '—')
        ssid = s.get('wifi_ssid', '')
        rssi = s.get('wifi_rssi', 0)

        _card(PAD, y, bw, 146, C_NET)
        _section_label(PAD + 10, y - 14, "RÉSEAU", C_NET)
        hdr = "  ".join(filter(None, [ip, f"GW {gw}" if gw else ""]))
        _draw_r(hdr, w - PAD - 10, y - 14,
                C_NET.colorWithAlphaComponent_(0.55), F_SM)

        # Vitesses (grosses)
        dl = s.get('dl_str', '0 B/s')
        ul = s.get('ul_str', '0 B/s')
        hw = int((bw - 20) / 2)

        _draw("↓", PAD + 10, y - 44, C_NET, SF(18, 0.3))
        _draw(dl, PAD + 30, y - 44, C_WHITE, SF(15, -0.2))
        _draw("↑", PAD + 20 + hw, y - 44, C_ORA, SF(18, 0.3))
        _draw(ul, PAD + 40 + hw, y - 44, C_WHITE, SF(15, -0.2))

        # Sparklines côte à côte
        sy = y - 80
        _spark(PAD + 10, sy, hw - 5, 22, s.get('dl_hist', []), C_NET)
        _spark(PAD + 15 + hw, sy, hw - 5, 22, s.get('ul_hist', []), C_ORA)

        # WiFi + totaux
        wy = y - 110
        _wifi_bars(PAD + 10, wy, rssi)
        if ssid:
            _draw(ssid[:18], PAD + 36, wy, C_GRAY, F_SM)
        _draw_r(f"🏓 {ping}" if ping != '—' else '',
                w - PAD - 10, wy, C_NET.colorWithAlphaComponent_(0.70), F_SM)

        tot_y = y - 128
        _draw(f"Total  ↓ {s.get('net_total_dl','—')}   ↑ {s.get('net_total_ul','—')}",
              PAD + 10, tot_y, _c(1, 1, 1, 0.28), F_SM)

        y -= 146 + 24

        # ── Disque Card ───────────────────────────────
        dp = s.get('disk_pct', 0)
        _card(PAD, y, bw, 112, C_DSK)
        _section_label(PAD + 10, y - 14, "STOCKAGE  /", C_DSK)
        _draw_r(s.get('disk_info', ''), w - PAD - 10, y - 14, C_GRAY, F_SM)

        vy = y - 52
        _big_value(PAD + 10, vy, dp, "%", C_DSK)
        _spark(PAD + 90, vy + 2, bw - 100, 28,
               [], C_DSK)          # pas d'historique disque — espace vide propre
        _bar(PAD + 10, vy - 14, bw - 20, 7, dp, C_DSK)

        ioy = vy - 30
        _draw(f"R  {s.get('disk_r','0 B/s')}", PAD + 10, ioy, C_GREEN, F_SM)
        _draw_r(f"W  {s.get('disk_w','0 B/s')}", w - PAD - 10, ioy,
                C_ORA, F_SM)

    # ── Page 3 : Agenda ──────────────────────────────────────
    def _draw_cal(self, y, bw, s, w):
        events = s.get('cal_events', [])
        n_ev   = max(len(events), 1)
        cal_h  = n_ev * 26 + 36

        _card(PAD, y, bw, cal_h, C_CAL)
        _section_label(PAD + 10, y - 14, "CALENDRIER", C_CAL)
        _draw_r("5 prochains jours", w - PAD - 10, y - 14,
                C_CAL.colorWithAlphaComponent_(0.40), F_SM)

        if not events:
            _draw("Aucun événement à venir", PAD + 10, y - 42,
                  _c(1, 1, 1, 0.22), F_INFO)
        else:
            for i, ev in enumerate(events):
                ey   = y - 38 - i * 26
                is_t = ev.get('is_today', False)
                is_m = ev.get('is_tomorrow', False)
                dc   = (C_GREEN if is_t else C_ORA if is_m
                        else C_CAL.colorWithAlphaComponent_(0.55))

                # Pastille couleur
                dc.setFill()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(PAD + 10, ey + 2, 6, 6)).fill()

                # Étiquette jour + heure
                tag = f"{ev.get('day','')} {ev.get('time','')}"
                _draw(tag, PAD + 22, ey, dc.colorWithAlphaComponent_(0.90), F_SM)

                # Titre
                title = ev.get('title', '')
                max_c = 24
                if len(title) > max_c: title = title[:max_c - 1] + "…"
                _draw(title, PAD + 88, ey, C_WHITE, F_INFO)

                # Séparateur léger entre events
                if i < len(events) - 1:
                    _sep(ey - 5, PAD + 10, PAD + bw - 10)

        y -= cal_h + 24

        # ── Musique Card ──────────────────────────────
        music = s.get('music', '')
        _card(PAD, y, bw, 62, C_MUS, alpha=0.06)
        _section_label(PAD + 10, y - 14, "MUSIQUE", C_MUS)
        if music:
            _draw_r("♪", w - PAD - 10, y - 14,
                    C_MUS.colorWithAlphaComponent_(0.80), F_SM)

        if music:
            # Titre
            parts = music.split(" — ", 1)
            track  = parts[0] if parts else music
            artist = parts[1] if len(parts) > 1 else ""
            t_max  = 30
            if len(track) > t_max: track = track[:t_max - 1] + "…"
            _draw(track,  PAD + 10, y - 36, C_WHITE, F_INFO)
            if artist:
                a_max = 34
                if len(artist) > a_max: artist = artist[:a_max - 1] + "…"
                _draw(artist, PAD + 10, y - 52, C_GRAY, F_SM)
        else:
            _draw("—", PAD + 10, y - 36, _c(1, 1, 1, 0.20), F_INFO)

    # ── Page 4 : Process ─────────────────────────────────────
    def _draw_proc(self, y, bw, s, w, rects):
        procs = s.get('top_procs', [])
        n_p   = len(procs)
        mode_col = C_RAM if self._top_mode == "mem" else C_SYS
        proc_h = n_p * 22 + 40

        _card(PAD, y, bw, proc_h, C_PROC, alpha=0.05)
        _section_label(PAD + 10, y - 14, "TOP PROCESSUS", C_PROC)

        # Badge mode tri
        badge = "● MEM" if self._top_mode == "mem" else "● CPU"
        _draw(badge,
              PAD + 10 + _tw("TOP PROCESSUS", F_LABEL) + 12,
              y - 14, mode_col.colorWithAlphaComponent_(0.60), F_SM)

        # En-têtes colonnes
        _draw_r("CPU%", w - PAD - 10,      y - 14,
                C_SYS.colorWithAlphaComponent_(0.60), F_SM)
        _draw_r("MEM%", w - PAD - 10 - 44, y - 14,
                C_RAM.colorWithAlphaComponent_(0.60), F_SM)

        # Clic sur en-tête → toggle mode
        rects["top_hdr"] = NSMakeRect(PAD, y - 18, bw, 18)

        sorted_p = sorted(procs,
                          key=lambda p: p['mem'] if self._top_mode == 'mem'
                                        else p['cpu'],
                          reverse=True)
        for i, p in enumerate(sorted_p):
            py  = y - 36 - i * 22
            cp  = p['cpu']; mp = p['mem']
            cc  = C_RED if cp > 50 else C_ORA if cp > 20 else C_WHITE
            mc  = C_RED if mp > 10 else C_ORA if mp > 5  else \
                  C_RAM.colorWithAlphaComponent_(0.75)
            _draw(p['name'][:24], PAD + 10, py, C_WHITE, F_MONO)
            _draw_r(f"{cp:5.1f}", w - PAD - 10,      py, cc, F_MONO)
            _draw_r(f"{mp:5.1f}", w - PAD - 10 - 44, py, mc, F_MONO)
            if i < n_p - 1:
                _sep(py - 5, PAD + 10, PAD + bw - 10)

        y -= proc_h + 24

        # ── Boutons d'action ──────────────────────────
        abh = 34
        abw = int((bw - 12) / 3)

        caff  = _app._caff_proc is not None and _app._caff_proc.poll() is None \
                if _app else False
        pomo  = (_app._pomo_end > 0) if _app else False

        caff_lbl = "💤 Veille OFF" if caff else "💤 Veille"
        caff_bg  = (_c(0.18,0.84,0.40, 0.28) if self._hover == "caff" else
                    _c(0.18,0.84,0.40, 0.16) if caff else _c(1,1,1, 0.06))
        caff_fg  = C_GREEN if caff else C_GRAY
        rects["caff"] = _btn(PAD, y - abh, abw, abh,
                             caff_lbl, caff_bg, caff_fg, r=9)

        if pomo and _app:
            left = max(0.0, _app._pomo_end - time.time())
            pomo_lbl = f"⏰ {int(left)//60:02d}:{int(left)%60:02d}"
        else:
            pomo_lbl = "⏰ Pomodoro"
        pomo_bg = (_c(0.30,0.62,1.00, 0.28) if self._hover == "pomo" else
                   _c(0.30,0.62,1.00, 0.16) if pomo else _c(1,1,1, 0.06))
        pomo_fg = C_SYS if pomo else C_GRAY
        rects["pomo"] = _btn(PAD + abw + 6, y - abh, abw, abh,
                             pomo_lbl, pomo_bg, pomo_fg, r=9)

        flashing = time.time() < self._copy_flash
        copy_bg  = _c(0.18, 0.84, 0.40, 0.28) if flashing else (
                   _c(1, 1, 1, 0.12) if self._hover == "copy" else _c(1, 1, 1, 0.06))
        copy_lbl = "✓ Copié" if flashing else "📋 Copier"
        copy_fg  = C_GREEN if flashing else C_GRAY
        rects["copy"] = _btn(PAD + (abw + 6) * 2, y - abh, abw, abh,
                             copy_lbl, copy_bg, copy_fg, r=9)
        y -= abh + 14

        # ── Quitter ───────────────────────────────────
        qh  = 34
        qbt = y - qh
        qbg = _c(1.0, 0.25, 0.22, 0.25) if self._hover == "quit2" \
              else _c(1.0, 0.25, 0.22, 0.10)
        qbg.setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(PAD, qbt, bw, qh), qh / 2, qh / 2).fill()
        _c(1.0, 0.35, 0.30, 0.15).setStroke()
        p = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(PAD, qbt, bw, qh), qh / 2, qh / 2)
        p.setLineWidth_(0.5); p.stroke()
        ql = _attr("Quitter MAC Monitor",
                   _c(1.0, 0.50, 0.48, 1.0), F_BTN)
        ql.drawAtPoint_(NSMakePoint(
            (PW - ql.size().width) / 2, qbt + (qh - ql.size().height) / 2))
        rects["quit2"] = NSMakeRect(PAD, qbt, bw, qh)

    # ── Interactions ─────────────────────────────────────────
    def mouseDown_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        h  = self.bounds().size.height

        # Clic header → party mode ou bouton ×
        if pt.y >= h - HDR_H:
            r = (self._btn_rects or {}).get("quit")
            if r and (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                      r.origin.y <= pt.y <= r.origin.y + r.size.height):
                if _menu: _menu.cancelTracking()
                return
            if _app: _app._party_end = time.time() + 5.0
            return

        # Clic onglets
        for tab, r in (self._tab_rects or {}).items():
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                self._tab = tab
                self.setNeedsDisplay_(True)
                return

        # Toggle mode tri processus
        r = (self._btn_rects or {}).get("top_hdr")
        if r and (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                  r.origin.y <= pt.y <= r.origin.y + r.size.height):
            self._top_mode = "mem" if self._top_mode == "cpu" else "cpu"
            self.setNeedsDisplay_(True)
            return

        # Boutons
        for name, r in (self._btn_rects or {}).items():
            if name in ("top_hdr",): continue
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                if name in ("quit", "quit2"):
                    if _menu: _menu.cancelTracking()
                elif _app:
                    if   name == "caff":    _app.toggle_caff()
                    elif name == "pomo":    _app.toggle_pomo()
                    elif name == "copy":
                        _app.copy_stats()
                        self._copy_flash = time.time() + 2.0
                    elif name == "act_cpu":
                        subprocess.Popen(["open", "-a", "Activity Monitor"])
                self.setNeedsDisplay_(True)
                break

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        hv = ""
        for name, r in (self._btn_rects or {}).items():
            if name in ("top_hdr",): continue
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                hv = name; break
        if hv != self._hover:
            self._hover = hv
            self.setNeedsDisplay_(True)

    def mouseExited_(self, _):
        if self._hover:
            self._hover = ""
            self.setNeedsDisplay_(True)

    def acceptsFirstMouse_(self, _): return True
    def refreshDisplay_(self, _): self.setNeedsDisplay_(True)


# ─── Delegate ─────────────────────────────────────────────────────────────────
class _StatsDelegate(NSObject):
    _app_ref = None
    def statsRefresh_(self, _):
        if self._app_ref: self._app_ref._do_stats()


# ─── Robot menubar v2 ─────────────────────────────────────────────────────────
_STATE_COL = {
    "chill": (0.18, 0.90, 0.62),
    "busy":  (0.30, 0.62, 1.00),
    "hot":   (1.00, 0.65, 0.12),
    "panic": (1.00, 0.20, 0.20),
}

def draw_character(t, state, blink, size=24, party=False):
    if party:
        rc, gc, bc = colorsys.hsv_to_rgb((t * 2) % 1.0, 1.0, 1.0)
        anim = "chill"
    else:
        rc, gc, bc = _STATE_COL[state]; anim = state

    main  = _c(rc,        gc,        bc,        1.0)
    dark  = _c(rc * 0.45, gc * 0.45, bc * 0.45, 1.0)
    mid   = _c(rc * 0.70, gc * 0.70, bc * 0.70, 1.0)
    shine = _c(1, 1, 1, 0.50)
    glow  = _c(rc, gc, bc, 0.16)

    img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill()
    NSRectFill(NSMakeRect(0, 0, size, size))

    bob   = (math.sin(t*6*math.pi)*1.8 if anim == "panic"
             else math.sin(t*2*math.pi)*(1.2 if anim == "chill" else 0.5))
    y0    = 1.0 + bob
    swing = math.sin(t*2*math.pi)*(2.8 if anim == "panic" else 1.6)

    # Jambes
    _rrect(7.5 - swing,  y0, 2.8, 3.8, 1.4, dark)
    _rrect(13.7 + swing, y0, 2.8, 3.8, 1.4, dark)

    # Corps
    body_y = y0 + 2.8
    _rrect(5.5, body_y, 13, 7, 2.5, main)
    shine.colorWithAlphaComponent_(0.28).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(7.0, body_y + 4.8, 4.5, 1.5), 0.8, 0.8).fill()
    mid.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(9.5, body_y + 1.5, 5, 2.5), 0.8, 0.8).fill()
    _c(0, 0, 0, 0.5).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(10, body_y + 1.8, 4, 1.8), 0.5, 0.5).fill()

    # Bras
    arm_s = (math.sin(t*4*math.pi)*2.0 if anim == "busy"
             else math.sin(t*6*math.pi)*2.8 if anim == "panic"
             else math.sin(t*2*math.pi)*1.4)
    _rrect(2.5,  body_y + 1.0 - arm_s, 2.8, 3.8, 1.4, dark)
    _rrect(18.7, body_y + 1.0 + arm_s, 2.8, 3.8, 1.4, dark)

    # Tête
    head_y = y0 + 9.5
    head_w, head_h = 12.0, 9.0
    head_x = (size - head_w) / 2.0
    glow.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(head_x-2, head_y-2, head_w+4, head_h+4)).fill()
    main.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(head_x, head_y, head_w, head_h)).fill()
    dark.setStroke()
    p = NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(head_x, head_y, head_w, head_h))
    p.setLineWidth_(0.4); p.stroke()
    shine.colorWithAlphaComponent_(0.22).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(head_x+2, head_y+5.5, 4.5, 2.5)).fill()

    # Yeux
    ey = head_y + 3.5
    if blink:
        for ex in (8.5, 13.0):
            p = NSBezierPath.bezierPath()
            p.moveToPoint_(NSMakePoint(ex, ey+1.0))
            p.lineToPoint_(NSMakePoint(ex+2.0, ey+1.0))
            NSColor.whiteColor().setStroke(); p.setLineWidth_(1.0); p.stroke()
    else:
        eye_h = 2.8 if anim == "panic" else 2.2
        for ex in (8.3, 12.8):
            _oval(ex,       ey,       2.2, eye_h, NSColor.whiteColor())
            _oval(ex+0.5,   ey+0.4,   1.2, 1.3,  NSColor.blackColor())
            _oval(ex+1.05,  ey+0.9,   0.55, 0.55, NSColor.whiteColor())

    # Bouche
    my = head_y + 1.6
    if anim == "chill":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.5, my+0.5))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(14.5, my+0.5),
            NSMakePoint(10.0, my-1.2), NSMakePoint(14.0, my-1.2))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.1); m.stroke()
    elif anim == "busy":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(10.0, my))
        m.lineToPoint_(NSMakePoint(14.0, my))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.0); m.stroke()
    elif anim == "hot":
        m = NSBezierPath.bezierPath()
        m.moveToPoint_(NSMakePoint(9.5, my+0.5))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(12.0, my-0.5), NSMakePoint(10.0, my+0.4),
            NSMakePoint(11.2, my-0.4))
        m.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(14.5, my+0.5), NSMakePoint(12.8, my-0.4),
            NSMakePoint(14.0, my+0.4))
        NSColor.whiteColor().setStroke(); m.setLineWidth_(1.0); m.stroke()
    else:
        _oval(10.5, my-0.5, 3.2, 2.8, NSColor.blackColor())

    # Antenne
    ax, ay = head_x + head_w/2, head_y + head_h - 0.3
    sway   = math.sin(t*2*math.pi + 0.7) * 1.8
    tx, ty = ax + sway, ay + 4.5
    al = NSBezierPath.bezierPath()
    al.moveToPoint_(NSMakePoint(ax, ay))
    al.lineToPoint_(NSMakePoint(tx, ty))
    mid.setStroke(); al.setLineWidth_(0.9); al.stroke()
    pulse   = 0.8 + math.sin(t*4*math.pi) * 0.2
    ball_c  = (_c(1.0, 0.3*pulse, 0.1)  if anim == "panic" else
               _c(1.0, 0.85*pulse, 0.1) if anim == "hot"   else
               _c(0.3, 0.8*pulse, 1.0)  if anim == "busy"  else
               _c(0.1, pulse, 0.7))
    ball_c.colorWithAlphaComponent_(0.28).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(tx-3.5, ty-3.5, 7, 7)).fill()
    ball_c.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(tx-2.2, ty-2.2, 4.4, 4.4)).fill()
    _oval(tx-0.7, ty+0.5, 1.1, 1.1, NSColor.whiteColor())

    # Effets
    if anim == "hot":
        sd_y = head_y + 8.5 - ((t*6) % 8)
        sw = NSBezierPath.bezierPath()
        sw.moveToPoint_(NSMakePoint(21.5, sd_y+3.0))
        sw.curveToPoint_controlPoint1_controlPoint2_(
            NSMakePoint(20.0, sd_y), NSMakePoint(23.0, sd_y+2.0),
            NSMakePoint(23.0, sd_y+0.8))
        sw.lineToPoint_(NSMakePoint(21.5, sd_y+3.0))
        _c(0.5, 0.82, 1.0, 0.85).setFill(); sw.fill()
    elif anim == "panic":
        for i in range(3):
            sp = NSBezierPath.bezierPath()
            sp.moveToPoint_(NSMakePoint(0, y0+3.5+i*2.5))
            sp.lineToPoint_(NSMakePoint(1.5+i*0.8, y0+3.5+i*2.5))
            _c(1, 0.3, 0.3, 0.7).setStroke()
            sp.setLineWidth_(0.7); sp.stroke()
        flk = math.sin(t*8*math.pi) * 0.9
        _oval(ax-1.5+flk, ty,     2.8, 2.8, _c(1.0, 0.55, 0.0, 0.9))
        _oval(ax-1.0+flk, ty+1.0, 2.2, 2.5, _c(1.0, 0.9,  0.1, 0.85))
    elif anim == "chill":
        np = (t*3) % 1.0
        if np < 0.5:
            ny, alpha = head_y + 7 + np*4, 1.0 - np*2
            nc = _c(rc, gc, bc, alpha)
            _oval(22.0, ny, 1.8, 1.5, nc)
            nl = NSBezierPath.bezierPath()
            nl.moveToPoint_(NSMakePoint(23.8, ny+1.5))
            nl.lineToPoint_(NSMakePoint(23.8, ny+4.5))
            nc.setStroke(); nl.setLineWidth_(0.8); nl.stroke()

    img.unlockFocus()
    img.setTemplate_(False)
    return img


# ─── Helpers système ──────────────────────────────────────────────────────────
def _get_network_info():
    try:
        out = subprocess.run(["netstat", "-rn", "-f", "inet"],
                             capture_output=True, text=True, timeout=2).stdout
        gw = iface = ""
        for ln in out.splitlines():
            p = ln.split()
            if len(p) >= 4 and p[0] == "default" and p[-1].startswith("en"):
                if not p[1].startswith("link#"):
                    gw = p[1]; iface = p[-1]; break
        if not iface:
            for c in ("en0","en1","en2","en3"):
                ip = subprocess.run(["ipconfig","getifaddr",c],
                                    capture_output=True,text=True,timeout=1
                                    ).stdout.strip()
                if ip: return ip, gw
        ip = subprocess.run(["ipconfig","getifaddr",iface],
                            capture_output=True,text=True,timeout=1).stdout.strip()
        return ip, gw
    except Exception:
        return "", ""

def _get_cpu_temp():
    try:
        r = subprocess.run(["osx-cpu-temp"],capture_output=True,text=True,timeout=1)
        if r.returncode == 0 and r.stdout.strip(): return r.stdout.strip()
    except Exception: pass
    return "—"

def _get_music():
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
        return subprocess.run(["osascript","-e",script],
                              capture_output=True,text=True,timeout=2).stdout.strip()
    except Exception: return ""

def _get_ping():
    try:
        r = subprocess.run(["ping","-c","1","-t","1","8.8.8.8"],
                           capture_output=True,text=True,timeout=2)
        m = re.search(r"time=(\d+\.?\d*)\s*ms", r.stdout)
        if m: return f"{float(m.group(1)):.0f} ms"
    except Exception: pass
    return "—"

def _get_mem_pressure():
    try:
        r = subprocess.run(["memory_pressure"],capture_output=True,text=True,timeout=3)
        for ln in r.stdout.splitlines():
            if "System-wide memory free percentage" in ln:
                m = re.search(r"(\d+)%", ln)
                if m:
                    p = int(m.group(1))
                    return "Critique" if p < 15 else "Avertissement" if p < 30 else "Normal"
    except Exception: pass
    return "—"

def _get_wifi_info():
    ap = ("/System/Library/PrivateFrameworks/Apple80211.framework"
          "/Versions/Current/Resources/airport")
    try:
        r = subprocess.run([ap,"-I"],capture_output=True,text=True,timeout=2)
        ssid = ""; rssi = 0
        for ln in r.stdout.splitlines():
            ln = ln.strip()
            if ln.startswith("SSID:") and not ln.startswith("BSSID"):
                ssid = ln.split(":",1)[1].strip()
            elif ln.startswith("agrCtlRSSI:"):
                try: rssi = int(ln.split(":")[1].strip())
                except ValueError: pass
        return ssid, rssi
    except Exception: return "", 0

def _get_batt_health():
    try:
        r = subprocess.run(["ioreg","-l","-n","AppleSmartBattery"],
                           capture_output=True,text=True,timeout=3)
        mx = re.search(r'"MaxCapacity"\s*=\s*(\d+)', r.stdout)
        ds = re.search(r'"DesignCapacity"\s*=\s*(\d+)', r.stdout)
        if mx and ds:
            return min(100, int(int(mx.group(1)) / int(ds.group(1)) * 100))
    except Exception: pass
    return -1

def _get_gpu_usage():
    try:
        r = subprocess.run(["ioreg","-r","-c","IOAccelerator","-d","2"],
                           capture_output=True,text=True,timeout=2)
        m = re.search(r'"GPU Activity"\s*=\s*(\d+)', r.stdout)
        if m: return int(m.group(1))
    except Exception: pass
    return -1

def _parse_cal_event(dt, title, now_dt):
    """Construit un dict d'événement depuis un datetime + titre."""
    today = now_dt.date()
    tmrw  = (now_dt + timedelta(days=1)).date()
    return {
        "title":       title,
        "time":        dt.strftime("%H:%M"),
        "day":         ("Auj." if dt.date() == today else
                        "Dem." if dt.date() == tmrw  else
                        DAYS_FR[dt.weekday()]),
        "is_today":    dt.date() == today,
        "is_tomorrow": dt.date() == tmrw,
        "dt":          dt,
    }


_GCALCLI = None
for _p in [
    os.path.expanduser("~/.local/bin/gcalcli"),
    "/usr/local/bin/gcalcli",
    "/opt/homebrew/bin/gcalcli",
]:
    if os.path.exists(_p):
        _GCALCLI = _p
        break
if _GCALCLI is None:
    _GCALCLI = "gcalcli"  # last resort: rely on PATH


def _get_calendar_events(n=5):
    """Google Calendar via gcalcli, fallback Apple Calendar via JXA."""
    now_dt = datetime.now()

    # ── Google Calendar (gcalcli) ──────────────────────────────────────────
    try:
        end_str = (now_dt + timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%S")
        r = subprocess.run(
            [_GCALCLI, "agenda", "--nostarted", "--tsv",
             now_dt.strftime("%Y-%m-%dT%H:%M:%S"), end_str],
            capture_output=True, text=True, timeout=10
        )
        if r.returncode == 0 and r.stdout.strip():
            events = []
            for line in r.stdout.strip().splitlines():
                parts = line.split('\t')
                if len(parts) < 4:
                    continue
                try:
                    dt = datetime.strptime(
                        f"{parts[0]} {parts[1]}", "%Y-%m-%d %H:%M")
                    if dt >= now_dt:
                        events.append(
                            _parse_cal_event(dt, parts[3].strip(), now_dt))
                except ValueError:
                    continue
            return sorted(events, key=lambda e: e["dt"])[:n]
    except FileNotFoundError:
        pass   # gcalcli pas installé → fallback
    except Exception:
        pass

    # ── Fallback : Apple Calendar via JXA ─────────────────────────────────
    script = f"""
var app = Application('Calendar');
var now = new Date();
var end = new Date(+now + 5*86400000);
var res = [];
app.calendars().forEach(function(cal) {{
    try {{
        cal.events().forEach(function(evt) {{
            var s = evt.startDate();
            if (s >= now && s <= end)
                res.push({{title: evt.summary(), ts: s.getTime()}});
        }});
    }} catch(e) {{}}
}});
res.sort(function(a,b){{return a.ts-b.ts;}});
JSON.stringify(res.slice(0,{n}));
"""
    try:
        r = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                           capture_output=True, text=True, timeout=6)
        raw = json.loads(r.stdout.strip())
        events = []
        for e in raw:
            dt = datetime.fromtimestamp(e["ts"] / 1000.0)
            events.append(_parse_cal_event(dt, e.get("title", ""), now_dt))
        return events
    except Exception:
        return []

def _top_procs(n=6):
    procs = []
    for p in psutil.process_iter(["pid","name","cpu_percent","memory_percent"]):
        try:
            i = p.info
            procs.append({"name": i["name"] or "?",
                          "cpu":  i["cpu_percent"] or 0.0,
                          "mem":  i["memory_percent"] or 0.0})
        except (psutil.NoSuchProcess, psutil.AccessDenied): continue
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:n]

def _ensure_launchagent():
    if not os.path.exists(PLIST_PATH): return
    if subprocess.run(["launchctl","list",PLIST_LABEL],
                      capture_output=True).returncode != 0:
        subprocess.run(["launchctl","load","-w",PLIST_PATH],capture_output=True)


# ─── Application ──────────────────────────────────────────────────────────────
_app = None; _panel_view = None; _menu = None; _S: dict = {}

class MacMonitorPro(rumps.App):

    def __init__(self):
        global _app
        super().__init__("", quit_button=None)
        _app = self

        self._t = 0.0; self._blink = False
        self._state = "chill"; self._dl = 0.0; self._ul = 0.0; self._cpu = 0.0

        self._cpu_hist = deque([0.0]*HIST, maxlen=HIST)
        self._ram_hist = deque([0.0]*HIST, maxlen=HIST)
        self._dl_hist  = deque([0.0]*HIST, maxlen=HIST)
        self._ul_hist  = deque([0.0]*HIST, maxlen=HIST)

        self._prev_disk = psutil.disk_io_counters()
        psutil.cpu_percent()
        self._prev_net  = psutil.net_io_counters()
        self._prev_time = time.time()

        self._last_notif: dict = {}
        self._nc   = psutil.cpu_count(logical=False) or 0
        self._nt   = psutil.cpu_count(logical=True)  or 0
        self._boot = psutil.boot_time()

        self._local_ip, self._gateway = _get_network_info()
        self._net_upd = time.time()

        self._disk_pct = 0.0; self._disk_info = ""; self._disk_last = 0.0
        self._top_cache: list = []; self._top_last = 0.0

        self._caff_proc  = None
        self._pomo_end   = 0.0
        self._party_end  = 0.0
        self._dl_hi_since = 0.0

        # Timer réseau dédié 1 s
        self._net_prev = psutil.net_io_counters()
        self._net_time = time.time()

        # Caches
        self._temp  = "—"; self._temp_t  = 0.0
        self._music = "";   self._music_t = 0.0
        self._ping  = "—"; self._ping_t  = 0.0
        self._mpres = "—"; self._mpres_t = 0.0
        self._wifi  = ("",0); self._wifi_t = 0.0
        self._bhealth = -1; self._bhealth_t = 0.0
        self._gpu   = -1;   self._gpu_t   = 0.0
        self._cal   = [];   self._cal_t   = 0.0

        self._setup_done = False
        _ensure_launchagent()

    @rumps.timer(0.05)
    def _late_init(self, timer):
        if self._setup_done: return
        try: nsitem = self._nsapp.nsstatusitem
        except AttributeError: return
        self._setup_done = True; timer.stop()

        global _panel_view, _menu
        view        = PanelView.alloc().initWithFrame_(NSMakeRect(0, 0, PW, PH))
        _panel_view = view

        item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("",None,"")
        item.setView_(view)
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.addItem_(item)
        menu.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        _menu = menu
        nsitem.setMenu_(menu)
        nsitem.button().setImagePosition_(2)

        rl = NSRunLoop.mainRunLoop()
        td = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, view, "refreshDisplay:", None, True)
        rl.addTimer_forMode_(td, "NSRunLoopCommonModes")

        self._stats_d = _StatsDelegate.alloc().init()
        self._stats_d._app_ref = self
        ts = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            2.0, self._stats_d, "statsRefresh:", None, True)
        rl.addTimer_forMode_(ts, "NSRunLoopCommonModes")

    @rumps.timer(0.2)
    def _animate(self, _):
        self._t     = (self._t + 0.1) % 1.0
        self._blink = ((self._t % 0.35) < 0.06)
        if not self._setup_done: return
        now   = time.time()
        party = now < self._party_end
        img   = draw_character(self._t, self._state, self._blink, party=party)
        btn   = self._nsapp.nsstatusitem.button()
        btn.setImage_(img)
        if self._pomo_end > 0:
            left  = max(0.0, self._pomo_end - now)
            title = f"  ⏰ {int(left)//60:02d}:{int(left)%60:02d}"
            if now >= self._pomo_end:
                self._pomo_end = 0.0
                self._notify("pomo", "Pomodoro terminé! 🍅",
                             "25 minutes — prenez une pause ☕")
        else:
            title = f"  {self._cpu:.0f}%  ↓{_b(self._dl)}/s"
        btn.setTitle_(title)

    @rumps.timer(1.0)
    def _net_tick(self, _):
        """Mise à jour vitesse réseau chaque seconde."""
        now = time.time()
        net = psutil.net_io_counters()
        dt  = max(now - self._net_time, 0.05)
        self._dl = (net.bytes_recv - self._net_prev.bytes_recv) / dt
        self._ul = (net.bytes_sent - self._net_prev.bytes_sent) / dt
        self._net_prev = net
        self._net_time = now
        self._dl_hist.append(min(self._dl / 10_000_000 * 100, 100))
        self._ul_hist.append(min(self._ul /  2_000_000 * 100, 100))
        _S['dl_str']  = f"{_b(self._dl)}/s"
        _S['ul_str']  = f"{_b(self._ul)}/s"
        _S['dl_hist'] = list(self._dl_hist)
        _S['ul_hist'] = list(self._ul_hist)
        if _panel_view and getattr(_panel_view, '_tab', '') == 'net':
            _panel_view.setNeedsDisplay_(True)

    @rumps.timer(2.0)
    def _update_stats(self, _): self._do_stats()

    def _do_stats(self):
        now = time.time()
        cpu = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        self._cpu = cpu; self._cpu_hist.append(cpu)

        vm = psutil.virtual_memory(); self._ram_hist.append(vm.percent)

        if now - self._disk_last > 30:
            try:
                out = subprocess.run(["diskutil","info","/"],
                                     capture_output=True,text=True,timeout=4).stdout
                tot  = float(re.search(r"Container Total Space:\s+([\d.]+) GB",out).group(1))
                fre  = float(re.search(r"Container Free Space:\s+([\d.]+) GB", out).group(1))
                used = tot - fre
                self._disk_pct  = used / tot * 100
                self._disk_info = f"{used:.1f} G / {tot:.1f} G"
            except Exception:
                d = psutil.disk_usage("/")
                self._disk_pct  = d.percent
                self._disk_info = f"{_b(d.used)} / {_b(d.total)}"
            self._disk_last = now

        nd = psutil.disk_io_counters()
        dt_io = max(now - self._prev_time, 0.1)
        dr = (nd.read_bytes  - self._prev_disk.read_bytes)  / dt_io if nd and self._prev_disk else 0
        dw = (nd.write_bytes - self._prev_disk.write_bytes) / dt_io if nd and self._prev_disk else 0
        self._prev_disk = nd

        net = psutil.net_io_counters()
        self._prev_time = now

        # Notification téléchargement (utilise _dl calculé par _net_tick)
        if self._dl > DL_HI:
            if self._dl_hi_since == 0: self._dl_hi_since = now
        elif self._dl < DL_LO and self._dl_hi_since > 0:
            if now - self._dl_hi_since > 3:
                self._notify("dl", "Téléchargement terminé ✓",
                             "La vitesse est retombée")
            self._dl_hi_since = 0

        batt = psutil.sensors_battery()
        up   = str(timedelta(seconds=int(now - self._boot)))

        self._state = ("panic" if cpu >= 80 else "hot" if cpu >= 60
                       else "busy" if cpu >= 35 else "chill")

        if now - self._net_upd    > 30:  self._local_ip, self._gateway = _get_network_info(); self._net_upd = now
        if now - self._top_last   > 8:   self._top_cache = _top_procs(6); self._top_last = now
        if now - self._temp_t     > 30:  self._temp  = _get_cpu_temp();   self._temp_t  = now
        if now - self._music_t    > 8:   self._music = _get_music();      self._music_t = now
        if now - self._ping_t     > 20:  self._ping  = _get_ping();       self._ping_t  = now
        if now - self._mpres_t    > 15:  self._mpres = _get_mem_pressure(); self._mpres_t = now
        if now - self._wifi_t     > 15:  self._wifi  = _get_wifi_info();  self._wifi_t  = now
        if now - self._bhealth_t  > 300: self._bhealth = _get_batt_health(); self._bhealth_t = now
        if now - self._gpu_t      > 3:   self._gpu   = _get_gpu_usage();  self._gpu_t   = now
        if now - self._cal_t      > 300: self._cal   = _get_calendar_events(5); self._cal_t = now

        freq_s = f"{freq.current:.0f} MHz" if freq else "—"

        _S.update({
            "cpu":          cpu,
            "cpu_info":     f"{self._nc}C · {self._nt}T · {freq_s}",
            "cpu_temp":     self._temp,
            "cpu_hist":     list(self._cpu_hist),
            "gpu":          self._gpu,
            "ram":          vm.percent,
            "ram_info":     f"{_b(vm.total - vm.available)} / {_b(vm.total)}",
            "ram_hist":     list(self._ram_hist),
            "mem_pressure": self._mpres,
            "net_total_dl": _b(net.bytes_recv),
            "net_total_ul": _b(net.bytes_sent),
            "wifi_ssid":    self._wifi[0],
            "wifi_rssi":    self._wifi[1],
            "disk_pct":     self._disk_pct,
            "disk_info":    self._disk_info,
            "disk_r":       f"{_b(dr)}/s",
            "disk_w":       f"{_b(dw)}/s",
            "uptime":       f"up {up}",
            "local_ip":     self._local_ip,
            "gateway":      self._gateway,
            "ping":         self._ping,
            "music":        self._music,
            "top_procs":    self._top_cache,
            "batt_health":  self._bhealth,
            "cal_events":   self._cal,
            "party":        time.time() < self._party_end,
        })
        if batt:
            ts = ("∞" if batt.secsleft == psutil.POWER_TIME_UNLIMITED
                  else "…" if batt.secsleft == psutil.POWER_TIME_UNKNOWN
                  else str(timedelta(seconds=int(batt.secsleft))))
            _S.update({
                "batt_pct":  batt.percent,
                "batt_plug": batt.power_plugged,
                "batt_time": "⚡ branché" if batt.power_plugged else f"{ts} restant",
            })
        else:
            _S["batt_pct"] = None
        if _panel_view: _panel_view.setNeedsDisplay_(True)
        self._check_notifs(cpu, vm.percent, batt)

    def toggle_caff(self):
        if self._caff_proc and self._caff_proc.poll() is None:
            self._caff_proc.terminate(); self._caff_proc = None
            rumps.notification("Anti-veille désactivé","MAC Monitor","",sound=False)
        else:
            self._caff_proc = subprocess.Popen(["caffeinate","-di"])
            rumps.notification("Anti-veille activé 💤","MAC Monitor",
                               "Le Mac ne se mettra pas en veille",sound=False)

    def toggle_pomo(self):
        if self._pomo_end > 0:
            self._pomo_end = 0.0
            rumps.notification("Pomodoro annulé","MAC Monitor","",sound=False)
        else:
            self._pomo_end = time.time() + POMO_DUR
            rumps.notification("Pomodoro démarré ⏰","MAC Monitor",
                               "25 minutes — bonne concentration!",sound=False)

    def copy_stats(self):
        s = _S
        lines = [f"MAC Monitor Pro — {time.strftime('%H:%M:%S')}",
                 f"CPU : {s.get('cpu',0):.1f}%  {s.get('cpu_info','')}"]
        if s.get('cpu_temp','—') != '—': lines[-1] += f"  {s['cpu_temp']}"
        if s.get('gpu',-1) >= 0: lines[-1] += f"  GPU {s['gpu']}%"
        lines += [
            f"RAM : {s.get('ram',0):.1f}%  {s.get('ram_info','')}  pression: {s.get('mem_pressure','—')}",
            f"Réseau : ↓ {s.get('dl_str','—')}  ↑ {s.get('ul_str','—')}  ping {s.get('ping','—')}",
            f"Stockage : {s.get('disk_pct',0):.1f}%  {s.get('disk_info','')}",
            f"IP : {s.get('local_ip','—')}  GW : {s.get('gateway','—')}",
        ]
        if s.get('wifi_ssid'):
            lines.append(f"WiFi : {s['wifi_ssid']}  RSSI {s.get('wifi_rssi',0)} dBm")
        if s.get('batt_pct') is not None:
            hs = f"  santé {s['batt_health']}%" if s.get('batt_health',-1) > 0 else ""
            lines.append(f"Batterie : {s['batt_pct']:.0f}%{hs}  {s.get('batt_time','')}")
        if s.get('music'): lines.append(f"Musique : {s['music']}")
        if s.get('cal_events'):
            lines.append("Calendrier :")
            for ev in s['cal_events']:
                lines.append(f"  {ev['day']} {ev['time']} — {ev['title']}")
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        pb.setString_forType_("\n".join(lines), "public.utf8-plain-text")
        rumps.notification("Stats copiées 📋","MAC Monitor","",sound=False)

    def _notify(self, key, title, msg):
        now = time.time()
        if now - self._last_notif.get(key, 0) > NOTIF_CD:
            self._last_notif[key] = now
            rumps.notification(title=title, subtitle="MAC Monitor Pro",
                               message=msg, sound=False)

    def _check_notifs(self, cpu, ram, batt):
        if cpu >= 90: self._notify("cpu", "CPU surchargé 🔥", f"{cpu:.0f}%")
        if ram >= 90: self._notify("ram", "Mémoire saturée", f"{ram:.0f}%")
        if batt and not batt.power_plugged and batt.percent <= 10:
            self._notify("bat", "Batterie faible 🔋",
                         f"{batt.percent:.0f}% — branchez votre Mac")
        if self._temp != "—":
            try:
                t = float(re.sub(r"[^\d.]","",self._temp.split("°")[0]))
                if t >= TEMP_ALERT:
                    self._notify("tmp","Température CPU élevée 🌡️",
                                 f"{self._temp} — vérifiez la ventilation")
            except (ValueError, IndexError): pass

        # Notifications réunions Google Calendar
        now_dt = datetime.now()
        for ev in self._cal:
            ev_dt = ev.get('dt')
            if not ev_dt or not ev.get('is_today'):
                continue
            diff  = (ev_dt - now_dt).total_seconds()
            title = ev.get('title', 'Réunion')[:30]
            key   = f"mtg_{title}"
            if 0 < diff <= 300:          # ≤ 5 min
                self._notify(key, f"📅 Réunion dans {int(diff/60)+1} min", title)
            elif 300 < diff <= 900:      # 5–15 min
                self._notify(key + "_15", f"📅 Réunion dans ~15 min", title)


if __name__ == "__main__":
    MacMonitorPro().run()
