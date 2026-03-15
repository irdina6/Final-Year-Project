from .common import *

# =========================================================
# Door servo
# =========================================================
door_pwm = None
def _servo_duty_from_angle(angle: float) -> float:
    return 2.5 + (angle / 18.0)

def door_init():
    global door_pwm
    if not IS_PI:
        return
    GPIO.setwarnings(False)
    GPIO.setmode(GPIO.BCM)
    GPIO.setup(DOOR_SERVO_PIN, GPIO.OUT)
    door_pwm = GPIO.PWM(DOOR_SERVO_PIN, 50)
    door_pwm.start(0)

def _set_servo_angle(pwm, angle: float):
    duty = _servo_duty_from_angle(angle)
    pwm.ChangeDutyCycle(duty)
    time.sleep(0.4)
    pwm.ChangeDutyCycle(0)

def servo_lock():
    if not IS_PI or not door_pwm:
        return
    _set_servo_angle(door_pwm, 0)

def servo_unlock():
    if not IS_PI or not door_pwm:
        return
    _set_servo_angle(door_pwm, 90)
