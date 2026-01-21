# core/bot.py - Global bot state
# Shared state variables for the bot, accessible across modules

import threading

# Bot running state
is_bot_running = False
bot_thread = None
bot_lock = threading.Lock()
stop_event = threading.Event()

# Hotkey configuration
hotkey = "f1"

# Device/platform configuration
use_adb = False
device_id = None

# Window reference (platform-specific)
# - On Windows: pygetwindow Window object
# - On macOS: None (uses AppleScript for focus)
windows_window = None

# Training state
PREFERRED_POSITION_SET = False
