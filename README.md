Confirmed unity works on a macbook air m2 w/ bluestacks air emulator, have to set display resolution to native (things will look small).

Press F6 to open the OCR region adjuster if fine adjustments are needed for the matching templates.

It's really hacky i'm sorry, just needed something to bang out the aptitude test event shop lol.



# Umamusume Auto Train

Like the title says, this is a simple auto training for Umamusume.

This project is inspired by [shiokaze/UmamusumeAutoTrainer](https://github.com/shiokaze/UmamusumeAutoTrainer)

Join our [discord server](https://discord.gg/vKKmYUNZuk)

[Demo video](https://youtu.be/CXSYVD-iMJk)

![Screenshot](screenshot.png)

# ⚠️ USE IT AT YOUR OWN RISK ⚠️

I am not responsible for any issues, account bans, or losses that may occur from using it.
Use responsibly and at your own discretion.

## Features

- Automatically trains Uma
- Keeps racing until fan count meets the goal, and always picks races with matching aptitude
- Checks mood
- Handle debuffs
- Rest
- Selectable G1 races in the race schedule
- Stat target feature, if a stat already hits the target, skip training that one
- Auto-purchase skill
- Web Interface for easier configuration
- Select running style position

## Getting Started

### Requirements

- [Python 3.10+](https://www.python.org/downloads/)

### Setup

#### Clone repository

```
git clone https://github.com/samsulpanjul/umamusume-auto-train.git
cd umamusume-auto-train
```

#### Install dependencies

```
pip install -r requirements.txt
```

### BEFORE YOU START

Make sure these conditions are met:

- Screen resolution must be 1920x1080
- The game should be in fullscreen
- Your Uma must have already won the trophy for each race (the bot will skips the race)
- Turn off all confirmation pop-ups in game settings
- The game must be in the career lobby screen (the one with the Tazuna hint icon)

### Bluestacks Settings

1. Set custom display size of 800x1080 and DPI to 160.
2. Make sure to set the window name in the config to match your emulator’s window title exactly. (case-sensitive)

### Bluestacks Air (macOS)

1. Install [BlueStacks Air](https://support.bluestacks.com/) and make sure the streaming window is running at 1920x1080 (Settings → Display → Set Custom profile).
2. Open **System Settings → Privacy & Security → Accessibility** and grant access to the Terminal / shell you use to run this bot so the global hotkey can be captured.
3. Update `config.json` (or the template) so that:
   - `window_name` matches the BlueStacks Air window title exactly.
   - `platform.profile` is set to `mac_bluestacks_air` (leave as `auto` if you want macOS detection to happen automatically).
   - Adjust the `platform.mac_bluestacks_air` overrides (`process_name`, `window_name`, `bounds`, etc.) only if your BlueStacks Air window title or screen layout differs from the defaults.
   - `platform.mac_bluestacks_air.display_aware_bounds` now only scales BlueStacks window bounds. The OCR-related flags under it are deprecated and ignored; use separate OCR region adjuster profiles such as `screen_share_1080p` instead.
4. Run `pip install -r requirements.txt` to ensure the optional `pynput` dependency needed for macOS hotkeys is installed.
5. If you plan to use the web UI, install its dependencies once with:

   ```
   cd web && npm install
   ```

   This pulls in the required TypeScript packages (like `zod`) so `npm run build`/`npm run dev` work locally.
6. Create `config.json` in the project root (copy `config.template.json` if needed) so both the Python bot and the React app can read your settings without TypeScript errors on build.

> The macOS flow relies on `osascript` to focus the BlueStacks Air window and may take a couple of seconds to resize the streaming canvas before the bot starts.

### Start

Run:

```
python main.py
```

Start:
press `f1` to start/stop the bot.

### OCR Region Adjuster

If `debug.region_adjuster.enabled` is set to `true` in your config, you can press `F6` at any time to open a calibration window:

- The tool captures your current screen, dims everything, and highlights the selected OCR region/BBox so you can verify alignment.
- Use the on-screen ▲ ▼ ◀ ▶ buttons (or the keyboard arrow keys) to nudge the active region 1px at a time; hold `Shift` to move in 5px steps. Pick regions from the list on the right to switch targets.
- The **Resize** controls widen/narrow or raise/lower the highlighted box so you can match different emulator/device layouts without editing constants.
- A status readout shows the detected BlueStacks window title and size so you can confirm the emulator matches the expected calibration dimensions.
- The **Window Bounds** section lets you edit the macOS `set_bounds` values (x/y/width/height) and press **Set Bounds** to immediately resize/move the BlueStacks Air window via AppleScript—handy when the window title isn't visible in the menubar.
- Click **Refresh Screenshot** after moving in-game UI elements or resizing BlueStacks to grab a new background image.
- Hit **Save Overrides** to write all current coordinates to `debug.region_adjuster.overrides_path`; closing the window automatically reloads the bot config so the new bounds take effect immediately.

This workflow replaces the older offset fields and deprecated OCR auto-scaling behavior, and makes it easier to keep macOS OCR regions tuned without editing `utils/constants.py` directly.

### Configuration

Open your browser and go to: `http://127.0.0.1:8000/` to easily edit the bot's configuration.

### Training Logic

There are 2 training logics used:

1. Train in the area with the most support cards.
2. Train in an area with a rainbow support bonus.

During the first year, the bot will prioritize the first logic to quickly unlock rainbow training.

Starting from the second year, it switches to the second logic. If there’s no rainbow training and the failure chance is still below the threshold, it falls back to the first one.

### Known Issue

- Some Uma that has special event/target goals (like Restricted Train Goldship or ~~2 G1 Race Oguri Cap~~) may not working. For Oguri Cap G1 race event goal, you need to set the races in the race schedule that match the dates of her G1 goal events.
- OCR might misread failure chance (e.g., reads 33% as 3%) and proceeds with training anyway.
- Automatically picks the top option during chain events. Be careful with Acupuncture event, it always picks the top option.
- If you bring a friend support card (like Tazuna/Aoi Kiryuin) and do recreation, the bot can't decide whether to date with the friend support card or the Uma.

### Contribute

If you run into any issues or something doesn’t work as expected, feel free to open an issue.
Contributions are very welcome! If you want to contribute, please check out the [dev](https://github.com/samsulpanjul/umamusume-auto-train/tree/dev) branch, which is used for testing new features. I truly appreciate any support to help improve this project further.
