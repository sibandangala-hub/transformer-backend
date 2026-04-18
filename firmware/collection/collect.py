import serial
import csv
import time
import os
from datetime import datetime

# ── CONFIGURATION ──────────────────────────
PORT        = "COM6"     # ← your ESP32 port
BAUD        = 115200
FILENAME    = "transformer_normal_data_v3.csv"
TARGET_ROWS = 500
# ───────────────────────────────────────────

COLUMNS = ["timestamp", "oil_temp", "winding_temp", "current", "vibration", "oil_level"]

file_exists   = os.path.isfile(FILENAME)
existing_rows = 0

if file_exists:
    with open(FILENAME, "r") as f:
        existing_rows = sum(1 for _ in f) - 1
    print(f"[INFO] Appending — {existing_rows} rows already in file")
else:
    print(f"[INFO] Creating new file: {FILENAME}")

try:
    ser = serial.Serial(PORT, BAUD, timeout=3)
    print(f"[OK] Connected to {PORT} @ {BAUD}")
    time.sleep(2)
    ser.flushInput()
except Exception as e:
    print(f"\n[ERROR] Cannot open port: {e}")
    input("\nPress Enter to exit...")
    exit()

rows_collected = 0
errors         = 0

with open(FILENAME, mode="a", newline="") as f:
    writer = csv.writer(f)
    if not file_exists:
        writer.writerow(COLUMNS)

    print(f"\n[START] Collecting {TARGET_ROWS} rows — press Ctrl+C to stop early\n")

    try:
        while rows_collected < TARGET_ROWS:
            try:
                raw = ser.readline().decode("utf-8", errors="ignore").strip()
            except Exception:
                errors += 1
                continue

            if not raw.startswith("DATA:"):
                continue

            parts = raw[5:].split(",")

            if len(parts) != 5:
                errors += 1
                continue

            try:
                oil_temp     = float(parts[0])
                winding_temp = float(parts[1])
                current      = float(parts[2])
                vibration    = float(parts[3])
                oil_level    = float(parts[4])
            except ValueError:
                errors += 1
                continue

            # Sanity checks
            if not (-10 < oil_temp     < 120): print(f"[WARN] oil_temp={oil_temp} skipped");         continue
            if not (-10 < winding_temp < 120): print(f"[WARN] winding_temp={winding_temp} skipped"); continue
            if not (0   <= current     <= 25):  print(f"[WARN] current={current} skipped");          continue
            if not (0   <= vibration   <= 50):  print(f"[WARN] vibration={vibration} skipped");      continue
            if not (0   <= oil_level   <= 100): print(f"[WARN] oil_level={oil_level} skipped");      continue

            ts  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            row = [ts, oil_temp, winding_temp, current, vibration, oil_level]
            writer.writerow(row)
            f.flush()

            rows_collected += 1
            total = rows_collected + existing_rows
            pct   = rows_collected / TARGET_ROWS * 100

            print(f"[{total:>4}] {pct:5.1f}%  "
                  f"OT={oil_temp:.2f}C  WT={winding_temp:.2f}C  "
                  f"I={current:.3f}A  Vib={vibration:.3f}  "
                  f"OL={oil_level:.1f}%  err={errors}")

    except KeyboardInterrupt:
        print(f"\n[STOPPED] Ctrl+C pressed")

ser.close()
print(f"\n[DONE] {rows_collected + existing_rows} rows saved to {FILENAME}")
print(f"File: {os.path.abspath(FILENAME)}")
input("\nPress Enter to exit...")