import platform
import threading
from typing import Callable, Dict, Optional

from utils.log import warning, error, debug

SYSTEM = platform.system().lower()

# The `keyboard` package is unstable on macOS/arm64 and crashes the interpreter
# (SIGBUS) as soon as it is imported. Only import it on non-mac platforms.
if SYSTEM != "darwin":
  try:
    import keyboard as kb  # type: ignore
  except Exception:  # pragma: no cover
    kb = None
else:  # pragma: no cover - macOS never loads `keyboard`
  kb = None

try:
  from pynput import keyboard as pynput_keyboard  # type: ignore
except Exception:  # pragma: no cover
  pynput_keyboard = None


class HotkeyListener:
  """Cross-platform hotkey listener with macOS fallback."""

  def __init__(
    self,
    hotkey: str,
    callback: Callable[[], None],
    extra_hotkeys: Optional[Dict[str, Callable[[], None]]] = None,
  ):
    self.primary_hotkey = (hotkey or "").lower()
    self.callbacks: Dict[str, Callable[[], None]] = {}
    if self.primary_hotkey and callback:
      self.callbacks[self.primary_hotkey] = callback

    if extra_hotkeys:
      for key, cb in extra_hotkeys.items():
        if key and cb:
          self.callbacks[key.lower()] = cb

    self._stop_event = threading.Event()
    self._thread: Optional[threading.Thread] = None
    self._listener: Optional["pynput_keyboard.Listener"] = None

  def add_hotkey(self, key: str, callback: Callable[[], None]) -> None:
    if key and callback:
      self.callbacks[key.lower()] = callback

  def start(self):
    if not self.callbacks:
      warning("HotkeyListener has no callbacks registered; nothing to start.")
      return

    if SYSTEM == "darwin":
      if pynput_keyboard:
        self._start_pynput_listener()
      else:
        warning("pynput is not installed; falling back to keyboard library.")
        self._start_keyboard_thread()
      return

    # Non-mac systems keep the existing keyboard-based behaviour.
    self._start_keyboard_thread()

  def stop(self):
    self._stop_event.set()
    if self._listener:
      self._listener.stop()
      self._listener = None

  def _start_keyboard_thread(self):
    if kb is None:
      warning("keyboard library not available; attempting pynput fallback.")
      if pynput_keyboard:
        self._start_pynput_listener()
      else:
        error("No keyboard listener backend available.")
      return

    if self._thread and self._thread.is_alive():
      return

    self._thread = threading.Thread(target=self._keyboard_loop, daemon=True)
    self._thread.start()

  def _keyboard_loop(self):
    if kb is None:
      return

    def handler(event):
      if self._stop_event.is_set():
        return
      if event.event_type != kb.KEY_DOWN:
        return
      key_name = (event.name or "").lower()
      self._invoke_hotkey(key_name, backend="keyboard")

    kb.hook(handler)

    try:
      while not self._stop_event.wait(0.1):
        pass
    except Exception as exc:  # pragma: no cover - defensive
      error(f"Hotkey listener error: {exc}")
    finally:
      kb.unhook(handler)

  def _start_pynput_listener(self):
    if not pynput_keyboard:
      error("pynput backend is not available.")
      return

    if self._listener and self._listener.running:
      return

    self._listener = pynput_keyboard.Listener(on_press=self._on_press)
    self._listener.start()
    debug("Hotkey listener started with pynput backend.")

  def _on_press(self, key):
    if self._stop_event.is_set():
      return False

    if not pynput_keyboard:
      return True

    key_name = self._normalize_pynput_key(key)
    if key_name:
      self._invoke_hotkey(key_name, backend="pynput")

    return True

  def _normalize_pynput_key(self, key) -> Optional[str]:
    if not pynput_keyboard:
      return None

    if isinstance(key, pynput_keyboard.KeyCode) and key.char:
      return key.char.lower()

    if hasattr(key, "name") and key.name:
      return key.name.lower()

    return None

  def _invoke_hotkey(self, key_name: str, backend: str) -> None:
    callback = self.callbacks.get(key_name)
    if not callback:
      return

    debug(f"Hotkey '{key_name}' pressed ({backend} backend).")
    try:
      callback()
    except Exception as exc:  # pragma: no cover - defensive
      error(f"Error in hotkey '{key_name}' callback: {exc}")
