// DirectivityTurntable — firmware de la mesa giratoria para medir directividad
// Autor: Sebastian Carvalho Salazar
//
// Hardware: Arduino Leonardo + puente H L298N + motor paso a paso 28BYJ-48 (12 V)
// Pines de control: 8, 9, 10, 11 -> IN1, IN2, IN3, IN4 del L298N
//
// Protocolo serial (texto, una orden por línea, terminada en '\n'):
//   PING            -> OK PONG
//   HOME            -> toma la posición actual como 0°            -> OK 0.00
//   GOTO <grados>   -> va a un ángulo absoluto (-360 a 360)        -> OK <ángulo real>
//   MOVE <grados>   -> gira de forma relativa                      -> OK <ángulo real>
//   POS             -> devuelve el ángulo actual                   -> OK <ángulo>
//   RELEASE         -> apaga las bobinas para que no se calienten  -> OK RELEASED
// Cualquier error responde: ERR <mensaje>
//
// La posición se lleva en pasos absolutos y cada destino se redondea contra
// el ángulo absoluto. Así el error de redondeo nunca se acumula: un giro de 5°
// equivale a 28,44 pasos, y redondear cada giro por separado perdería ~5,6°
// por vuelta.

#include <Stepper.h>

// Pasos por vuelta del eje de salida (valor nominal del 28BYJ-48 en este montaje).
// Calíbralo: HOME, GOTO 360 y verifica que la marca del plato vuelva al inicio.
const long STEPS_PER_REV = 2048;
const int RPM = 5;               // velocidad conservadora para este motor
const float MAX_ABS_DEG = 360.0; // límite para no enrollar el cable del parlante

const int PIN_A = 8;
const int PIN_B = 9;
const int PIN_C = 10;
const int PIN_D = 11;

Stepper motor(STEPS_PER_REV, PIN_A, PIN_B, PIN_C, PIN_D);

long positionSteps = 0;          // posición absoluta en pasos
char lineBuffer[32];
byte lineLength = 0;
bool discardingLine = false;

long degreesToSteps(float deg) {
  return lround(deg * STEPS_PER_REV / 360.0);
}

float stepsToDegrees(long steps) {
  return steps * 360.0 / STEPS_PER_REV;
}

void releaseCoils() {
  digitalWrite(PIN_A, LOW);
  digitalWrite(PIN_B, LOW);
  digitalWrite(PIN_C, LOW);
  digitalWrite(PIN_D, LOW);
}

void replyPosition() {
  Serial.print(F("OK "));
  Serial.println(stepsToDegrees(positionSteps), 2);
}

void moveToDegrees(float targetDeg) {
  if (targetDeg > MAX_ABS_DEG || targetDeg < -MAX_ABS_DEG) {
    Serial.println(F("ERR fuera de rango (-360 a 360)"));
    return;
  }
  long targetSteps = degreesToSteps(targetDeg);
  long delta = targetSteps - positionSteps;
  if (delta != 0) {
    motor.step((int)delta);      // |delta| <= 2 * STEPS_PER_REV, cabe en int
  }
  positionSteps = targetSteps;
  replyPosition();
}

bool parseNumber(const char *text, float &value) {
  char *end;
  value = strtod(text, &end);
  return end != text;
}

void handleCommand(char *line) {
  // Pasar a mayúsculas para aceptar "goto 5" o "GOTO 5"
  for (char *p = line; *p; ++p) {
    *p = toupper(*p);
  }

  float value;
  if (strcmp(line, "PING") == 0) {
    Serial.println(F("OK PONG"));
  } else if (strcmp(line, "HOME") == 0) {
    positionSteps = 0;
    replyPosition();
  } else if (strcmp(line, "POS") == 0) {
    replyPosition();
  } else if (strcmp(line, "RELEASE") == 0) {
    releaseCoils();
    Serial.println(F("OK RELEASED"));
  } else if (strncmp(line, "GOTO ", 5) == 0) {
    if (parseNumber(line + 5, value)) {
      moveToDegrees(value);
    } else {
      Serial.println(F("ERR angulo invalido"));
    }
  } else if (strncmp(line, "MOVE ", 5) == 0) {
    if (parseNumber(line + 5, value)) {
      moveToDegrees(stepsToDegrees(positionSteps) + value);
    } else {
      Serial.println(F("ERR angulo invalido"));
    }
  } else if (line[0] != '\0') {
    Serial.println(F("ERR comando desconocido"));
  }
}

void setup() {
  Serial.begin(115200);
  while (!Serial) {
    // El Leonardo usa USB nativo: espera a que el computador abra el puerto
  }
  motor.setSpeed(RPM);
  releaseCoils();
}

void loop() {
  while (Serial.available() > 0) {
    char c = Serial.read();
    if (c == '\r') {
      continue;
    }
    if (c == '\n') {
      if (discardingLine) {
        Serial.println(F("ERR linea demasiado larga"));
      } else {
        lineBuffer[lineLength] = '\0';
        handleCommand(lineBuffer);
      }
      lineLength = 0;
      discardingLine = false;
    } else if (discardingLine) {
      // ignorar el resto de una línea demasiado larga
    } else if (lineLength < sizeof(lineBuffer) - 1) {
      lineBuffer[lineLength++] = c;
    } else {
      discardingLine = true;
    }
  }
}
