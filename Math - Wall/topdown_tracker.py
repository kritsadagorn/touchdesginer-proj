"""
topdown_tracker.py
------------------
Top-down person position tracker
Uses Background Subtraction to remove projector background
then finds person blobs - works even with projector interference

OSC output (port 7001):
  /persons/count     int
  /persons/0/x       float  -0.5 to 0.5
  /persons/0/y       float  -0.5 to 0.5
  /persons/1/x ...
"""

import sys
import cv2
import numpy as np
from pythonosc import udp_client

# ── config ─────────────────────────────────────────────────────────────────────
OSC_IP      = "127.0.0.1"
OSC_PORT    = 7001
MAX_PERSONS = 6
SMOOTH      = 0.2

# Background subtraction config
BG_HISTORY       = 500    # frames to learn background
BG_THRESHOLD     = 25     # sensitivity (lower = more sensitive)
BG_LEARN_RATE    = 0.002  # how fast bg updates (0=static, 1=instant)

# Blob filter
MIN_BLOB_AREA    = 3000   # min pixel area to count as person
MAX_BLOB_AREA    = 200000 # max pixel area (filter noise)

# ── RealSense ──────────────────────────────────────────────────────────────────
USE_REALSENSE = False
rs_devices    = []
try:
    import pyrealsense2 as rs
    ctx = rs.context()
    rs_devices = list(ctx.query_devices())
    if rs_devices:
        USE_REALSENSE = True
except ImportError:
    pass


# ── camera scanning ────────────────────────────────────────────────────────────
def scan_webcams(max_index=8):
    found = []
    print("[scan] Scanning webcams...")
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                found.append({'type': 'webcam', 'index': i,
                              'name': f"Webcam index {i}", 'cap': cap})
                print(f"  [found] Webcam index {i}")
            else:
                cap.release()
        else:
            cap.release()
    return found


def scan_all_cameras():
    cameras = []
    if USE_REALSENSE:
        for dev in rs_devices:
            name   = dev.get_info(rs.camera_info.name)
            serial = dev.get_info(rs.camera_info.serial_number)
            cameras.append({'type': 'realsense', 'serial': serial,
                            'name': f"RealSense {name} (S/N: {serial})"})
            print(f"  [found] RealSense: {name}")
    cameras.extend(scan_webcams())
    return cameras


def select_camera(cameras):
    if not cameras:
        print("[ERROR] No cameras found")
        sys.exit(1)
    print("\n" + "="*52)
    print(" Available cameras:")
    print("="*52)
    for i, cam in enumerate(cameras):
        print(f"  {i+1}. {cam['name']}")
    print("="*52)
    while True:
        try:
            idx = int(input(f"\nSelect top-down camera [1-{len(cameras)}]: ").strip()) - 1
            if 0 <= idx < len(cameras):
                return cameras[idx]
        except (ValueError, KeyboardInterrupt):
            sys.exit(0)


# ── camera open ────────────────────────────────────────────────────────────────
def open_realsense(serial):
    pipeline = rs.pipeline()
    config   = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
    pipeline.start(config)
    return pipeline


def get_frame_realsense(pipeline):
    try:
        frames = pipeline.wait_for_frames(timeout_ms=3000)
        f = frames.get_color_frame()
        return np.asanyarray(f.get_data()) if f else None
    except RuntimeError:
        return None


def open_webcam(selected):
    if 'cap' in selected and selected['cap'].isOpened():
        cap = selected['cap']
    else:
        cap = cv2.VideoCapture(selected['index'], cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def get_frame_webcam(cap):
    ret, frame = cap.read()
    if not ret or frame is None:
        return None
    h, w = frame.shape[:2]
    target_h = int(w * 9 / 16)
    if target_h > h:
        target_h = h
        target_w = int(h * 16 / 9)
    else:
        target_w = w
    x0 = (w - target_w) // 2
    y0 = (h - target_h) // 2
    frame = frame[y0:y0+target_h, x0:x0+target_w]
    return cv2.resize(frame, (1280, 720))


# ── smoothing ──────────────────────────────────────────────────────────────────
smooth_positions = [[0.0, 0.0] for _ in range(MAX_PERSONS)]

def smooth_pos(slot, nx, ny):
    smooth_positions[slot][0] = smooth_positions[slot][0] * SMOOTH + nx * (1 - SMOOTH)
    smooth_positions[slot][1] = smooth_positions[slot][1] * SMOOTH + ny * (1 - SMOOTH)
    return smooth_positions[slot][0], smooth_positions[slot][1]


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*52)
    print(" Top-Down Person Tracker (Background Subtraction)")
    print(f" OSC -> {OSC_IP}:{OSC_PORT}")
    print("="*52)

    print("\n[scan] Looking for cameras...")
    cameras = scan_all_cameras()
    if not cameras:
        print("[ERROR] No cameras found")
        input("Press Enter to exit...")
        sys.exit(1)

    selected = select_camera(cameras)
    print(f"\n[tracker] Using: {selected['name']}")

    # release unused webcams
    for cam_info in cameras:
        if cam_info is not selected and cam_info.get('type') == 'webcam':
            c = cam_info.get('cap')
            if c and c.isOpened():
                c.release()

    if selected['type'] == 'realsense':
        cam       = open_realsense(selected['serial'])
        get_frame = lambda: get_frame_realsense(cam)
    else:
        cam       = open_webcam(selected)
        get_frame = lambda: get_frame_webcam(cam)

    osc = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    print(f"[tracker] OSC ready -> {OSC_IP}:{OSC_PORT}")

    # ── background subtractor ──────────────────────────────────────────────────
    bg_sub = cv2.createBackgroundSubtractorMOG2(
        history=BG_HISTORY,
        varThreshold=BG_THRESHOLD,
        detectShadows=False
    )

    print("\n" + "="*52)
    print(" CONTROLS:")
    print("  SPACE = capture static background (freeze BG learning)")
    print("  R     = reset background (re-learn)")
    print("  +/-   = increase/decrease sensitivity")
    print("  Q     = quit")
    print("="*52)
    print("\n[tracker] Learning background... move out of frame first!")
    print("[tracker] Press SPACE when background is ready\n")

    bg_frozen   = False
    static_bg   = None
    threshold   = BG_THRESHOLD
    learn_rate  = BG_LEARN_RATE

    kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5,  5))
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (25, 25))

    while True:
        frame = get_frame()
        if frame is None:
            continue

        h, w = frame.shape[:2]
        display = frame.copy()

        # ── compute foreground mask ────────────────────────────────────────────
        if bg_frozen and static_bg is not None:
            # static background subtraction
            diff   = cv2.absdiff(frame, static_bg)
            gray   = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg_mask = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
        else:
            # adaptive MOG2
            fg_mask = bg_sub.apply(frame, learningRate=learn_rate)

        # morphology: remove noise, fill holes
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_OPEN,  kernel_open)
        fg_mask = cv2.morphologyEx(fg_mask, cv2.MORPH_CLOSE, kernel_close)

        # ── find blobs ─────────────────────────────────────────────────────────
        contours, _ = cv2.findContours(
            fg_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        persons = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if MIN_BLOB_AREA < area < MAX_BLOB_AREA:
                M  = cv2.moments(cnt)
                if M['m00'] == 0:
                    continue
                cx = int(M['m10'] / M['m00'])
                cy = int(M['m01'] / M['m00'])
                nx =  (cx / w) - 0.5
                ny = -(cy / h) + 0.5
                persons.append((nx, ny, cx, cy, cnt))

        # sort left to right
        persons.sort(key=lambda p: p[0])
        count = min(len(persons), MAX_PERSONS)

        # ── send OSC ───────────────────────────────────────────────────────────
        osc.send_message("/persons/count", count)
        for i in range(MAX_PERSONS):
            if i < count:
                nx, ny = persons[i][0], persons[i][1]
                sx, sy = smooth_pos(i, nx, ny)
                osc.send_message(f"/persons/{i}/x", float(sx))
                osc.send_message(f"/persons/{i}/y", float(sy))
            else:
                osc.send_message(f"/persons/{i}/x", 0.0)
                osc.send_message(f"/persons/{i}/y", 0.0)

        # ── draw overlay ───────────────────────────────────────────────────────
        # fg mask overlay (semi-transparent)
        mask_color = cv2.cvtColor(fg_mask, cv2.COLOR_GRAY2BGR)
        mask_color[:,:,0] = 0  # only green/red channels
        display = cv2.addWeighted(display, 0.7, mask_color, 0.3, 0)

        for i, (nx, ny, cx, cy, cnt) in enumerate(persons[:MAX_PERSONS]):
            sx, sy = smooth_positions[i]
            cv2.drawContours(display, [cnt], -1, (0, 255, 0), 2)
            cv2.circle(display, (cx, cy), 10, (0, 255, 255), -1)
            cv2.putText(display, f"P{i} ({sx:+.2f},{sy:+.2f})",
                        (cx+12, cy), cv2.FONT_HERSHEY_SIMPLEX,
                        0.6, (0, 255, 0), 2)

        status = "BG: FROZEN" if bg_frozen else f"BG: LEARNING (rate={learn_rate:.3f})"
        cv2.putText(display, f"Persons: {count}  |  {status}  |  thr={threshold}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (0, 200, 255), 2)
        cv2.putText(display, "SPACE=freeze BG  R=reset  +/-=sensitivity  Q=quit",
                    (10, h-12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

        # mask thumbnail (top-right)
        thumb = cv2.resize(fg_mask, (320, 180))
        thumb_bgr = cv2.cvtColor(thumb, cv2.COLOR_GRAY2BGR)
        display[0:180, w-320:w] = thumb_bgr
        cv2.putText(display, "FG mask", (w-310, 170),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)

        cv2.imshow("Top-Down Tracker  [Q=quit]", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord(' '):
            # freeze background
            static_bg  = frame.copy()
            bg_frozen  = True
            learn_rate = 0
            print("[tracker] Background FROZEN")
        elif key == ord('r'):
            # reset and re-learn
            bg_sub     = cv2.createBackgroundSubtractorMOG2(
                history=BG_HISTORY, varThreshold=threshold, detectShadows=False)
            static_bg  = None
            bg_frozen  = False
            learn_rate = BG_LEARN_RATE
            print("[tracker] Background RESET - learning...")
        elif key == ord('+') or key == ord('='):
            threshold = max(5, threshold - 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('-'):
            threshold = min(100, threshold + 5)
            print(f"[tracker] Threshold = {threshold}")

    cv2.destroyAllWindows()
    if selected['type'] == 'realsense':
        cam.stop()
    else:
        cam.release()
    print("[tracker] Stopped")


if __name__ == "__main__":
    main()
