import cv2
import time

# Test camera FPS
cap = cv2.VideoCapture(0)

# Request 30 FPS
cap.set(cv2.CAP_PROP_FPS, 30)

# Get what the camera reports
reported_fps = cap.get(cv2.CAP_PROP_FPS)
print(f"Camera reports FPS: {reported_fps}")

# Measure actual FPS
frame_count = 0
start_time = time.time()
duration = 5  # Test for 5 seconds

print(f"Measuring actual FPS for {duration} seconds...")

while time.time() - start_time < duration:
    ret, frame = cap.read()
    if ret:
        frame_count += 1

end_time = time.time()
elapsed = end_time - start_time
actual_fps = frame_count / elapsed

print(f"\nResults:")
print(f"Frames captured: {frame_count}")
print(f"Time elapsed: {elapsed:.2f} seconds")
print(f"Actual FPS: {actual_fps:.2f}")
print(f"Reported FPS: {reported_fps}")
print(f"FPS Ratio (Actual/Reported): {actual_fps/reported_fps:.2f}x")

cap.release()
