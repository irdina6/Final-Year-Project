# Smart Home IoT System (Final Year Project)

This project is an integrated Smart Home IoT system developed using a Raspberry Pi 3B+ running Debian Trixie Raspberry Pi OS. The system combines sensors, actuators, computer vision, and a Flask web dashboard to enable intelligent monitoring and automation within a smart home environment.

## Technologies Used
- Raspberry Pi 3B+
- Python
- Flask Web Framework
- OpenCV
- YOLO (Fall Detection)
- LBPH Face Recognition
- INA219 Power Sensor
- PIR Motion Sensor
- Barcode Recognition (pyzbar)
- Open Food Facts API
- Telegram Bot API

## Key Features

### Smart Door Access
Uses LBPH face recognition through a laptop camera to identify authorised users and unlock a servo-based door lock.

### Intrusion Detection
Detects suspicious motion and loud noise using PIR and microphone monitoring. Alerts are sent through Telegram and a buzzer is triggered.

### Energy Monitoring
INA219 sensor measures current and power consumption of a connected 5V fan and displays usage levels on the dashboard.

### Smart Pantry System
Uses barcode scanning through a laptop camera to identify pantry items via the Open Food Facts API. Expiry dates are stored and Telegram alerts are sent when items are close to expiry.

### Fall Detection
Uses a YOLO computer vision model through a laptop camera to detect falls and send alert notifications.

## System Architecture
The Raspberry Pi runs a Flask-based dashboard that integrates all subsystem modules including sensors, computer vision services, and alert systems.

## Author
Major contributor to the Smart Home IoT system developed as part of the Major Project module in polytechnic.
