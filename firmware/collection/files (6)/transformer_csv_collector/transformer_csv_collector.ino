// =============================================================
//  Transformer PM — CSV Data Collector Firmware
//  Target : 5000 labelled readings for ML training
//
//  Output format (Serial @ 115200):
//  CSV line → timestamp_ms,oil_temp,winding_temp,current,
//              vibration,oil_level,label
//
//  Labels (set via Serial command or #define):
//    0 = NORMAL
//    1 = OVERLOAD
//    2 = OVERTEMP
//    3 = LOW_OIL
//    4 = HIGH_VIB
//
//  Commands (send via Serial Monitor):
//    L0  → set label NORMAL
//    L1  → set label OVERLOAD
//    L2  → set label OVERTEMP
//    L3  → set label LOW_OIL
//    L4  → set label HIGH_VIB
//    START → begin collecting
//    STOP  → pause collecting
//    RESET → reset reading counter
//    STATUS → print current stats
// =============================================================

#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>

// ─────────────────────────────────────────────
//  PINS
// ─────────────────────────────────────────────
#define PIN_DS18B20          5
#define PIN_TRIG             13
#define PIN_ECHO             2
#define PIN_ACS712           34
#define PIN_BUZZER           25
#define PIN_LED_NORMAL       26
#define PIN_LED_FAULT        27
#define I2C_SDA              21
#define I2C_SCL              22

// ─────────────────────────────────────────────
//  DS18B20 ADDRESSES
// ─────────────────────────────────────────────
DeviceAddress ADDR_OIL     = {0x28, 0xD1, 0x4D, 0xE1, 0xA0, 0x24, 0x0B, 0x02};
DeviceAddress ADDR_WINDING = {0x28, 0xA0, 0xE4, 0x98, 0xA0, 0x24, 0x0B, 0x56};

// ─────────────────────────────────────────────
//  OIL LEVEL CALIBRATION
// ─────────────────────────────────────────────
#define DIST_EMPTY           12.0f
#define DIST_FULL             6.0f

// ─────────────────────────────────────────────
//  ACS712
// ─────────────────────────────────────────────
#define ACS712_SENSITIVITY   0.100f
#define ADC_VREF             3.3f
#define ADC_MAX              4095
#define MAINS_FREQ           50.0f
#define WINDOW_CYCLES        4.0f
#define ACS_SAMPLE_RATE      6000UL
#define OFFSET_SAMPLES       200
#define CURRENT_DEADBAND     0.15f

// ─────────────────────────────────────────────
//  DS18B20 async timing
// ─────────────────────────────────────────────
#define DS18B20_CONV_MS      200UL

// ─────────────────────────────────────────────
//  COLLECTION CONFIG
// ─────────────────────────────────────────────
#define TARGET_READINGS      5000
#define BAUD_RATE            115200
#define COLLECTION_INTERVAL  500UL  // ms between readings — adjust for throughput

// Label names for display
const char* LABEL_NAMES[] = {
  "NORMAL", "OVERLOAD", "OVERTEMP", "LOW_OIL", "HIGH_VIB"
};

// ─────────────────────────────────────────────
//  OBJECTS
// ─────────────────────────────────────────────
OneWire            oneWireBus(PIN_DS18B20);
DallasTemperature  tempSensors(&oneWireBus);
Adafruit_MPU6050   mpu;

// ─────────────────────────────────────────────
//  STATE
// ─────────────────────────────────────────────
float acsVOffset  = 1.65f;
float baseX = 0, baseY = 0, baseZ = 0;
bool  mpuOK = false;

float oilTemp     = 25.0f;
float windingTemp = 25.0f;
float currentAmps = 0.0f;
float vibration   = 0.0f;
float oilLevelPct = 0.0f;

int   currentLabel  = 0;      // default: NORMAL
bool  collecting    = false;
long  readingCount  = 0;
bool  headerPrinted = false;

// DS18B20 async
enum TempState { TEMP_IDLE, TEMP_CONVERTING };
TempState     tempState       = TEMP_IDLE;
unsigned long tempRequestedAt = 0;
bool          tempReady       = false;

unsigned long lastCollection = 0;

// ─────────────────────────────────────────────
//  ACS712 — calibrate offset (blocking, called once)
// ─────────────────────────────────────────────
float calibrateACSOffset() {
  unsigned long sum = 0;
  for (int i = 0; i < OFFSET_SAMPLES; i++) {
    sum += analogRead(PIN_ACS712);
    delayMicroseconds(300);
  }
  return ((float)sum / OFFSET_SAMPLES / ADC_MAX) * ADC_VREF;
}

// ─────────────────────────────────────────────
//  ACS712 — RMS current (no recalibration)
// ─────────────────────────────────────────────
float readCurrent() {
  unsigned long windowUs   = (unsigned long)((WINDOW_CYCLES / MAINS_FREQ) * 1e6);
  unsigned long intervalUs = 1000000UL / ACS_SAMPLE_RATE;
  unsigned long endUs      = micros() + windowUs;
  unsigned long nextSample = micros();
  double sumSq = 0.0;
  unsigned long n = 0;

  while (micros() < endUs) {
    while (micros() < nextSample) {}
    nextSample += intervalUs;
    int raw = 0;
    for (int i = 0; i < 8; i++) raw += analogRead(PIN_ACS712);
    float voltage = ((float)raw / 8.0f / ADC_MAX) * ADC_VREF;
    float inst    = (voltage - acsVOffset) / ACS712_SENSITIVITY;
    if (inst > 30.0f || inst < -30.0f) inst = 0.0f;
    sumSq += (double)inst * inst;
    n++;
  }

  float rms = (n > 0) ? sqrtf((float)(sumSq / n)) : 0.0f;
  if (rms < CURRENT_DEADBAND) rms = 0.0f;
  return rms;
}

// ─────────────────────────────────────────────
//  MPU6050 — vibration (micros spin, no delay)
// ─────────────────────────────────────────────
float readVibration() {
  if (!mpuOK) return 0.0f;
  float peak = 0.0f;
  for (int i = 0; i < 10; i++) {
    sensors_event_t a, g, t;
    mpu.getEvent(&a, &g, &t);
    float dx = a.acceleration.x - baseX;
    float dy = a.acceleration.y - baseY;
    float dz = a.acceleration.z - baseZ;
    float m  = sqrtf(dx*dx + dy*dy + dz*dz);
    if (m > peak) peak = m;
    unsigned long wait = micros() + 5000UL;
    while (micros() < wait) {}
  }
  return peak;
}

// ─────────────────────────────────────────────
//  HC-SR04 — oil level
// ─────────────────────────────────────────────
float readOilLevel() {
  digitalWrite(PIN_TRIG, LOW);  delayMicroseconds(2);
  digitalWrite(PIN_TRIG, HIGH); delayMicroseconds(10);
  digitalWrite(PIN_TRIG, LOW);
  long dur = pulseIn(PIN_ECHO, HIGH, 30000UL);
  if (dur == 0) return oilLevelPct;
  float d = constrain(dur / 58.2f, DIST_FULL, DIST_EMPTY);
  return constrain((DIST_EMPTY - d) / (DIST_EMPTY - DIST_FULL) * 100.0f, 0.0f, 100.0f);
}

// ─────────────────────────────────────────────
//  DS18B20 async
// ─────────────────────────────────────────────
void requestTemps() {
  tempSensors.requestTemperatures();
  tempRequestedAt = millis();
  tempState = TEMP_CONVERTING;
  tempReady = false;
}

bool readTempsIfReady() {
  if (tempState != TEMP_CONVERTING) return false;
  if (millis() - tempRequestedAt < DS18B20_CONV_MS) return false;

  float t;
  t = tempSensors.getTempC(ADDR_OIL);
  if (t != -127.0f && t != 85.0f && t >= -10.0f && t <= 120.0f) oilTemp = t;

  t = tempSensors.getTempC(ADDR_WINDING);
  if (t != -127.0f && t != 85.0f && t >= -10.0f && t <= 120.0f) windingTemp = t;

  tempState = TEMP_IDLE;
  tempReady = true;
  return true;
}

// ─────────────────────────────────────────────
//  MPU calibration (blocking — only in setup)
// ─────────────────────────────────────────────
void calibrateMPU() {
  Serial.println("# MPU calibrating — keep still for 5s...");
  float sx = 0, sy = 0, sz = 0;
  for (int i = 0; i < 500; i++) {
    sensors_event_t a, g, t;
    mpu.getEvent(&a, &g, &t);
    sx += a.acceleration.x;
    sy += a.acceleration.y;
    sz += a.acceleration.z;
    delay(10);
  }
  baseX = sx / 500.0f;
  baseY = sy / 500.0f;
  baseZ = sz / 500.0f;
  Serial.printf("# MPU base: X=%.3f Y=%.3f Z=%.3f\n", baseX, baseY, baseZ);
}

// ─────────────────────────────────────────────
//  CSV HEADER
// ─────────────────────────────────────────────
void printCSVHeader() {
  Serial.println("timestamp_ms,oil_temp_c,winding_temp_c,current_a,vibration_ms2,oil_level_pct,label");
  headerPrinted = true;
}

// ─────────────────────────────────────────────
//  EMIT ONE CSV ROW
// ─────────────────────────────────────────────
void emitRow() {
  Serial.printf("%lu,%.2f,%.2f,%.3f,%.3f,%.1f,%d\n",
    millis(),
    oilTemp,
    windingTemp,
    currentAmps,
    vibration,
    oilLevelPct,
    currentLabel
  );
  readingCount++;

  // Progress blink
  digitalWrite(PIN_LED_NORMAL, (readingCount % 2 == 0) ? HIGH : LOW);

  // Milestone report (sent as CSV comment — Python will skip these)
  if (readingCount % 100 == 0) {
    Serial.printf("# Progress: %ld / %d readings | label=%s\n",
                  readingCount, TARGET_READINGS, LABEL_NAMES[currentLabel]);
  }

  if (readingCount >= TARGET_READINGS) {
    collecting = false;
    digitalWrite(PIN_LED_NORMAL, HIGH);
    Serial.printf("# DONE: %ld readings collected. Send RESET to start again.\n",
                  readingCount);
  }
}

// ─────────────────────────────────────────────
//  SERIAL COMMAND PARSER
// ─────────────────────────────────────────────
void handleSerial() {
  if (!Serial.available()) return;
  String cmd = Serial.readStringUntil('\n');
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "START") {
    if (!headerPrinted) printCSVHeader();
    collecting = true;
    Serial.printf("# COLLECTING started | label=%s | target=%d\n",
                  LABEL_NAMES[currentLabel], TARGET_READINGS);
  } else if (cmd == "STOP") {
    collecting = false;
    Serial.println("# COLLECTING paused");
  } else if (cmd == "RESET") {
    collecting    = false;
    readingCount  = 0;
    headerPrinted = false;
    Serial.println("# Counter reset");
  } else if (cmd == "STATUS") {
    Serial.printf("# Status: count=%ld label=%s(%d) collecting=%s\n",
                  readingCount, LABEL_NAMES[currentLabel],
                  currentLabel, collecting ? "YES" : "NO");
    Serial.printf("# ACS Voffset=%.4f MPU=%s\n",
                  acsVOffset, mpuOK ? "OK" : "MISSING");
  } else if (cmd.startsWith("L") && cmd.length() == 2) {
    int lbl = cmd[1] - '0';
    if (lbl >= 0 && lbl <= 4) {
      currentLabel = lbl;
      Serial.printf("# Label set to %d = %s\n", currentLabel, LABEL_NAMES[currentLabel]);
    } else {
      Serial.println("# Invalid label. Use L0..L4");
    }
  } else {
    Serial.printf("# Unknown command: %s\n", cmd.c_str());
    Serial.println("# Commands: START, STOP, RESET, STATUS, L0..L4");
  }
}

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup() {
  Serial.begin(BAUD_RATE);
  delay(500);

  pinMode(PIN_BUZZER,     OUTPUT);
  pinMode(PIN_TRIG,       OUTPUT);
  pinMode(PIN_ECHO,       INPUT);
  pinMode(PIN_LED_NORMAL, OUTPUT);
  pinMode(PIN_LED_FAULT,  OUTPUT);
  digitalWrite(PIN_LED_NORMAL, LOW);
  digitalWrite(PIN_LED_FAULT,  LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(PIN_ACS712, ADC_11db);

  Wire.begin(I2C_SDA, I2C_SCL);

  // DS18B20
  tempSensors.begin();
  tempSensors.setResolution(10);
  tempSensors.setWaitForConversion(false);

  Serial.println("# =============================================");
  Serial.println("# Transformer CSV Collector — 5000 Readings");
  Serial.println("# =============================================");
  Serial.printf("# DS18B20 count: %d\n", tempSensors.getDeviceCount());
  Serial.printf("# Oil addr:      %s\n", tempSensors.isConnected(ADDR_OIL)     ? "OK" : "NOT FOUND");
  Serial.printf("# Winding addr:  %s\n", tempSensors.isConnected(ADDR_WINDING) ? "OK" : "NOT FOUND");

  // ACS712 offset
  Serial.println("# Calibrating ACS712 offset — no load on circuit...");
  acsVOffset = calibrateACSOffset();
  Serial.printf("# ACS712 Voffset = %.4f V\n", acsVOffset);

  // MPU6050
  mpuOK = mpu.begin();
  if (mpuOK) {
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_94_HZ);
    calibrateMPU();
  } else {
    Serial.println("# WARNING: MPU6050 not found — vibration will be 0.000");
  }

  // Flush reads
  Serial.println("# Flushing sensors...");
  for (int f = 0; f < 3; f++) {
    requestTemps();
    delay(DS18B20_CONV_MS);
    readTempsIfReady();
    readCurrent();
    readVibration();
    readOilLevel();
  }
  oilTemp = windingTemp = 25.0f;
  currentAmps = vibration = oilLevelPct = 0.0f;

  Serial.println("# Ready. Commands:");
  Serial.println("#   L0=NORMAL L1=OVERLOAD L2=OVERTEMP L3=LOW_OIL L4=HIGH_VIB");
  Serial.println("#   START / STOP / RESET / STATUS");
  Serial.println("# Set your label first, then send START");

  digitalWrite(PIN_LED_NORMAL, HIGH);
}

// ─────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  handleSerial();

  // Kick off DS18B20 conversion if idle
  if (tempState == TEMP_IDLE && (now - lastCollection >= COLLECTION_INTERVAL)) {
    requestTemps();
    // Read other sensors immediately
    currentAmps = readCurrent();
    vibration   = readVibration();
    oilLevelPct = readOilLevel();
  }

  // Read DS18B20 result when ready, then emit row
  if (readTempsIfReady()) {
    lastCollection = millis();
    if (collecting) {
      emitRow();
    }
  }
}
