# Math - Wall Interactive Quiz Game

🌐 **Language:** [ภาษาไทย](README.md) | English

---

## Table of Contents

- [System Overview](#system-overview)
- [File Structure](#file-structure)
- [Installation](#installation)
- [TouchDesigner Setup](#touchdesigner-setup)
- [Using the Tracker](#using-the-tracker)
- [Game Flow](#game-flow)
- [Node Reference](#node-reference)
- [Script Reference](#script-reference)
- [OSC Reference](#osc-reference)
- [Configuration](#configuration)

---

## System Overview

```
Camera (D435 / Webcam)
        ↓
combined_tracker.py  ──OSC port 7000──→  TouchDesigner
        ↓                                       ↓
Zone Detection                         Level Select / Game Logic
Jump Detection                         Video Playback
Answer Zone Detection                  Audio Feedback
```

**3 Screens:**
- Screen 0 — Question (q) + Video (v)
- Screen 1 — Choice (c) + Answer (a) + Check overlay
- Screen 2 — Reserved (expandable)

**3 Levels, 10 Questions/Level**

---

## File Structure

```
Math - Wall/
├── Main.toe                    ← TouchDesigner project
├── combined_tracker.py         ← Zone + Jump tracker (single camera)
├── topdown_tracker.py          ← Top-down zone tracker (overhead camera)
├── yolo_tracker.py             ← Wrist tracker (not primary)
├── run.bat                     ← Launch tracker (Windows)
├── requirements.txt            ← Python dependencies
│
└── scripts/
    ├── execute_keyboard_row.py       ← Press 1-0 to select question
    ├── execute_keyboard_level.py     ← Ctrl+1/2/3 to select level
    ├── execute_keyboard_reset.py     ← Press = to reset
    ├── execute_mouse_levelselect.py  ← Level Select logic (OSC)
    ├── execute_timer_hold.py         ← timer_hold done → select level
    ├── execute_timer_intro.py        ← timer_intro done → countdown
    ├── execute_timer_countdown.py    ← 3,2,1 → start game
    ├── execute_timer_cdelay.py       ← 3s delay → show Duration
    ├── execute_timer_question.py     ← timer_question done → show Answer
    ├── execute_timer_answer.py       ← timer_answer done → show Video
    └── execute_answer_check.py       ← Show checkmark by zone
```

---

## Installation

### Python (Tracker)

```
Python 3.11+ recommended
```

Double-click `run.bat` — packages will be installed automatically on first run.

**Packages:**
```
ultralytics
pyrealsense2
python-osc
opencv-python
numpy
```

### TouchDesigner

- TouchDesigner 2025.32280+
- Open `Main.toe`

---

## TouchDesigner Setup

### OSC In CHOP

| Node Name | Port | Purpose |
|---|---|---|
| `oscin_combined` | 7000 | Receives all channels from combined_tracker |

### Table DAT (Question Data)

`table1`, `table2`, `table3` — each table has the following columns:

| Column | Type | Example |
|---|---|---|
| `question` | path | `videos/q1.mp4` |
| `choice` | path | `videos/c1.mp4` |
| `answer` | path | `videos/a1.mp4` |
| `video` | path | `videos/v1.mp4` |
| `video2` | path | `videos/v1_s2.mp4` |
| `duration` | float | `10` |

### Keyboard Shortcuts

| Key | Function |
|---|---|
| `=` | Reset to Level Select |
| `Ctrl+1/2/3` | Select Level 1/2/3 |
| `1-9, 0` | Select Question 1-10 |

---

## Using the Tracker

Open `run.bat` and select:

```
1. Combined Tracker  ← Recommended (Zone + Jump, single camera)
2. Zone Only Tracker
3. Wrist Only Tracker
```

### Combined Tracker Controls

| Key | Function |
|---|---|
| `SPACE` | Capture background (clear frame first) |
| `R` | Reset background + baseline |
| `B` | Reset jump baseline only |
| `+` / `-` | Adjust BG sensitivity (5-255) |
| `D` | Print blob areas (debug) |
| `Q` | Quit |

### Calibration Steps

1. Clear all people from camera frame
2. Press `SPACE` to capture background
3. Stand still in frame for 2-3 seconds to calibrate jump baseline
4. If jump baseline is wrong, press `B` and stand still again

---

## Game Flow

### Level Select

```
Launch / Press =
        ↓
Show Level Select (ls videos + BG ls)
        ↓
Stand in Zone 1/2/3  →  Banana scales up (0.8→0.85) + hover sound
        ↓
Jump once  →  Select that level
        ↓
Show Topic video for 5s
        ↓
Countdown 3, 2, 1
        ↓
Start Question 1
```

### Question Flow

```
Q + C appear simultaneously
        ↓
Wait 3s  →  Duration Countdown appears + timer_question starts
        ↓
timer_question ends  →  Answer shown for 5s
        ↓
Video Loop (Screen 0 = v, Screen 1 = v2)
        ↓
Press 1-0 to go to next question
```

### Answer Zone (Answering Questions)

```
Stand left side   →  Check A blinks
Stand right side  →  Check B blinks
Last 3 seconds    →  Lock current side (no blink, can't change)
timer_question ends  →  Hide all checkmarks
```

---

## Node Reference

### Movie File In TOPs

| Name | Purpose |
|---|---|
| `moviefilein_q` | Question video (Screen 0) |
| `moviefilein_c` | Choice video (Screen 1) |
| `moviefilein_a` | Answer video (Screen 1) |
| `moviefilein_v` | Result video (Screen 0) |
| `moviefilein_v2` | Result video (Screen 1) |
| `moviefilein_ls1/2/3` | Level Select videos |
| `moviefilein_topic1/2/3` | Topic intro videos |
| `moviefilein_bg0_ls/1/2/3` | Background Screen 0 |
| `moviefilein_bg1_ls/1/2/3` | Background Screen 1 |
| `moviefilein_check_a/b` | Checkmark overlay |

### Level TOPs (Opacity Control)

| Name | Purpose |
|---|---|
| `level_top_q` | Controls q |
| `level_top_c` | Controls c |
| `level_top_a` | Controls a |
| `level_top_v` | Controls v (Screen 0) |
| `level_top_v2` | Controls v2 (Screen 1) |
| `level_top_ls` | Controls Level Select |
| `level_topic1/2/3` | Controls Topic |
| `level_duration` | Controls Duration countdown |
| `level_top_check_a/b` | Controls Checkmark |

### Switch TOPs

| Name | Input 0 | Input 1 | Input 2 | Input 3 |
|---|---|---|---|---|
| `switch_bg0` | bg0_ls | bg0_1 | bg0_2 | bg0_3 |
| `switch_bg1` | bg1_ls | bg1_1 | bg1_2 | bg1_3 |

### Transform TOPs

| Name | Normal Scale | Hover Scale |
|---|---|---|
| `transform_ls1/2/3` | 0.8 | 0.85 |

### Timer CHOPs

| Name | Length | Purpose |
|---|---|---|
| `timer_intro` | 5s | Show Topic |
| `timer_countdown` | 1s × 3 | Count 3,2,1 |
| `timer_hold` | 0.1s | Trigger level selection |
| `timer_cdelay` | 3s | Delay before showing Duration |
| `timer_question` | duration+1 | Question timer |
| `timer_answer` | 5s | Show Answer |

### Constant CHOPs

| Name | Value | Purpose |
|---|---|---|
| `constant_level` | 0-3 | Current level (0=ls) |
| `constant_state` | 0-2 | State (0=q, 1=a, 2=v) |
| `constant_row` | 1-10 | Current question |
| `constant_duration` | float | Question duration |
| `constant_hover_level` | 0-3 | Currently hovered level |

### Audio

| Name | Plays When |
|---|---|
| `audiofilein_hover` | Entering a new zone |
| `audiofilein_select` | Successfully selecting a level |

---

## OSC Reference

All channels are sent from `combined_tracker.py` to `oscin_combined` on port 7000.

| Channel | Type | Value |
|---|---|---|
| `zone/1/active` | int | 1=person in zone 1 |
| `zone/2/active` | int | 1=person in zone 2 |
| `zone/3/active` | int | 1=person in zone 3 |
| `jump/active` | int | 1=jumping |
| `jump/y` | float | jump height above baseline |
| `answer/a/active` | int | 1=person on left side |
| `answer/b/active` | int | 1=person on right side |
| `persons/count` | int | number of detected blobs |

---

## Configuration

### combined_tracker.py

```python
JUMP_THRESHOLD   = 0.06   # minimum jump height
JUMP_VEL_THRESH  = 0.02   # minimum jump velocity
JUMP_HOLD_FRAMES = 6      # frames to hold active after detection
MIN_BLOB_AREA    = 40000  # minimum blob size (pixels)
MAX_BLOB_AREA    = 150000 # maximum blob size
DEPTH_MIN        = 0.3    # near clipping (meters) for D435
DEPTH_MAX        = 2.4    # far clipping (meters)

DEFAULT_ZONES = {
    1: (0.00, 0.00, 0.33, 1.00),  # Zone 1 (left)
    2: (0.33, 0.00, 0.67, 1.00),  # Zone 2 (center)
    3: (0.67, 0.00, 1.00, 1.00),  # Zone 3 (right)
}
```

### execute_mouse_levelselect.py

```python
SCALE_HOVER  = 0.85  # banana scale when hovering
SCALE_NORMAL = 0.8   # banana scale normal
JUMP_COOLDOWN_FRAMES = 90  # ~1.5s cooldown after jump
```

### execute_answer_check.py

```python
LOCK_BEFORE  = 3.0   # lock answer N seconds before time is up
BLINK_SPEED  = 3.0   # blink rate before lock (times per second)
```