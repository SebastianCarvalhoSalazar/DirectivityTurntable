/**
 * @file Stepper.h
 * @brief Sustituto de la librería Stepper: registra cada giro como "@step <pasos>".
 */
#ifndef DIRECTIVITY_HOST_STEPPER_H
#define DIRECTIVITY_HOST_STEPPER_H

#include "Arduino.h"

class Stepper {
 public:
  Stepper(int, int, int, int, int) {}
  void setSpeed(long) {}
  void step(int steps) {
    char text[32];
    snprintf(text, sizeof(text), "@step %d", steps);
    host::event(text);
  }
};

#endif  // DIRECTIVITY_HOST_STEPPER_H
