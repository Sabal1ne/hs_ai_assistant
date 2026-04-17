"""
overlay.py
----------
Transparent, click-through, always-on-top overlay window for the Hearthstone
AI assistant.

Features
~~~~~~~~
* Semi-transparent black background, white text, no title bar.
* Always stays on top of other windows.
* Click-through: mouse events pass straight through to the game.
* Draggable: hold and drag the label to reposition.
* Windows-native layered window flags via ctypes (``WS_EX_LAYERED``,
  ``WS_EX_TRANSPARENT``, ``WS_EX_TOPMOST``).
* Cross-platform fallback for macOS / Linux using Tkinter attributes.

Usage
~~~~~
    from overlay import AIOverlay

    overlay = AIOverlay()
    overlay.update_suggestion("Fireball", win_rate_delta=12.5)
    overlay.run()            # blocking – call in main thread
    # Or: overlay.start_async()  # non-blocking background thread
"""

from __future__ import annotations

import platform
import threading
import tkinter as tk
from typing import Optional


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"
_IS_MACOS = platform.system() == "Darwin"

# Windows API constants
_GWL_EXSTYLE = -20
_WS_EX_LAYERED = 0x00080000
_WS_EX_TRANSPARENT = 0x00000020
_WS_EX_TOPMOST = 0x00000008   # used via Tkinter's -topmost attribute
_LWA_ALPHA = 0x00000002


def _apply_windows_click_through(hwnd: int, alpha: int = 180) -> None:
    """Apply layered + transparent style so clicks pass through the window."""
    try:
        import ctypes
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        ex_style = user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        user32.SetWindowLongW(
            hwnd,
            _GWL_EXSTYLE,
            ex_style | _WS_EX_LAYERED | _WS_EX_TRANSPARENT,
        )
        user32.SetLayeredWindowAttributes(hwnd, 0, alpha, _LWA_ALPHA)
    except Exception:
        pass  # Silently ignore – fallback transparency is handled by Tkinter


# ---------------------------------------------------------------------------
# Overlay
# ---------------------------------------------------------------------------

class AIOverlay:
    """
    Transparent AI-suggestion overlay window.

    Parameters
    ----------
    alpha:
        Window opacity in the range 0–255 (Windows) or 0.0–1.0 (Tkinter).
        Defaults to 200 (≈ 78 % opacity).
    initial_x, initial_y:
        Initial screen position of the overlay window (pixels from top-left).
    width, height:
        Dimensions of the overlay panel (pixels).
    """

    def __init__(
        self,
        alpha: int = 200,
        initial_x: int = 20,
        initial_y: int = 20,
        width: int = 400,
        height: int = 60,
    ) -> None:
        self._alpha = alpha
        self._width = width
        self._height = height
        self._x = initial_x
        self._y = initial_y

        # Drag state
        self._drag_start_x: int = 0
        self._drag_start_y: int = 0

        self._suggestion_text = "AI Suggestion: Waiting…"
        self._root: Optional[tk.Tk] = None
        self._label: Optional[tk.Label] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_suggestion(
        self, card_name: str, win_rate_delta: Optional[float] = None
    ) -> None:
        """
        Update the text displayed in the overlay.

        Parameters
        ----------
        card_name:
            The name of the card the AI recommends playing.
        win_rate_delta:
            Estimated win-rate improvement in percentage points.  Pass
            ``None`` to omit the figure.
        """
        if win_rate_delta is not None:
            sign = "+" if win_rate_delta >= 0 else ""
            self._suggestion_text = (
                f"AI Suggestion: Play {card_name} (Winrate: {sign}{win_rate_delta:.1f}%)"
            )
        else:
            self._suggestion_text = f"AI Suggestion: Play {card_name}"

        if self._label is not None:
            try:
                self._label.config(text=self._suggestion_text)
            except tk.TclError:
                pass

    def clear(self) -> None:
        """Reset the suggestion text."""
        self.update_suggestion("—")

    def run(self) -> None:
        """Build the window and enter the Tkinter main loop (blocking)."""
        self._build()
        self._root.mainloop()  # type: ignore[union-attr]

    def start_async(self) -> threading.Thread:
        """Start the overlay in a background daemon thread."""
        t = threading.Thread(target=self.run, daemon=True, name="hs-overlay")
        t.start()
        return t

    def destroy(self) -> None:
        """Close the overlay window."""
        if self._root is not None:
            try:
                self._root.destroy()
            except tk.TclError:
                pass

    # ------------------------------------------------------------------
    # Window construction
    # ------------------------------------------------------------------

    def _build(self) -> None:
        root = tk.Tk()
        self._root = root

        # --- basic window setup -----------------------------------------
        root.overrideredirect(True)          # no title bar / borders
        root.attributes("-topmost", True)    # always on top

        # Transparency
        tk_alpha = self._alpha / 255.0
        try:
            root.attributes("-alpha", tk_alpha)
        except tk.TclError:
            pass  # some platforms don't support -alpha

        # macOS: transparent background
        if _IS_MACOS:
            try:
                root.attributes("-transparent", True)
            except tk.TclError:
                pass

        root.geometry(f"{self._width}x{self._height}+{self._x}+{self._y}")
        root.configure(bg="black")

        # --- suggestion label -------------------------------------------
        label = tk.Label(
            root,
            text=self._suggestion_text,
            fg="white",
            bg="black",
            font=("Arial", 13, "bold"),
            wraplength=self._width - 10,
            justify="left",
            padx=8,
            pady=6,
        )
        label.pack(fill=tk.BOTH, expand=True)
        self._label = label

        # --- drag bindings (on the label so the whole surface is draggable) ---
        label.bind("<ButtonPress-1>", self._on_drag_start)
        label.bind("<B1-Motion>", self._on_drag_motion)

        # --- Windows click-through via ctypes ----------------------------
        if _IS_WINDOWS:
            root.update_idletasks()
            try:
                import ctypes
                hwnd = ctypes.windll.user32.FindWindowW(  # type: ignore[attr-defined]
                    None, root.title()
                )
                if not hwnd:
                    # Alternative: use the window's native handle via winfo_id
                    hwnd = root.winfo_id()
                _apply_windows_click_through(hwnd, self._alpha)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Drag handlers
    # ------------------------------------------------------------------

    def _on_drag_start(self, event: tk.Event) -> None:
        self._drag_start_x = event.x
        self._drag_start_y = event.y

    def _on_drag_motion(self, event: tk.Event) -> None:
        if self._root is None:
            return
        dx = event.x - self._drag_start_x
        dy = event.y - self._drag_start_y
        x = self._root.winfo_x() + dx
        y = self._root.winfo_y() + dy
        self._root.geometry(f"+{x}+{y}")


# ---------------------------------------------------------------------------
# CLI entry-point for quick manual testing
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    overlay = AIOverlay(initial_x=100, initial_y=100)
    overlay.update_suggestion("Fireball", win_rate_delta=12.5)
    print("Overlay running – close the window to exit.")
    overlay.run()
