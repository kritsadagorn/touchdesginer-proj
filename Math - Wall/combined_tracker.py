"""
combined_tracker.py
-------------------
Zone detection + Wrist raise detection in ONE script, ONE camera
Camera is behind/side of person (like D435 setup)

OSC output (port 7000 and 7001 combined into port 7000):
  /zone/1/active      int  1=person in zone 1
  /zone/2/active      int  1=person in zone 2
  /zone/3/active      int  1=person in zone 3
  /wrist/right/y      float  wrist y position
  /wrist/right/active int    1=detected
  /wrist/left/y       float
  /wrist/left/active  int

Controls:
  SPACE = capture static background
  R     = reset background
  +/-   = adjust BG sensitivity
  D     = debug blob areas
  Q     = quit
"""

import sys
import cv2
import numpy as np
from pythonosc import udp_client

# ── config ─────────────────────────────────────────────────────────────────────
OSC_IP    = "127.0.0.1"
OSC_PORT  = 7000          # ส่งทุกอย่างออก port เดียว

MODEL_NAME   = "yolov8n-pose.pt"
CONF_THRESH  = 0.4
SMOOTH_WRIST = 0.3
INFER_EVERY  = 2

# Background subtraction
MIN_BLOB_AREA = 2000
MAX_BLOB_AREA = 150000
BG_THRESHOLD  = 30

# Wrist raise threshold (y > ค่านี้ = ยกมือ, 0=กลางจอ, 0.2=ยกขึ้น)
HAND_RAISE_Y  = 0.15

# YOLO keypoints
KP_RIGHT_WRIST = 10
KP_LEFT_WRIST  = 9

# Zones (normalized 0.0-1.0) - แบ่งตามแนวนอน ซ้าย/กลาง/ขวา
DEFAULT_ZONES = {
    1: (0.00, 0.00, 0.33, 1.00),
    2: (0.33, 0.00, 0.67, 1.00),
    3: (0.67, 0.00, 1.00, 1.00),
}
ZONE_COLORS = {
    1: (255, 80,  80),
    2: (80,  255, 80),
    3: (80,  80,  255),
}


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


# ── zone helper ────────────────────────────────────────────────────────────────
def zone_px(zone, w, h):
    return int(zone[0]*w), int(zone[1]*h), int(zone[2]*w), int(zone[3]*h)


def blob_in_zone(cx, cy, zone, w, h):
    x1, y1, x2, y2 = zone_px(zone, w, h)
    return x1 <= cx <= x2 and y1 <= cy <= y2


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    from ultralytics import YOLO

    print("\n" + "="*52)
    print(" Combined Tracker (Zone + Wrist) - Single Camera")
    print(f" OSC -> {OSC_IP}:{OSC_PORT}")
    print("="*52)

    cameras = scan_webcams()
    if not cameras:
        print("[ERROR] No webcams found")
        input("Press Enter...")
        sys.exit(1)

    # release all first, then re-open selected
    selected = select_camera(cameras)
    for c in cameras:
        if c is not selected:
            cap_c = c.get('cap')
            if cap_c and cap_c.isOpened():
                cap_c.release()

    cam = open_webcam(selected)
    print(f"\n[tracker] Camera: {selected['name']}")

    print(f"[tracker] Loading {MODEL_NAME} ...")
    model = YOLO(MODEL_NAME)
    print("[tracker] Model loaded")

    osc = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    print(f"[tracker] OSC -> {OSC_IP}:{OSC_PORT}")
    print("[tracker] Running — press Q to quit\n")
    print("SPACE=freeze BG  R=reset  +/-=sensitivity  D=debug  Q=quit\n")

    # state
    static_bg   = None
    threshold   = BG_THRESHOLD
    zones       = dict(DEFAULT_ZONES)
    frame_count = 0
    last_results = []
    smooth_wrist = {'right': [0.0, 0.0], 'left': [0.0, 0.0]}

    kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,  7))
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (20, 20))

    print("[tracker] Clear the frame then press SPACE to capture background")

    while True:
        frame = get_frame_webcam(cam)
        if frame is None:
            continue

        h, w = frame.shape[:2]
        display = frame.copy()

        # ── YOLO pose (every N frames) ─────────────────────────────────────────
        frame_count += 1
        if frame_count % INFER_EVERY == 0:
            last_results = model(frame, verbose=False, conf=CONF_THRESH,
                                 classes=[0], imgsz=320)

        right_detected = False

        for res in last_results:
            if res.keypoints is None or res.keypoints.conf is None:
                continue
            kpts  = res.keypoints.xy.cpu().numpy()
            confs = res.keypoints.conf.cpu().numpy()
            if len(kpts) == 0:
                continue
            pk = kpts[0]
            pc = confs[0]

            for side, kp_idx, col in [
                ('right', KP_RIGHT_WRIST, (0, 255, 0)),
            ]:
                if pc[kp_idx] < CONF_THRESH:
                    continue
                px, py = pk[kp_idx]
                nx =  (px / w) - 0.5
                ny = -(py / h) + 0.5
                smooth_wrist[side][0] = smooth_wrist[side][0] * SMOOTH_WRIST + nx * (1 - SMOOTH_WRIST)
                smooth_wrist[side][1] = smooth_wrist[side][1] * SMOOTH_WRIST + ny * (1 - SMOOTH_WRIST)
                sy = smooth_wrist[side][1]

                osc.send_message(f"/wrist/{side}/x",      float(smooth_wrist[side][0]))
                osc.send_message(f"/wrist/{side}/y",      float(sy))
                osc.send_message(f"/wrist/{side}/active", 1)

                label = 'R' if side == 'right' else 'L'
                raised = '↑' if sy > HAND_RAISE_Y else ''
                cv2.circle(display, (int(px), int(py)), 12, col, -1)
                cv2.putText(display, f"{label}{raised} y={sy:+.2f}",
                            (int(px)+14, int(py)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col, 2)

                right_detected = True
            break

        if not right_detected:
            osc.send_message("/wrist/right/active", 0)

        # ── background subtraction ─────────────────────────────────────────────
        if static_bg is not None:
            diff = cv2.absdiff(frame, static_bg)
            gray = cv2.cvtColor(diff, cv2.COLOR_BGR2GRAY)
            _, fg = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN,  kernel_open)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel_close)
        else:
            fg = np.zeros((h, w), dtype=np.uint8)

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
                valid_blobs.append((cx, cy, cnt))

        # ── zone detection ─────────────────────────────────────────────────────
        zone_hit = {1: False, 2: False, 3: False}
        for cx, cy, cnt in valid_blobs:
            for zid, zone in zones.items():
                if blob_in_zone(cx, cy, zone, w, h):
                    zone_hit[zid] = True
            cv2.drawContours(display, [cnt], -1, (0, 255, 255), 2)
            cv2.circle(display, (cx, cy), 8, (0, 255, 255), -1)

        osc.send_message("/persons/count", len(valid_blobs))
        for zid in [1, 2, 3]:
            osc.send_message(f"/zone/{zid}/active", 1 if zone_hit[zid] else 0)

        # ── draw zones ─────────────────────────────────────────────────────────
        for zid, zone in zones.items():
            x1, y1, x2, y2 = zone_px(zone, w, h)
            color  = ZONE_COLORS[zid]
            active = zone_hit[zid]
            alpha  = 0.3 if active else 0.08
            overlay = display.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            display = cv2.addWeighted(overlay, alpha, display, 1-alpha, 0)
            cv2.rectangle(display, (x1, y1), (x2, y2), color, 3 if active else 1)
            cv2.putText(display, f"L{zid} {'ACTIVE' if active else ''}",
                        (x1+10, y1+40),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2)

        # status
        bg_ok = static_bg is not None
        cv2.putText(display,
                    f"{'BG:OK' if bg_ok else 'BG:NOT SET (press SPACE)'}  thr={threshold}  blobs={len(valid_blobs)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (0,255,0) if bg_ok else (0,100,255), 2)

        # fg thumbnail
        thumb = cv2.resize(fg, (320, 180))
        display[0:180, w-320:w] = cv2.cvtColor(thumb, cv2.COLOR_GRAY2BGR)
        cv2.putText(display, "FG mask", (w-310, 170),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0,200,255), 1)

        cv2.imshow("Combined Tracker  [Q=quit]", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord(' '):
            static_bg = frame.copy()
            print("[tracker] Background captured!")
        elif key == ord('r'):
            static_bg = None
            print("[tracker] Background RESET")
        elif key in (ord('+'), ord('=')):
            threshold = max(5, threshold - 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('-'):
            threshold = min(100, threshold + 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('d'):
            areas = sorted([cv2.contourArea(c) for c in contours], reverse=True)
            print(f"[debug] Blob areas: {[int(a) for a in areas[:8]]}")

    cv2.destroyAllWindows()
    cam.release()
    print("[tracker] Stopped")


if __name__ == "__main__":
    main()
