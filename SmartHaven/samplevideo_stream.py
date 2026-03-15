from flask import Flask, Response
import cv2, time, os

app = Flask(__name__)

VIDEO_PATH = r"C:\Users\sabri\OneDrive\Desktop\Y3S2\MP2025\2025\samplefall.mp4" #replace path with path video is saved to
LOOP = True
FPS_CAP = 15

def gen_frames():
    if not os.path.exists(VIDEO_PATH):
        raise FileNotFoundError(f"Video not found: {VIDEO_PATH}")

    cap = cv2.VideoCapture(VIDEO_PATH)
    if not cap.isOpened():
        raise RuntimeError("Cannot open video file")

    fps = cap.get(cv2.CAP_PROP_FPS)
    if not fps or fps <= 0:
        fps = FPS_CAP
    delay = 1.0 / min(FPS_CAP, fps)

    while True:
        ok, frame = cap.read()
        if not ok:
            if LOOP:
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                continue
            break

        frame = cv2.resize(frame, (640, 360))
        ok2, buffer = cv2.imencode(".jpg", frame)
        if not ok2:
            continue

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")
        time.sleep(delay)

    cap.release()

@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/")
def index():
    return """
    <h2>Laptop Video Stream (MP4)</h2>
    <img src="/video_feed" />
    """

if __name__ == "__main__":
    # MUST match Pi's LAPTOP_PORT (10000)
    app.run(host="0.0.0.0", port=10000, debug=False)
