"""
yolo_tracker.py
---------------
YOLO Pose wrist tracker - tracks both hands of 1 person
Sends normalized x,y to TouchDesigner via OSC

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

# YOLO pose keypoint indices
KP_RIGHT_WRIST = 10
KP_LEFT_WRIST  = 9

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
                              'cap': cap})   # keep cap open, reuse later
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

    print("\n" + "="*50)
    print(" Available cameras:")
    print("="*50)
    for i, cam in enumerate(cameras):
        print(f"  {i+1}. {cam['name']}")
    print("="*50)

    while True:
        try:
            choice = input(f"\nSelect camera [1-{len(cameras)}]: ").strip()
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
    config.enable_stream(rs.stream.color, 640, 480, rs.format.bgr8, 30)
    pipeline.start(config)
    return pipeline


def open_webcam(selected):
    # reuse cap from scan if available (avoid opening twice)
    if 'cap' in selected and selected['cap'].isOpened():
        cap = selected['cap']
    else:
        cap = cv2.VideoCapture(selected['index'], cv2.CAP_DSHOW)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH,  640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    return cap


def get_frame_realsense(pipeline):
    frames = pipeline.wait_for_frames()
    f = frames.get_color_frame()
    return np.asanyarray(f.get_data()) if f else None


def get_frame_webcam(cap):
    ret, frame = cap.read()
    return frame if ret else None


# ── main ───────────────────────────────────────────────────────────────────────
def main():
    from ultralytics import YOLO

    print("\n" + "="*50)
    print(" YOLO Wrist Tracker (Both Hands)")
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
    print("[tracker] Tracking: RIGHT wrist (index 10) + LEFT wrist (index 9)")
    print("[tracker] Running — press Q to quit\n")

    # smoothing state per hand
    smooth = {
        'right': [0.0, 0.0],
        'left':  [0.0, 0.0],
    }

    def apply_smooth(hand, nx, ny):
        smooth[hand][0] = smooth[hand][0] * SMOOTH + nx * (1 - SMOOTH)
        smooth[hand][1] = smooth[hand][1] * SMOOTH + ny * (1 - SMOOTH)
        return smooth[hand][0], smooth[hand][1]

    while True:
        frame = get_frame()
        if frame is None:
            continue

        h, w = frame.shape[:2]

        results = model(frame, verbose=False, conf=CONF_THRESH, classes=[0])

        right_detected = False
        left_detected  = False

        for r in results:
            if r.keypoints is None or r.keypoints.conf is None:
                continue

            kpts  = r.keypoints.xy.cpu().numpy()    # (N, 17, 2)
            confs = r.keypoints.conf.cpu().numpy()  # (N, 17)

            # track first person only
            if len(kpts) == 0:
                continue

            person_kpts = kpts[0]
            person_conf = confs[0]

            # ── RIGHT wrist ──
            if person_conf[KP_RIGHT_WRIST] >= CONF_THRESH:
                px, py = person_kpts[KP_RIGHT_WRIST]
                nx =  (px / w) - 0.5
                ny = -(py / h) + 0.5
                sx, sy = apply_smooth('right', nx, ny)

                osc.send_message("/wrist/right/x",      float(sx))
                osc.send_message("/wrist/right/y",      float(sy))
                osc.send_message("/wrist/right/active", 1)
                right_detected = True

                cv2.circle(frame, (int(px), int(py)), 12, (0, 255, 0), -1)
                cv2.putText(frame, f"R ({sx:+.2f},{sy:+.2f})",
                            (int(px) + 14, int(py)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 255, 0), 2)

            # ── LEFT wrist ──
            if person_conf[KP_LEFT_WRIST] >= CONF_THRESH:
                px, py = person_kpts[KP_LEFT_WRIST]
                nx =  (px / w) - 0.5
                ny = -(py / h) + 0.5
                sx, sy = apply_smooth('left', nx, ny)

                osc.send_message("/wrist/left/x",      float(sx))
                osc.send_message("/wrist/left/y",      float(sy))
                osc.send_message("/wrist/left/active", 1)
                left_detected = True

                cv2.circle(frame, (int(px), int(py)), 12, (0, 100, 255), -1)
                cv2.putText(frame, f"L ({sx:+.2f},{sy:+.2f})",
                            (int(px) + 14, int(py)),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 100, 255), 2)

            break  # first person only

        if not right_detected:
            osc.send_message("/wrist/right/active", 0)
        if not left_detected:
            osc.send_message("/wrist/left/active", 0)

        # status overlay
        r_status = "RIGHT: OK" if right_detected else "RIGHT: --"
        l_status = "LEFT:  OK" if left_detected  else "LEFT:  --"
        cv2.putText(frame, r_status, (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 255, 0) if right_detected else (80, 80, 80), 2)
        cv2.putText(frame, l_status, (10, 58),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 100, 255) if left_detected else (80, 80, 80), 2)

        cv2.imshow(f"YOLO Wrist Tracker  [Q=quit]", frame)
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
