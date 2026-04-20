import cv2
import os
import numpy as np

DATA_DIR = "face_dataset"
MODEL_PATH = "face_model.yml"

# Create recognizer (LBPH)
recognizer = cv2.face.LBPHFaceRecognizer_create()

faces = []
labels = []

# Standard size for training images
IMG_SIZE = (200, 200)

for filename in os.listdir(DATA_DIR):
    if filename.endswith(".jpg"):
        path = os.path.join(DATA_DIR, filename)
        img = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue

        # Resize to standard size
        img_resized = cv2.resize(img, IMG_SIZE)

        # Filename format: user_<id>_<count>.jpg
        try:
            parts = filename.split("_")
            label = int(parts[1])  # User ID
        except Exception:
            label = 1

        faces.append(img_resized)
        labels.append(label)

if not faces:
    print("No face images found in dataset. Run capture_faces.py first.")
    exit(1)

faces_np = np.array(faces)
labels_np = np.array(labels)

print("Training model with", len(faces_np), "images...")
recognizer.train(faces_np, labels_np)
recognizer.save(MODEL_PATH)
print(f"Model saved to {MODEL_PATH}")