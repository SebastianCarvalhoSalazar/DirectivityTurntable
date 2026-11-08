/**
 * @file host_main.cpp
 * @brief Ejecuta el sketch en el computador: stdin -> Serial, traza -> stdout.
 *
 * Compilar con -DSKETCH='"ruta/al/sketch.ino"'. Lee todo stdin, llama a
 * setup() y luego a loop() hasta consumir la entrada.
 */
#include "Arduino.h"

#include <iostream>
#include <iterator>

namespace host {
std::string input;
size_t inputPos = 0;
bool atLineStart = true;
}  // namespace host

HostSerial Serial;

#include SKETCH

int main() {
  host::input.assign(std::istreambuf_iterator<char>(std::cin), std::istreambuf_iterator<char>());
  setup();
  do {
    loop();
  } while (Serial.available() > 0);
  return 0;
}
