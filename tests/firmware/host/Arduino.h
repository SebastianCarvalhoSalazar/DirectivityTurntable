/**
 * @file Arduino.h
 * @brief Sustituto mínimo de Arduino.h para compilar el firmware en el computador.
 *
 * Reproduce solo lo que usa DirectivityTurntable.ino, con la semántica del AVR:
 *   - Serial lee de un buffer cargado desde stdin y escribe en stdout.
 *   - digitalWrite() se registra como un evento "@dw <pin> <valor>".
 *   - Print::printFloat se reimplementa con float (en AVR double == float).
 *   - strtod() se reemplaza por una versión con la gramática de avr-libc
 *     (decimal, INF/INFINITY, NAN; sin hexadecimales).
 *
 * Los eventos empiezan con '@' y siempre ocupan una línea propia, así que la
 * salida es una traza comparable: respuestas del protocolo intercaladas con
 * las acciones sobre el hardware.
 */
#ifndef DIRECTIVITY_HOST_ARDUINO_H
#define DIRECTIVITY_HOST_ARDUINO_H

#include <ctype.h>
#include <math.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>

#include <string>

typedef uint8_t byte;

#define HIGH 0x1
#define LOW 0x0
#define OUTPUT 0x1

class __FlashStringHelper;
#define F(literal) (reinterpret_cast<const __FlashStringHelper *>(literal))

namespace host {

/** Bytes pendientes de "recibir" por el puerto serial. */
extern std::string input;
/** Posición de lectura dentro de input. */
extern size_t inputPos;
/** true si lo último escrito en stdout fue un salto de línea. */
extern bool atLineStart;

/** Escribe texto en stdout recordando si terminó en salto de línea. */
inline void write(const char *text) {
  const size_t n = strlen(text);
  if (n == 0) return;
  fwrite(text, 1, n, stdout);
  atLineStart = text[n - 1] == '\n';
}

/** Registra un evento de hardware en su propia línea; aborta si se intercala en una respuesta. */
inline void event(const char *text) {
  if (!atLineStart) {
    fputs("\n!! evento de hardware en medio de una respuesta\n", stdout);
    abort();
  }
  write(text);
  write("\n");
}

/**
 * strtod con la gramática de avr-libc: espacios, signo, dígitos con punto y
 * exponente opcional, o INF / INFINITY / NAN sin distinguir mayúsculas.
 */
inline double avrStrtod(const char *text, char **end) {
  const char *p = text;
  while (isspace(static_cast<unsigned char>(*p))) ++p;
  const char *start = p;
  bool negative = false;
  if (*p == '+' || *p == '-') negative = *p++ == '-';
  if (strncasecmp(p, "INFINITY", 8) == 0 || strncasecmp(p, "INF", 3) == 0) {
    *end = const_cast<char *>(p + (strncasecmp(p, "INFINITY", 8) == 0 ? 8 : 3));
    return negative ? -INFINITY : INFINITY;
  }
  if (strncasecmp(p, "NAN", 3) == 0) {
    *end = const_cast<char *>(p + 3);
    return NAN;
  }
  const char *digits = p;
  while (isdigit(static_cast<unsigned char>(*p))) ++p;
  if (*p == '.') {
    ++p;
    while (isdigit(static_cast<unsigned char>(*p))) ++p;
  }
  if (p == digits || (p == digits + 1 && *digits == '.')) {
    *end = const_cast<char *>(text);
    return 0.0;
  }
  if (*p == 'e' || *p == 'E') {
    const char *q = p + 1;
    if (*q == '+' || *q == '-') ++q;
    if (isdigit(static_cast<unsigned char>(*q))) {
      while (isdigit(static_cast<unsigned char>(*q))) ++q;
      p = q;
    }
  }
  const std::string number(start, p);
  *end = const_cast<char *>(p);
  // En AVR el resultado es float: se redondea una sola vez desde el decimal.
  return static_cast<double>(strtof(number.c_str(), nullptr));
}

}  // namespace host

#define strtod host::avrStrtod

/** Serial del Leonardo (USB CDC) simulado sobre stdin/stdout. */
class HostSerial {
 public:
  void begin(long) {}
  explicit operator bool() const { return true; }

  int available() const { return static_cast<int>(host::input.size() - host::inputPos); }
  int read() {
    if (host::inputPos >= host::input.size()) return -1;
    return static_cast<unsigned char>(host::input[host::inputPos++]);
  }

  void print(const char *text) { host::write(text); }
  void print(const __FlashStringHelper *text) { print(reinterpret_cast<const char *>(text)); }
  void print(char c) {
    const char text[2] = {c, '\0'};
    host::write(text);
  }
  void print(long value) {
    char text[24];
    snprintf(text, sizeof(text), "%ld", value);
    host::write(text);
  }
  void print(int value) { print(static_cast<long>(value)); }
  void print(unsigned long value) {
    char text[24];
    snprintf(text, sizeof(text), "%lu", value);
    host::write(text);
  }
  void print(unsigned int value) { print(static_cast<unsigned long>(value)); }
  /** Print::printFloat del core AVR, con float en lugar de double. */
  void print(double number, int digits = 2) {
    float value = static_cast<float>(number);
    if (isnan(value)) return print("nan");
    if (isinf(value)) return print("inf");
    if (value > 4294967040.0f || value < -4294967040.0f) return print("ovf");
    if (value < 0.0f) {
      print('-');
      value = -value;
    }
    float rounding = 0.5f;
    for (int i = 0; i < digits; ++i) rounding /= 10.0f;
    value += rounding;
    const unsigned long intPart = static_cast<unsigned long>(value);
    float remainder = value - static_cast<float>(intPart);
    print(intPart);
    if (digits > 0) print('.');
    while (digits-- > 0) {
      remainder *= 10.0f;
      const unsigned int toPrint = static_cast<unsigned int>(remainder);
      print(toPrint);
      remainder -= static_cast<float>(toPrint);
    }
  }

  void println() { host::write("\n"); }
  template <typename T>
  void println(T value) {
    print(value);
    println();
  }
  void println(double value, int digits) {
    print(value, digits);
    println();
  }
};

extern HostSerial Serial;

inline void pinMode(uint8_t, uint8_t) {}

inline void digitalWrite(uint8_t pin, uint8_t value) {
  char text[32];
  snprintf(text, sizeof(text), "@dw %u %u", pin, value);
  host::event(text);
}

#endif  // DIRECTIVITY_HOST_ARDUINO_H
