"""通用 Tk 组件。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable, Optional

from . import theme as T


def style_app(root: tk.Tk) -> ttk.Style:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except Exception:
        pass

    style.configure(".", font=T.FONT_UI, background=T.BG)
    style.configure("TFrame", background=T.BG)
    style.configure("Card.TFrame", background=T.CARD)
    style.configure("Sidebar.TFrame", background=T.SIDEBAR_BG)
    style.configure("TLabel", background=T.BG, foreground=T.TEXT, font=T.FONT_UI)
    style.configure("Card.TLabel", background=T.CARD, foreground=T.TEXT, font=T.FONT_UI)
    style.configure("Muted.TLabel", background=T.BG, foreground=T.TEXT_MUTED, font=T.FONT_SMALL)
    style.configure("CardMuted.TLabel", background=T.CARD, foreground=T.TEXT_MUTED, font=T.FONT_SMALL)
    style.configure("Title.TLabel", background=T.BG, foreground=T.TEXT, font=T.FONT_TITLE)
    style.configure("H2.TLabel", background=T.CARD, foreground=T.TEXT, font=T.FONT_H2)
    style.configure("Sidebar.TLabel", background=T.SIDEBAR_BG, foreground=T.SIDEBAR_TEXT, font=T.FONT_UI)
    style.configure("SidebarTitle.TLabel", background=T.SIDEBAR_BG, foreground="#FFFFFF", font=T.FONT_H2)
    style.configure("SidebarMuted.TLabel", background=T.SIDEBAR_BG, foreground=T.SIDEBAR_MUTED, font=T.FONT_SMALL)

    style.configure(
        "Primary.TButton",
        font=T.FONT_UI_BOLD,
        background=T.PRIMARY,
        foreground="#FFFFFF",
        borderwidth=0,
        focusthickness=0,
        padding=(14, 8),
    )
    style.map(
        "Primary.TButton",
        background=[("active", T.PRIMARY_HOVER), ("disabled", "#94A3B8")],
        foreground=[("disabled", "#E2E8F0")],
    )

    style.configure(
        "Accent.TButton",
        font=T.FONT_UI_BOLD,
        background=T.ACCENT,
        foreground="#FFFFFF",
        borderwidth=0,
        padding=(14, 8),
    )
    style.map("Accent.TButton", background=[("active", "#0F766E")])

    style.configure(
        "Ghost.TButton",
        font=T.FONT_UI,
        background=T.CARD,
        foreground=T.TEXT,
        borderwidth=1,
        padding=(12, 7),
    )
    style.map("Ghost.TButton", background=[("active", T.PRIMARY_SOFT)])

    style.configure(
        "Sidebar.TButton",
        font=T.FONT_UI,
        background=T.SIDEBAR_BG,
        foreground=T.SIDEBAR_TEXT,
        borderwidth=0,
        anchor="w",
        padding=(16, 12),
    )
    style.map(
        "Sidebar.TButton",
        background=[("active", T.SIDEBAR_ACTIVE)],
        foreground=[("active", "#FFFFFF")],
    )

    style.configure(
        "Danger.TButton",
        font=T.FONT_UI,
        background=T.DANGER_SOFT,
        foreground=T.DANGER,
        borderwidth=0,
        padding=(12, 7),
    )

    style.configure("TEntry", padding=6, fieldbackground="#FFFFFF")
    style.configure("TCombobox", padding=5)
    style.configure(
        "Horizontal.TProgressbar",
        troughcolor=T.BORDER,
        background=T.PRIMARY,
        thickness=8,
    )
    style.configure("TCheckbutton", background=T.CARD, font=T.FONT_UI)
    style.configure("TRadiobutton", background=T.CARD, font=T.FONT_UI)
    style.configure("TNotebook", background=T.BG, borderwidth=0)
    style.configure("TNotebook.Tab", padding=(14, 8), font=T.FONT_UI)
    return style


class Card(ttk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, style="Card.TFrame", **kw)
        self.configure(padding=16)
        # 用外侧 canvas/frame 模拟圆角阴影较复杂，这里用边框色块
        self._border = tk.Frame(master, bg=T.BORDER, bd=0)
        # 简化：直接白底

class RoundedButton(tk.Canvas):
    """圆角主按钮。"""

    def __init__(
        self,
        master,
        text: str,
        command: Optional[Callable] = None,
        bg: str = T.PRIMARY,
        fg: str = "#FFFFFF",
        hover: str = T.PRIMARY_HOVER,
        width: int = 140,
        height: int = 36,
        radius: int = 8,
        font=T.FONT_UI_BOLD,
        **kw,
    ):
        super().__init__(
            master,
            width=width,
            height=height,
            bg=master.cget("bg") if master.winfo_class() != "TFrame" else T.BG,
            highlightthickness=0,
            bd=0,
            cursor="hand2",
            **kw,
        )
        # ttk parent bg fallback
        try:
            parent_bg = master.cget("background")
        except Exception:
            parent_bg = T.BG
        self.configure(bg=parent_bg)

        self._bg = bg
        self._hover = hover
        self._fg = fg
        self._cmd = command
        self._text = text
        self._w = width
        self._h = height
        self._r = radius
        self._font = font
        self._enabled = True
        self._draw(bg)
        self.bind("<Enter>", lambda e: self._enabled and self._draw(self._hover))
        self.bind("<Leave>", lambda e: self._draw(self._bg if self._enabled else "#94A3B8"))
        self.bind("<Button-1>", self._on_click)

    def _round_rect(self, x1, y1, x2, y2, r, **kwargs):
        points = [
            x1 + r, y1,
            x2 - r, y1,
            x2, y1,
            x2, y1 + r,
            x2, y2 - r,
            x2, y2,
            x2 - r, y2,
            x1 + r, y2,
            x1, y2,
            x1, y2 - r,
            x1, y1 + r,
            x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def _draw(self, color: str):
        self.delete("all")
        self._round_rect(1, 1, self._w - 1, self._h - 1, self._r, fill=color, outline=color)
        self.create_text(
            self._w // 2,
            self._h // 2,
            text=self._text,
            fill=self._fg,
            font=self._font,
        )

    def _on_click(self, _e=None):
        if self._enabled and self._cmd:
            self._cmd()

    def set_enabled(self, enabled: bool):
        self._enabled = enabled
        self._draw(self._bg if enabled else "#94A3B8")
        self.configure(cursor="hand2" if enabled else "arrow")

    def set_text(self, text: str):
        self._text = text
        self._draw(self._bg if self._enabled else "#94A3B8")


def labeled_entry(parent, label: str, textvariable: tk.Variable, width: int = 36, show: str = None) -> ttk.Entry:
    row = ttk.Frame(parent, style="Card.TFrame")
    row.pack(fill="x", pady=4)
    ttk.Label(row, text=label, style="Card.TLabel", width=16).pack(side="left")
    e = ttk.Entry(row, textvariable=textvariable, width=width, show=show or "")
    e.pack(side="left", fill="x", expand=True)
    return e


def section_title(parent, text: str) -> ttk.Label:
    lab = ttk.Label(parent, text=text, style="H2.TLabel")
    lab.pack(anchor="w", pady=(0, 8))
    return lab


def badge(parent, text: str, color: str) -> tk.Label:
    return tk.Label(
        parent,
        text=f" {text} ",
        bg=color,
        fg="#FFFFFF",
        font=T.FONT_SMALL,
        padx=6,
        pady=2,
    )
