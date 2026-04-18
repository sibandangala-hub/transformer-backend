#!/usr/bin/env python3
"""
transformer_csv_collector.py
─────────────────────────────
Reads CSV lines from ESP32 Serial port and saves them to a
timestamped CSV file.  Skips comment lines (starting with '#').

Usage:
  python transformer_csv_collector.py               # auto-detect port
  python transformer_csv_collector.py --port COM3   # Windows
  python transformer_csv_collector.py --port /dev/ttyUSB0 --baud 115200

Requirements:
  pip install pyserial

Workflow:
  1. Flash transformer_csv_collector.ino to ESP32
  2. Run this script
  3. In Serial Monitor (or this script's console), send:
       L0       → label = NORMAL
       START    → begin sending CSV rows
       (collect ~1000 rows)
       L1       → switch to OVERLOAD label
       (collect ~1000 rows)
       ... repeat for L2, L3, L4
  4. Script auto-saves CSV when target is reached or on Ctrl+C
"""

import argparse
import csv
import os
import sys
import time
import threading
from datetime import datetime
from pathlib import Path

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("ERROR: pyserial not installed. Run: pip install pyserial")
    sys.exit(1)

# ─────────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────────
BAUD_RATE      = 115200
TARGET_ROWS    = 5000
OUTPUT_DIR     = Path("collected_data")
CSV_HEADER     = ["timestamp_ms", "oil_temp_c", "winding_temp_c",
                  "current_a", "vibration_ms2", "oil_level_pct", "label"]

LABEL_NAMES    = {
    "0": "NORMAL",
    "1": "OVERLOAD",
    "2": "OVERTEMP",
    "3": "LOW_OIL",
    "4": "HIGH_VIB",
}

# ─────────────────────────────────────────────
#  AUTO-DETECT ESP32 PORT
# ─────────────────────────────────────────────
def auto_detect_port() -> str | None:
    """Return first likely ESP32/CH340/CP210x port found."""
    KEYWORDS = ["ESP32", "CP210", "CH340", "USB Serial", "UART", "Silicon Labs"]
    ports = list(serial.tools.list_ports.comports())
    for p in ports:
        desc = (p.description or "") + (p.manufacturer or "")
        if any(k.lower() in desc.lower() for k in KEYWORDS):
            return p.device
    # Fallback: return first available port
    if ports:
        return ports[0].device
    return None

# ─────────────────────────────────────────────
#  COLLECTOR
# ─────────────────────────────────────────────
class CSVCollector:
    def __init__(self, port: str, baud: int, target: int):
        self.port    = port
        self.baud    = baud
        self.target  = target
        self.rows    = []
        self.running = False

        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.filepath = OUTPUT_DIR / f"transformer_data_{ts}.csv"

        # label stats
        self.label_counts: dict[str, int] = {k: 0 for k in LABEL_NAMES}

    # ── open serial ──
    def connect(self) -> serial.Serial:
        print(f"[INFO] Connecting to {self.port} @ {self.baud} baud...")
        ser = serial.Serial(self.port, self.baud, timeout=2)
        time.sleep(1.5)      # wait for ESP32 reset
        ser.reset_input_buffer()
        print(f"[INFO] Connected. Output → {self.filepath}")
        return ser

    # ── keyboard command thread ──
    def _cmd_thread(self, ser: serial.Serial):
        """Read commands from stdin and forward to ESP32."""
        while self.running:
            try:
                cmd = input()
            except EOFError:
                break
            if cmd.strip():
                ser.write((cmd.strip() + "\n").encode())

    # ── save CSV ──
    def save(self):
        with open(self.filepath, "w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADER)
            writer.writerows(self.rows)
        print(f"\n[SAVED] {len(self.rows)} rows → {self.filepath}")

        # Print label distribution
        print("\n── Label distribution ────────────────────")
        for lbl, name in LABEL_NAMES.items():
            count = sum(1 for r in self.rows if r[-1] == lbl)
            pct   = count / max(len(self.rows), 1) * 100
            print(f"  {name:12s} (L{lbl})  : {count:5d} rows  ({pct:.1f}%)")
        print("──────────────────────────────────────────\n")

    # ── main collect loop ──
    def collect(self):
        ser = self.connect()
        self.running = True

        # Start command thread
        cmd_t = threading.Thread(target=self._cmd_thread, args=(ser,), daemon=True)
        cmd_t.start()

        print("\n[READY] Type commands below (forwarded to ESP32):")
        print("  L0=NORMAL  L1=OVERLOAD  L2=OVERTEMP  L3=LOW_OIL  L4=HIGH_VIB")
        print("  START / STOP / RESET / STATUS")
        print(f"  Target: {self.target} rows\n")

        try:
            while self.running:
                raw = ser.readline()
                if not raw:
                    continue

                line = raw.decode("utf-8", errors="replace").strip()

                # Print all lines (comments too)
                print(line)

                # Skip comments / empty lines
                if not line or line.startswith("#"):
                    continue

                # Validate CSV row
                parts = line.split(",")
                if len(parts) != len(CSV_HEADER):
                    continue

                # Check it's numeric (not accidental garbage)
                try:
                    float(parts[0])   # timestamp_ms
                    float(parts[1])   # oil_temp
                    label = parts[6].strip()
                except (ValueError, IndexError):
                    continue

                self.rows.append(parts)

                # Update label count display
                label_name = LABEL_NAMES.get(label, f"L{label}")
                count = len(self.rows)
                if count % 50 == 0:
                    print(f"  ▶ {count}/{self.target} rows | current label: {label_name}")

                # Auto-save checkpoint every 500 rows
                if count % 500 == 0:
                    self.save()
                    print(f"[CHECKPOINT] Saved at {count} rows")

                # Done
                if count >= self.target:
                    print(f"\n[TARGET REACHED] {count} rows collected!")
                    self.running = False

        except serial.SerialException as e:
            print(f"\n[ERROR] Serial error: {e}")
        except KeyboardInterrupt:
            print("\n[INTERRUPTED] Saving current data...")
        finally:
            self.running = False
            ser.close()
            self.save()

# ─────────────────────────────────────────────
#  ENTRY POINT
# ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="Collect ESP32 transformer sensor data to CSV"
    )
    parser.add_argument("--port",   default=None, help="Serial port (e.g. COM3 or /dev/ttyUSB0)")
    parser.add_argument("--baud",   type=int, default=BAUD_RATE, help="Baud rate (default 115200)")
    parser.add_argument("--target", type=int, default=TARGET_ROWS, help="Target rows (default 5000)")
    args = parser.parse_args()

    # Resolve port
    port = args.port
    if port is None:
        port = auto_detect_port()
        if port is None:
            print("[ERROR] No serial port found. Use --port to specify.")
            sys.exit(1)
        print(f"[INFO] Auto-detected port: {port}")

    collector = CSVCollector(port, args.baud, args.target)
    collector.collect()

if __name__ == "__main__":
    main()
