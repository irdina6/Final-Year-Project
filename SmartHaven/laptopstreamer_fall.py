from flask import Flask, Response
import cv2
import time

app = Flask(__name__)

CAM_INDEX = 0  # change if needed (1,2...)
cap = cv2.VideoCapture(CAM_INDEX)

def gen_frames():
    while True:
        ok, frame = cap.read()
        if not ok:
            time.sleep(0.03)
            continue

        # optional: resize to reduce bandwidth
        frame = cv2.resize(frame, (640, 360))

        ok, buffer = cv2.imencode(".jpg", frame)
        if not ok:
            continue

        yield (b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + buffer.tobytes() + b"\r\n")

@app.route("/video_feed")
def video_feed():
    return Response(gen_frames(), mimetype="multipart/x-mixed-replace; boundary=frame")

@app.route("/")
def index():
    return """
    <h2>Laptop Raw Stream</h2>
    <img src="/video_feed" />
    """

if __name__ == "__main__":
    # streamer only
    app.run(host="0.0.0.0", port=5000, debug=False)