"""
combined_tracker.py
-------------------
Zone detection + Jump detection in ONE script, ONE camera

OSC output (port 7000):
  /zone/1/active   int  1=person in zone 1
  /zone/2/active   int  1=person in zone 2
  /zone/3/active   int  1=person in zone 3
  /jump/active     int  1=jumping
  /jump/y          float jump height
  /persons/count   int  blob count

Controls:
  SPACE = capture static background (clear frame first!)
  R     = reset background
  +/-   = adjust sensitivity (max 255)
  D     = debug blob areas
  Q     = quit
"""

import sys
import cv2
import numpy as np
from pythonosc import udp_client

# ── config ─────────────────────────────────────────────────────────────────────
OSC_IP    = "127.0.0.1"
OSC_PORT  = 7000

MODEL_NAME     = "yolov8n-pose.pt"
CONF_THRESH    = 0.4
INFER_EVERY    = 2
JUMP_THRESHOLD = 0.08
NOSE_SMOOTH    = 0.3

MIN_BLOB_AREA = 2000
MAX_BLOB_AREA = 150000
BG_THRESHOLD  = 30

KP_NOSE = 0

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

DEPTH_MIN = 0.3
DEPTH_MAX = 2.4


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


class RealSenseCamera:
    def __init__(self, serial):
        self.pipeline = rs.pipeline()
        config = rs.config()
        config.enable_device(serial)
        config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
        self.pipeline.start(config)
        self.align = rs.align(rs.stream.color)
        print("[RealSense] Color + Depth streams started")

    def get_frame(self):
        try:
            frames  = self.pipeline.wait_for_frames(timeout_ms=3000)
            aligned = self.align.process(frames)
            cf = aligned.get_color_frame()
            df = aligned.get_depth_frame()
            if not cf or not df:
                return None, None
            return np.asanyarray(cf.get_data()), np.asanyarray(df.get_data())
        except RuntimeError as e:
            print(f"[WARN] RealSense: {e}")
            return None, None

    def stop(self):
        self.pipeline.stop()


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


def apply_depth_mask(color, depth):
    depth_m = depth.astype(np.float32) / 1000.0
    mask    = ((depth_m >= DEPTH_MIN) & (depth_m <= DEPTH_MAX)).astype(np.uint8)
    kernel  = np.ones((15, 15), np.uint8)
    mask    = cv2.dilate(mask, kernel, iterations=2)
    masked  = color.copy()
    masked[mask == 0] = 0
    return masked


def zone_px(zone, w, h):
    return int(zone[0]*w), int(zone[1]*h), int(zone[2]*w), int(zone[3]*h)


def blob_in_zone(cx, cy, zone, w, h):
    x1, y1, x2, y2 = zone_px(zone, w, h)
    return x1 <= cx <= x2 and y1 <= cy <= y2


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    from ultralytics import YOLO

    print("\n" + "="*52)
    print(" Combined Tracker (Zone + Jump)")
    print(f" OSC -> {OSC_IP}:{OSC_PORT}")
    print("="*52)

    cameras = scan_all_cameras()
    if not cameras:
        print("[ERROR] No cameras found")
        input("Press Enter...")
        sys.exit(1)

    selected = select_camera(cameras)

    # release unused cams
    for c in cameras:
        if c is not selected and c.get('type') == 'webcam':
            cap_c = c.get('cap')
            if cap_c and cap_c.isOpened():
                cap_c.release()

    # ── open camera ────────────────────────────────────────────────────────────
    use_depth  = False
    rs_cam_obj = None
    webcam_cap = None

    if selected['type'] == 'realsense':
        try:
            rs_cam_obj = RealSenseCamera(selected['serial'])
            tc, td = rs_cam_obj.get_frame()
            if tc is None:
                raise RuntimeError("No frame from RealSense")
            use_depth = True
            print("[tracker] RealSense depth masking ON")

            def get_color_depth():
                return rs_cam_obj.get_frame()

        except RuntimeError as e:
            print(f"[WARN] RealSense SDK failed: {e}")
            print("[WARN] Trying depth-only + webcam color...")
            try:
                serial = selected['serial']
                dep_pipeline = rs.pipeline()
                dep_config   = rs.config()
                dep_config.enable_device(serial)
                dep_config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
                dep_pipeline.start(dep_config)
                dep_pipeline.wait_for_frames(timeout_ms=3000)
                print("[tracker] RealSense depth-only OK")
                cidx = int(input("Enter webcam index for D435 color (e.g. 2): ").strip())
                webcam_cap = open_webcam({'index': cidx})
                use_depth  = True

                def get_color_depth():
                    color = get_frame_webcam(webcam_cap)
                    try:
                        dframes = dep_pipeline.wait_for_frames(timeout_ms=1000)
                        df = dframes.get_depth_frame()
                        if df:
                            raw = np.asanyarray(df.get_data())
                            return color, cv2.resize(raw, (1280, 720),
                                                     interpolation=cv2.INTER_NEAREST)
                    except:
                        pass
                    return color, None

            except Exception as e2:
                print(f"[WARN] Depth-only failed: {e2} -> RGB only")
                cidx = int(input("Enter webcam index: ").strip())
                webcam_cap = open_webcam({'index': cidx})
                use_depth  = False

                def get_color_depth():
                    return get_frame_webcam(webcam_cap), None
    else:
        webcam_cap = open_webcam(selected)
        use_depth  = False

        def get_color_depth():
            return get_frame_webcam(webcam_cap), None

    print(f"\n[tracker] Camera: {selected['name']}")
    print(f"[tracker] Depth mask: {'ON' if use_depth else 'OFF'}")

    print(f"\n[tracker] Loading {MODEL_NAME} ...")
    model = YOLO(MODEL_NAME)
    print("[tracker] Model loaded")

    osc = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    print(f"[tracker] OSC -> {OSC_IP}:{OSC_PORT}")
    print("[tracker] Running — press Q to quit\n")
    print("SPACE=freeze BG  R=reset  +/-=sensitivity  D=debug  Q=quit\n")
    print("[tracker] Clear the frame then press SPACE to capture background")

    static_bg     = None
    threshold     = BG_THRESHOLD
    frame_count   = 0
    last_results  = []
    nose_baseline = None
    nose_smooth   = 0.0

    kernel_open  = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (7,  7))
    kernel_close = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (20, 20))

    while True:
        color, depth = get_color_depth()
        if color is None:
            continue

        # depth mask
        if use_depth and depth is not None:
            frame = apply_depth_mask(color, depth)
        else:
            frame = color

        h, w = frame.shape[:2]
        display = color.copy()

        # ── YOLO pose ──────────────────────────────────────────────────────────
        frame_count += 1
        if frame_count % INFER_EVERY == 0:
            last_results = model(frame, verbose=False, conf=CONF_THRESH,
                                 classes=[0], imgsz=320)

        jump_detected = False

        for res in last_results:
            if res.keypoints is None or res.keypoints.conf is None:
                continue
            kpts  = res.keypoints.xy.cpu().numpy()
            confs = res.keypoints.conf.cpu().numpy()
            if len(kpts) == 0:
                continue
            pk = kpts[0]
            pc = confs[0]

            if pc[KP_NOSE] >= CONF_THRESH:
                ny_nose = -(pk[KP_NOSE][1] / h) + 0.5
                nose_smooth = nose_smooth * NOSE_SMOOTH + ny_nose * (1 - NOSE_SMOOTH)

                if nose_baseline is None:
                    nose_baseline = nose_smooth
                else:
                    diff = nose_smooth - nose_baseline
                    if diff < JUMP_THRESHOLD:
                        nose_baseline = nose_baseline * 0.99 + nose_smooth * 0.01

                jump_diff = nose_smooth - nose_baseline
                jump_detected = jump_diff > JUMP_THRESHOLD

                osc.send_message("/jump/active", 1 if jump_detected else 0)
                osc.send_message("/jump/y",      float(jump_diff))

                col_nose = (0, 255, 0) if jump_detected else (200, 200, 200)
                cv2.circle(display, (int(pk[KP_NOSE][0]), int(pk[KP_NOSE][1])), 10, col_nose, -1)
                cv2.putText(display,
                            f"JUMP! +{jump_diff:.2f}" if jump_detected else f"y={jump_diff:+.2f}",
                            (int(pk[KP_NOSE][0])+14, int(pk[KP_NOSE][1])),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, col_nose, 2)
            else:
                osc.send_message("/jump/active", 0)
            break

        if not jump_detected:
            osc.send_message("/jump/active", 0)

        # ── background subtraction ─────────────────────────────────────────────
        if static_bg is not None:
            fb = cv2.GaussianBlur(frame,     (21, 21), 0)
            bb = cv2.GaussianBlur(static_bg, (21, 21), 0)
            diff2 = cv2.absdiff(fb, bb)
            gray  = cv2.cvtColor(diff2, cv2.COLOR_BGR2GRAY)
            _, fg = cv2.threshold(gray, threshold, 255, cv2.THRESH_BINARY)
            fg = cv2.morphologyEx(fg, cv2.MORPH_OPEN,  kernel_open)
            fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, kernel_close)
        else:
            fg = np.zeros((h, w), dtype=np.uint8)

        contours, _ = cv2.findContours(fg, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
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
            for zid, zone in DEFAULT_ZONES.items():
                if blob_in_zone(cx, cy, zone, w, h):
                    zone_hit[zid] = True
            cv2.drawContours(display, [cnt], -1, (0, 255, 255), 2)
            cv2.circle(display, (cx, cy), 8, (0, 255, 255), -1)

        osc.send_message("/persons/count", len(valid_blobs))
        for zid in [1, 2, 3]:
            osc.send_message(f"/zone/{zid}/active", 1 if zone_hit[zid] else 0)

        # ── draw zones ─────────────────────────────────────────────────────────
        for zid, zone in DEFAULT_ZONES.items():
            x1, y1, x2, y2 = zone_px(zone, w, h)
            col    = ZONE_COLORS[zid]
            active = zone_hit[zid]
            ov = display.copy()
            cv2.rectangle(ov, (x1, y1), (x2, y2), col, -1)
            display = cv2.addWeighted(ov, 0.3 if active else 0.08, display, 1-(0.3 if active else 0.08), 0)
            cv2.rectangle(display, (x1, y1), (x2, y2), col, 3 if active else 1)
            cv2.putText(display, f"L{zid} {'ACTIVE' if active else ''}",
                        (x1+10, y1+40), cv2.FONT_HERSHEY_SIMPLEX, 1.0, col, 2)

        # status
        bg_ok = static_bg is not None
        cv2.putText(display,
                    f"{'BG:OK' if bg_ok else 'NO BG - press SPACE'}  thr={threshold}  blobs={len(valid_blobs)}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.65,
                    (0,255,0) if bg_ok else (0,100,255), 2)

        thumb = cv2.resize(fg, (320, 180))
        display[0:180, w-320:w] = cv2.cvtColor(thumb, cv2.COLOR_GRAY2BGR)

        cv2.imshow("Combined Tracker  [Q=quit]", display)

        key = cv2.waitKey(1) & 0xFF
        if key == ord('q'):
            break
        elif key == ord(' '):
            static_bg = frame.copy()
            print("[tracker] Background captured!")
        elif key == ord('r'):
            static_bg     = None
            nose_baseline = None
            print("[tracker] Reset")
        elif key in (ord('+'), ord('=')):
            threshold = max(5, threshold - 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('-'):
            threshold = min(255, threshold + 5)
            print(f"[tracker] Threshold = {threshold}")
        elif key == ord('d'):
            areas = sorted([cv2.contourArea(c) for c in contours], reverse=True)
            print(f"[debug] Blob areas: {[int(a) for a in areas[:8]]}")

    cv2.destroyAllWindows()
    if rs_cam_obj:
        rs_cam_obj.stop()
    if webcam_cap:
        webcam_cap.release()
    print("[tracker] Stopped")


if __name__ == "__main__":
    main()
