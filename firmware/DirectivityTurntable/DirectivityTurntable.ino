/**
 * @file DirectivityTurntable.ino
 * @brief Firmware de la mesa giratoria para medir directividad de parlantes.
 * @author Sebastian Carvalho Salazar
 * @version 2.0.0
 *
 * Hardware: Arduino Leonardo + puente H L298N + motor paso a paso 28BYJ-48 (12 V).
 * Pines 8, 9, 10 y 11 del Arduino -> IN1, IN2, IN3 e IN4 del L298N.
 *
 * Protocolo serial (texto ASCII, una orden por línea terminada en '\n'; se
 * ignora '\r' y no se distinguen mayúsculas de minúsculas):
 *
 *   PING            -> OK PONG
 *   HOME            -> toma la posición actual como 0°          -> OK 0.00
 *   GOTO <grados>   -> va a un ángulo absoluto (-360 a 360)      -> OK <ángulo real>
 *   MOVE <grados>   -> gira de forma relativa                    -> OK <ángulo real>
 *   POS             -> devuelve el ángulo actual                 -> OK <ángulo>
 *   RELEASE         -> apaga las bobinas para que no se calienten -> OK RELEASED
 *
 * Errores: "ERR <mensaje>". Las líneas vacías se ignoran (no tienen respuesta).
 * Los ángulos se informan con 2 decimales (Serial.print(float, 2)).
 *
 * Posición absoluta: la posición se lleva en pasos absolutos y cada destino se
 * redondea contra el ángulo absoluto, así que el error de cuantización nunca
 * supera medio paso ni se acumula. Un giro de 5° equivale a 28,44 pasos;
 * redondear cada giro por separado perdería ~5,6° por vuelta.
 *
 * Cambios respecto de la versión 1:
 *   - Un ángulo NaN ("GOTO NAN", "MOVE NAN") pasaba la verificación de rango y
 *     producía un giro indefinido. Ahora responde "ERR angulo invalido".
 *   - Se rechaza texto sobrante después del número ("GOTO 5abc", "GOTO 9O");
 *     antes se ignoraba y la mesa giraba al prefijo numérico.
 *   - toupper() recibía char con signo (comportamiento indefinido fuera de ASCII).
 *   - Las bobinas quedan apagadas antes de esperar la conexión USB.
 *   - Funciones documentadas, estado con enlace interno (static), sin números
 *     mágicos. Fuera de los casos anteriores, las respuestas y los giros son
 *     idénticos a los de la versión 1 (verificado con una prueba diferencial).
 *
 * El emulador tests/support/firmware_emulator.py replica este archivo; los tests
 * de tests/firmware/ compilan este sketch en el computador y comparan ambos.
 */

#include <Stepper.h>
#include <ctype.h>
#include <math.h>
#include <stdlib.h>
#include <string.h>

// ---------------------------------------------------------------- Configuración

/** Pasos por vuelta del eje de salida (nominal del 28BYJ-48). Calibrar con HOME + GOTO 360. */
const long STEPS_PER_REV = 2048;

/** Velocidad del motor en RPM: conservadora para este motor (una vuelta en ~12 s). */
const int MOTOR_RPM = 5;

/** Límite de giro absoluto, en grados, para no enrollar el cable del parlante. */
const float MAX_ABS_DEG = 360.0f;

/** Velocidad del puerto serial (el Leonardo usa USB nativo y la ignora). */
const long SERIAL_BAUD = 115200;

/** Pines de control del L298N, en el orden IN1..IN4. */
const uint8_t PIN_IN1 = 8;
const uint8_t PIN_IN2 = 9;
const uint8_t PIN_IN3 = 10;
const uint8_t PIN_IN4 = 11;

/** Decimales con que se informan los ángulos. */
const int ANGLE_DECIMALS = 2;

/** Tamaño del buffer de línea, incluido el terminador: admite órdenes de hasta 31 caracteres. */
const uint8_t LINE_BUFFER_SIZE = 32;

/** Prefijos de las órdenes que llevan un argumento (incluyen el espacio separador). */
const char GOTO_PREFIX[] = "GOTO ";
const char MOVE_PREFIX[] = "MOVE ";
const size_t ARG_PREFIX_LENGTH = sizeof(GOTO_PREFIX) - 1;
static_assert(sizeof(GOTO_PREFIX) == sizeof(MOVE_PREFIX), "los prefijos deben medir lo mismo");

// ---------------------------------------------------------------- Estado

/** Driver del motor. La librería configura los pines como salida al construirse. */
static Stepper motor(STEPS_PER_REV, PIN_IN1, PIN_IN2, PIN_IN3, PIN_IN4);

/** Posición absoluta del plato, en pasos desde el último HOME. */
static long positionSteps = 0;

/** Línea en recepción, su longitud y si se está descartando por demasiado larga. */
static char lineBuffer[LINE_BUFFER_SIZE];
static uint8_t lineLength = 0;
static bool discardingLine = false;

// ---------------------------------------------------------------- Conversión de ángulos

/**
 * @brief Convierte un ángulo absoluto en la posición absoluta en pasos más cercana.
 *
 * Con STEPS_PER_REV potencia de 2 (2048) el producto es exacto en float, así que
 * el resultado solo se redondea una vez, en la división.
 *
 * @param degrees Ángulo absoluto en grados.
 * @return Pasos absolutos, redondeados con los empates alejándose de cero.
 */
static long degreesToSteps(float degrees) {
  return lround(degrees * STEPS_PER_REV / 360.0f);
}

/**
 * @brief Convierte una posición absoluta en pasos a grados.
 * @param steps Posición absoluta en pasos.
 * @return Ángulo en grados (exacto en float si STEPS_PER_REV es potencia de 2).
 */
static float stepsToDegrees(long steps) {
  return steps * 360.0f / STEPS_PER_REV;
}

/**
 * @brief Indica si un ángulo absoluto está dentro del límite de giro.
 *
 * Escrito como negación de la condición válida para que NaN quede fuera de rango.
 *
 * @param degrees Ángulo absoluto en grados.
 * @return true si -MAX_ABS_DEG <= degrees <= MAX_ABS_DEG.
 */
static bool isWithinLimits(float degrees) {
  return degrees >= -MAX_ABS_DEG && degrees <= MAX_ABS_DEG;
}

// ---------------------------------------------------------------- Respuestas

/** @brief Responde "OK <ángulo actual>" con ANGLE_DECIMALS decimales. */
static void replyPosition() {
  Serial.print(F("OK "));
  Serial.println(stepsToDegrees(positionSteps), ANGLE_DECIMALS);
}

/**
 * @brief Responde "ERR <mensaje>".
 * @param message Mensaje guardado en flash con F().
 */
static void replyError(const __FlashStringHelper *message) {
  Serial.print(F("ERR "));
  Serial.println(message);
}

// ---------------------------------------------------------------- Motor

/** @brief Apaga las cuatro salidas del puente H; la posición lógica se conserva. */
static void releaseCoils() {
  digitalWrite(PIN_IN1, LOW);
  digitalWrite(PIN_IN2, LOW);
  digitalWrite(PIN_IN3, LOW);
  digitalWrite(PIN_IN4, LOW);
}

/**
 * @brief Gira hasta un ángulo absoluto y responde con el ángulo real alcanzado.
 *
 * El giro es bloqueante. Las bobinas quedan energizadas al terminar (torque de
 * retención) hasta la siguiente orden RELEASE.
 *
 * @param targetDegrees Ángulo absoluto pedido, en grados.
 */
static void moveToDegrees(float targetDegrees) {
  if (!isWithinLimits(targetDegrees)) {
    replyError(F("fuera de rango (-360 a 360)"));
    return;
  }
  const long targetSteps = degreesToSteps(targetDegrees);
  const long delta = targetSteps - positionSteps;
  if (delta != 0) {
    motor.step(static_cast<int>(delta));  // |delta| <= 2 * STEPS_PER_REV: cabe en int
  }
  positionSteps = targetSteps;
  replyPosition();
}

// ---------------------------------------------------------------- Órdenes

/**
 * @brief Interpreta el argumento numérico de GOTO o MOVE.
 *
 * Acepta la gramática de strtod (espacios iniciales, signo, decimales,
 * exponente) seguida solo de espacios. Rechaza texto sobrante y NaN. Un valor
 * infinito se acepta aquí y lo rechaza después la verificación de rango.
 *
 * @param text Texto que sigue al prefijo de la orden.
 * @param[out] degrees Ángulo leído, si el texto es válido.
 * @return true si el texto es un número válido.
 */
static bool parseAngle(const char *text, float *degrees) {
  char *end = nullptr;
  const float value = strtod(text, &end);
  if (end == text || isnan(value)) {
    return false;
  }
  while (*end == ' ') {
    ++end;
  }
  if (*end != '\0') {
    return false;
  }
  *degrees = value;
  return true;
}

/**
 * @brief Pasa una línea a mayúsculas, en el lugar.
 * @param line Texto terminado en '\0'.
 */
static void toUpperAscii(char *line) {
  for (char *p = line; *p != '\0'; ++p) {
    *p = static_cast<char>(toupper(static_cast<unsigned char>(*p)));
  }
}

/**
 * @brief Orden GOTO: gira hasta un ángulo absoluto.
 * @param argument Texto que sigue a "GOTO ".
 */
static void handleGoto(const char *argument) {
  float degrees;
  if (!parseAngle(argument, &degrees)) {
    replyError(F("angulo invalido"));
    return;
  }
  moveToDegrees(degrees);
}

/**
 * @brief Orden MOVE: gira de forma relativa a la posición actual.
 * @param argument Texto que sigue a "MOVE ".
 */
static void handleMove(const char *argument) {
  float degrees;
  if (!parseAngle(argument, &degrees)) {
    replyError(F("angulo invalido"));
    return;
  }
  moveToDegrees(stepsToDegrees(positionSteps) + degrees);
}

/**
 * @brief Ejecuta una línea completa del protocolo y envía su respuesta.
 * @param line Línea sin terminador de línea; se modifica (pasa a mayúsculas).
 */
static void handleCommand(char *line) {
  toUpperAscii(line);
  if (line[0] == '\0') {
    return;  // línea vacía: se ignora sin responder
  }
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
  } else if (strncmp(line, GOTO_PREFIX, ARG_PREFIX_LENGTH) == 0) {
    handleGoto(line + ARG_PREFIX_LENGTH);
  } else if (strncmp(line, MOVE_PREFIX, ARG_PREFIX_LENGTH) == 0) {
    handleMove(line + ARG_PREFIX_LENGTH);
  } else {
    replyError(F("comando desconocido"));
  }
}

// ---------------------------------------------------------------- Recepción serial

/**
 * @brief Acumula un carácter recibido y ejecuta la orden al llegar el fin de línea.
 *
 * Las líneas de más de LINE_BUFFER_SIZE - 1 caracteres se descartan completas y
 * se responde un único error al recibir su '\n'.
 *
 * @param c Carácter recibido.
 */
static void processChar(char c) {
  if (c == '\r') {
    return;
  }
  if (c == '\n') {
    if (discardingLine) {
      replyError(F("linea demasiado larga"));
    } else {
      lineBuffer[lineLength] = '\0';
      handleCommand(lineBuffer);
    }
    lineLength = 0;
    discardingLine = false;
  } else if (discardingLine) {
    // se ignora el resto de una línea demasiado larga
  } else if (lineLength < LINE_BUFFER_SIZE - 1) {
    lineBuffer[lineLength++] = c;
  } else {
    discardingLine = true;
  }
}

// ---------------------------------------------------------------- Arduino

/** @brief Deja el motor en estado seguro y espera la conexión USB. */
void setup() {
  motor.setSpeed(MOTOR_RPM);
  releaseCoils();
  Serial.begin(SERIAL_BAUD);
  while (!Serial) {
    // El Leonardo usa USB nativo: espera a que el computador abra el puerto.
  }
}

/** @brief Procesa todos los caracteres recibidos por el puerto serial. */
void loop() {
  while (Serial.available() > 0) {
    processChar(static_cast<char>(Serial.read()));
  }
}
