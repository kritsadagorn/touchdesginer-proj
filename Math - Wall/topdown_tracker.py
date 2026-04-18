"""
topdown_tracker.py
------------------
Wall/Floor person zone detector using static background subtraction
Detects which ZONE (1/2/3) a person is standing in
Sends OSC to TouchDesigner for level selection

OSC output (port 7001):
  /zone/1/active   int  1=person detected in zone 1, 0=not
  /zone/2/active   int  1=person detected in zone 2, 0=not
  /zone/3/active   int  1=person detected in zone 3, 0=not
  /persons/count   int  total persons detected

Controls:
  SPACE = capture static background (must be empty frame)
  R     = reset background
  1/2/3 = toggle zone edit mode (drag to set zone)
  +/-   = adjust sensitivity
  D     = debug blob areas
  Q     = quit
"""

import sys
import cv2
import numpy as np
from pythonosc import udp_client

# ── config ─────────────────────────────────────────────────────────────────────
OSC_IP    = "127.0.0.1"
OSC_PORT  = 7001
SMOOTH    = 0.15  # smoothing for zone activity (0=instant 1=freeze)

# Blob filter - ปรับตามขนาดคนในภาพ
MIN_BLOB_AREA = 2000
MAX_BLOB_AREA = 150000

# Default zones (x1, y1, x2, y2) normalized 0.0-1.0
# แบ่ง 3 แนวตั้ง (ซ้าย กลาง ขวา)
DEFAULT_ZONES = {
    1: (0.00, 0.00, 0.33, 1.00),
    2: (0.33, 0.00, 0.67, 1.00),
    3: (0.67, 0.00, 1.00, 1.00),
}

ZONE_COLORS = {
    1: (255, 80,  80),   # blue
    2: (80,  255, 80),   # green
    3: (80,  80,  255),  # red
}

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


# ── camera ─────────────────────────────────────────────────────────────────────
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
            idx = int(input(f"\nSelect camera [1-{len(cameras)}]: ").strip()) - 1
            if 0 <= idx < len(cameras):
                return cameras[idx]
        except (ValueError, KeyboardInterrupt):
            sys.exit(0)


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
    return cv2.resize(frame[y0:y0+target_h, x0:x0+target_w], (1280, 720))


# ── zone helpers ───────────────────────────────────────────────────────────────
def zone_px(zone, w, h):
    """Convert normalized zone to pixel coords"""
    z = zone
    return int(z[0]*w), int(z[1]*h), int(z[2]*w), int(z[3]*h)


def blob_in_zone(cx, cy, zone, w, h):
    x1, y1, x2, y2 = zone_px(zone, w, h)
    return x1 <= cx <= x2 and y1 <= cy <= y2


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    print("\n" + "="*52)
    print(" Zone Detector (Static Background Subtraction)")
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
    print(f"[tracker] OSC -> {OSC_IP}:{OSC_PORT}")

    zones     = dict(DEFAULT_ZONES)   # mutable copy
    static_bg = None
    threshold = 30
    zone_active = {1: 0.0, 2: 0.0, 3: 0.0}  # smoothed activity

    kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,  7))
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (20, 20))

    print("\n" + "="*52)
    print(" CONTROLS:")
    print("  SPACE = capture background (clear the frame first!)")
    print("  R     = reset background")
    print("  +/-   = adjust sensitivity")
    print("  D     = print blob areas for tuning")
    print("  Q     = quit")
    print("="*52)
    print("\n[tracker] Clear the frame then press SPACE to capture background\n")

    while True:
        frame = get_frame()
        if frame is None:
            continue

        h, w = frame.shape[:2]
        display = frame.copy()

        # ── background subtraction (static only) ──────────────────────────────
        if static_bg is not None:
            diff  = cv2.absdiff(frame, static_bg)
            gray  = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
            fg    = cv2.morphologyEx(fg, cv2.MORPH_OPEN,  kernel_open)
            fg    = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel_close)
        else:
            fg = np.zeros((h, w), dtype=np.uint8)

        # ── find blobs ─────────────────────────────────────────────────────────
        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL,
                                       cv2.CHAIN_APPROX_SIMPLE)
        valid_blobs = []
        for cnt in contours:
            area = cv2.contourArea(cnt)
            if MIN_BLOB_AREA < area < MAX_BLOB_AREA:
                M = cv2.moments(cnt)
                if M['m00'] == 0:
                    continue
                cx = int(M['m10'] / M['m00'])
                cy = int(M['m01'] / M['m00'])
                valid_blobs.append((cx, cy, cnt, area))

        # ── check zones ────────────────────────────────────────────────────────
        zone_hit = {1: False, 2: False, 3: False}
        for cx, cy, cnt, area in valid_blobs:
            for zid, zone in zones.items():
                if blob_in_zone(cx, cy, zone, w, h):
                    zone_hit[zid] = True

        # smooth zone activity
        for zid in [1, 2, 3]:
            target = 1.0 if zone_hit[zid] else 0.0
            zone_active[zid] = zone_active[zid] * SMOOTH + target * (1 - SMOOTH)

        # send OSC
        osc.send_message("/persons/count", len(valid_blobs))
        for zid in [1, 2, 3]:
            active = 1 if zone_active[zid] > 0.5 else 0
            osc.send_message(f"/zone/{zid}/active", active)

        # ── draw zones ─────────────────────────────────────────────────────────
        for zid, zone in zones.items():
            x1, y1, x2, y2 = zone_px(zone, w, h)
            color  = ZONE_COLORS[zid]
            active = zone_active[zid] > 0.5
            alpha  = 0.35 if active else 0.1
            overlay = display.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            display = cv2.addWeighted(overlay, alpha, display, 1-alpha, 0)
            thick = 3 if active else 1
            cv2.rectangle(display, (x1, y1), (x2, y2), color, thick)
            label = f"L{zid}  {'ACTIVE' if active else ''}"
            cv2.putText(display, label,
                        (x1+10, y1+40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

        # draw blobs
        for cx, cy, cnt, area in valid_blobs:
            cv2.drawContours(display, [cnt], -1, (0, 255, 255), 2)
            cv2.circle(display, (cx, cy), 8, (0, 255, 255), -1)

        # status bar
        bg_status = "BG: READY" if static_bg is not None else "BG: NOT SET (press SPACE)"
        cv2.putText(display,
                    f"{bg_status}  |  thr={threshold}  |  blobs={len(valid_blobs)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (0, 255, 0) if static_bg is not None else (0, 100, 255), 2)
        cv2.putText(display,
                    "SPACE=capture BG  R=reset  +/-=sensitivity  D=debug  Q=quit",
                    (10, h-12), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (180, 180, 180), 1)

        # fg mask thumbnail
        thumb = cv2.resize(fg, (320, 180))
        display[0:180, w-320:w] = cv2.cvtColor(thumb, cv2.COLOR_GRAY2BGR)
        cv2.putText(display, "FG mask", (w-310, 170),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,200,255), 1)

        cv2.imshow("Zone Detector  [Q=quit]", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord(' '):
            static_bg = frame.copy()
            print("[tracker] Background captured!")
        elif key == ord('r'):
            static_bg = None
            zone_active = {1: 0.0, 2: 0.0, 3: 0.0}
            print("[tracker] Background RESET")
        elif key in (ord('+'), ord('=')):
            threshold = max(5, threshold - 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('-'):
            threshold = min(100, threshold + 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('d'):
            areas = sorted([cv2.contourArea(c) for c in contours], reverse=True)
            print(f"[debug] Blob areas: {[int(a) for a in areas[:10]]}")
            print(f"[debug] MIN={MIN_BLOB_AREA} MAX={MAX_BLOB_AREA} thr={threshold}")

    cv2.destroyAllWindows()
    if selected['type'] == 'realsense':
        cam.stop()
    else:
        cam.release()
    print("[tracker] Stopped")


if __name__ == "__main__":
    main()
