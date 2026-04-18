"""
yolo_tracker.py
---------------
YOLO Pose wrist tracker with RealSense depth masking
Depth mask removes projector-lit background so YOLO works reliably

OSC output (port 7000):
  /wrist/right/x      float  -0.5 to 0.5
  /wrist/right/y      float  -0.5 to 0.5
  /wrist/right/active int    1=detected 0=not
  /wrist/left/x       float  -0.5 to 0.5
  /wrist/left/y       float  -0.5 to 0.5
  /wrist/left/active  int    1=detected 0=not
"""

import sys
import cv2
import numpy as np
from pythonosc import udp_client

# ── config ─────────────────────────────────────────────────────────────────────
OSC_IP      = "127.0.0.1"
OSC_PORT    = 7000
MODEL_NAME  = "yolov8n-pose.pt"
CONF_THRESH = 0.4
SMOOTH      = 0.3
INFER_EVERY = 2      # run YOLO every N frames

# Depth mask config (เมื่อใช้ RealSense)
# คนที่ยืนอยู่จะอยู่ในระยะนี้ (หน่วย: เมตร)
DEPTH_MIN = 0.5      # ใกล้สุด (เมตร)
DEPTH_MAX = 2.4      # ไกลสุด (เมตร) — ปรับตามระยะคนกับกล้อง

# Keypoint indices
KP_RIGHT_WRIST = 10
KP_LEFT_WRIST  = 9

# ── RealSense detection ────────────────────────────────────────────────────────
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
def scan_webcams(max_index=6):
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


# ── RealSense pipeline (color + depth) ────────────────────────────────────────
class RealSenseCamera:
    def __init__(self, serial):
        self.pipeline = rs.pipeline()
        self.align    = None
        config = rs.config()
        config.enable_device(serial)
        config.enable_stream(rs.stream.color, 1280, 720, rs.format.bgr8, 30)
        config.enable_stream(rs.stream.depth, 1280, 720, rs.format.z16, 30)
        self.pipeline.start(config)
        self.align = rs.align(rs.stream.color)
        print("[RealSense] Color + Depth streams started (1280x720)")

    def get_frame(self):
        try:
            frames        = self.pipeline.wait_for_frames(timeout_ms=3000)
            aligned       = self.align.process(frames)
            color_frame   = aligned.get_color_frame()
            depth_frame   = aligned.get_depth_frame()
            if not color_frame or not depth_frame:
                return None, None
            color = np.asanyarray(color_frame.get_data())
            depth = np.asanyarray(depth_frame.get_data())  # uint16, mm
            return color, depth
        except RuntimeError as e:
            print(f"[WARN] RealSense timeout: {e}")
            return None, None

    def stop(self):
        self.pipeline.stop()


# ── depth masking ──────────────────────────────────────────────────────────────
def apply_depth_mask(color, depth):
    """
    ตัด background ออกโดยใช้ depth
    พื้นที่ที่ไกลเกิน DEPTH_MAX หรือใกล้เกิน DEPTH_MIN จะเป็นสีดำ
    ทำให้ projector background หายไป YOLO ทำงานได้แม่นขึ้น
    """
    depth_m = depth.astype(np.float32) / 1000.0  # mm -> m
    mask = (depth_m >= DEPTH_MIN) & (depth_m <= DEPTH_MAX)

    # ขยาย mask เล็กน้อยเพื่อไม่ให้ตัดขอบคน
    kernel = np.ones((15, 15), np.uint8)
    mask   = cv2.dilate(mask.astype(np.uint8), kernel, iterations=2)

    masked = color.copy()
    masked[mask == 0] = 0   # พื้นที่ที่ตัดออกเป็นสีดำ
    return masked, mask


# ── webcam ─────────────────────────────────────────────────────────────────────
def open_webcam(selected):
    if 'cap' in selected and selected['cap'].isOpened():
        cap = selected['cap']
    else:
        cap = cv2.VideoCapture(selected['index'], cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
    cap.set(cv2.CAP_PROP_FPS, 30)
    return cap


def get_frame_webcam_16x9(cap):
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
smooth = {'right': [0.0, 0.0], 'left': [0.0, 0.0]}

def apply_smooth(hand, nx, ny):
    smooth[hand][0] = smooth[hand][0] * SMOOTH + nx * (1 - SMOOTH)
    smooth[hand][1] = smooth[hand][1] * SMOOTH + ny * (1 - SMOOTH)
    return smooth[hand][0], smooth[hand][1]


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    from ultralytics import YOLO

    print("\n" + "="*52)
    print(" YOLO Wrist Tracker (Both Hands + Depth Mask)")
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

    use_depth = False

    if selected['type'] == 'realsense':
        try:
            rs_cam    = RealSenseCamera(selected['serial'])
            test_c, test_d = rs_cam.get_frame()
            if test_c is None:
                raise RuntimeError("No frame")
            use_depth = True
            print(f"[tracker] Depth masking ON  (range {DEPTH_MIN}-{DEPTH_MAX}m)")
            print(f"[tracker] Tip: adjust DEPTH_MIN/DEPTH_MAX in script if mask is wrong")
            get_frame = lambda: rs_cam.get_frame()
        except RuntimeError as e:
            print(f"[WARN] RealSense SDK failed: {e}")
            print("[WARN] Falling back to webcam index")
            webcams = [c for c in cameras if c.get('type') == 'webcam']
            for wc in webcams:
                print(f"  index {wc['index']} -> {wc['name']}")
            fb_idx = int(input("Enter webcam index for D435: ").strip())
            fb_cam = open_webcam({'index': fb_idx})
            get_frame = lambda: (get_frame_webcam_16x9(fb_cam), None)
    else:
        cap = open_webcam(selected)
        get_frame = lambda: (get_frame_webcam_16x9(cap), None)
        print("[tracker] No depth available — using RGB only")

    print(f"\n[tracker] Loading {MODEL_NAME} ...")
    model = YOLO(MODEL_NAME)
    print("[tracker] Model loaded")

    osc = udp_client.SimpleUDPClient(OSC_IP, OSC_PORT)
    print(f"[tracker] OSC ready -> {OSC_IP}:{OSC_PORT}")
    print("[tracker] Running — press Q to quit\n")

    frame_count = 0
    last_results = []

    while True:
        color, depth = get_frame()
        if color is None:
            continue

        h, w = color.shape[:2]
        display = color.copy()

        # depth mask
        if use_depth and depth is not None:
            masked, mask_vis = apply_depth_mask(color, depth)
            infer_frame = masked

            # depth overlay (small, top-right)
            depth_norm = cv2.normalize(depth, None, 0, 255, cv2.NORM_MINMAX)
            depth_vis  = cv2.applyColorMap(depth_norm.astype(np.uint8),
                                           cv2.COLORMAP_JET)
            thumb_h, thumb_w = h // 4, w // 4
            depth_thumb = cv2.resize(depth_vis, (thumb_w, thumb_h))
            display[0:thumb_h, w-thumb_w:w] = depth_thumb
            cv2.putText(display, f"Depth mask ({DEPTH_MIN}-{DEPTH_MAX}m)",
                        (w-thumb_w, thumb_h + 18),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 200, 255), 1)
        else:
            infer_frame = color

        # YOLO inference (every N frames)
        frame_count += 1
        if frame_count % INFER_EVERY == 0:
            last_results = model(infer_frame, verbose=False,
                                 conf=CONF_THRESH, classes=[0], imgsz=320)

        right_detected = False
        left_detected  = False

        for r in last_results:
            if r.keypoints is None or r.keypoints.conf is None:
                continue
            kpts  = r.keypoints.xy.cpu().numpy()
            confs = r.keypoints.conf.cpu().numpy()
            if len(kpts) == 0:
                continue

            person_kpts = kpts[0]
            person_conf = confs[0]

            for hand, kp_idx, color_dot in [
                ('right', KP_RIGHT_WRIST, (0, 255, 0)),
                ('left',  KP_LEFT_WRIST,  (0, 100, 255)),
            ]:
                if person_conf[kp_idx] < CONF_THRESH:
                    continue
                px, py = person_kpts[kp_idx]
                nx =  (px / w) - 0.5
                ny = -(py / h) + 0.5
                sx, sy = apply_smooth(hand, nx, ny)

                osc.send_message(f"/wrist/{hand}/x",      float(sx))
                osc.send_message(f"/wrist/{hand}/y",      float(sy))
                osc.send_message(f"/wrist/{hand}/active", 1)

                label = 'R' if hand == 'right' else 'L'
                cv2.circle(display, (int(px), int(py)), 12, color_dot, -1)
                cv2.putText(display, f"{label} ({sx:+.2f},{sy:+.2f})",
                            (int(px)+14, int(py)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, color_dot, 2)

                if hand == 'right':
                    right_detected = True
                else:
                    left_detected = True
            break

        if not right_detected:
            osc.send_message("/wrist/right/active", 0)
        if not left_detected:
            osc.send_message("/wrist/left/active", 0)

        # status
        cv2.putText(display, "RIGHT: OK" if right_detected else "RIGHT: --",
                    (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0,255,0) if right_detected else (80,80,80), 2)
        cv2.putText(display, "LEFT:  OK" if left_detected else "LEFT:  --",
                    (10, 58), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0,100,255) if left_detected else (80,80,80), 2)
        if use_depth:
            cv2.putText(display, "DEPTH MASK ON", (10, 86),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,200,255), 2)

        cv2.imshow("YOLO Wrist Tracker  [Q=quit]", display)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break

    cv2.destroyAllWindows()
    if use_depth:
        rs_cam.stop()

    print("[tracker] Stopped")


if __name__ == "__main__":
    main()
