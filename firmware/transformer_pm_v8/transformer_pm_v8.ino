#include <OneWire.h>
#include <DallasTemperature.h>
#include <Wire.h>
#include <Adafruit_MPU6050.h>
#include <Adafruit_Sensor.h>
#include <LiquidCrystal_I2C.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <ArduinoJson.h>
#include <time.h>

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
//  WIFI & RENDER
// ─────────────────────────────────────────────
#define WIFI_SSID        "YOUR_SSID"
#define WIFI_PASS        "YOUR_PASSWORD"
#define RENDER_URL       "https://YOUR-APP.onrender.com/api/data"
#define RENDER_API_KEY   "your_secret_key_123"

#define NTP_SERVER       "pool.ntp.org"
#define NTP_GMT_OFFSET   0      // adjust for your timezone e.g. 3600 = UTC+1
#define NTP_DST_OFFSET   0

// ─────────────────────────────────────────────
//  DS18B20 ADDRESS
// ─────────────────────────────────────────────
DeviceAddress ADDR_WINDING = {0x28, 0xA0, 0xE4, 0x98, 0xA0, 0x24, 0x0B, 0x56};

// ─────────────────────────────────────────────
//  OIL LEVEL CALIBRATION
// ─────────────────────────────────────────────
#define DIST_EMPTY           12.0f
#define DIST_FULL             6.0f

// ─────────────────────────────────────────────
//  ACS712 SETTINGS
// ─────────────────────────────────────────────
#define ADC_VREF             3.3f
#define ADC_MAX              4095
#define MAINS_FREQ           50.0f
#define WINDOW_CYCLES        4
#define SENSOR_SAMPLE_RATE   6000UL
#define OFFSET_SAMPLES       100
#define ACS712_SENSITIVITY   0.100f
#define CURRENT_DEADBAND     0.05f

// Precomputed window constants — calculated once, not per call
static const unsigned long WINDOW_US   = (unsigned long)((WINDOW_CYCLES / MAINS_FREQ) * 1e6);
static const unsigned long INTERVAL_US = 1000000UL / SENSOR_SAMPLE_RATE;

// ─────────────────────────────────────────────
//  TIMING
// ─────────────────────────────────────────────
#define SENSOR_READ_MS       2000
#define LCD_UPDATE_MS        500
#define SERIAL_UPDATE_MS     2000
#define BOOT_FLUSH_READS     3
#define HTTP_POST_RETRIES    2

// ─────────────────────────────────────────────
//  OBJECTS
// ─────────────────────────────────────────────
OneWire            oneWireBus(PIN_DS18B20);
DallasTemperature  tempSensors(&oneWireBus);
Adafruit_MPU6050   mpu;
LiquidCrystal_I2C  lcd(0x27, 20, 4);

// ─────────────────────────────────────────────
//  LIVE VALUES
// ─────────────────────────────────────────────
float windingTemp = 0.0f;
float currentAmps = 0.0f;
float vibration   = 0.0f;
float oilLevelPct = 0.0f;

bool  mpuOK              = false;
bool  sensorsInitialised = false;
bool  tempFault          = false;   // FIX: track DS18B20 fault explicitly

float baseX = 0, baseY = 0, baseZ = 0;
float acsOffset = 0.0f;            // FIX: calibrated once in setup(), not per read

unsigned long lastSensorRead = 0;
unsigned long lastLCD        = 0;
unsigned long lastSerial     = 0;

// ─────────────────────────────────────────────
//  BUZZER
// ─────────────────────────────────────────────
void buzzPattern(int n, int on, int off) {
  for (int i = 0; i < n; i++) {
    digitalWrite(PIN_BUZZER, HIGH); delay(on);
    digitalWrite(PIN_BUZZER, LOW);
    if (i < n - 1) delay(off);
  }
}

// ─────────────────────────────────────────────
//  MPU6050 CALIBRATION
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
//  ACS712 OFFSET — called ONCE in setup()
// ─────────────────────────────────────────────
float calibrateAnalogOffset(int pin) {
  unsigned long sum = 0;
  for (int i = 0; i < OFFSET_SAMPLES; i++) {
    sum += analogRead(pin);
    delayMicroseconds(300);
  }
  return ((float)sum / OFFSET_SAMPLES / ADC_MAX) * ADC_VREF;
}

// ─────────────────────────────────────────────
//  DS18B20 — Winding Temperature
// ─────────────────────────────────────────────
float readWindingTemp() {
  tempSensors.requestTemperaturesByAddress(ADDR_WINDING);
  float t = tempSensors.getTempC(ADDR_WINDING);
  Serial.printf("[WIND TEMP] raw=%.2f\n", t);

  // FIX: explicit fault flag instead of silent stale return
  if (t == -127.0f || t == 85.0f || t < -10.0f || t > 120.0f) {
    tempFault = true;
    Serial.println("[WIND TEMP] FAULT — returning last known value");
    return windingTemp;
  }
  tempFault = false;
  return t;
}

// ─────────────────────────────────────────────
//  ACS712 — RMS current (uses pre-calibrated acsOffset)
// ─────────────────────────────────────────────
float readCurrent() {
  unsigned long endUs      = micros() + WINDOW_US;
  unsigned long nextSample = micros();
  double        sumSq      = 0.0;
  unsigned long n          = 0;

  while (micros() < endUs) {
    while (micros() < nextSample) {}
    nextSample += INTERVAL_US;
    int raw = 0;
    for (int i = 0; i < 8; i++) raw += analogRead(PIN_ACS712);
    float voltage = ((float)raw / 8.0f / ADC_MAX) * ADC_VREF;
    float inst    = (voltage - acsOffset) / ACS712_SENSITIVITY;
    if (inst > 30.0f || inst < -30.0f) inst = 0.0f;
    sumSq += (double)inst * inst;
    n++;
  }
  float rms = (n > 0) ? sqrtf((float)(sumSq / n)) : 0.0f;
  if (rms < CURRENT_DEADBAND) rms = 0.0f;
  return rms;
}

// ─────────────────────────────────────────────
//  MPU6050 — vibration
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
    delay(5);
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
//  NTP — ISO8601 timestamp string
// ─────────────────────────────────────────────
String getTimestamp() {
  struct tm ti;
  if (!getLocalTime(&ti)) return "1970-01-01T00:00:00Z";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &ti);
  return String(buf);
}

// ─────────────────────────────────────────────
//  LCD
// ─────────────────────────────────────────────
void updateLCD() {
  char line[21];

  // FIX: show FAULT on LCD if DS18B20 is bad
  if (tempFault) {
    snprintf(line, 21, " WT: FAULT          ");
  } else {
    snprintf(line, 21, " WT:%-5.1fC          ", windingTemp);
  }
  lcd.setCursor(0, 0); lcd.print(line);

  snprintf(line, 21, "I:%-6.3fA Vib:%-5.3f", currentAmps, vibration);
  lcd.setCursor(0, 1); lcd.print(line);

  snprintf(line, 21, "Oil Level: %-5.1f%%   ", oilLevelPct);
  lcd.setCursor(0, 2); lcd.print(line);

  if ((millis() / 800) % 2 == 0) {
    lcd.setCursor(0, 3); lcd.print(">> COLLECTING DATA  ");
  } else {
    lcd.setCursor(0, 3); lcd.print("                    ");
  }
}

// ─────────────────────────────────────────────
//  HTTP POST with retry
// ─────────────────────────────────────────────
void postToRender() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Disconnected — skipping POST");
    return;
  }

  // FIX: JSON buffer bumped to 384; timestamp is now real UTC ISO8601
  StaticJsonDocument<384> doc;
  doc["winding_temp"] = windingTemp;
  doc["current"]      = currentAmps;
  doc["vibration"]    = vibration;
  doc["oil_level"]    = oilLevelPct;
  doc["temp_fault"]   = tempFault;
  doc["timestamp"]    = getTimestamp();

  String payload;
  serializeJson(doc, payload);

  // FIX: retry loop
  for (int attempt = 1; attempt <= HTTP_POST_RETRIES; attempt++) {
    HTTPClient http;
    http.begin(RENDER_URL);
    http.addHeader("Content-Type", "application/json");
    http.addHeader("x-api-key", RENDER_API_KEY);
    http.setTimeout(5000);

    int code = http.POST(payload);
    if (code > 0) {
      Serial.printf("[HTTP] POST %d — %s\n", code, http.getString().c_str());
      http.end();
      return;
    }
    Serial.printf("[HTTP] Attempt %d failed: %s\n", attempt, http.errorToString(code).c_str());
    http.end();
    if (attempt < HTTP_POST_RETRIES) delay(1000);
  }
  Serial.println("[HTTP] All retries exhausted");
}

// ─────────────────────────────────────────────
//  SERIAL DEBUG
// ─────────────────────────────────────────────
void printSerial() {
  Serial.println("---");
  Serial.printf("winding_temp:%.2f current:%.3f vibration:%.3f oil_level:%.1f temp_fault:%d\n",
                windingTemp, currentAmps, vibration, oilLevelPct, (int)tempFault);
  postToRender();
}

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Transformer PM v8 ===");

  pinMode(PIN_BUZZER,     OUTPUT);
  pinMode(PIN_TRIG,       OUTPUT);
  pinMode(PIN_ECHO,       INPUT);
  pinMode(PIN_LED_NORMAL, OUTPUT);
  pinMode(PIN_LED_FAULT,  OUTPUT);
  digitalWrite(PIN_LED_NORMAL, LOW);
  digitalWrite(PIN_LED_FAULT,  LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(PIN_ACS712, ADC_ATTEN_DB_12); // FIX: not deprecated ADC_11db

  Wire.begin(I2C_SDA, I2C_SCL);
  lcd.init(); lcd.backlight(); lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Transformer PM v8   ");
  lcd.setCursor(0, 1); lcd.print("Initialising...     ");
  delay(2000);
  buzzPattern(2, 200, 100);

  // ── DS18B20 ──
  tempSensors.begin();
  tempSensors.setResolution(10);
  tempSensors.setWaitForConversion(true);
  Serial.printf("[BOOT] DS18B20 found: %d\n", tempSensors.getDeviceCount());
  Serial.printf("[BOOT] Winding addr valid: %s\n",
                tempSensors.isConnected(ADDR_WINDING) ? "YES" : "NO");

  // ── ACS712 offset — calibrate ONCE here, not per read ──
  lcd.setCursor(0, 2); lcd.print("Calibrating ACS712..");
  acsOffset = calibrateAnalogOffset(PIN_ACS712);
  Serial.printf("[BOOT] ACS712 offset=%.4fV\n", acsOffset);

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
    delay(3000);
    lcd.clear();
  }

  // ── WiFi — FIX: ALWAYS runs, not conditional on MPU failure ──
  Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
  lcd.setCursor(0, 0); lcd.print("Connecting WiFi...  ");
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {
    delay(500); Serial.print("."); tries++;
    lcd.setCursor(tries % 20, 1); lcd.print(".");
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected — IP: %s\n", WiFi.localIP().toString().c_str());
    // ── NTP sync ──
    configTime(NTP_GMT_OFFSET, NTP_DST_OFFSET, NTP_SERVER);
    Serial.println("[NTP] Syncing...");
    struct tm ti;
    if (getLocalTime(&ti, 5000)) {
      Serial.printf("[NTP] Time: %s", asctime(&ti));
    } else {
      Serial.println("[NTP] Sync failed — timestamps will be epoch");
    }
  } else {
    Serial.println("\n[WiFi] FAILED — running offline");
  }

  // ── Boot flush ──
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Flushing sensors... ");
  for (int f = 0; f < BOOT_FLUSH_READS; f++) {
    float t = readWindingTemp();
    float c = readCurrent();
    float v = readVibration();
    float o = readOilLevel();
    lcd.setCursor(0, 1); lcd.printf("Pass %d/%d            ", f+1, BOOT_FLUSH_READS);
    // FIX: on last flush pass, use actual sensor values not hardcoded defaults
    if (f == BOOT_FLUSH_READS - 1) {
      windingTemp = (t > -998.0f) ? t : 25.0f;
      currentAmps = c;
      vibration   = v;
      oilLevelPct = o;
    }
    delay(100);
  }

  sensorsInitialised = true;
  Serial.println("[BOOT] Complete — entering loop");
  lcd.clear();
}

// ─────────────────────────────────────────────
//  LOOP
// ─────────────────────────────────────────────
void loop() {
  unsigned long now = millis();

  if (sensorsInitialised && (now - lastSensorRead >= SENSOR_READ_MS)) {
    lastSensorRead = now;
    float t = readWindingTemp();
    if (t > -998.0f) windingTemp = t;
    currentAmps = readCurrent();
    vibration   = readVibration();
    oilLevelPct = readOilLevel();
  }

  if (now - lastLCD >= LCD_UPDATE_MS) {
    lastLCD = now;
    updateLCD();
  }

  // FIX: serial/POST gated to after first sensor read completes
  if (sensorsInitialised && (now - lastSerial >= SERIAL_UPDATE_MS)
      && (now - lastSensorRead < SERIAL_UPDATE_MS)) {
    lastSerial = now;
    printSerial();
  }
}