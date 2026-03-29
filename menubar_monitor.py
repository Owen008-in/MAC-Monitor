#!/usr/bin/env python3
"""MAC Monitor Pro v7 — Interface à onglets, design pro."""

import colorsys, json, math, os, re, subprocess, time, threading, psutil, objc, rumps
import urllib.request
from collections import deque
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from Foundation import NSMakeRect, NSMakeSize, NSMakePoint, NSTimer, NSRunLoop, NSObject
from AppKit import (
    NSMenuItem, NSMenu, NSView, NSFont, NSColor, NSBezierPath,
    NSAttributedString, NSForegroundColorAttributeName, NSFontAttributeName,
    NSImage, NSRectFill, NSPasteboard, NSAppearance,
    NSPanel, NSEvent,
)

# ─── Dimensions ───────────────────────────────────────────────────────────────
PW          = 320
PAD         = 20
INNER_W     = PW - PAD * 2          # 280
CORNER      = 14
HDR_H       = 52
TAB_H       = 36
TAB_COUNT   = 4
TAB_W       = INNER_W / TAB_COUNT   # 70
Y_FLOOR     = 14

# Hauteur par onglet (calculée pour que tout le contenu rentre)
PH_BY_TAB = {
    "sys":  520,   # cpu(136) + gap + ram(116) + gap + batt+tz(106) + marges
    "net":  540,   # réseau(188) + disque(130) + speedtest(88) + marges
    "cal":  490,   # cal(62+) + volume(36) + musique(84) + météo(58) + marges
    "proc": 720,   # 10 procs(284) + actions(48) + icônes(160) + thèmes(54) + quitter(34)
}
PH          = PH_BY_TAB["sys"]   # hauteur initiale (onglet sys par défaut)

def _y_content(ph):
    """Coordonnée y de départ du contenu pour une hauteur de panel donnée."""
    return ph - HDR_H - 8 - TAB_H - 14

HIST        = 60
NOTIF_CD    = 300
TITLE_MODES = ["cpu", "ram", "net", "clock"]
_TITLE_MODE = "cpu"
POMO_DUR    = 25 * 60
DL_HI       = 1_000_000
DL_LO       =   100_000
TEMP_ALERT  = 90

TABS        = ["sys", "net", "cal", "proc"]
TAB_LABEL   = {"sys": "Système", "net": "Réseau",
               "cal": "Utils",   "proc": "Process"}

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

C_SYS   = _c(0.30, 0.62, 1.00)   # bleu — CPU / Process
C_NET   = _c(0.18, 0.84, 0.94)   # cyan — réseau download
C_CAL   = _c(0.55, 0.42, 1.00)   # violet — utils tab
C_PROC  = _c(0.85, 0.85, 0.92)   # blanc cassé — process tab
C_RAM   = _c(0.28, 0.85, 0.72)   # teal mint — mémoire / MEM%
C_DSK   = _c(1.00, 0.50, 0.25)   # orange-coral — stockage
C_BAT   = _c(0.98, 0.78, 0.28)   # amber — batterie / système
C_MUS   = _c(1.00, 0.42, 0.72)   # rose — musique
C_UL    = _c(0.78, 0.55, 1.00)   # lavande — upload réseau
C_WEA   = _c(1.00, 0.78, 0.15)   # or — météo

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

def _bar(x, y, w, h, value, accent, force=False):
    """Barre de progression arrondie avec glow."""
    r = h / 2.0
    C_DIM.setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(x, y, w, h), r, r).fill()
    if value <= 0: return
    fw    = max(r * 2, w * min(value, 100) / 100.0)
    color = accent if force else _bar_col(value, accent)
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

def _big_value(x, y, value, unit, accent, force=False):
    """Grosse valeur numérique + unité."""
    vs = f"{value:.1f}"
    _draw(vs, x, y, accent if force else _bar_col(value, accent), F_VALUE)
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
    _icon_hover: str = ""
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
        w  = PW
        h  = int(self.bounds().size.height)
        bw = INNER_W
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
        y = _y_content(h)
        if   self._tab == "sys":  self._draw_sys(y, bw, s, w, rects)
        elif self._tab == "net":  self._draw_net(y, bw, s, w, rects)
        elif self._tab == "cal":  self._draw_cal(y, bw, s, w, rects)
        elif self._tab == "proc": self._draw_proc(y, bw, s, w, rects)

        self._btn_rects = rects

    # ── Header ───────────────────────────────────────────────
    def _draw_header(self, w, h, bw, s, rects):
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
        _draw_c("MAC Monitor", w / 2, h - HDR_H + 18, C_WHITE, F_TITLE)
        rects["title_mode"] = NSMakeRect(PAD + 40, h - HDR_H + 8, w - PAD*2 - 60, HDR_H - 16)
        focus = s.get('focus')
        if focus:
            flbl = "🎯 DND" if focus == "Ne pas déranger" else f"🎯 {focus[:8]}"
            _draw_r(flbl, w - PAD - 22, h - HDR_H + 18, C_CAL, F_SM)
        else:
            _draw_r("Pro", w - PAD - 22, h - HDR_H + 18, _c(1, 1, 1, 0.28), F_SM)

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
        # Historique long (6 min)
        _spark(PAD + 90, vy - 12, bw - 100, 14,
               s.get('cpu_hist_long', []), C_SYS.colorWithAlphaComponent_(0.45))

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

        y -= cpu_card_h + 18

        # ── RAM Card ──────────────────────────────────
        # Label basé sur le % RAM réel (mis à jour toutes les 2s)
        if ram >= 85:
            mem_label = "Critique"; pres_col = C_RED
        elif ram >= 70:
            mem_label = "Élevé";    pres_col = C_ORA
        else:
            mem_label = "Normal";   pres_col = C_GREEN
        _card(PAD, y, bw, 98, C_RAM)
        _section_label(PAD + 10, y - 14, "MÉMOIRE", C_RAM)
        pc = pres_col.colorWithAlphaComponent_(0.85)
        _draw_r(f"● {mem_label}", w - PAD - 10, y - 14, pc, F_SM)

        vy = y - 50
        _big_value(PAD + 10, vy, ram, "%", pres_col, force=True)
        _spark(PAD + 90, vy + 2, bw - 100, 28,
               s.get('ram_hist', []), pres_col)
        _bar(PAD + 10, vy - 14, bw - 20, 7, ram, pres_col, force=True)
        _draw(s.get('ram_info', ''), PAD + 10, vy - 28, C_GRAY, F_SM)

        y -= 98 + 18

        # ── Batterie / Système Card ────────────────────
        has_b  = bpct is not None
        wtimes = s.get('world_times', [])
        wt_h   = 18 if wtimes else 0
        bc_h   = (88 if has_b else 46) + wt_h
        _card(PAD, y, bw, bc_h, C_BAT, alpha=0.06)
        _section_label(PAD + 10, y - 14, "SYSTÈME", C_BAT)
        _draw_r(f"⏱ {s.get('uptime','—')}", w - PAD - 10, y - 14,
                _c(1, 1, 1, 0.28), F_SM)
        if has_b:
            bp   = bpct
            icon = "⚡" if s.get('batt_plug') else "🔋"
            bp_col = C_GREEN if bp > 50 else C_ORA if bp > 20 else C_RED
            _draw(f"{icon}  {bp:.0f}%", PAD + 10, y - 32, bp_col, F_INFO)
            _bar(PAD + 10, y - 46, bw - 20, 8, bp, C_BAT)
            batt_t = s.get('batt_time', '')
            _draw(batt_t, PAD + 10, y - 62, C_GRAY, F_SM)
            bh = s.get('batt_health', -1)
            if bh > 0:
                hc = C_GREEN if bh >= 80 else C_ORA if bh >= 60 else C_RED
                _draw_r(f"Santé {bh}%", w - PAD - 10, y - 62, hc, F_SM)
            if bh > 0:
                _bar(PAD + 10, y - 76, bw - 20, 4, bh,
                     C_GREEN if bh >= 80 else C_ORA if bh >= 60 else C_RED)
        else:
            _draw("Pas de batterie détectée", PAD + 10, y - 32,
                  _c(1, 1, 1, 0.25), F_SM)
        # Fuseaux horaires (en bas de la card, sous tout le contenu batterie)
        if wtimes:
            base_y = y - bc_h + 10   # 10px depuis le bas de la card
            _sep(base_y + 11, PAD + 10, PAD + bw - 10)
            tz_txt = "   ·   ".join(f"{lbl} {t}" for lbl, t in wtimes[:3])
            _draw_c(tz_txt, w / 2, base_y, _c(1, 1, 1, 0.30), SF(9, 0.0))

    # ── Page 2 : Réseau ──────────────────────────────────────
    def _draw_net(self, y, bw, s, w, rects):
        # ── Réseau Card ───────────────────────────────
        ip   = s.get('local_ip', '—')
        gw   = s.get('gateway', '')
        ping = s.get('ping', '—')
        ssid = s.get('wifi_ssid', '')
        rssi = s.get('wifi_rssi', 0)
        vpn  = s.get('vpn')

        net_h = 164 if vpn else 146
        _card(PAD, y, bw, net_h, C_NET)
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
        _draw("↑", PAD + 20 + hw, y - 44, C_UL, SF(18, 0.3))
        _draw(ul, PAD + 40 + hw, y - 44, C_WHITE, SF(15, -0.2))

        # Sparklines côte à côte
        sy = y - 80
        _spark(PAD + 10, sy, hw - 5, 22, s.get('dl_hist', []), C_NET)
        _spark(PAD + 15 + hw, sy, hw - 5, 22, s.get('ul_hist', []), C_UL)

        # WiFi + ping
        wy = y - 110
        _wifi_bars(PAD + 10, wy, rssi)
        if ssid:
            _draw(ssid[:18], PAD + 36, wy, C_GRAY, F_SM)
        _draw_r(f"🏓 {ping}" if ping != '—' else '',
                w - PAD - 10, wy, C_NET.colorWithAlphaComponent_(0.70), F_SM)

        tot_y = y - 128
        _draw(f"Total  ↓ {s.get('net_total_dl','—')}   ↑ {s.get('net_total_ul','—')}",
              PAD + 10, tot_y, _c(1, 1, 1, 0.28), F_SM)

        # VPN
        if vpn:
            _sep(y - 134, PAD + 10, PAD + bw - 10)
            C_GREEN.setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(PAD + 10, y - 150, 7, 7)).fill()
            _draw(f"VPN  {vpn}", PAD + 22, y - 150, C_GREEN, F_SM)

        y -= net_h + 24

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
        _draw(f"R  {s.get('disk_r','0 B/s')}", PAD + 10, ioy, C_NET, F_SM)
        _draw_r(f"W  {s.get('disk_w','0 B/s')}", w - PAD - 10, ioy,
                C_UL, F_SM)

        y -= 112 + 18

        # ── Speedtest Card ────────────────────────────
        st_state = s.get('stest_state', 'idle')
        st_h = 70
        _card(PAD, y, bw, st_h, C_NET, alpha=0.05)
        _section_label(PAD + 10, y - 14, "SPEED TEST", C_NET)

        # Bouton relancer (sauf si en cours)
        if st_state != "running" and _app:
            rbg = (C_NET.colorWithAlphaComponent_(0.25) if self._hover == "stest_run"
                   else C_NET.colorWithAlphaComponent_(0.10))
            rects["stest_run"] = _btn(w - PAD - 64, y - 18, 64, 16,
                                      "▶ Relancer", rbg, C_NET, r=7)

        if st_state == "running":
            # Animation "En cours"
            dots = "." * (int(time.time() * 2) % 4)
            _draw_c(f"Mesure en cours{dots}", w / 2, y - 40,
                    _c(1, 1, 1, 0.45), F_SM)
        elif st_state in ("done",):
            dl_m = s.get('stest_dl', 0) / 1_000_000
            ul_m = s.get('stest_ul', 0) / 1_000_000
            rtt  = s.get('stest_rtt', 0)
            dl_c = C_GREEN if dl_m >= 50 else C_ORA if dl_m >= 10 else C_RED
            ul_c = C_GREEN if ul_m >= 20 else C_ORA if ul_m >= 5  else C_RED
            _draw("↓", PAD + 10, y - 36, dl_c, SF(14, 0.2))
            _draw(f"{dl_m:.0f} Mbps", PAD + 26, y - 36, C_WHITE, F_INFO)
            _draw("↑", PAD + 10, y - 54, ul_c, SF(14, 0.2))
            _draw(f"{ul_m:.0f} Mbps", PAD + 26, y - 54, C_WHITE, F_INFO)
            _draw_r(f"RTT {rtt:.0f} ms", w - PAD - 10, y - 36, C_GRAY, F_SM)
            ago = int(time.time() - s.get('stest_t', time.time()))
            _draw_r(f"il y a {ago}s" if ago < 120 else "", w - PAD - 10, y - 54,
                    _c(1, 1, 1, 0.25), SF(8, 0.0))
        elif st_state == "error":
            _draw_c("Échec — réseau indisponible ?", w / 2, y - 44,
                    C_RED.colorWithAlphaComponent_(0.70), F_SM)
        else:
            _draw_c("Démarrage au prochain refresh…", w / 2, y - 44,
                    _c(1, 1, 1, 0.25), F_SM)

    # ── Page 3 : Agenda ──────────────────────────────────────
    def _draw_cal(self, y, bw, s, w, rects):
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

        y -= cal_h + 18

        # ── Volume ────────────────────────────────────
        vol = s.get('volume', -1)
        if vol >= 0:
            vh = 36
            _card(PAD, y, bw, vh, C_MUS, alpha=0.05)
            _draw(f"{'🔇' if vol == 0 else '🔊'}", PAD + 10, y - 24, C_WHITE, SF(11, 0.0))
            _draw("Volume", PAD + 28, y - 24, C_GRAY, F_SM)
            bar_x = PAD + 75; bar_w = bw - 75 - 90; bar_h = 5
            bar_y = y - 23
            _c(1,1,1,0.08).setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(bar_x, bar_y, bar_w, bar_h), 2.5, 2.5).fill()
            if vol > 0:
                fw = bar_w * vol / 100
                vc = C_RED if vol > 80 else C_ORA if vol > 60 else C_MUS
                vc.setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(bar_x, bar_y, fw, bar_h), 2.5, 2.5).fill()
            _draw_r(f"{vol}%", bar_x + bar_w + 38, y - 24, C_GRAY, F_SM)
            bsz = 22
            for btn_name, lbl, bx in (
                ("vol_dn", "−", bar_x + bar_w + 40),
                ("vol_up", "+", bar_x + bar_w + 64),
            ):
                hbg = _c(1,1,1,0.14) if self._hover == btn_name else _c(1,1,1,0.07)
                rects[btn_name] = _btn(bx, y - vh + 7, bsz, bsz, lbl, hbg, C_WHITE, r=5)
            y -= vh + 14
        else:
            y -= 14

        # ── Musique Card ──────────────────────────────
        music = s.get('music', '')
        _card(PAD, y, bw, 84, C_MUS, alpha=0.06)
        _section_label(PAD + 10, y - 14, "MUSIQUE", C_MUS)
        if music:
            _draw_r("♪", w - PAD - 10, y - 14,
                    C_MUS.colorWithAlphaComponent_(0.80), F_SM)

        if music:
            parts  = music.split(" — ", 1)
            track  = parts[0] if parts else music
            artist = parts[1] if len(parts) > 1 else ""
            if len(track)  > 28: track  = track[:27]  + "…"
            if len(artist) > 32: artist = artist[:31] + "…"
            _draw(track,  PAD + 10, y - 32, C_WHITE, F_INFO)
            if artist:
                _draw(artist, PAD + 10, y - 46, C_GRAY, F_SM)
        else:
            _draw("—", PAD + 10, y - 32, _c(1, 1, 1, 0.20), F_INFO)

        # Boutons prev / play / next
        mbw = (bw - 24) // 3
        _btn(PAD + 8,             y - 74, mbw, 22, "◀◀  Préc",
             _c(1, 1, 1, 0.08), C_GRAY, r=7)
        _btn(PAD + 8 + mbw + 4,   y - 74, mbw, 22, "⏯  Play",
             _c(1, 1, 1, 0.08), C_MUS,  r=7)
        _btn(PAD + 8 + (mbw+4)*2, y - 74, mbw, 22, "Suiv  ▶▶",
             _c(1, 1, 1, 0.08), C_GRAY, r=7)
        rects["music_prev"] = NSMakeRect(PAD + 8,             y - 74, mbw, 22)
        rects["music_play"] = NSMakeRect(PAD + 8 + mbw + 4,   y - 74, mbw, 22)
        rects["music_next"] = NSMakeRect(PAD + 8 + (mbw+4)*2, y - 74, mbw, 22)

        y -= 84 + 14

        # ── Météo Card ────────────────────────────────
        weather = s.get('weather', '')
        _card(PAD, y, bw, 58, C_WEA, alpha=0.06)
        _section_label(PAD + 10, y - 14, "MÉTÉO", C_WEA)
        if weather:
            _draw(weather, PAD + 10, y - 36, C_WHITE, SF(14, 0.0))
        else:
            _draw("Chargement…", PAD + 10, y - 36, _c(1, 1, 1, 0.20), F_INFO)

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
        for i, proc_entry in enumerate(sorted_p):
            py   = y - 36 - i * 22
            cp   = proc_entry['cpu']; mp = proc_entry['mem']
            pid  = proc_entry.get('pid', 0)
            cc   = C_RED if cp > 50 else C_ORA if cp > 20 else C_WHITE
            mc   = C_RED if mp > 10 else C_ORA if mp > 5  else \
                   C_RAM.colorWithAlphaComponent_(0.75)
            kill_key = f"kill_{pid}"
            hovering = (self._hover == kill_key)
            if hovering:
                _c(1, 0.25, 0.22, 0.12).setFill()
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(PAD + 4, py - 4, bw - 8, 18), 4, 4).fill()
            _draw(proc_entry['name'][:20], PAD + 10, py, C_WHITE, F_MONO)
            _draw_r(f"{cp:5.1f}", w - PAD - 10,      py, cc, F_MONO)
            _draw_r(f"{mp:5.1f}", w - PAD - 10 - 44, py, mc, F_MONO)
            # Bouton kill (×) affiché au survol
            kill_bg = _c(1, 0.25, 0.22, 0.30) if hovering else _c(0,0,0,0)
            rects[kill_key] = _btn(PAD + 4, py - 3, 16, 16, "×" if hovering else "", kill_bg, C_RED, r=4)
            if i < n_p - 1:
                _sep(py - 5, PAD + 10, PAD + bw - 10)

        y -= proc_h + 24

        # ── Boutons d'action ──────────────────────────
        abh = 34
        abw = int((bw - 18) / 4)

        caff  = _app._caff_proc is not None and _app._caff_proc.poll() is None \
                if _app else False
        pomo  = (_app._pomo_end > 0) if _app else False

        caff_lbl = "💤 OFF" if caff else "💤 Veille"
        caff_bg  = (_c(0.18,0.84,0.40, 0.28) if self._hover == "caff" else
                    _c(0.18,0.84,0.40, 0.16) if caff else _c(1,1,1, 0.06))
        caff_fg  = C_GREEN if caff else C_GRAY
        rects["caff"] = _btn(PAD, y - abh, abw, abh,
                             caff_lbl, caff_bg, caff_fg, r=9)

        if pomo and _app:
            left = max(0.0, _app._pomo_end - time.time())
            pomo_lbl = f"⏰ {int(left)//60:02d}:{int(left)%60:02d}"
        else:
            pomo_lbl = "⏰ Pomo"
        pomo_bg = (_c(0.30,0.62,1.00, 0.28) if self._hover == "pomo" else
                   _c(0.30,0.62,1.00, 0.16) if pomo else _c(1,1,1, 0.06))
        pomo_fg = C_SYS if pomo else C_GRAY
        rects["pomo"] = _btn(PAD + abw + 6, y - abh, abw, abh,
                             pomo_lbl, pomo_bg, pomo_fg, r=9)

        flashing = time.time() < self._copy_flash
        copy_bg  = C_NET.colorWithAlphaComponent_(0.28) if flashing else (
                   _c(1, 1, 1, 0.12) if self._hover == "copy" else _c(1, 1, 1, 0.06))
        copy_lbl = "✓ Copié" if flashing else "📋 Copier"
        copy_fg  = C_NET if flashing else C_GRAY
        rects["copy"] = _btn(PAD + (abw + 6) * 2, y - abh, abw, abh,
                             copy_lbl, copy_bg, copy_fg, r=9)

        lock_bg = (_c(1.00, 0.25, 0.22, 0.28) if self._hover == "lock"
                   else _c(1, 1, 1, 0.06))
        lock_fg = C_RED if self._hover == "lock" else C_GRAY
        rects["lock"] = _btn(PAD + (abw + 6) * 3, y - abh, abw, abh,
                             "🔒 Lock", lock_bg, lock_fg, r=9)
        y -= abh + 14

        # ── Sélecteur d'icône ─────────────────────────
        n_cols   = 5
        icon_bw  = int((bw - (n_cols - 1) * 3) / n_cols)
        icon_bh  = 34
        icon_gap = 3
        n_rows   = (len(ICON_STYLES) + n_cols - 1) // n_cols
        # sel_h: couvre label(14) + gap(6) + lignes + padding(6)
        sel_h    = 44 + n_rows * (icon_bh + icon_gap) - icon_gap
        _card(PAD, y, bw, sel_h, C_PROC, alpha=0.04)
        _section_label(PAD + 10, y - 14, "ICÔNE MENUBAR", C_PROC)
        # row 0 top = y-18 (6px sous le bas du label à y-4), pas d'overlap
        for idx, (style, label) in enumerate(zip(ICON_STYLES, ICON_LABELS)):
            col_i = idx % n_cols
            row_i = idx // n_cols
            ix = PAD + col_i * (icon_bw + icon_gap)
            iy = y - 38 - row_i * (icon_bh + icon_gap)
            selected = (_ICON_STYLE == style)
            hov      = (self._icon_hover == style)
            if selected:
                ibg = C_PROC.colorWithAlphaComponent_(0.30)
                ifg = C_PROC
            elif hov:
                ibg = _c(1, 1, 1, 0.12)
                ifg = C_GRAY
            else:
                ibg = _c(1, 1, 1, 0.05)
                ifg = _c(1, 1, 1, 0.28)
            # Fond du bouton
            ibg.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(ix, iy, icon_bw, icon_bh), 5, 5).fill()
            if selected:
                ifg.colorWithAlphaComponent_(0.5).setStroke()
                brd = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(ix, iy, icon_bw, icon_bh), 5, 5)
                brd.setLineWidth_(0.8); brd.stroke()
            # Mini icône
            t_now = (_app._t if _app else 0.0)
            st_now = (_app._state if _app else "chill")
            bl_now = (_app._blink if _app else False)
            try:
                mini = _ICON_DRAW.get(style, _draw_robot)(t_now, st_now, bl_now, size=16)
                mini.drawInRect_fromRect_operation_fraction_(
                    NSMakeRect(ix + (icon_bw - 16) / 2, iy + 5, 16, 16),
                    NSMakeRect(0, 0, 16, 16), 2, 1.0)
            except Exception:
                pass
            # Label sous l'icône
            _draw_c(label, ix + icon_bw/2, iy + 1, ifg, SF(7.5, 0.0))
            rects[f"icon_{style}"] = NSMakeRect(ix, iy, icon_bw, icon_bh)
        y -= sel_h + 8

        # ── Thème ─────────────────────────────────────
        th_bw = int((bw - (len(THEMES) - 1) * 3) / len(THEMES))
        th_h  = 22
        _section_label(PAD + 10, y - 14, "THÈME", C_PROC)
        for ti, (theme, tlbl) in enumerate(zip(THEMES, THEME_LABELS)):
            tx = PAD + ti * (th_bw + 3)
            ty = y - 38
            tp = _THEME_PALETTES[theme]
            is_sel = (_THEME == theme)
            tbg = _c(*tp["bg"], 1.0) if is_sel else _c(*tp["bg"], 0.7)
            tac = _c(*tp["acc"], 1.0)
            tbg.setFill()
            NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(tx, ty, th_bw, th_h), 5, 5).fill()
            if is_sel:
                tac.colorWithAlphaComponent_(0.8).setStroke()
                brd = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(tx, ty, th_bw, th_h), 5, 5)
                brd.setLineWidth_(1.2); brd.stroke()
            _draw_c(tlbl, tx + th_bw/2, ty + (th_h - 11)/2, tac, SF(8.5, 0.3))
            rects[f"theme_{theme}"] = NSMakeRect(tx, ty, th_bw, th_h)
        y -= 46 + 8

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

        # Clic header → bouton × ou cycle titre
        if pt.y >= h - HDR_H:
            r = (self._btn_rects or {}).get("quit")
            if r and (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                      r.origin.y <= pt.y <= r.origin.y + r.size.height):
                if _panel_win: _panel_win.orderOut_(None)
                return
            r2 = (self._btn_rects or {}).get("title_mode")
            if r2 and (r2.origin.x <= pt.x <= r2.origin.x + r2.size.width and
                       r2.origin.y <= pt.y <= r2.origin.y + r2.size.height):
                global _TITLE_MODE
                idx = TITLE_MODES.index(_TITLE_MODE) if _TITLE_MODE in TITLE_MODES else 0
                _TITLE_MODE = TITLE_MODES[(idx + 1) % len(TITLE_MODES)]
            return

        # Clic onglets
        for tab, r in (self._tab_rects or {}).items():
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                self._tab = tab
                if tab == "cal":
                    n_ev = max(len(_app._cal), 1) if _app else 1
                    cal_h = n_ev * 26 + 36
                    new_ph = cal_h + 18 + 36 + 14 + 84 + 14 + 58 + 90
                    new_ph = max(new_ph, 420)
                else:
                    new_ph = PH_BY_TAB.get(tab, 520)
                self.setFrame_(NSMakeRect(0, 0, PW, new_ph))
                if _panel_win:
                    _panel_win.setContentSize_(NSMakeSize(PW, new_ph))
                # Lancer speedtest au switch sur onglet réseau
                if tab == "net" and _app and _app._stest_state != "running":
                    _app._stest_state = "running"
                    _run_speedtest(_app)
                self.display()
                return

        # Toggle mode tri processus
        r = (self._btn_rects or {}).get("top_hdr")
        if r and (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                  r.origin.y <= pt.y <= r.origin.y + r.size.height):
            self._top_mode = "mem" if self._top_mode == "cpu" else "cpu"
            self.display()
            return

        # Boutons
        for name, r in (self._btn_rects or {}).items():
            if name in ("top_hdr",): continue
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                if name in ("quit", "quit2"):
                    if _panel_win: _panel_win.orderOut_(None)
                    from AppKit import NSApplication
                    NSApplication.sharedApplication().terminate_(None)
                elif _app:
                    if   name == "caff":       _app.toggle_caff()
                    elif name == "pomo":       _app.toggle_pomo()
                    elif name == "copy":
                        _app.copy_stats()
                        self._copy_flash = time.time() + 2.0
                    elif name == "act_cpu":    subprocess.Popen(["open", "-a", "Activity Monitor"])
                    elif name == "lock":       _lock_screen()
                    elif name == "stest_run":
                        if _app._stest_state != "running":
                            _app._stest_state = "running"
                            _run_speedtest(_app)
                    elif name in ("music_prev", "music_play", "music_next"):
                        _music_control(name[6:])  # "prev"/"play"/"next"
                        # Forcer re-fetch au prochain statsRefresh_ (dans ≤2s)
                        if _app: _app._music_t = 0
                    elif name == "vol_dn":
                        cur = _S.get('volume', 50)
                        nv  = max(0, (cur if cur >= 0 else 50) - 5)
                        _set_volume(nv)
                        _S['volume'] = nv
                        if _app: _app._volume = nv
                    elif name == "vol_up":
                        cur = _S.get('volume', 50)
                        nv  = min(100, (cur if cur >= 0 else 50) + 5)
                        _set_volume(nv)
                        _S['volume'] = nv
                        if _app: _app._volume = nv
                    elif name.startswith("kill_"):
                        pid = int(name[5:])
                        try:
                            import signal as _sig
                            os.kill(pid, _sig.SIGTERM)
                        except Exception:
                            pass
                    elif name.startswith("theme_"):
                        _save_theme(name[6:])
                    elif name.startswith("icon_"):
                        _save_icon_style(name[5:])
                self.display()
                break

    def mouseMoved_(self, event):
        pt = self.convertPoint_fromView_(event.locationInWindow(), None)
        hv = ""; ihv = ""
        for name, r in (self._btn_rects or {}).items():
            if name in ("top_hdr",): continue
            if (r.origin.x <= pt.x <= r.origin.x + r.size.width and
                    r.origin.y <= pt.y <= r.origin.y + r.size.height):
                if name.startswith("icon_"):
                    ihv = name[5:]
                else:
                    hv = name
                break
        changed = (hv != self._hover or ihv != self._icon_hover)
        self._hover = hv; self._icon_hover = ihv
        if changed:
            self.display()

    def mouseExited_(self, _):
        if self._hover or self._icon_hover:
            self._hover = ""; self._icon_hover = ""
            self.display()

    def acceptsFirstMouse_(self, _): return True
    def refreshDisplay_(self, _):
        if not _panel_win or not _panel_win.isVisible(): return
        self.setNeedsDisplay_(True)
        self.display()


# ─── Delegate ─────────────────────────────────────────────────────────────────
class _StatsDelegate(NSObject):
    _app_ref = None
    def statsRefresh_(self, _):
        if self._app_ref: self._app_ref._do_stats(
            panel_open=bool(_panel_win and _panel_win.isVisible()))


# ─── Robot menubar v2 ─────────────────────────────────────────────────────────
_STATE_COL = {
    "chill": (0.18, 0.90, 0.62),
    "busy":  (0.30, 0.62, 1.00),
    "hot":   (1.00, 0.65, 0.12),
    "panic": (1.00, 0.20, 0.20),
}

ICON_STYLES = ["robot", "pulse", "circuit", "terminal", "alien",
               "astronaut", "cube", "ninja", "cat", "ghost",
               "skull", "eye", "planet", "flame", "panda"]
ICON_LABELS = ["Robot", "Pulse", "Circuit", ">_", "Alien",
               "Astro", "Cube", "Ninja", "Cat", "Ghost",
               "Skull", "Eye", "Planet", "Flame", "Panda"]

_ICON_STYLE = "robot"
_STYLE_FILE  = os.path.expanduser("~/.config/macmonitor/style.txt")

THEMES = ["violet", "midnight", "matrix", "sunset", "mono"]
THEME_LABELS = ["Violet", "Night", "Matrix", "Sunset", "Mono"]

_THEME = "violet"
_THEME_FILE = os.path.expanduser("~/.config/macmonitor/theme.txt")

_THEME_PALETTES = {
    "violet":   {"bg": (0.07, 0.06, 0.11), "hdr": (0.10, 0.07, 0.16), "acc": (0.55, 0.42, 1.00)},
    "midnight": {"bg": (0.04, 0.06, 0.12), "hdr": (0.06, 0.08, 0.20), "acc": (0.30, 0.62, 1.00)},
    "matrix":   {"bg": (0.03, 0.08, 0.04), "hdr": (0.04, 0.12, 0.06), "acc": (0.18, 0.90, 0.40)},
    "sunset":   {"bg": (0.12, 0.05, 0.04), "hdr": (0.18, 0.07, 0.05), "acc": (1.00, 0.45, 0.10)},
    "mono":     {"bg": (0.08, 0.08, 0.10), "hdr": (0.13, 0.13, 0.15), "acc": (0.75, 0.75, 0.80)},
}

def _load_theme():
    global _THEME
    try:
        with open(_THEME_FILE) as f:
            s = f.read().strip()
            if s in THEMES: _THEME = s
    except Exception:
        pass

def _save_theme(t):
    global _THEME, C_BG, C_HDR
    _THEME = t
    p = _THEME_PALETTES[t]
    C_BG  = _c(*p["bg"],  0.99)
    C_HDR = _c(*p["hdr"], 1.00)
    try:
        os.makedirs(os.path.dirname(_THEME_FILE), exist_ok=True)
        with open(_THEME_FILE, "w") as f: f.write(t)
    except Exception:
        pass

def _load_icon_style():
    global _ICON_STYLE
    try:
        with open(_STYLE_FILE) as f:
            s = f.read().strip()
            if s in ICON_STYLES:
                _ICON_STYLE = s
    except Exception:
        pass

def _save_icon_style(style):
    global _ICON_STYLE
    _ICON_STYLE = style
    try:
        os.makedirs(os.path.dirname(_STYLE_FILE), exist_ok=True)
        with open(_STYLE_FILE, "w") as f:
            f.write(style)
    except Exception:
        pass


def _draw_robot(t, state, blink, size=24):
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


def _draw_pulse(t, state, blink, size=24):
    """Ligne EKG animée."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    spd  = {"chill": 0.55, "busy": 0.85, "hot": 1.15, "panic": 1.7}[state]
    amp  = {"chill": 3.5,  "busy": 5.5,  "hot": 8.5,  "panic": 11.5}[state]
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cy   = size / 2
    sx   = ((t * spd * 1.6) % 1.0) * (size + 10) - 5
    pts  = []
    for x in range(size + 1):
        dx = x - sx
        if -1 < dx < 8:
            if   dx < 0:   y = cy
            elif dx < 1:   y = cy - amp * dx
            elif dx < 2.2: y = cy - amp + amp * 2 * (dx - 1) / 1.2
            elif dx < 3.8: y = cy + amp * (dx - 2.2) / 1.6
            elif dx < 5.5: y = cy + amp * (1 - (dx - 3.8) / 1.7) * 0.30
            else:          y = cy
        else:
            y = cy
        pts.append((x, y))
    path = NSBezierPath.bezierPath()
    path.moveToPoint_(NSMakePoint(pts[0][0], pts[0][1]))
    for x, y in pts[1:]: path.lineToPoint_(NSMakePoint(x, y))
    col.colorWithAlphaComponent_(0.22).setStroke()
    path.setLineWidth_(4.5); path.stroke()
    col.setStroke(); path.setLineWidth_(1.4); path.stroke()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_circuit(t, state, blink, size=24):
    """Chip CPU avec pins et LED pulsante."""
    rc, gc, bc = _STATE_COL[state]
    col   = _c(rc, gc, bc, 1.0)
    pulse = 0.65 + math.sin(t * 2 * math.pi) * 0.35
    img   = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cw = size * 0.48; cx = (size - cw) / 2; cy = (size - cw) / 2
    _c(0.08, 0.08, 0.12, 1.0).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx, cy, cw, cw), 2.5, 2.5).fill()
    col.colorWithAlphaComponent_(0.38).setStroke()
    brd = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx, cy, cw, cw), 2.5, 2.5)
    brd.setLineWidth_(0.7); brd.stroke()
    plen = 2.8; pw = 1.1; step = cw / 4
    col.colorWithAlphaComponent_(0.6).setFill()
    for i in range(3):
        off = cy + step * (i + 1) - pw / 2
        NSBezierPath.fillRect_(NSMakeRect(cx - plen, off, plen, pw))
        NSBezierPath.fillRect_(NSMakeRect(cx + cw,   off, plen, pw))
        NSBezierPath.fillRect_(NSMakeRect(off, cy - plen, pw, plen))
        NSBezierPath.fillRect_(NSMakeRect(off, cy + cw,   pw, plen))
    lr = size * 0.09 * pulse
    col.colorWithAlphaComponent_(0.25 * pulse).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(size/2 - lr*2.2, size/2 - lr*2.2, lr*4.4, lr*4.4)).fill()
    col.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(size/2 - lr, size/2 - lr, lr*2, lr*2)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_terminal(t, state, blink, size=24):
    """>_ curseur clignotant."""
    rc, gc, bc = _STATE_COL[state]
    col = _c(rc, gc, bc, 1.0)
    img = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    _c(0.04, 0.04, 0.09, 0.95).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(1, 1, size - 2, size - 2), 4, 4).fill()
    col.colorWithAlphaComponent_(0.45).setStroke()
    brd = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(1, 1, size - 2, size - 2), 4, 4)
    brd.setLineWidth_(0.6); brd.stroke()
    arr = NSBezierPath.bezierPath(); arr.setLineWidth_(1.5)
    arr.moveToPoint_(NSMakePoint(4.5, size * 0.34))
    arr.lineToPoint_(NSMakePoint(8.5, size * 0.50))
    arr.lineToPoint_(NSMakePoint(4.5, size * 0.66))
    col.setStroke(); arr.stroke()
    if not blink:
        col.setFill()
        NSBezierPath.fillRect_(NSMakeRect(10.5, size * 0.36, 5.5, 1.4))
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_alien(t, state, blink, size=24):
    """Tête alien avec grands yeux et antennes."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 0.85)
    dark = _c(rc * 0.15, gc * 0.15, bc * 0.15, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 1.0
    sway = math.sin(t * 2.5 * math.pi + 0.4) * 1.3
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size * 0.46 + bob
    hw = size * 0.42; hh = size * 0.52
    col.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - hw/2, cy - hh/2, hw, hh)).fill()
    ew = hw * 0.38; eh = hh * 0.30; ey = cy + hh * 0.08
    for ex in (cx - hw*0.23 - ew/2, cx + hw*0.23 - ew/2):
        dark.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(NSMakeRect(ex, ey, ew, eh)).fill()
        _c(1, 1, 1, 0.50).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex + ew*0.55, ey + eh*0.52, ew*0.22, ew*0.22)).fill()
    if state == "panic":
        _oval(cx - hw*0.14, cy - hh*0.28, hw*0.28, hh*0.18, dark)
    else:
        m = NSBezierPath.bezierPath(); m.setLineWidth_(0.9)
        m.moveToPoint_(NSMakePoint(cx - hw*0.18, cy - hh*0.22))
        m.lineToPoint_(NSMakePoint(cx + hw*0.18, cy - hh*0.22))
        dark.colorWithAlphaComponent_(0.8).setStroke(); m.stroke()
    for side in (-1, 1):
        ax = cx + side * hw * 0.22; ay_b = cy + hh/2 - 1
        tip_x = ax + side * sway * 0.6; tip_y = ay_b + 5.5
        al = NSBezierPath.bezierPath()
        al.moveToPoint_(NSMakePoint(ax, ay_b))
        al.lineToPoint_(NSMakePoint(tip_x, tip_y))
        col.colorWithAlphaComponent_(0.75).setStroke(); al.setLineWidth_(0.9); al.stroke()
        col.setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(tip_x - 1.4, tip_y - 1.4, 2.8, 2.8)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_astronaut(t, state, blink, size=24):
    """Casque d'astronaute avec visière."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 1.2
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size / 2 + bob; r = size * 0.43
    _c(0.82, 0.84, 0.90, 1.0).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r, cy - r, r*2, r*2)).fill()
    vr = r * 0.66
    _c(0.04, 0.04, 0.14, 0.96).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - vr, cy - vr*0.82, vr*2, vr*1.64)).fill()
    hor_y = cy - vr * 0.15
    col.colorWithAlphaComponent_(0.38).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - vr, hor_y - vr*1.64, vr*2, vr*1.64)).fill()
    star_phase = (t * 0.7) % 1.0
    n_stars = {"chill": 3, "busy": 2, "hot": 1, "panic": 0}[state]
    for i in range(n_stars):
        sa = max(0.0, math.sin((star_phase + i * 0.33) * math.pi * 2) * 0.5 + 0.5)
        sx = cx + (i - 1) * vr * 0.42; sy = cy + vr * (0.15 + i * 0.1)
        _c(1, 1, 1, sa * 0.8).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(sx - 0.7, sy - 0.7, 1.4, 1.4)).fill()
    _c(1, 1, 1, 0.32).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r*0.38, cy + r*0.16, r*0.5, r*0.28)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_cube(t, state, blink, size=24):
    """Cube isométrique flottant."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    mid  = _c(rc*0.62, gc*0.62, bc*0.62, 1.0)
    dark = _c(rc*0.38, gc*0.38, bc*0.38, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 0.9
    sc   = size / 24.0
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))

    def _v(x, y): return NSMakePoint(x * sc, y * sc + bob)
    def _face(*pts):
        fp = NSBezierPath.bezierPath()
        fp.moveToPoint_(pts[0])
        for pt in pts[1:]: fp.lineToPoint_(pt)
        fp.closePath(); return fp

    apex  = _v(12, 21);  left  = _v(3,  16.2); right = _v(21, 16.2)
    front = _v(12, 11.4); bl   = _v(3,  6.6);  br    = _v(21, 6.6)
    btm   = _v(12, 1.8)

    top = _face(apex, right, front, left); col.setFill();  top.fill()
    rf  = _face(right, front, btm, br);    mid.setFill();  rf.fill()
    lf  = _face(left,  front, btm, bl);    dark.setFill(); lf.fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_ninja(t, state, blink, size=24):
    """Ninja avec bandeau et yeux luisants."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 0.9
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size / 2 + bob; r = size * 0.42
    _c(0.07, 0.07, 0.11, 1.0).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r, cy - r, r*2, r*2)).fill()
    col.colorWithAlphaComponent_(0.75).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - r*0.93, cy + r*0.18, r*1.86, r*0.32), 1.2, 1.2).fill()
    col.colorWithAlphaComponent_(0.13).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - r*0.88, cy - r*0.78, r*1.76, r*0.62), 1.5, 1.5).fill()
    col.colorWithAlphaComponent_(0.6).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx + r*0.78, cy + r*0.26, r*0.30, r*0.22)).fill()
    ew = r * 0.27; eh = r * 0.20; ey = cy + r * 0.01
    if blink:
        for ex in (cx - r*0.30, cx + r*0.30):
            np = NSBezierPath.bezierPath()
            np.moveToPoint_(NSMakePoint(ex - ew/2, ey))
            np.lineToPoint_(NSMakePoint(ex + ew/2, ey))
            col.setStroke(); np.setLineWidth_(1.1); np.stroke()
    else:
        for ex in (cx - r*0.30 - ew/2, cx + r*0.30 - ew/2):
            col.colorWithAlphaComponent_(0.28).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex - 1.2, ey - 1.2, ew + 2.4, eh + 2.4)).fill()
            col.setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex, ey, ew, eh)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_cat(t, state, blink, size=24):
    """Tête de chat avec oreilles et moustaches."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    dark = _c(rc*0.40, gc*0.40, bc*0.40, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 0.9
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size * 0.45 + bob; r = size * 0.32
    for side in (-1.0, 1.0):
        ear = NSBezierPath.bezierPath()
        ear.moveToPoint_(NSMakePoint(cx + side*r*0.12, cy + r*0.80))
        ear.lineToPoint_(NSMakePoint(cx + side*r*0.90, cy + r*0.80))
        ear.lineToPoint_(NSMakePoint(cx + side*r*0.62, cy + r*1.65))
        ear.closePath(); col.setFill(); ear.fill()
        inn = NSBezierPath.bezierPath()
        inn.moveToPoint_(NSMakePoint(cx + side*r*0.22, cy + r*0.80))
        inn.lineToPoint_(NSMakePoint(cx + side*r*0.80, cy + r*0.80))
        inn.lineToPoint_(NSMakePoint(cx + side*r*0.58, cy + r*1.42))
        inn.closePath()
        dark.colorWithAlphaComponent_(0.45).setFill(); inn.fill()
    col.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r, cy - r, r*2, r*2)).fill()
    ew = r * 0.34; eh = r * 0.34; ey = cy + r * 0.12
    if blink:
        for ex in (cx - r*0.36, cx + r*0.36):
            cp = NSBezierPath.bezierPath()
            cp.moveToPoint_(NSMakePoint(ex - ew/2, ey))
            cp.lineToPoint_(NSMakePoint(ex + ew/2, ey))
            dark.setStroke(); cp.setLineWidth_(1.1); cp.stroke()
    else:
        for ex in (cx - r*0.36 - ew/2, cx + r*0.36 - ew/2):
            dark.setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex, ey - eh/2, ew, eh)).fill()
            _c(1, 1, 1, 0.55).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex + ew*0.52, ey + eh*0.08, ew*0.28, ew*0.28)).fill()
    nose = NSBezierPath.bezierPath()
    nose.moveToPoint_(NSMakePoint(cx,           cy - r*0.04))
    nose.lineToPoint_(NSMakePoint(cx - r*0.10,  cy - r*0.20))
    nose.lineToPoint_(NSMakePoint(cx + r*0.10,  cy - r*0.20))
    nose.closePath()
    dark.colorWithAlphaComponent_(0.7).setFill(); nose.fill()
    for side in (-1.0, 1.0):
        for dy in (-0.08, 0.05, 0.18):
            wp = NSBezierPath.bezierPath()
            wp.moveToPoint_(NSMakePoint(cx + side*r*0.14, cy - r*0.06 + dy*r))
            wp.lineToPoint_(NSMakePoint(cx + side*r*0.88, cy - r*0.06 + dy*r*0.35))
            dark.colorWithAlphaComponent_(0.38).setStroke()
            wp.setLineWidth_(0.6); wp.stroke()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_ghost(t, state, blink, size=24):
    """Fantôme flottant avec bas ondulé."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 1.5
    swg  = math.sin(t * 3 * math.pi) * 1.0
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; r = size * 0.40; cy = size * 0.56 + bob
    bot = cy - r * 1.05
    ghost = NSBezierPath.bezierPath()
    ghost.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        NSMakePoint(cx, cy), r, 0, 180)
    ghost.lineToPoint_(NSMakePoint(cx - r, bot + r*0.12))
    seg = r * 2 / 4
    for i in range(4):
        hump = bot + r*0.22 if i % 2 == 0 else bot
        ghost.lineToPoint_(NSMakePoint(cx - r + seg*(i+0.5),
                                       hump + swg*(0.4 if i%2==0 else -0.4)))
        ghost.lineToPoint_(NSMakePoint(cx - r + seg*(i+1), bot))
    ghost.lineToPoint_(NSMakePoint(cx + r, cy))
    ghost.closePath()
    col.setFill(); ghost.fill()
    ey  = cy + r * 0.14; ew = r * 0.32
    eh  = r * (0.38 if state == "panic" else 0.30)
    if blink:
        for ex in (cx - r*0.34, cx + r*0.34):
            gp = NSBezierPath.bezierPath()
            gp.moveToPoint_(NSMakePoint(ex - ew/2, ey))
            gp.lineToPoint_(NSMakePoint(ex + ew/2, ey))
            _c(0, 0, 0, 0.88).setStroke(); gp.setLineWidth_(1.1); gp.stroke()
    else:
        for ex in (cx - r*0.34 - ew/2, cx + r*0.34 - ew/2):
            _c(0.03, 0.03, 0.12, 0.96).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex, ey - eh/2, ew, eh)).fill()
            _c(1, 1, 1, 0.55).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex + ew*0.48, ey + eh*0.05, ew*0.30, ew*0.30)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_skull(t, state, blink, size=24):
    """Crâne avec orbites brillantes."""
    rc, gc, bc = _STATE_COL[state]
    col   = _c(rc, gc, bc, 1.0)
    bone  = _c(0.86, 0.86, 0.88, 1.0)
    shade = _c(0.32, 0.32, 0.36, 1.0)
    pulse = 0.55 + math.sin(t * 3 * math.pi) * 0.45
    bob   = math.sin(t * 2 * math.pi) * 0.8
    img   = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size * 0.48 + bob
    # Crâne (cranium arrondi)
    bone.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - size*0.34, cy - size*0.08, size*0.68, size*0.56)).fill()
    # Mâchoire
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
        NSMakeRect(cx - size*0.24, cy - size*0.25, size*0.48, size*0.20), 3, 3).fill()
    # Dents (4)
    shade.setFill()
    tw = size * 0.085; th = size * 0.09
    for i in range(4):
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(cx - size*0.185 + i*(tw + size*0.018),
                       cy - size*0.25, tw, th), 1.5, 1.5).fill()
    # Cavité nasale
    shade.setFill()
    ns = NSBezierPath.bezierPath()
    ns.moveToPoint_(NSMakePoint(cx, cy + size*0.06))
    ns.lineToPoint_(NSMakePoint(cx - size*0.06, cy - size*0.03))
    ns.lineToPoint_(NSMakePoint(cx + size*0.06, cy - size*0.03))
    ns.closePath(); ns.fill()
    # Orbites avec lueur
    ey = cy + size * 0.20
    for ex in (cx - size*0.15, cx + size*0.15):
        _c(0.12, 0.12, 0.16, 1.0).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex - size*0.105, ey - size*0.090, size*0.21, size*0.18)).fill()
        col.colorWithAlphaComponent_(0.28 * pulse).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex - size*0.095, ey - size*0.082, size*0.19, size*0.164)).fill()
        col.colorWithAlphaComponent_(0.80 * pulse).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex - size*0.048, ey - size*0.040, size*0.096, size*0.080)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_eye(t, state, blink, size=24):
    """Œil omniscient avec iris pulsant."""
    rc, gc, bc = _STATE_COL[state]
    col   = _c(rc, gc, bc, 1.0)
    pulse = 0.70 + math.sin(t * 2 * math.pi) * 0.30
    bob   = math.sin(t * 1.5 * math.pi) * 0.6
    img   = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size / 2 + bob
    ew = size * 0.44; eh = size * 0.24
    # Forme œil (vesica piscis)
    eye_p = NSBezierPath.bezierPath()
    eye_p.moveToPoint_(NSMakePoint(cx - ew, cy))
    eye_p.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx + ew, cy),
        NSMakePoint(cx - ew*0.5, cy + eh),
        NSMakePoint(cx + ew*0.5, cy + eh))
    eye_p.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx - ew, cy),
        NSMakePoint(cx + ew*0.5, cy - eh),
        NSMakePoint(cx - ew*0.5, cy - eh))
    eye_p.closePath()
    _c(0.90, 0.90, 0.95, 1.0).setFill(); eye_p.fill()
    # Iris coloré
    ir = eh * 0.90
    col.colorWithAlphaComponent_(0.92 * pulse).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - ir, cy - ir, ir*2, ir*2)).fill()
    # Pupille
    pr = ir * 0.48
    if blink:
        _c(0.02, 0.02, 0.06, 1.0).setFill()
        NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
            NSMakeRect(cx - pr*1.5, cy - pr*0.18, pr*3, pr*0.36), 2, 2).fill()
    else:
        _c(0.02, 0.02, 0.06, 1.0).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx - pr, cy - pr, pr*2, pr*2)).fill()
        _c(1, 1, 1, 0.68).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx + pr*0.22, cy + pr*0.18, pr*0.40, pr*0.40)).fill()
    _c(0.07, 0.05, 0.12, 0.75).setStroke()
    eye_p.setLineWidth_(0.8); eye_p.stroke()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_planet(t, state, blink, size=24):
    """Planète avec anneau (style Saturne)."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    dark = _c(rc*0.48, gc*0.48, bc*0.48, 1.0)
    spin = t * 2 * math.pi
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size / 2
    pr = size * 0.27; rxa = size * 0.44; rya = size * 0.13
    # Anneau arrière (sombre)
    dark.colorWithAlphaComponent_(0.50).setStroke()
    rb = NSBezierPath.bezierPath()
    rb.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        NSMakePoint(cx, cy + rya * 0.3), rxa, 0, 180)
    rb.setLineWidth_(2.2); rb.stroke()
    # Planète
    col.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - pr, cy - pr, pr*2, pr*2)).fill()
    # Bande atmosphérique
    dark.colorWithAlphaComponent_(0.20).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - pr, cy - pr*0.18, pr*2, pr*0.36)).fill()
    # Reflet
    _c(1, 1, 1, 0.28).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - pr*0.48, cy + pr*0.22, pr*0.52, pr*0.28)).fill()
    # Anneau avant (brillant)
    col.colorWithAlphaComponent_(0.78).setStroke()
    rf = NSBezierPath.bezierPath()
    rf.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_(
        NSMakePoint(cx, cy + rya * 0.3), rxa, 180, 360)
    rf.setLineWidth_(2.2); rf.stroke()
    # Étoiles orbitales
    for i in range(3):
        sa = math.sin(spin * 0.4 + i * 2.1) * 0.5 + 0.5
        sx = cx + math.cos(spin * 0.25 + i * 2.1) * size * 0.44
        sy = cy + math.sin(spin * 0.25 + i * 2.1) * size * 0.14 + rya * 0.3
        _c(1, 1, 1, sa * 0.65).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(sx - 0.9, sy - 0.9, 1.8, 1.8)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_flame(t, state, blink, size=24):
    """Flamme vive animée."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    flk1 = math.sin(t * 7 * math.pi) * 1.3
    flk2 = math.sin(t * 5 * math.pi + 1.0) * 0.9
    bob  = math.sin(t * 3 * math.pi) * 0.6
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; base = size * 0.09
    # Flamme extérieure
    fl = NSBezierPath.bezierPath()
    fl.moveToPoint_(NSMakePoint(cx - size*0.18, base))
    fl.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx - size*0.28 + flk1, size*0.54 + bob),
        NSMakePoint(cx - size*0.34, size*0.20),
        NSMakePoint(cx - size*0.30, size*0.40))
    fl.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx, size*0.93 + bob),
        NSMakePoint(cx - size*0.20 + flk2, size*0.72),
        NSMakePoint(cx - size*0.10, size*0.86))
    fl.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx + size*0.28 - flk1, size*0.54 + bob),
        NSMakePoint(cx + size*0.10, size*0.86),
        NSMakePoint(cx + size*0.20 - flk2, size*0.72))
    fl.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx + size*0.18, base),
        NSMakePoint(cx + size*0.30, size*0.40),
        NSMakePoint(cx + size*0.34, size*0.20))
    fl.closePath(); col.setFill(); fl.fill()
    # Flamme intérieure (claire)
    fi = NSBezierPath.bezierPath()
    fi.moveToPoint_(NSMakePoint(cx - size*0.10, base + size*0.07))
    fi.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx, size*0.76 + bob*0.5),
        NSMakePoint(cx - size*0.20, size*0.34),
        NSMakePoint(cx - size*0.08, size*0.60))
    fi.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx + size*0.10, base + size*0.07),
        NSMakePoint(cx + size*0.08, size*0.60),
        NSMakePoint(cx + size*0.20, size*0.34))
    fi.closePath()
    col.colorWithAlphaComponent_(0.50).setFill(); fi.fill()
    _c(1, 1, 1, 0.28).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - size*0.06, base + size*0.08, size*0.12, size*0.16)).fill()
    img.unlockFocus(); img.setTemplate_(False)
    return img


def _draw_panda(t, state, blink, size=24):
    """Tête de panda avec taches d'yeux."""
    rc, gc, bc = _STATE_COL[state]
    col  = _c(rc, gc, bc, 1.0)
    bob  = math.sin(t * 2 * math.pi) * 0.8
    img  = NSImage.alloc().initWithSize_(NSMakeSize(size, size))
    img.lockFocus()
    NSColor.clearColor().setFill(); NSRectFill(NSMakeRect(0, 0, size, size))
    cx = size / 2; cy = size * 0.46 + bob; r = size * 0.36
    # Oreilles rondes (derrière la tête)
    for side in (-1.0, 1.0):
        col.colorWithAlphaComponent_(0.80).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(cx + side*r*0.72 - r*0.30, cy + r*0.56 - r*0.30,
                       r*0.60, r*0.60)).fill()
    # Tête blanche
    _c(0.92, 0.92, 0.95, 1.0).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r, cy - r, r*2, r*2)).fill()
    # Taches œils (state color = la couleur change avec CPU)
    ey = cy + r * 0.14
    for side in (-1.0, 1.0):
        ex = cx + side * r * 0.34
        col.colorWithAlphaComponent_(0.88).setFill()
        NSBezierPath.bezierPathWithOvalInRect_(
            NSMakeRect(ex - r*0.26, ey - r*0.22, r*0.52, r*0.42)).fill()
        if blink:
            lp = NSBezierPath.bezierPath()
            lp.moveToPoint_(NSMakePoint(ex - r*0.14, ey))
            lp.lineToPoint_(NSMakePoint(ex + r*0.14, ey))
            _c(1, 1, 1, 0.9).setStroke(); lp.setLineWidth_(1.0); lp.stroke()
        else:
            _c(1, 1, 1, 0.95).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex - r*0.12, ey - r*0.11, r*0.24, r*0.22)).fill()
            _c(0.06, 0.05, 0.10, 1.0).setFill()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(ex - r*0.07, ey - r*0.07, r*0.14, r*0.14)).fill()
    # Nez (ovale sombre)
    _c(0.18, 0.18, 0.22, 1.0).setFill()
    NSBezierPath.bezierPathWithOvalInRect_(
        NSMakeRect(cx - r*0.12, cy - r*0.14, r*0.24, r*0.14)).fill()
    # Sourire
    sm = NSBezierPath.bezierPath(); sm.setLineWidth_(1.0)
    sm.moveToPoint_(NSMakePoint(cx - r*0.14, cy - r*0.18))
    sm.curveToPoint_controlPoint1_controlPoint2_(
        NSMakePoint(cx + r*0.14, cy - r*0.18),
        NSMakePoint(cx - r*0.08, cy - r*0.28),
        NSMakePoint(cx + r*0.08, cy - r*0.28))
    _c(0.18, 0.18, 0.22, 0.7).setStroke(); sm.stroke()
    img.unlockFocus(); img.setTemplate_(False)
    return img


_ICON_DRAW = {
    "robot":     _draw_robot,
    "pulse":     _draw_pulse,
    "circuit":   _draw_circuit,
    "terminal":  _draw_terminal,
    "alien":     _draw_alien,
    "astronaut": _draw_astronaut,
    "cube":      _draw_cube,
    "ninja":     _draw_ninja,
    "cat":       _draw_cat,
    "ghost":     _draw_ghost,
    "skull":     _draw_skull,
    "eye":       _draw_eye,
    "planet":    _draw_planet,
    "flame":     _draw_flame,
    "panda":     _draw_panda,
}

def draw_character(t, state, blink, size=24):
    """Dispatche vers la fonction de dessin selon _ICON_STYLE."""
    return _ICON_DRAW.get(_ICON_STYLE, _draw_robot)(t, state, blink, size)


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
    """Récupère le titre/artiste en cours sans passer par System Events."""
    for app_name, proc_name in (("Music", "Music"), ("Spotify", "Spotify")):
        # Vérifie si le process tourne via pgrep (pas de permission nécessaire)
        if subprocess.run(["pgrep", "-x", proc_name],
                          capture_output=True).returncode != 0:
            continue
        script = f'''
tell application "{app_name}"
    if player state is playing then
        return (name of current track) & " — " & (artist of current track)
    else if player state is paused then
        return "⏸ " & (name of current track) & " — " & (artist of current track)
    end if
end tell
return ""
'''
        try:
            out = subprocess.run(["osascript", "-e", script],
                                 capture_output=True, text=True, timeout=3).stdout.strip()
            if out:
                return out
        except Exception:
            pass
    return ""

def _run_speedtest(app):
    """Lance networkQuality en background et met à jour les résultats."""
    import json, threading
    def _worker():
        try:
            r = subprocess.run(["networkQuality", "-c"],
                               capture_output=True, text=True, timeout=35)
            if r.returncode == 0:
                data = json.loads(r.stdout)
                app._stest_dl    = data.get("dl_throughput", 0)
                app._stest_ul    = data.get("ul_throughput", 0)
                app._stest_rtt   = data.get("base_rtt", 0)
                app._stest_state = "done"
            else:
                app._stest_state = "error"
        except Exception:
            app._stest_state = "error"
        app._stest_t = time.time()
        # Mettre à jour _S directement pour affichage immédiat
        _S.update({
            "stest_dl":    app._stest_dl,
            "stest_ul":    app._stest_ul,
            "stest_rtt":   app._stest_rtt,
            "stest_state": app._stest_state,
            "stest_t":     app._stest_t,
        })
    threading.Thread(target=_worker, daemon=True).start()
    # Marquer "running" dans _S immédiatement
    _S["stest_state"] = "running"

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
                    used = 100 - p
                    label = "Critique" if p < 15 else "Avertissement" if p < 30 else "Normal"
                    return f"{label} ({used}%)"
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
                           capture_output=True, timeout=3)
        # ioreg contient des données binaires → décoder avec remplacement
        out = r.stdout.decode("utf-8", errors="replace")
        mx = re.search(r'"AppleRawMaxCapacity"\s*=\s*(\d+)', out)
        ds = re.search(r'"DesignCapacity"\s*=\s*(\d+)', out)
        if mx and ds:
            return min(100, int(int(mx.group(1)) / int(ds.group(1)) * 100))
    except Exception: pass
    return -1

def _get_gpu_usage():
    try:
        r = subprocess.run(["ioreg","-r","-c","IOAccelerator","-d","2"],
                           capture_output=True, timeout=2)
        out = r.stdout.decode("utf-8", errors="replace")
        m = re.search(r'"GPU Activity"\s*=\s*(\d+)', out)
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
                          "mem":  i["memory_percent"] or 0.0,
                          "pid":  i["pid"] or 0})
        except (psutil.NoSuchProcess, psutil.AccessDenied): continue
    procs.sort(key=lambda x: x["cpu"], reverse=True)
    return procs[:n]

def _music_control(cmd):
    """Contrôle Apple Music ou Spotify sans passer par System Events."""
    actions = {"prev": "previous track", "play": "playpause", "next": "next track"}
    action  = actions.get(cmd, "playpause")
    for app_name, proc_name in (("Music", "Music"), ("Spotify", "Spotify")):
        if subprocess.run(["pgrep", "-x", proc_name], capture_output=True).returncode != 0:
            continue
        try:
            subprocess.Popen(["osascript", "-e",
                              f'tell application "{app_name}" to {action}'])
            return
        except Exception:
            pass

def _lock_screen():
    try:
        import ctypes
        lib = ctypes.cdll.LoadLibrary(
            "/System/Library/PrivateFrameworks/login.framework"
            "/Versions/Current/login")
        lib.SACLockScreenImmediate()
    except Exception:
        pass

def _get_volume():
    try:
        out = subprocess.run(["osascript", "-e",
            "output volume of (get volume settings)"],
            capture_output=True, text=True, timeout=2).stdout.strip()
        return int(out)
    except Exception:
        return -1

def _set_volume(v):
    try:
        v = max(0, min(100, v))
        subprocess.Popen(["osascript", "-e", f"set volume output volume {v}"])
    except Exception:
        pass

def _toggle_mute():
    try:
        subprocess.Popen(["osascript", "-e",
            "set volume output muted not (output muted of (get volume settings))"])
    except Exception:
        pass

def _get_weather():
    try:
        req = urllib.request.Request(
            "https://wttr.in/?format=%c+%t",
            headers={"User-Agent": "curl/7.79.1"})
        with urllib.request.urlopen(req, timeout=6) as r:
            return r.read().decode().strip()
    except Exception:
        return ""

def _vpn_iface_connected():
    """Retourne True si une interface utun/tun/tap a une IP réelle assignée."""
    import socket as _sock
    try:
        for iface, addrs in psutil.net_if_addrs().items():
            if not iface.startswith(("tun", "tap", "utun")):
                continue
            for addr in addrs:
                if (addr.family == _sock.AF_INET
                        and addr.address
                        and not addr.address.startswith("127.")):
                    return True
    except Exception:
        pass
    return False


def _get_vpn_status():
    """Retourne tous les VPNs actifs : scutil + Tailscale CLI + OpenVPN."""
    active = []

    # VPNs système (WireGuard, IKEv2, L2TP…) via scutil
    # Double vérif : scutil (Connected) ET interface utun avec IP
    try:
        r = subprocess.run(["scutil", "--nc", "list"],
                           capture_output=True, text=True, timeout=2)
        iface_ok = _vpn_iface_connected()
        for ln in r.stdout.splitlines():
            if "(Connected)" in ln and "tailscale" not in ln.lower():
                if not iface_ok:
                    continue   # scutil dit Connected mais pas d'interface → faux positif
                m = re.search(r'"([^"]+)"\s*$', ln.strip())
                active.append(m.group(1)[:18] if m else "VPN")
    except Exception:
        pass

    # Tailscale : CLI uniquement (scutil toujours Connected même à l'arrêt)
    for ts_bin in ("/usr/local/bin/tailscale", "/opt/homebrew/bin/tailscale"):
        if not os.path.exists(ts_bin):
            continue
        try:
            r = subprocess.run([ts_bin, "status", "--json"],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                d = json.loads(r.stdout)
                if (d.get("BackendState") == "Running"
                        and d.get("Self", {}).get("Online")):
                    active.append("Tailscale")
        except Exception:
            pass
        break

    # OpenVPN : process actif ET interface tun/tap avec IP
    try:
        has_proc = any("openvpn" in (p.info.get("name") or "").lower()
                       for p in psutil.process_iter(["name"]))
        if has_proc and _vpn_iface_connected():
            active.append("OpenVPN")
    except Exception:
        pass

    return "  ·  ".join(active) if active else None

def _get_focus_mode():
    for path in [
        os.path.expanduser("~/Library/Preferences/com.apple.notificationcenterui"),
        os.path.expanduser("~/Library/Preferences/ByHost/com.apple.notificationcenterui"),
    ]:
        try:
            r = subprocess.run(
                ["defaults", "-currentHost", "read", path, "doNotDisturb"],
                capture_output=True, text=True, timeout=2)
            if r.returncode == 0 and r.stdout.strip() == "1":
                return "Ne pas déranger"
        except Exception:
            pass
    return None

def _get_world_times():
    now = datetime.now()
    result = []
    for label, tz in [("Paris", "Europe/Paris"), ("NY", "America/New_York"), ("Tokyo", "Asia/Tokyo")]:
        try:
            t = datetime.now(ZoneInfo(tz))
            result.append((label, t.strftime("%H:%M")))
        except Exception:
            pass
    return result


def _ensure_launchagent():
    if not os.path.exists(PLIST_PATH): return
    if subprocess.run(["launchctl","list",PLIST_LABEL],
                      capture_output=True).returncode != 0:
        subprocess.run(["launchctl","load","-w",PLIST_PATH],capture_output=True)


# ─── Application ──────────────────────────────────────────────────────────────
_app = None; _panel_view = None; _panel_win = None; _menu = None; _S: dict = {}

class _MenuDelegate(NSObject):
    """Delegate NSMenu : annule immédiatement le menu et affiche un NSPanel."""
    _nsitem  = None
    _panel   = None
    _view    = None
    _monitor = None

    def menuWillOpen_(self, menu):
        # Annuler le menu AVANT qu'il entre dans son event-tracking loop
        menu.cancelTracking()
        # Montrer/cacher le panel après que le menu soit fermé
        from Foundation import NSTimer as _T
        _T.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.0, self, "togglePanel:", None, False)

    def togglePanel_(self, _):
        if self._panel and self._panel.isVisible():
            self._panel.orderOut_(None)
            if self._monitor:
                NSEvent.removeMonitor_(self._monitor)
                self._monitor = None
        else:
            self._showPanel()

    def _showPanel(self):
        if not self._panel or not self._nsitem: return
        btn = self._nsitem.button()
        btn_rect    = btn.convertRect_toView_(btn.bounds(), None)
        screen_rect = btn.window().convertRectToScreen_(btn_rect)
        current_tab = getattr(self._view, '_tab', 'sys')
        if current_tab == "cal":
            n_ev = max(len(_app._cal), 1) if _app else 1
            cal_h = n_ev * 26 + 36
            ph = cal_h + 18 + 36 + 14 + 84 + 14 + 58 + 90
            ph = max(ph, 420)
        else:
            ph = PH_BY_TAB.get(current_tab, 520)
        panel_x = screen_rect.origin.x + screen_rect.size.width / 2 - PW / 2
        panel_y = screen_rect.origin.y - ph - 4
        from AppKit import NSScreen
        sw = NSScreen.mainScreen().frame().size.width
        panel_x = max(4.0, min(panel_x, sw - PW - 4))
        self._panel.setContentSize_(NSMakeSize(PW, ph))
        self._view.setFrame_(NSMakeRect(0, 0, PW, ph))
        self._panel.setFrameOrigin_(NSMakePoint(panel_x, panel_y))
        self._panel.makeKeyAndOrderFront_(None)
        def _outside(event):
            if self._panel and self._panel.isVisible():
                self._panel.orderOut_(None)
            if self._monitor:
                NSEvent.removeMonitor_(self._monitor)
                self._monitor = None
        self._monitor = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
            1 << 1, _outside)


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
        self._cpu_hist_long = deque([0.0] * 180, maxlen=180)  # 180 × 2s = 6 minutes
        self._ram_hist_long = deque([0.0] * 180, maxlen=180)
        self._hist_long_t   = 0.0
        self._hist_save_t   = 0.0
        self._load_hist()

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
        self._dl_hi_since = 0.0

        # Timer réseau dédié 1 s
        self._net_prev = psutil.net_io_counters()
        self._net_time = time.time()

        # Caches
        self._temp  = "—"; self._temp_t  = 0.0
        self._music = "";   self._music_t = 0.0
        self._volume = -1;  self._volume_t = 0.0
        self._ping  = "—"; self._ping_t  = 0.0
        self._mpres = "—"; self._mpres_t = 0.0
        self._wifi  = ("",0); self._wifi_t = 0.0
        self._bhealth = -1; self._bhealth_t = 0.0
        self._gpu   = -1;   self._gpu_t   = 0.0
        self._cal   = [];   self._cal_t   = 0.0
        self._weather = "";   self._weather_t = 0.0
        self._vpn     = None; self._vpn_t     = 0.0
        self._focus   = None; self._focus_t   = 0.0
        self._wtimes  = [];   self._wtimes_t  = 0.0

        # Speedtest
        self._stest_dl    = 0.0   # bits/s
        self._stest_ul    = 0.0
        self._stest_rtt   = 0.0   # ms
        self._stest_state = "idle"  # idle | running | done | error
        self._stest_t     = 0.0   # dernière mesure

        self._last_icon_key = None
        self._last_icon_img = None
        self._setup_done = False
        _load_icon_style()
        _load_theme()
        _save_theme(_THEME)
        _ensure_launchagent()

    @rumps.timer(0.05)
    def _late_init(self, timer):
        if self._setup_done: return
        try: nsitem = self._nsapp.nsstatusitem
        except AttributeError: return
        self._setup_done = True; timer.stop()

        global _panel_view, _panel_win, _menu

        # Vue contenu dans un NSPanel (pas de freeze NSMenu)
        view = PanelView.alloc().initWithFrame_(NSMakeRect(0, 0, PW, PH_BY_TAB["sys"]))
        _panel_view = view

        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PW, PH_BY_TAB["sys"]), 128, 2, False)
        panel.setContentView_(view)
        panel.setLevel_(8)
        panel.setOpaque_(False)
        panel.setHasShadow_(True)
        from AppKit import NSColor as _NC
        panel.setBackgroundColor_(_NC.clearColor())
        panel.setAppearance_(NSAppearance.appearanceNamed_("NSAppearanceNameDarkAqua"))
        _panel_win = panel

        # NSMenu vide — sert uniquement à détecter le clic sur l'icône
        delegate = _MenuDelegate.alloc().init()
        delegate._nsitem = nsitem
        delegate._panel  = panel
        delegate._view   = view
        self._menu_delegate = delegate   # strong ref

        dummy_item = NSMenuItem.alloc().initWithTitle_action_keyEquivalent_("", None, "")
        menu = NSMenu.alloc().init()
        menu.setAutoenablesItems_(False)
        menu.addItem_(dummy_item)
        menu.setDelegate_(delegate)
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
        panel_open = bool(_panel_win and _panel_win.isVisible())
        step = 0.1 if panel_open else 0.025   # ~1fps icon quand panel fermé
        self._t     = (self._t + step) % 1.0
        self._blink = ((self._t % 0.35) < 0.06)
        if not self._setup_done: return
        now   = time.time()
        icon_key = (round(self._t * 20) / 20, self._state, self._blink, _ICON_STYLE)
        btn = self._nsapp.nsstatusitem.button()
        if icon_key != self._last_icon_key:
            img = draw_character(self._t, self._state, self._blink)
            self._last_icon_key = icon_key
            self._last_icon_img = img
            btn.setImage_(img)
        elif panel_open:
            btn.setImage_(self._last_icon_img)
        if self._pomo_end > 0:
            left  = max(0.0, self._pomo_end - now)
            title = f"  ⏰ {int(left)//60:02d}:{int(left)%60:02d}"
            if now >= self._pomo_end:
                self._pomo_end = 0.0
                self._notify("pomo", "Pomodoro terminé! 🍅",
                             "25 minutes — prenez une pause ☕")
        elif _TITLE_MODE == "cpu":
            title = f" {self._cpu:.0f}%"
        elif _TITLE_MODE == "ram":
            vm_pct = _S.get('ram', 0)
            title = f" {vm_pct:.0f}%"
        elif _TITLE_MODE == "net":
            title = f" ↓{_b(self._dl)}/s" if self._dl > 1024 else " ↓—"
        elif _TITLE_MODE == "clock":
            title = f" {time.strftime('%H:%M')}"
        else:
            title = ""
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
            _panel_view.display()

    @rumps.timer(2.0)
    def _update_stats(self, _): self._do_stats()

    def _do_stats(self, panel_open=True):
        now = time.time()
        cpu = psutil.cpu_percent(interval=None)
        freq = psutil.cpu_freq()
        self._cpu = cpu; self._cpu_hist.append(cpu)

        vm = psutil.virtual_memory(); self._ram_hist.append(vm.percent)
        self._cpu_hist_long.append(cpu)
        self._ram_hist_long.append(vm.percent)

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
        _secs = int(now - self._boot)
        _d, _h, _m = _secs // 86400, (_secs % 86400) // 3600, (_secs % 3600) // 60
        up = (f"{_d}j {_h}h" if _d else f"{_h}h {_m}m" if _h else f"{_m}m")

        self._state = ("panic" if cpu >= 80 else "hot" if cpu >= 60
                       else "busy" if cpu >= 35 else "chill")

        def _bg(attr, fn, interval, t_attr=None, *args):
            """Lance fn() en background si l'intervalle est écoulé."""
            ta = t_attr or f"_{attr}_t"
            if now - getattr(self, ta, 0) > interval:
                setattr(self, ta, now)
                def _run(a=attr, f=fn, ar=args):
                    setattr(self, a, f(*ar))
                threading.Thread(target=_run, daemon=True).start()

        if panel_open:
            if now - self._net_upd > 30:
                self._net_upd = now
                def _fetch_net(app=self):
                    ip, gw = _get_network_info()
                    app._local_ip = ip; app._gateway = gw
                threading.Thread(target=_fetch_net, daemon=True).start()
            _bg("_top_cache", lambda: _top_procs(6),      8,  "_top_last")
            _bg("_temp",      _get_cpu_temp,               30)
            _bg("_ping",      _get_ping,                   20)
            _bg("_wifi",      _get_wifi_info,               15)
            _bg("_bhealth",   _get_batt_health,             300)
            _bg("_gpu",       _get_gpu_usage,               3)
            _bg("_focus",     _get_focus_mode,              10)
            _bg("_volume",    _get_volume,                  3)

        if now - self._music_t > 3:
            self._music_t = now
            def _fetch_music(app=self):
                m = _get_music(); app._music = m; _S['music'] = m
            threading.Thread(target=_fetch_music, daemon=True).start()
        if now - self._cal_t > 300:
            self._cal_t = now
            threading.Thread(target=lambda: setattr(self, '_cal', _get_calendar_events(5)),
                             daemon=True).start()
        if now - self._wtimes_t > 60:
            self._wtimes_t = now
            threading.Thread(target=lambda: setattr(self, '_wtimes', _get_world_times()),
                             daemon=True).start()
        if now - self._weather_t > 600:
            self._weather_t = now
            threading.Thread(target=self._fetch_weather, daemon=True).start()
        if now - self._vpn_t > 5:
            self._vpn_t = now
            threading.Thread(target=self._fetch_vpn, daemon=True).start()
        if now - self._hist_save_t > 60:
            self._hist_save_t = now
            threading.Thread(target=self._save_hist, daemon=True).start()

        freq_s = f"{freq.current:.0f} MHz" if freq else "—"

        _S.update({
            "cpu":          cpu,
            "cpu_info":     f"{self._nc}C · {self._nt}T · {freq_s}",
            "cpu_temp":     self._temp,
            "cpu_hist":     list(self._cpu_hist),
            "cpu_hist_long": list(self._cpu_hist_long),
            "ram_hist_long": list(self._ram_hist_long),
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
            "weather":      self._weather,
            "vpn":          self._vpn,
            "focus":        self._focus,
            "world_times":  self._wtimes,
            "volume":       self._volume,
            "stest_dl":     self._stest_dl,
            "stest_ul":     self._stest_ul,
            "stest_rtt":    self._stest_rtt,
            "stest_state":  self._stest_state,
            "stest_t":      self._stest_t,
        })
        # Relancer speedtest toutes les 90s si l'onglet réseau est actif
        tab = getattr(_panel_view, '_tab', '') if _panel_view else ''
        if tab == "net" and self._stest_state != "running":
            if time.time() - self._stest_t > 90:
                self._stest_state = "running"
                _run_speedtest(self)
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
        if _panel_view: _panel_view.display()
        self._check_notifs(cpu, vm.percent, batt)

    def _fetch_weather(self):
        result = _get_weather()
        self._weather = result
        _S['weather'] = result
        if _panel_view and getattr(_panel_view, '_tab', '') == 'cal':
            _panel_view.display()

    def _load_hist(self):
        try:
            path = os.path.expanduser("~/.cache/macmonitor_hist.json")
            if os.path.exists(path):
                with open(path) as f:
                    d = json.load(f)
                if time.time() - d.get("t", 0) < 3600:
                    for v in d.get("cpu", [])[-HIST:]: self._cpu_hist.append(v)
                    for v in d.get("ram", [])[-HIST:]: self._ram_hist.append(v)
        except Exception:
            pass

    def _save_hist(self):
        try:
            os.makedirs(os.path.expanduser("~/.cache"), exist_ok=True)
            path = os.path.expanduser("~/.cache/macmonitor_hist.json")
            with open(path, "w") as f:
                json.dump({"t": time.time(),
                           "cpu": list(self._cpu_hist),
                           "ram": list(self._ram_hist)}, f)
        except Exception:
            pass

    def _fetch_vpn(self):
        result = _get_vpn_status()
        self._vpn = result
        _S['vpn'] = result
        if _panel_view and getattr(_panel_view, '_tab', '') == 'net':
            _panel_view.display()

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
