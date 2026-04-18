"""
topdown_tracker.py
------------------
Top-down camera person position tracker using YOLOv8n
Detects all people in frame and sends their positions via OSC

OSC output (port 7001):
  /persons/count     int    number of people detected
  /persons/0/x       float  -0.5 to 0.5 (person 0, left=-0.5 right=+0.5)
  /persons/0/y       float  -0.5 to 0.5 (person 0, top=+0.5 bottom=-0.5)
  /persons/1/x       float  person 1 x
  /persons/1/y       float  person 1 y
  ... up to MAX_PERSONS

TouchDesigner:
  - OSC In CHOP port=7001
  - channels: /persons/count, /persons/0/x, /persons/0/y, ...
"""

import sys
import cv2
import numpy as np
from pythonosc import udp_client

# ── config ─────────────────────────────────────────────────────────────────────
OSC_IP      = "127.0.0.1"
OSC_PORT    = 7001           # คนละ port กับ wrist tracker (7000)
MODEL_NAME  = "yolov8n.pt"  # detection model (ไม่ต้อง pose)
CONF_THRESH = 0.4
MAX_PERSONS = 6              # track สูงสุดกี่คน
SMOOTH      = 0.2

# ── RealSense detection ────────────────────────────────────────────────────────
USE_REALSENSE = False
try:
    import pyrealsense2 as rs
    ctx = rs.context()
    rs_devices = list(ctx.query_devices())
    if rs_devices:
        USE_REALSENSE = True
except ImportError:
    rs_devices = []


# ── camera scanning ────────────────────────────────────────────────────────────
def scan_webcams(max_index=6):
    found = []
    print("[scan] Scanning for webcams...")
    for i in range(max_index):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW)
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                found.append({'type': 'webcam', 'index': i,
                              'name': f"Webcam index {i}",
                              'cap': cap})
                print(f"  [found] Webcam index {i}")
            else:
                cap.release()
        else:
            cap.release()
    return found


class OrbbecCamera:
    """Orbbec via OpenNI2 - color + depth"""
    def __init__(self):
        from openni import openni2, utils
        self.device     = openni2.Device.open_any()
        self.color_stream = self.device.create_color_stream()
        self.depth_stream = self.device.create_depth_stream()
        self.color_stream.set_video_mode(
            openni2.c_api.OniVideoMode(
                pixelFormat=openni2.PIXEL_FORMAT_RGB888,
                resolutionX=1280, resolutionY=720, fps=30))
        self.depth_stream.set_video_mode(
            openni2.c_api.OniVideoMode(
                pixelFormat=openni2.PIXEL_FORMAT_DEPTH_1_MM,
                resolutionX=640, resolutionY=480, fps=30))
        self.color_stream.start()
        self.depth_stream.start()
        print("[Orbbec] Color + Depth streams started")

    def get_frame(self):
        try:
            import numpy as np
            cf = self.color_stream.read_frame()
            df = self.depth_stream.read_frame()
            color_data = cf.get_buffer_as_uint8()
            depth_data = df.get_buffer_as_uint16()
            color = np.frombuffer(color_data, dtype=np.uint8).reshape(720, 1280, 3)
            color = cv2.cvtColor(color, cv2.COLOR_RGB2BGR)
            depth = np.frombuffer(depth_data, dtype=np.uint16).reshape(480, 640)
            depth = cv2.resize(depth, (1280, 720))
            return color, depth
        except Exception as e:
            print(f"[WARN] Orbbec frame error: {e}")
            return None, None

    def stop(self):
        self.color_stream.stop()
        self.depth_stream.stop()
        from openni import openni2
        openni2.unload()


def scan_all_cameras():
    cameras = []
    if USE_REALSENSE:
        for dev in rs_devices:
            name   = dev.get_info(rs.camera_info.name)
            serial = dev.get_info(rs.camera_info.serial_number)
            cameras.append({'type': 'realsense', 'serial': serial,
                            'name': f"RealSense {name} (S/N: {serial})"})
            print(f"  [found] RealSense: {name}")
    if USE_OPENNI:
        cameras.append({'type': 'orbbec', 'name': 'Orbbec Depth Camera (OpenNI2)'})
        print("  [found] Orbbec via OpenNI2")
    cameras.extend(scan_webcams())
    return cameras


def select_camera(cameras):
    if not cameras:
        print("[ERROR] No cameras found")
        sys.exit(1)

    print("\n" + "="*50)
    print(" Available cameras:")
    print("="*50)
    for i, cam in enumerate(cameras):
        print(f"  {i+1}. {cam['name']}")
    print("="*50)

    while True:
        try:
            choice = input(f"\nSelect top-down camera [1-{len(cameras)}]: ").strip()
            idx = int(choice) - 1
            if 0 <= idx < len(cameras):
                return cameras[idx]
            print(f"  Enter 1-{len(cameras)}")
        except (ValueError, KeyboardInterrupt):
            print("\n[EXIT]")
            sys.exit(0)


# ── camera open ────────────────────────────────────────────────────────────────
def open_realsense(serial):
    pipeline = rs.pipeline()
    config   = rs.config()
    config.enable_device(serial)
    config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
    pipeline.start(config)
    return pipeline


def open_webcam(selected):
    if 'cap' in selected and selected['cap'].isOpened():
        cap = selected['cap']
    else:
        cap = cv2.VideoCapture(selected['index'], cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def get_frame_realsense(pipeline):
    try:
        frames = pipeline.wait_for_frames(timeout_ms=3000)
        f = frames.get_color_frame()
        if not f:
            return None
        frame = np.asanyarray(f.get_data())
        # brightness enhancement
        lab   = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        l     = clahe.apply(l)
        lab   = cv2.merge((l, a, b))
        return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    except RuntimeError as e:
        print(f"[WARN] RealSense frame timeout: {e}")
        return None


def get_frame_webcam(cap):
    ret, frame = cap.read()
    if not ret or frame is None:
        return None
    # force 16:9 crop then resize
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


# ── smoothing per person slot ──────────────────────────────────────────────────
smooth_positions = [[0.0, 0.0] for _ in range(MAX_PERSONS)]


def smooth_pos(slot, nx, ny):
    smooth_positions[slot][0] = smooth_positions[slot][0] * SMOOTH + nx * (1 - SMOOTH)
    smooth_positions[slot][1] = smooth_positions[slot][1] * SMOOTH + ny * (1 - SMOOTH)
    return smooth_positions[slot][0], smooth_positions[slot][1]


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    from ultralytics import YOLO

    print("\n" + "="*50)
    print(" Top-Down Person Tracker")
    print(f" OSC -> {OSC_IP}:{OSC_PORT}")
    print("="*50)

    print("\n[scan] Looking for cameras...")
    cameras = scan_all_cameras()

    if not cameras:
        print("[ERROR] No cameras found")
        input("Press Enter to exit...")
        sys.exit(1)

    selected = select_camera(cameras)
    print(f"\n[tracker] Using: {selected['name']}")

    if selected['type'] == 'realsense':
        cam       = open_realsense(selected['serial'])
        get_frame = lambda: get_frame_realsense(cam)
    else:
        cam       = open_webcam(selected)
        get_frame = lambda: get_frame_webcam(cam)

    print(f"\n[tracker] Loading {MODEL_NAME} ...")
    model = YOLO(MODEL_NAME)
    print("[tracker] Model loaded")

    osc = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    print(f"[tracker] OSC ready -> {OSC_IP}:{OSC_PORT}")
    print(f"[tracker] Max persons: {MAX_PERSONS}")
    print("[tracker] Running — press Q to quit\n")

    while True:
        result = get_frame()
        frame = result if not isinstance(result, tuple) else result[0]
        if frame is None:
            continue

        h, w = frame.shape[:2]

        color, depth = frame if isinstance(frame, tuple) else (frame, None)
        if color is None:
            continue
        frame = color

        # depth masking (ถ้ามี depth stream)
        if use_depth and depth is not None:
            depth_m = depth.astype(np.float32) / 1000.0
            mask    = ((depth_m >= 0.3) & (depth_m <= 2.4)).astype(np.uint8)
            kernel  = np.ones((15,15), np.uint8)
            mask    = cv2.dilate(mask, kernel, iterations=2)
            frame   = frame.copy()
            frame[mask == 0] = 0

        # brightness enhancement (CLAHE)
        lab   = cv2.cvtColor(frame, cv2.COLOR_BGR2LAB)
        l, a, b = cv2.split(lab)
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8,8))
        l     = clahe.apply(l)
        lab   = cv2.merge((l, a, b))
        frame = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)

        # detect persons only (class 0)
        results = model(frame, verbose=False, conf=CONF_THRESH, classes=[0], imgsz=320)

        persons = []
        for r in results:
            if r.boxes is None:
                continue
            for box in r.boxes:
                x1, y1, x2, y2 = box.xyxy[0].cpu().numpy()
                # centroid
                cx = (x1 + x2) / 2
                cy = (y1 + y2) / 2
                # normalize to -0.5..0.5
                nx =  (cx / w) - 0.5
                ny = -(cy / h) + 0.5
                persons.append((nx, ny, int(x1), int(y1), int(x2), int(y2)))

        # sort left to right for consistent slot assignment
        persons.sort(key=lambda p: p[0])
        count = min(len(persons), MAX_PERSONS)

        # send OSC
        osc.send_message("/persons/count", count)

        for i in range(MAX_PERSONS):
            if i < count:
                nx, ny = persons[i][0], persons[i][1]
                sx, sy = smooth_pos(i, nx, ny)
                osc.send_message(f"/persons/{i}/x", float(sx))
                osc.send_message(f"/persons/{i}/y", float(sy))
            else:
                # send 0 for empty slots
                osc.send_message(f"/persons/{i}/x", 0.0)
                osc.send_message(f"/persons/{i}/y", 0.0)

        # draw overlay
        for i, (nx, ny, x1, y1, x2, y2) in enumerate(persons[:MAX_PERSONS]):
            sx, sy = smooth_positions[i]
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            cx = int((x1 + x2) / 2)
            cy = int((y1 + y2) / 2)
            cv2.circle(frame, (cx, cy), 8, (0, 255, 255), -1)
            cv2.putText(frame, f"P{i} ({sx:+.2f},{sy:+.2f})",
                        (x1, y1 - 8), cv2.FONT_HERSHEY_SIMPLEX,
                        0.55, (0, 255, 0), 2)

        cv2.putText(frame, f"Persons: {count}",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX,
                    0.8, (255, 255, 0), 2)

        cv2.imshow(f"Top-Down Tracker  [Q=quit]", frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()
    if selected['type'] == 'realsense':
        cam.stop()
    else:
        cam.release()

    print("[tracker] Stopped")


if __name__ == "__main__":
    main()
