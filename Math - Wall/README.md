# Math - Wall Interactive Quiz Game

🌐 **ภาษา / Language:** ภาษาไทย | [English](README-en.md)

---

ระบบเกมตอบคำถามแบบ Interactive สำหรับการฉายลงพื้นและผนัง ใช้งานร่วมกับ TouchDesigner และ Python tracker

---

## สารบัญ

- [ภาพรวมระบบ](#ภาพรวมระบบ)
- [โครงสร้างไฟล์](#โครงสร้างไฟล์)
- [การติดตั้ง](#การติดตั้ง)
- [การตั้งค่า TouchDesigner](#การตั้งค่า-touchdesigner)
- [การใช้งาน Tracker](#การใช้งาน-tracker)
- [Game Flow](#game-flow)
- [Node Reference](#node-reference)
- [Script Reference](#script-reference)
- [OSC Reference](#osc-reference)
- [การปรับแต่ง](#การปรับแต่ง)

---

## ภาพรวมระบบ

```
กล้อง (D435 / Webcam)
        ↓
combined_tracker.py  ──OSC port 7000──→  TouchDesigner
        ↓                                       ↓
Zone Detection                         Level Select / Game Logic
Jump Detection                         Video Playback
Answer Zone Detection                  Audio Feedback
```

**3 จอ:**
- จอ 0 — Question (q) + Video (v)
- จอ 1 — Choice (c) + Answer (a) + Check overlay
- จอ 2 — สำรอง (ขยายภาพได้)

**3 Level, 10 ข้อ/Level**

---

## โครงสร้างไฟล์

```
Math - Wall/
├── Main.toe                    ← TouchDesigner project
├── combined_tracker.py         ← Zone + Jump tracker (กล้องเดียว)
├── topdown_tracker.py          ← Top-down zone tracker (กล้องบนหัว)
├── yolo_tracker.py             ← Wrist tracker (ไม่ได้ใช้หลัก)
├── run.bat                     ← เปิด tracker (Windows)
├── requirements.txt            ← Python dependencies
│
└── scripts/
    ├── execute_keyboard_row.py       ← กด 1-0 เลือกข้อ
    ├── execute_keyboard_level.py     ← Ctrl+1/2/3 เลือก Level
    ├── execute_keyboard_reset.py     ← กด = รีเซ็ต
    ├── execute_mouse_levelselect.py  ← Level Select logic (OSC)
    ├── execute_timer_hold.py         ← timer_hold done → เลือก Level
    ├── execute_timer_intro.py        ← timer_intro done → countdown
    ├── execute_timer_countdown.py    ← 3,2,1 → เริ่มเกม
    ├── execute_timer_cdelay.py       ← 3s delay → แสดง Duration
    ├── execute_timer_question.py     ← timer_question done → แสดง Answer
    ├── execute_timer_answer.py       ← timer_answer done → แสดง Video
    └── execute_answer_check.py       ← แสดง Checkmark ตาม zone
```

---

## การติดตั้ง

### Python (Tracker)

```
Python 3.11+ แนะนำ
```

ดับเบิลคลิก `run.bat` — จะติดตั้ง packages อัตโนมัติครั้งแรก

**packages:**
```
ultralytics
pyrealsense2
python-osc
opencv-python
numpy
```

### TouchDesigner

- TouchDesigner 2025.32280+
- เปิดไฟล์ `Main.toe`

---

## การตั้งค่า TouchDesigner

### OSC In CHOP

| ชื่อ Node | Port | ใช้งาน |
|---|---|---|
| `oscin_combined` | 7000 | รับทุก channel จาก combined_tracker |

### Table DAT (ข้อมูลคำถาม)

`table1`, `table2`, `table3` — แต่ละตารางมีคอลัมน์:

| คอลัมน์ | ชนิด | ตัวอย่าง |
|---|---|---|
| `question` | path | `videos/q1.mp4` |
| `choice` | path | `videos/c1.mp4` |
| `answer` | path | `videos/a1.mp4` |
| `video` | path | `videos/v1.mp4` |
| `video2` | path | `videos/v1_s2.mp4` |
| `duration` | float | `10` |

### Keyboard Shortcuts

| ปุ่ม | ฟังก์ชัน |
|---|---|
| `=` | รีเซ็ตกลับ Level Select |
| `Ctrl+1/2/3` | เลือก Level 1/2/3 |
| `1-9, 0` | เลือกข้อ 1-10 |

---

## การใช้งาน Tracker

เปิด `run.bat` แล้วเลือก:

```
1. Combined Tracker  ← แนะนำ (Zone + Jump, กล้องเดียว)
2. Zone Only Tracker
3. Wrist Only Tracker
```

### Combined Tracker Controls

| ปุ่ม | ฟังก์ชัน |
|---|---|
| `SPACE` | ถ่าย Background (ต้องเคลียร์กรอบก่อน) |
| `R` | รีเซ็ต Background + Baseline |
| `B` | รีเซ็ต Jump Baseline เฉพาะ |
| `+` / `-` | ปรับความไว Background (5-255) |
| `D` | แสดง Blob areas (debug) |
| `Q` | ออก |

### การ Calibrate

1. ให้คนออกจากกรอบกล้อง
2. กด `SPACE` เพื่อถ่าย Background
3. ยืนในกรอบนิ่งๆ 2-3 วิ เพื่อ calibrate Jump baseline
4. ถ้า jump baseline ผิด กด `B` แล้วยืนนิ่งใหม่

---

## Game Flow

### Level Select

```
เปิดโปรแกรม / กด =
        ↓
แสดง Level Select (ls videos + BG ls)
        ↓
ยืนใน Zone 1/2/3  →  กล้วย Scale ขึ้น (0.8→0.85) + เสียง hover
        ↓
กระโดด 1 ครั้ง  →  เลือก Level นั้น
        ↓
แสดง Topic video 5 วิ
        ↓
นับถอยหลัง 3, 2, 1
        ↓
เริ่มข้อ 1
```

### Question Flow

```
Q + C แสดงพร้อมกัน
        ↓
รอ 3 วิ  →  Duration Countdown แสดง + timer_question เริ่มนับ
        ↓
timer_question หมดเวลา  →  Answer แสดง 5 วิ
        ↓
Video Loop (จอ 0 = v, จอ 1 = v2)
        ↓
กด 1-0 เพื่อไปข้อถัดไป
```

### Answer Zone (ตอบคำถาม)

```
ยืนฝั่งซ้าย  →  Check A กระพริบ
ยืนฝั่งขวา  →  Check B กระพริบ
เหลือ 3 วิสุดท้าย  →  Lock ฝั่งที่ยืนอยู่ (ไม่กระพริบ ย้ายไม่ได้)
timer_question หมด  →  ซ่อน checkmark ทั้งหมด
```

---

## Node Reference

### Movie File In TOPs

| ชื่อ | ใช้งาน |
|---|---|
| `moviefilein_q` | Question video (จอ 0) |
| `moviefilein_c` | Choice video (จอ 1) |
| `moviefilein_a` | Answer video (จอ 1) |
| `moviefilein_v` | Result video (จอ 0) |
| `moviefilein_v2` | Result video (จอ 1) |
| `moviefilein_ls1/2/3` | Level Select videos |
| `moviefilein_topic1/2/3` | Topic intro videos |
| `moviefilein_bg0_ls/1/2/3` | Background จอ 0 |
| `moviefilein_bg1_ls/1/2/3` | Background จอ 1 |
| `moviefilein_check_a/b` | Checkmark overlay |

### Level TOPs (Opacity Control)

| ชื่อ | ใช้งาน |
|---|---|
| `level_top_q` | ควบคุม q |
| `level_top_c` | ควบคุม c |
| `level_top_a` | ควบคุม a |
| `level_top_v` | ควบคุม v (จอ 0) |
| `level_top_v2` | ควบคุม v2 (จอ 1) |
| `level_top_ls` | ควบคุม Level Select |
| `level_topic1/2/3` | ควบคุม Topic |
| `level_duration` | ควบคุม Duration countdown |
| `level_top_check_a/b` | ควบคุม Checkmark |

### Switch TOPs

| ชื่อ | Input 0 | Input 1 | Input 2 | Input 3 |
|---|---|---|---|---|
| `switch_bg0` | bg0_ls | bg0_1 | bg0_2 | bg0_3 |
| `switch_bg1` | bg1_ls | bg1_1 | bg1_2 | bg1_3 |

### Transform TOPs

| ชื่อ | Normal Scale | Hover Scale |
|---|---|---|
| `transform_ls1/2/3` | 0.8 | 0.85 |

### Timer CHOPs

| ชื่อ | ความยาว | ใช้งาน |
|---|---|---|
| `timer_intro` | 5s | แสดง Topic |
| `timer_countdown` | 1s × 3 | นับ 3,2,1 |
| `timer_hold` | 0.1s | Trigger Level select |
| `timer_cdelay` | 3s | Delay ก่อนแสดง Duration |
| `timer_question` | duration+1 | นับเวลาตอบ |
| `timer_answer` | 5s | แสดง Answer |

### Constant CHOPs

| ชื่อ | ค่า | ใช้งาน |
|---|---|---|
| `constant_level` | 0-3 | Level ปัจจุบัน (0=ls) |
| `constant_state` | 0-2 | State (0=q, 1=a, 2=v) |
| `constant_row` | 1-10 | ข้อปัจจุบัน |
| `constant_duration` | float | Duration ของข้อ |
| `constant_hover_level` | 0-3 | Level ที่กำลัง hover |

### Audio

| ชื่อ | เล่นตอน |
|---|---|
| `audiofilein_hover` | เดินเข้า zone ใหม่ |
| `audiofilein_select` | เลือก Level สำเร็จ |

---

## OSC Reference

ทุก channel ส่งจาก `combined_tracker.py` ไปที่ `oscin_combined` port 7000

| Channel | ชนิด | ค่า |
|---|---|---|
| `zone/1/active` | int | 1=มีคนใน zone 1 |
| `zone/2/active` | int | 1=มีคนใน zone 2 |
| `zone/3/active` | int | 1=มีคนใน zone 3 |
| `jump/active` | int | 1=กระโดด |
| `jump/y` | float | ความสูงของการกระโดด |
| `answer/a/active` | int | 1=มีคนยืนฝั่งซ้าย |
| `answer/b/active` | int | 1=มีคนยืนฝั่งขวา |
| `persons/count` | int | จำนวน blob ที่ detect |

---

## การปรับแต่ง

### combined_tracker.py

```python
JUMP_THRESHOLD   = 0.06   # ความสูงขั้นต่ำของการกระโดด
JUMP_VEL_THRESH  = 0.02   # ความเร็วขั้นต่ำของการกระโดด
JUMP_HOLD_FRAMES = 6      # จำนวน frame ที่ค้าง active หลัง detect
MIN_BLOB_AREA    = 40000  # ขนาด blob ขั้นต่ำ (pixel)
MAX_BLOB_AREA    = 150000 # ขนาด blob สูงสุด
DEPTH_MIN        = 0.3    # ระยะใกล้สุด (เมตร) สำหรับ D435
DEPTH_MAX        = 2.4    # ระยะไกลสุด (เมตร)

DEFAULT_ZONES = {
    1: (0.00, 0.00, 0.33, 1.00),  # Zone 1 (ซ้าย)
    2: (0.33, 0.00, 0.67, 1.00),  # Zone 2 (กลาง)
    3: (0.67, 0.00, 1.00, 1.00),  # Zone 3 (ขวา)
}
```

### execute_mouse_levelselect.py

```python
SCALE_HOVER  = 0.85  # ขนาดกล้วยตอน hover
SCALE_NORMAL = 0.8   # ขนาดกล้วยปกติ
JUMP_COOLDOWN_FRAMES = 90  # ~1.5 วิ cooldown หลังกระโดด
```

### execute_answer_check.py

```python
LOCK_BEFORE  = 3.0   # ล็อกคำตอบก่อนหมดเวลากี่วิ
BLINK_SPEED  = 3.0   # กระพริบกี่ครั้งต่อวิ (ตอนยังไม่ล็อก)
```