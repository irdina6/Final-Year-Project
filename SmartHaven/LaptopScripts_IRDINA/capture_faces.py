import cv2
import os

# Folder to save your face images
DATA_DIR = "face_dataset"
os.makedirs(DATA_DIR, exist_ok=True)

# Use your name or ID
USER_ID = 1  # we only have 1 user: you

# Load Haar cascade for face detection
cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")

cap = cv2.VideoCapture(0)
count = 0
TARGET_IMAGES = 40  # number of images to capture

print(">>> Look at the camera. Press 'q' to stop early.")
print(f"Capturing up to {TARGET_IMAGES} face images...")

while True:
    ret, frame = cap.read()
    if not ret:
        break

    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    faces = cascade.detectMultiScale(gray, 1.3, 5)

    for (x, y, w, h) in faces:
        # draw rectangle for you to see
        cv2.rectangle(frame, (x, y), (x+w, y+h), (0, 255, 0), 2)

        # crop and save face region
        face_roi = gray[y:y+h, x:x+w]
        img_path = os.path.join(DATA_DIR, f"user_{USER_ID}_{count}.jpg")
        cv2.imwrite(img_path, face_roi)
        count += 1
        print(f"Saved {img_path}")

        if count >= TARGET_IMAGES:
            print(">>> Done capturing face images.")
            cap.release()
            cv2.destroyAllWindows()
            exit(0)

    cv2.imshow("Capture Your Face", frame)
    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

cap.release()
cv2.destroyAllWindows()