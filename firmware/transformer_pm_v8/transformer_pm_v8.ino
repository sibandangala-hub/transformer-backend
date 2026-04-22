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
#define PIN_VOLTAGE          35
#define PIN_BUZZER           25
#define PIN_LED_NORMAL       26
#define PIN_LED_FAULT        27
#define I2C_SDA              21
#define I2C_SCL              22

// ─────────────────────────────────────────────
//  WIFI & RENDER
// ─────────────────────────────────────────────
#define WIFI_SSID        "VILLA-EXT2G"
#define WIFI_PASS        "vill@2026"
#define RENDER_URL       "https://transformer-pm-api.onrender.com/api/data"
#define RENDER_API_KEY   "your_secret_key_123"
#define NTP_SERVER       "pool.ntp.org"
#define NTP_GMT_OFFSET   0
#define NTP_DST_OFFSET   0

// ─────────────────────────────────────────────
//  DS18B20 ADDRESSES
// ─────────────────────────────────────────────
DeviceAddress ADDR_WINDING = {0x28, 0xA0, 0xE4, 0x98, 0xA0, 0x24, 0x0B, 0x56};
DeviceAddress ADDR_OIL     = {0x28, 0xD1, 0x4D, 0xE1, 0xA0, 0x24, 0x0B, 0x02};

// ─────────────────────────────────────────────
//  OIL LEVEL CALIBRATION
// ─────────────────────────────────────────────
#define DIST_EMPTY           12.0f
#define DIST_FULL             6.0f

#define PHASE_CORRECTION  0.75f
// ─────────────────────────────────────────────
//  ACS712 SETTINGS
// ─────────────────────────────────────────────
#define ADC_VREF             3.3f
#define ADC_MAX              4095
#define MAINS_FREQ           50.0f
#define WINDOW_CYCLES        4
#define SENSOR_SAMPLE_RATE   6000UL
#define OFFSET_SAMPLES       500
#define ACS712_SENSITIVITY   0.100f
#define CURRENT_DEADBAND     0.30f

static const unsigned long WINDOW_US   = (unsigned long)((WINDOW_CYCLES / MAINS_FREQ) * 1e6);
static const unsigned long INTERVAL_US = 1000000UL / SENSOR_SAMPLE_RATE;

// ─────────────────────────────────────────────
//  ZMPT101B SETTINGS
// ─────────────────────────────────────────────
#define VOLTAGE_CAL_FACTOR   572.6f
#define VOLTAGE_ZERO_CUTOFF  0.008f
#define VOLTAGE_SAMPLE_US    200000UL

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
float windingTemp      = 0.0f;
float oilTemp          = 0.0f;
float currentAmps      = 0.0f;
float vibration        = 0.0f;
float oilLevelPct      = 0.0f;
float mainsVoltage     = 0.0f;
float idleCurrentNoise = 0.0f;

bool  mpuOK              = false;
bool  sensorsInitialised = false;
bool  tempFault          = false;
bool  oilTempFault       = false;
bool  voltageFault       = false;

float baseX = 0, baseY = 0, baseZ = 0;
float acsOffset     = 0.0f;
float voltageOffset = 0.0f;

unsigned long lastSensorRead = 0;
unsigned long lastLCD        = 0;
unsigned long lastSerial     = 0;

// ─────────────────────────────────────────────
//  POWER STRUCT
// ─────────────────────────────────────────────
struct PowerReading {
  float W;
  float VA;
  float VAR;
  float PF;
  float Vrms;
  float Irms;
};
PowerReading pr = {0, 0, 0, 0, 0, 0};

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
//  WIFI CONNECT
// ─────────────────────────────────────────────
bool connectWiFi() {
  Serial.printf("[WiFi] Connecting to %s", WIFI_SSID);
  WiFi.disconnect();
  delay(500);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  int tries = 0;
  while (WiFi.status() != WL_CONNECTED && tries < 20) {
    delay(500); Serial.print("."); tries++;
  }
  if (WiFi.status() == WL_CONNECTED) {
    Serial.printf("\n[WiFi] Connected — IP: %s\n", WiFi.localIP().toString().c_str());
    configTime(NTP_GMT_OFFSET, NTP_DST_OFFSET, NTP_SERVER);
    struct tm ti;
    if (getLocalTime(&ti, 5000)) {
      Serial.printf("[NTP] Time: %s", asctime(&ti));
    } else {
      Serial.println("[NTP] Sync failed");
    }
    return true;
  }
  Serial.println("\n[WiFi] FAILED");
  return false;
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
}

// ─────────────────────────────────────────────
//  ACS712 OFFSET
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
//  ZMPT101B OFFSET
// ─────────────────────────────────────────────
void calibrateVoltageOffset() {
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Calibrating ZMPT...  ");
  lcd.setCursor(0, 1); lcd.print("Finding DC offset... ");
  long sum = 0;
  const int samples = 2000;
  for (int i = 0; i < samples; i++) {
    sum += analogRead(PIN_VOLTAGE);
  }
  voltageOffset = (float)sum / samples;
  Serial.printf("[ZMPT] DC offset=%.1f ADC counts\n", voltageOffset);
  lcd.clear();
}

// ─────────────────────────────────────────────
//  ZMPT101B — read mains voltage RMS
// ─────────────────────────────────────────────
float readVoltage() {
  float sumSq = 0;
  int   count = 0;
  unsigned long rmsStart = micros();

  while (micros() - rmsStart < VOLTAGE_SAMPLE_US) {
    float centered = (float)analogRead(PIN_VOLTAGE) - voltageOffset;
    sumSq += centered * centered;
    count++;
  }

  float rmsADC           = sqrtf(sumSq / count);
  float sensorRmsVoltage = (rmsADC * ADC_VREF) / ADC_MAX;
  float voltage          = sensorRmsVoltage * VOLTAGE_CAL_FACTOR;

  if (sensorRmsVoltage < VOLTAGE_ZERO_CUTOFF) {
    voltageFault = true;
    return 0.0f;
  }

  voltageFault = false;
  Serial.printf("[ZMPT] sensorRMS=%.4fV  mainsV=%.1fV\n", sensorRmsVoltage, voltage);
  return voltage;
}

// ─────────────────────────────────────────────
//  DS18B20 — winding temperature
// ─────────────────────────────────────────────
float readWindingTemp() {
  tempSensors.requestTemperaturesByAddress(ADDR_WINDING);
  float t = tempSensors.getTempC(ADDR_WINDING);
  Serial.printf("[WINDING] raw=%.2f\n", t);
  if (t == -127.0f || t == 85.0f || t < -10.0f || t > 120.0f) {
    tempFault = true;
    Serial.println("[WINDING] FAULT — returning last known");
    return windingTemp;
  }
  tempFault = false;
  return t;
}

// ─────────────────────────────────────────────
//  DS18B20 — oil temperature
// ─────────────────────────────────────────────
float readOilTemp() {
  tempSensors.requestTemperaturesByAddress(ADDR_OIL);
  float t = tempSensors.getTempC(ADDR_OIL);
  Serial.printf("[OIL TEMP] raw=%.2f\n", t);
  if (t == -127.0f || t == 85.0f || t < -10.0f || t > 120.0f) {
    oilTempFault = true;
    Serial.println("[OIL TEMP] FAULT — returning last known");
    return oilTemp;
  }
  oilTempFault = false;
  return t;
}

// ─────────────────────────────────────────────
//  ACS712 — raw RMS
// ─────────────────────────────────────────────
float readCurrent_raw() {
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
    if (inst > 20.0f || inst < -20.0f) inst = 0.0f;
    sumSq += (double)inst * inst;
    n++;
  }
  return (n > 0) ? sqrtf((float)(sumSq / n)) : 0.0f;
}

// ─────────────────────────────────────────────
//  ACS712 — with dynamic deadband
// ─────────────────────────────────────────────
float readCurrent() {
  float rms       = readCurrent_raw();
  float threshold = max(CURRENT_DEADBAND, idleCurrentNoise);
  if (rms < threshold) rms = 0.0f;
  return rms;
}

// ─────────────────────────────────────────────
//  NOISE FLOOR
// ─────────────────────────────────────────────
void calibrateCurrentNoise() {
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Measuring noise...  ");
  lcd.setCursor(0, 1); lcd.print("NO LOAD — keep idle ");
  delay(1000);
  float sum = 0.0f;
  for (int i = 0; i < 20; i++) {
    sum += readCurrent_raw();
    lcd.setCursor(0, 2); lcd.printf("Sample %2d/20        ", i + 1);
    delay(100);
  }
  idleCurrentNoise = (sum / 20.0f) + 0.05f;
  Serial.printf("[ACS] Noise floor: %.3fA\n", idleCurrentNoise);
  lcd.clear();
}

// ─────────────────────────────────────────────
//  MPU6050 — vibration (high-pass differential)
// ─────────────────────────────────────────────
float readVibration() {
  if (!mpuOK) return 0.0f;

  const int N = 100;
  float sumSq = 0.0f;
  float prevX = 0, prevY = 0, prevZ = 0;
  bool first = true;

  for (int i = 0; i < N; i++) {
    sensors_event_t a, g, t;
    mpu.getEvent(&a, &g, &t);

    float dx = a.acceleration.x - baseX;
    float dy = a.acceleration.y - baseY;
    float dz = a.acceleration.z - baseZ;

    if (!first) {
      float ddx = dx - prevX;
      float ddy = dy - prevY;
      float ddz = dz - prevZ;
      sumSq += ddx*ddx + ddy*ddy + ddz*ddz;
    }

    prevX = dx; prevY = dy; prevZ = dz;
    first = false;
    delay(2);
  }

  return sqrtf(sumSq / (N - 1));
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
//  POWER — separate sampling, no sensor fn calls
// ─────────────────────────────────────────────
PowerReading calculatePower() {
  float realPower = 0, sumV2 = 0, sumI2 = 0;
  const int N = 2000;

  for (int i = 0; i < N; i++) {
   float v_raw_prev = (float)analogRead(PIN_VOLTAGE) - voltageOffset;
float v_raw      = (float)analogRead(PIN_VOLTAGE) - voltageOffset;
float i_raw      = (float)analogRead(PIN_ACS712)  - (acsOffset / ADC_VREF * ADC_MAX);

float v_inst = ((v_raw_prev + v_raw) / 2.0f / ADC_MAX) * ADC_VREF * VOLTAGE_CAL_FACTOR;
float i_inst = -((i_raw / ADC_MAX) * ADC_VREF / ACS712_SENSITIVITY);
    if (fabsf(i_inst) < max(CURRENT_DEADBAND, idleCurrentNoise))
      i_inst = 0.0f;

    realPower += v_inst * i_inst;
    sumV2     += v_inst * v_inst;
    sumI2     += i_inst * i_inst;

    delayMicroseconds(200);
  }

 PowerReading p;
  p.Vrms = sqrtf(sumV2 / N);
  p.Irms = sqrtf(sumI2 / N);
  p.W    = (realPower / N) / PHASE_CORRECTION;   // phase corrected
  p.VA   = p.Vrms * p.Irms;
  p.PF   = (p.VA > 0.5f) ? constrain(p.W / p.VA, -1.0f, 1.0f) : 0.0f;
  p.VAR  = sqrtf(max(0.0f, p.VA*p.VA - p.W*p.W));
  return p;
}

// ─────────────────────────────────────────────
//  NTP TIMESTAMP
// ─────────────────────────────────────────────
String getTimestamp() {
  struct tm ti;
  if (!getLocalTime(&ti)) return "1970-01-01T00:00:00Z";
  char buf[25];
  strftime(buf, sizeof(buf), "%Y-%m-%dT%H:%M:%SZ", &ti);
  return String(buf);
}

// ─────────────────────────────────────────────
//  LCD — all 4 rows
// ─────────────────────────────────────────────
void updateLCD() {
  char line[21];

  // Row 0: winding temp + voltage
  if (tempFault) {
    snprintf(line, 21, "WT:FAULT  V:%5.1fV  ", mainsVoltage);
  } else {
    snprintf(line, 21, "WT:%-4.1fC  V:%5.1fV  ", windingTemp, mainsVoltage);
  }
  lcd.setCursor(0, 0); lcd.print(line);

  // Row 1: oil temp + power factor
  if (oilTempFault) {
    snprintf(line, 21, "OT:FAULT  PF:%-5.3f ", pr.PF);
  } else {
    snprintf(line, 21, "OT:%-4.1fC  PF:%-5.3f ", oilTemp, pr.PF);
  }
  lcd.setCursor(0, 1); lcd.print(line);

  // Row 2: current + real power
  snprintf(line, 21, "I:%-5.2fA  W:%-6.1f ", currentAmps, pr.W);
  lcd.setCursor(0, 2); lcd.print(line);

  // Row 3: oil level + vibration
  snprintf(line, 21, "Oil:%-4.1f%% Vib:%-5.3f", oilLevelPct, vibration);
  lcd.setCursor(0, 3); lcd.print(line);
}

// ─────────────────────────────────────────────
//  HTTP POST
// ─────────────────────────────────────────────
void postToRender() {
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Disconnected — skipping POST");
    return;
  }

  StaticJsonDocument<512> doc;
  doc["winding_temp"]    = windingTemp;
  doc["oil_temp"]        = oilTemp;
  doc["current"]         = currentAmps;
  doc["vibration"]       = vibration;
  doc["oil_level"]       = oilLevelPct;
  doc["voltage"]         = mainsVoltage;
  doc["real_power"]      = pr.W;
  doc["apparent_power"]  = pr.VA;
  doc["reactive_power"]  = pr.VAR;
  doc["power_factor"]    = pr.PF;
  doc["temp_fault"]      = tempFault;
  doc["oil_temp_fault"]  = oilTempFault;
  doc["voltage_fault"]   = voltageFault;
  doc["timestamp"]       = getTimestamp();

  String payload;
  serializeJson(doc, payload);

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
  Serial.printf(
    "winding_temp:%.2f oil_temp:%.2f voltage:%.1f current:%.3f vibration:%.3f oil_level:%.1f "
    "W:%.2f VA:%.2f VAR:%.2f PF:%.3f tfault:%d otfault:%d vfault:%d noise:%.3f\n",
    windingTemp, oilTemp, mainsVoltage, currentAmps, vibration, oilLevelPct,
    pr.W, pr.VA, pr.VAR, pr.PF,
    (int)tempFault, (int)oilTempFault, (int)voltageFault, idleCurrentNoise
  );
  postToRender();
}

// ─────────────────────────────────────────────
//  SETUP
// ─────────────────────────────────────────────
void setup() {
  Serial.begin(115200);
  delay(500);
  Serial.println("\n=== Transformer PM v14 ===");

  pinMode(PIN_BUZZER,     OUTPUT);
  pinMode(PIN_TRIG,       OUTPUT);
  pinMode(PIN_ECHO,       INPUT);
  pinMode(PIN_LED_NORMAL, OUTPUT);
  pinMode(PIN_LED_FAULT,  OUTPUT);
  digitalWrite(PIN_LED_NORMAL, LOW);
  digitalWrite(PIN_LED_FAULT,  LOW);

  analogReadResolution(12);
  analogSetPinAttenuation(PIN_ACS712,  ADC_11db);
  analogSetPinAttenuation(PIN_VOLTAGE, ADC_11db);

  Wire.begin(I2C_SDA, I2C_SCL);
  lcd.init(); lcd.backlight(); lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Transformer PM v14  ");
  lcd.setCursor(0, 1); lcd.print("Initialising...     ");
  delay(2000);
  buzzPattern(2, 200, 100);

  // DS18B20
  tempSensors.begin();
  tempSensors.setResolution(10);
  tempSensors.setWaitForConversion(true);
  int found = tempSensors.getDeviceCount();
  Serial.printf("[BOOT] DS18B20 found: %d\n", found);
  Serial.printf("[BOOT] Winding addr valid: %s\n",
                tempSensors.isConnected(ADDR_WINDING) ? "YES" : "NO");
  Serial.printf("[BOOT] Oil addr valid:     %s\n",
                tempSensors.isConnected(ADDR_OIL)     ? "YES" : "NO");

  // MPU6050
  mpuOK = mpu.begin();
  if (mpuOK) {
    mpu.setAccelerometerRange(MPU6050_RANGE_2_G);
    mpu.setGyroRange(MPU6050_RANGE_500_DEG);
    mpu.setFilterBandwidth(MPU6050_BAND_260_HZ);
    calibrateMPU();
  } else {
    Serial.println("[BOOT] MPU6050 not found");
    lcd.clear();
    lcd.setCursor(0, 0); lcd.print("WARNING:            ");
    lcd.setCursor(0, 1); lcd.print("MPU6050 NOT FOUND   ");
    lcd.setCursor(0, 2); lcd.print("Check I2C wiring    ");
    delay(3000);
    lcd.clear();
  }

  // WiFi
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Connecting WiFi...  ");
  connectWiFi();

  // ACS712 offset
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Calibrating ACS712..");
  lcd.setCursor(0, 1); lcd.print("NO LOAD please...   ");
  delay(500);
  acsOffset = calibrateAnalogOffset(PIN_ACS712);
  Serial.printf("[BOOT] ACS712 offset=%.4fV\n", acsOffset);

  // ZMPT101B offset
  calibrateVoltageOffset();

  // Current noise floor
  calibrateCurrentNoise();

  // Boot flush
  lcd.clear();
  lcd.setCursor(0, 0); lcd.print("Flushing sensors... ");
  for (int f = 0; f < BOOT_FLUSH_READS; f++) {
    float t  = readWindingTemp();
    float ot = readOilTemp();
    float v  = readVoltage();
    float c  = readCurrent();
    float vb = readVibration();
    float o  = readOilLevel();
    lcd.setCursor(0, 1); lcd.printf("Pass %d/%d            ", f+1, BOOT_FLUSH_READS);
    if (f == BOOT_FLUSH_READS - 1) {
      windingTemp  = (t  > -998.0f) ? t  : 25.0f;
      oilTemp      = (ot > -998.0f) ? ot : 25.0f;
      mainsVoltage = v;
      currentAmps  = c;
      vibration    = vb;
      oilLevelPct  = o;
      pr           = calculatePower();
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

  // WiFi watchdog
  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("[WiFi] Lost — reconnecting...");
    lcd.setCursor(0, 3); lcd.print("WiFi RECONNECTING...");
    bool ok = connectWiFi();
    if (!ok) { delay(5000); return; }
    delay(1000);
    acsOffset = calibrateAnalogOffset(PIN_ACS712);
    calibrateVoltageOffset();
    calibrateCurrentNoise();
    lcd.setCursor(0, 3); lcd.print("                    ");
  }

  // Heap watchdog
  if (ESP.getFreeHeap() < 10000) {
    Serial.printf("[HEAP] Critical (%d) — restarting\n", ESP.getFreeHeap());
    delay(1000);
    ESP.restart();
  }

  unsigned long now = millis();

  // Sensor read
  if (sensorsInitialised && (now - lastSensorRead >= SENSOR_READ_MS)) {
    lastSensorRead = now;

    // Existing sensor reads — untouched
    float t  = readWindingTemp();
    float ot = readOilTemp();
    float v  = readVoltage();
    if (t  > -998.0f) windingTemp  = t;
    if (ot > -998.0f) oilTemp      = ot;
    mainsVoltage = v;
    currentAmps  = readCurrent();
    vibration    = readVibration();
    oilLevelPct  = readOilLevel();

    // Power — separate
    pr = calculatePower();
  }

  // LCD
  if (now - lastLCD >= LCD_UPDATE_MS) {
    lastLCD = now;
    updateLCD();
  }

  // Serial + POST
  if (sensorsInitialised && (now - lastSerial >= SERIAL_UPDATE_MS)) {
    lastSerial = now;
    printSerial();
  }
}