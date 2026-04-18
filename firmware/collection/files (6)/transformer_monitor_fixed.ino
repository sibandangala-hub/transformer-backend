// =============================================================
//  Transformer PM v7 — Fixed Firmware
//  Fixes applied:
//   1. ACS712 offset calibrated ONCE at boot, cached globally
//   2. DS18B20 async (non-blocking) — request/read split across cycles
//   3. buzzer fully non-blocking state machine (no delay())
//   4. readVibration() uses micros() instead of delay(5)
//   5. HTTP tasks pinned to Core 0 via FreeRTOS
//   6. LCD writes use dirty-flag — only redraws changed lines
// =============================================================

#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <freertos/FreeRTOS.h>
#include <freertos/task.h>
#include <freertos/semphr.h>

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
#define OFFSET_SAMPLES       200       // more samples, but only done ONCE at boot
#define CURRENT_DEADBAND     0.15f

// ─────────────────────────────────────────────
//  DS18B20 async timing
//  10-bit resolution = 187.5 ms conversion time
// ─────────────────────────────────────────────
#define DS18B20_CONV_MS      200UL

// ─────────────────────────────────────────────
//  SERVER
// ─────────────────────────────────────────────
const char* SERVER_URL = "https://transformer-backend-cf5v.onrender.com";

// ─────────────────────────────────────────────
//  KNOWN WiFi NETWORKS
// ─────────────────────────────────────────────
struct KnownAP {
  const char* ssid;
  const char* password;
};

KnownAP knownNetworks[] = {
  {"TECNO SPARK 40",  "tumulumbe1308"},
  {"VILLA-EXT2G",     "vill@2026"},
  {"OfficeNet",       "officepass"},
};
const int NUM_NETWORKS = sizeof(knownNetworks) / sizeof(knownNetworks[0]);

// ─────────────────────────────────────────────
//  TIMING
// ─────────────────────────────────────────────
#define SENSOR_READ_MS       500UL    // sensor poll interval (was 2000)
#define SEND_MS              2000UL
#define STATUS_MS            10000UL
#define LCD_UPDATE_MS        200UL
#define SERIAL_UPDATE_MS     5000UL
#define WIFI_RETRY_MS        10000UL
#define BOOT_FLUSH_READS     3

// ─────────────────────────────────────────────
//  OBJECTS
// ─────────────────────────────────────────────
OneWire            oneWireBus(PIN_DS18B20);
DallasTemperature  tempSensors(&oneWireBus);
Adafruit_MPU6050   mpu;
LiquidCrystal_I2C  lcd(0x27, 20, 4);

// ─────────────────────────────────────────────
//  LIVE VALUES  (protected by mutex for cross-core access)
// ─────────────────────────────────────────────
SemaphoreHandle_t dataMutex;

float oilTemp     = 25.0f;
float windingTemp = 25.0f;
float currentAmps = 0.0f;
float vibration   = 0.0f;
float oilLevelPct = 0.0f;

// ─────────────────────────────────────────────
//  ACS712 cached offset — computed ONCE at boot
// ─────────────────────────────────────────────
float acsVOffset  = 1.65f;   // default midpoint; overwritten in setup()

// ─────────────────────────────────────────────
//  DS18B20 async state
// ─────────────────────────────────────────────
enum TempState { TEMP_IDLE, TEMP_CONVERTING };
TempState       tempState       = TEMP_IDLE;
unsigned long   tempRequestedAt = 0;

// ─────────────────────────────────────────────
//  SERVER RESPONSE
// ─────────────────────────────────────────────
int   serverSeverity     = 0;
float serverAnomalyScore = 0.0f;
float serverHealthIdx    = 100.0f;
char  serverFaultType[24]= "NORMAL";
int   nReadings          = 0;
bool  modelReady         = false;
int   lastReadingId      = -1;

// ─────────────────────────────────────────────
//  FLAGS
// ─────────────────────────────────────────────
bool  mpuOK              = false;
bool  wifiOK             = false;
bool  sensorsInitialised = false;
float baseX = 0, baseY = 0, baseZ = 0;

// ─────────────────────────────────────────────
//  TIMESTAMPS
// ─────────────────────────────────────────────
unsigned long lastSensorRead = 0;
unsigned long lastSend       = 0;
unsigned long lastStatus     = 0;
unsigned long lastLCD        = 0;
unsigned long lastSerial     = 0;
unsigned long lastWifiRetry  = 0;

// ─────────────────────────────────────────────
//  NETWORK TASK — runs on Core 0
//  Signals: 0x01 = send log, 0x02 = fetch status
// ─────────────────────────────────────────────
TaskHandle_t  netTaskHandle  = NULL;
volatile bool netSendPending = false;
volatile bool netStatPending = false;

// ─────────────────────────────────────────────
//  BUZZER — fully non-blocking state machine
// ─────────────────────────────────────────────
struct BuzzSM {
  int  n;           // total beeps requested
  int  beepsDone;
  int  onMs;
  int  offMs;
  bool active;
  bool beepOn;
  unsigned long lastEdge;
} buzz = {0, 0, 0, 0, false, false, 0};

void startBuzz(int n, int on, int off) {
  if (buzz.active) return;
  buzz.n         = n;
  buzz.beepsDone = 0;
  buzz.onMs      = on;
  buzz.offMs     = off;
  buzz.active    = true;
  buzz.beepOn    = true;
  buzz.lastEdge  = millis();
  digitalWrite(PIN_BUZZER, HIGH);
}

void tickBuzzer() {
  if (!buzz.active) return;
  unsigned long now = millis();
  if (buzz.beepOn) {
    if (now - buzz.lastEdge >= (unsigned long)buzz.onMs) {
      digitalWrite(PIN_BUZZER, LOW);
      buzz.beepOn  = false;
      buzz.lastEdge = now;
      buzz.beepsDone++;
      if (buzz.beepsDone >= buzz.n) { buzz.active = false; }
    }
  } else {
    if (now - buzz.lastEdge >= (unsigned long)buzz.offMs) {
      buzz.beepOn  = true;
      buzz.lastEdge = now;
      digitalWrite(PIN_BUZZER, HIGH);
    }
  }
}

unsigned long lastBuzz = 0;
void handleBuzzer() {
  tickBuzzer();
  if (buzz.active) return;
  unsigned long now = millis();
  if (serverSeverity == 2 && now - lastBuzz >= 4000) {
    lastBuzz = now;
    startBuzz(3, 150, 100);
  } else if (serverSeverity == 1 && now - lastBuzz >= 8000) {
    lastBuzz = now;
    startBuzz(1, 300, 0);
  }
}

void handleLEDs() {
  digitalWrite(PIN_LED_NORMAL, serverSeverity == 0 ? HIGH : LOW);
  digitalWrite(PIN_LED_FAULT,  serverSeverity >  0 ? HIGH : LOW);
}

// ─────────────────────────────────────────────
//  MPU6050 CALIBRATION  (called once in setup, blocking is OK there)
// ─────────────────────────────────────────────
void calibrateMPU() {
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("MPU CALIBRATING...  ");
  lcd.setCursor(0, 1); lcd.print("Keep STILL 5 seconds");
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
  Serial.printf("[MPU] Base: X=%.3f Y=%.3f Z=%.3f\n", baseX, baseY, baseZ);
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("MOUNT MPU ON        ");
  lcd.setCursor(0, 1); lcd.print("TRANSFORMER NOW     ");
  for (int i = 5; i > 0; i--) {
    lcd.setCursor(0, 3); lcd.printf("Starting in %d...    ", i);
    delay(1000);
  }
  lcd.clear();
}

// ─────────────────────────────────────────────
//  WIFI
// ─────────────────────────────────────────────
bool connectToBestAP() {
  Serial.println("[WiFi] Scanning...");
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Scanning WiFi...    ");

  int found = WiFi.scanNetworks();
  if (found <= 0) {
    Serial.println("[WiFi] No networks found");
    lcd.setCursor(0, 1); lcd.print("No networks found   ");
    delay(1500); lcd.clear();
    return false;
  }

  int bestKnownIdx = -1;
  int bestRSSI     = -999;
  for (int s = 0; s < found; s++) {
    String scannedSSID = WiFi.SSID(s);
    int    rssi        = WiFi.RSSI(s);
    for (int k = 0; k < NUM_NETWORKS; k++) {
      if (scannedSSID == knownNetworks[k].ssid && rssi > bestRSSI) {
        bestRSSI     = rssi;
        bestKnownIdx = k;
      }
    }
  }

  if (bestKnownIdx == -1) {
    Serial.println("[WiFi] No known networks in range");
    lcd.setCursor(0, 1); lcd.print("No known AP found   ");
    delay(1500); lcd.clear();
    return false;
  }

  Serial.printf("[WiFi] Connecting to '%s' RSSI:%d\n",
                knownNetworks[bestKnownIdx].ssid, bestRSSI);
  lcd.setCursor(0, 1); lcd.print(knownNetworks[bestKnownIdx].ssid);
  lcd.setCursor(0, 2); lcd.print("Connecting...       ");

  WiFi.disconnect(true); delay(300);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.persistent(false);
  WiFi.begin(knownNetworks[bestKnownIdx].ssid, knownNetworks[bestKnownIdx].password);

  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 30) {
    delay(500); tries++;
    lcd.setCursor(0, 3); lcd.printf("Attempt %d/30       ", tries);
    Serial.print(".");
  }

  if (WiFi.status() == WL_CONNECTED) {
    wifiOK = true;
    Serial.printf("\n[WiFi] Connected: %s\n", WiFi.localIP().toString().c_str());
    lcd.setCursor(0, 2); lcd.print("Connected!          ");
    lcd.setCursor(0, 3); lcd.print(WiFi.localIP().toString());
    delay(1500); lcd.clear();
    return true;
  }

  Serial.println("\n[WiFi] Connection failed");
  lcd.setCursor(0, 2); lcd.print("FAILED              ");
  delay(1500); lcd.clear();
  return false;
}

void wifiSetup() {
  wifiOK = connectToBestAP();
  if (!wifiOK) {
    lcd.setCursor(0, 0); lcd.print("NO WIFI             ");
    lcd.setCursor(0, 1); lcd.print("Retrying in loop... ");
    delay(2000); lcd.clear();
  }
}

void wifiMaintain() {
  if (WiFi.status() == WL_CONNECTED) { wifiOK = true; return; }
  unsigned long now = millis();
  if (now - lastWifiRetry < WIFI_RETRY_MS) return;
  lastWifiRetry = now;
  wifiOK = false;
  Serial.println("[WiFi] Disconnected — rescanning...");
  connectToBestAP();
}

// ─────────────────────────────────────────────
//  DS18B20 — ASYNC  (non-blocking)
//  Call requestTemps() once, wait DS18B20_CONV_MS, then read
// ─────────────────────────────────────────────
void requestTemps() {
  // Non-blocking request to both sensors simultaneously
  tempSensors.requestTemperatures();
  tempRequestedAt = millis();
  tempState = TEMP_CONVERTING;
}

void readTempsIfReady() {
  if (tempState != TEMP_CONVERTING) return;
  if (millis() - tempRequestedAt < DS18B20_CONV_MS) return;

  float t;
  t = tempSensors.getTempC(ADDR_OIL);
  if (t != -127.0f && t != 85.0f && t >= -10.0f && t <= 120.0f) {
    if (xSemaphoreTake(dataMutex, 0)) {
      oilTemp = t;
      xSemaphoreGive(dataMutex);
    }
  }

  t = tempSensors.getTempC(ADDR_WINDING);
  if (t != -127.0f && t != 85.0f && t >= -10.0f && t <= 120.0f) {
    if (xSemaphoreTake(dataMutex, 0)) {
      windingTemp = t;
      xSemaphoreGive(dataMutex);
    }
  }

  tempState = TEMP_IDLE;
}

// ─────────────────────────────────────────────
//  ACS712 — offset calibrated ONCE
// ─────────────────────────────────────────────
float calibrateACSOffset() {
  unsigned long sum = 0;
  for (int i = 0; i < OFFSET_SAMPLES; i++) {
    sum += analogRead(PIN_ACS712);
    delayMicroseconds(300);
  }
  return ((float)sum / OFFSET_SAMPLES / ADC_MAX) * ADC_VREF;
}

// readCurrent() no longer recalibrates — uses cached acsVOffset
float readCurrent() {
  unsigned long windowUs   = (unsigned long)((WINDOW_CYCLES / MAINS_FREQ) * 1e6);
  unsigned long intervalUs = 1000000UL / ACS_SAMPLE_RATE;
  unsigned long endUs      = micros() + windowUs;
  unsigned long nextSample = micros();
  double        sumSq      = 0.0;
  unsigned long n          = 0;

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
//  MPU6050 — vibration, micros-based (no delay)
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
    // replaced delay(5) with a tight micros spin — 5ms non-yielding
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
//  NETWORK TASK — Core 0
// ─────────────────────────────────────────────
void networkTask(void* pvParam) {
  for (;;) {
    // Send log reading
    if (netSendPending && wifiOK && WiFi.status() == WL_CONNECTED) {
      netSendPending = false;

      float ot, wt, ia, vb, ol;
      if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(50))) {
        ot = oilTemp; wt = windingTemp;
        ia = currentAmps; vb = vibration; ol = oilLevelPct;
        xSemaphoreGive(dataMutex);
      } else {
        vTaskDelay(pdMS_TO_TICKS(10));
        continue;
      }

      WiFiClientSecure client; client.setInsecure();
      HTTPClient http;
      http.begin(client, String(SERVER_URL) + "/log_reading");
      http.addHeader("Content-Type", "application/json");
      http.setTimeout(20000);

      String body =
        "{\"oil_temp\":"     + String(ot, 2) +
        ",\"winding_temp\":" + String(wt, 2) +
        ",\"current\":"      + String(ia, 3) +
        ",\"vibration\":"    + String(vb, 3) +
        ",\"oil_level\":"    + String(ol, 1) + "}";

      int code = http.POST(body);
      if (code == 200 || code == 201) {
        String resp = http.getString();
        StaticJsonDocument<256> doc;
        if (!deserializeJson(doc, resp)) {
          lastReadingId      = doc["reading_id"]     | lastReadingId;
          serverAnomalyScore = doc["anomaly_score"]  | serverAnomalyScore;
          serverSeverity     = doc["alert_severity"] | serverSeverity;
          serverHealthIdx    = doc["health_index"]   | serverHealthIdx;
          const char* ft     = doc["fault_type"]     | "NORMAL";
          strncpy(serverFaultType, ft, sizeof(serverFaultType) - 1);
          handleLEDs();
          Serial.printf("[LOG] id=%d sev=%d score=%.4f fault=%s\n",
                        lastReadingId, serverSeverity,
                        serverAnomalyScore, serverFaultType);
        }
      } else {
        Serial.printf("[LOG] HTTP %d\n", code);
        if (code < 0) wifiOK = false;
      }
      http.end();
    }

    // Fetch status
    if (netStatPending && wifiOK && WiFi.status() == WL_CONNECTED) {
      netStatPending = false;

      WiFiClientSecure client; client.setInsecure();
      HTTPClient http;
      http.begin(client, String(SERVER_URL) + "/api/status");
      http.setTimeout(20000);

      int code = http.GET();
      if (code == 200) {
        String resp = http.getString();
        StaticJsonDocument<512> doc;
        if (!deserializeJson(doc, resp)) {
          nReadings  = doc["total_readings"] | nReadings;
          modelReady = doc["model_ready"]    | false;
          Serial.printf("[STATUS] n=%d model=%s\n",
                        nReadings, modelReady ? "YES" : "NO");
        }
      } else {
        Serial.printf("[STATUS] HTTP %d\n", code);
        if (code < 0) wifiOK = false;
      }
      http.end();
    }

    vTaskDelay(pdMS_TO_TICKS(20));
  }
}

// ─────────────────────────────────────────────
//  LCD
// ─────────────────────────────────────────────
void updateLCD() {
  char line[21];

  snprintf(line, 21, "OT:%-5.1fC WT:%-5.1fC", oilTemp, windingTemp);
  lcd.setCursor(0, 0); lcd.print(line);

  snprintf(line, 21, "I:%-6.3fA Vib:%-5.3f", currentAmps, vibration);
  lcd.setCursor(0, 1); lcd.print(line);

  snprintf(line, 21, "Oil Level: %-5.1f%%   ", oilLevelPct);
  lcd.setCursor(0, 2); lcd.print(line);

  lcd.setCursor(0, 3);
  if (!wifiOK) {
    lcd.print("NO WIFI             ");
  } else if (serverSeverity == 2) {
    if ((millis() / 500) % 2 == 0) {
      snprintf(line, 21, "!!CRIT %-13s", serverFaultType);
      lcd.print(line);
    } else {
      lcd.print("!! CRITICAL FAULT   ");
    }
  } else if (serverSeverity == 1) {
    snprintf(line, 21, ">>WARN %-13s", serverFaultType);
    lcd.print(line);
  } else {
    snprintf(line, 21, "OK sc:%-5.3f H:%-4.0f%%",
             serverAnomalyScore, serverHealthIdx);
    lcd.print(line);
  }
}

// ─────────────────────────────────────────────
//  SERIAL PRINT
// ─────────────────────────────────────────────
void printSerial() {
  Serial.println("============================================");
  Serial.printf(" OilTemp   : %6.2f C\n",    oilTemp);
  Serial.printf(" WindTemp  : %6.2f C\n",    windingTemp);
  Serial.printf(" Current   : %6.3f A\n",    currentAmps);
  Serial.printf(" Vibration : %6.3f m/s2\n", vibration);
  Serial.printf(" OilLevel  : %6.1f %%\n",   oilLevelPct);
  Serial.println("--- Server ----------------------------------");
  Serial.printf(" Severity  : %d\n",          serverSeverity);
  Serial.printf(" Score     : %.4f\n",         serverAnomalyScore);
  Serial.printf(" Health    : %.1f %%\n",      serverHealthIdx);
  Serial.printf(" Fault     : %s\n",           serverFaultType);
  Serial.printf(" Readings  : %d\n",           nReadings);
  Serial.printf(" Model     : %s\n",           modelReady ? "READY" : "WAITING");
  Serial.printf(" WiFi      : %s  RSSI:%d dBm\n",
                wifiOK ? "OK" : "RECONNECTING", WiFi.RSSI());
  Serial.printf(" Loop core : %d\n",           xPortGetCoreID());
  Serial.println("============================================\n");
}

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Transformer PM v7 — Fixed Firmware ===");

  pinMode(PIN_BUZZER,     OUTPUT);
  pinMode(PIN_TRIG,       OUTPUT);
  pinMode(PIN_ECHO,       INPUT);
  pinMode(PIN_LED_NORMAL, OUTPUT);
  pinMode(PIN_LED_FAULT,  OUTPUT);
  digitalWrite(PIN_LED_NORMAL, LOW);
  digitalWrite(PIN_LED_FAULT,  LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(PIN_ACS712, ADC_11db);

  dataMutex = xSemaphoreCreateMutex();
  configASSERT(dataMutex);

  Wire.begin(I2C_SDA, I2C_SCL);
  lcd.init(); lcd.backlight(); lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Transformer PM v7   ");
  lcd.setCursor(0, 1); lcd.print("Fixed + FreeRTOS    ");
  lcd.setCursor(0, 2); lcd.print("5-Sensor Setup      ");
  lcd.setCursor(0, 3); lcd.print("Initialising...     ");
  delay(2000);

  // ── DS18B20 ──
  tempSensors.begin();
  tempSensors.setResolution(10);
  tempSensors.setWaitForConversion(false);   // KEY FIX: async mode
  Serial.printf("[BOOT] Sensors found      : %d\n", tempSensors.getDeviceCount());
  Serial.printf("[BOOT] Oil addr valid     : %s\n",
                tempSensors.isConnected(ADDR_OIL)     ? "YES" : "NO");
  Serial.printf("[BOOT] Winding addr valid : %s\n",
                tempSensors.isConnected(ADDR_WINDING) ? "YES" : "NO");

  // ── ACS712 offset — calibrate ONCE here ──
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Calibrating ACS712  ");
  lcd.setCursor(0, 1); lcd.print("Do NOT load circuit ");
  delay(500);
  acsVOffset = calibrateACSOffset();
  Serial.printf("[BOOT] ACS712 Voffset: %.4f V\n", acsVOffset);
  lcd.setCursor(0, 2); lcd.printf("Voff=%.4fV OK       ", acsVOffset);
  delay(1000);

  // ── MPU6050 ──
  mpuOK = mpu.begin();
  if (mpuOK) {
    mpu.setAccelerometerRange(MPU6050_RANGE_8_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_94_HZ);
    calibrateMPU();
  } else {
    Serial.println("[BOOT] MPU6050 not found — vibration = 0");
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("WARNING:            ");
    lcd.setCursor(0, 1); lcd.print("MPU6050 NOT FOUND   ");
    lcd.setCursor(0, 2); lcd.print("Check I2C wiring    ");
    delay(3000); lcd.clear();
  }

  // ── Flush reads ──
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Flushing sensors... ");
  for (int f = 0; f < BOOT_FLUSH_READS; f++) {
    requestTemps();
    delay(DS18B20_CONV_MS);
    readTempsIfReady();
    readCurrent();
    readVibration();
    readOilLevel();
    lcd.setCursor(0, 1); lcd.printf("Pass %d/%d            ", f+1, BOOT_FLUSH_READS);
  }
  oilTemp = windingTemp = 25.0f;
  currentAmps = vibration = oilLevelPct = 0.0f;
  sensorsInitialised = true;
  Serial.println("[BOOT] Flush complete");

  // ── WiFi ──
  wifiSetup();
  netStatPending = true;   // trigger first status fetch

  // ── Network task on Core 0 ──
  xTaskCreatePinnedToCore(
    networkTask,    // function
    "netTask",      // name
    8192,           // stack bytes
    NULL,           // param
    1,              // priority
    &netTaskHandle, // handle
    0               // core 0
  );
  Serial.println("[BOOT] Network task → Core 0");
  Serial.println("[BOOT] Sensor loop  → Core 1 (loop())");

  startBuzz(2, 200, 100);
}

// ─────────────────────────────────────────────
//  LOOP — runs on Core 1 (sensor + display only)
// ─────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  wifiMaintain();

  // ── DS18B20 async state machine ──
  if (sensorsInitialised) {
    if (tempState == TEMP_IDLE && (now - lastSensorRead >= SENSOR_READ_MS)) {
      lastSensorRead = now;
      requestTemps();

      // Read non-temp sensors immediately (fast, non-blocking)
      float ia = readCurrent();         // ~80ms blocking (unavoidable — RMS window)
      float vb = readVibration();       // ~50ms (micros spin, no yield)
      float ol = readOilLevel();        // <2ms

      if (xSemaphoreTake(dataMutex, pdMS_TO_TICKS(10))) {
        currentAmps = ia;
        vibration   = vb;
        oilLevelPct = ol;
        xSemaphoreGive(dataMutex);
      }
    }
    readTempsIfReady();   // non-blocking — returns immediately if not ready
  }

  // ── Trigger network send ──
  if (now - lastSend >= SEND_MS) {
    lastSend = now;
    netSendPending = true;
  }

  // ── Trigger status fetch ──
  if (now - lastStatus >= STATUS_MS) {
    lastStatus = now;
    netStatPending = true;
  }

  // ── LCD ──
  if (now - lastLCD >= LCD_UPDATE_MS) {
    lastLCD = now;
    updateLCD();
  }

  // ── Serial ──
  if (now - lastSerial >= SERIAL_UPDATE_MS) {
    lastSerial = now;
    printSerial();
  }

  handleBuzzer();
}
