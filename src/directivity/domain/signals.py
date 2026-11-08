"""Señales de audio y estrategias de excitación (patrón Strategy).

Una ``ExcitationSignal`` sabe generar la señal que se reproduce y obtener la respuesta
al impulso (IR) a partir de la grabación. ``LogSweep`` implementa el barrido senoidal
exponencial de Farina (2000), cuya deconvolución separa la distorsión armónica de la
respuesta lineal.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import numpy.typing as npt

FloatArray = npt.NDArray[np.float64]


@dataclass(frozen=True, eq=False)
class Signal:
    """Señal de audio monofónica e inmutable (sirve para grabaciones y para IRs).

    Attributes:
        samples: Muestras en un array 1-D de ``float64``; se guarda como solo lectura.
        sample_rate: Frecuencia de muestreo en Hz.
    """

    samples: FloatArray = field(repr=False)
    sample_rate: int

    def __post_init__(self) -> None:
        """Valida y congela las muestras.

        Raises:
            ValueError: Si las muestras no son 1-D o la frecuencia de muestreo no es positiva.
        """
        samples = np.array(self.samples, dtype=np.float64, copy=True)
        if samples.ndim != 1:
            raise ValueError(f"se esperaba una señal 1-D, llegó una de forma {samples.shape}")
        if self.sample_rate <= 0:
            raise ValueError(f"frecuencia de muestreo inválida: {self.sample_rate}")
        samples.setflags(write=False)
        object.__setattr__(self, "samples", samples)

    def __len__(self) -> int:
        """Número de muestras."""
        return int(self.samples.shape[0])

    @property
    def duration_s(self) -> float:
        """Duración de la señal en segundos."""
        return len(self) / self.sample_rate

    def with_samples(self, samples: npt.ArrayLike) -> Signal:
        """Crea una señal nueva con otras muestras y la misma frecuencia de muestreo.

        Args:
            samples: Muestras nuevas.

        Returns:
            La señal nueva.
        """
        return Signal(np.asarray(samples, dtype=np.float64), self.sample_rate)


class ExcitationSignal(Protocol):
    """Estrategia de excitación: qué se reproduce y cómo se recupera la IR."""

    def generate(self, sample_rate: int) -> Signal:
        """Genera la señal que se reproduce por el parlante.

        Args:
            sample_rate: Frecuencia de muestreo en Hz.

        Returns:
            La señal de excitación.
        """
        ...

    def deconvolve(self, recording: Signal) -> Signal:
        """Obtiene la respuesta al impulso a partir de la grabación.

        Args:
            recording: Lo grabado por el micrófono mientras sonaba la excitación.

        Returns:
            La IR, con el instante 0 en la muestra 0 y la misma longitud que la grabación.
        """
        ...


def linear_convolve(a: FloatArray, b: FloatArray) -> FloatArray:
    """Convolución lineal (no circular) por FFT, con el relleno de ceros necesario.

    Args:
        a: Primera secuencia.
        b: Segunda secuencia.

    Returns:
        La convolución completa, de longitud ``len(a) + len(b) - 1``.
    """
    n_out = len(a) + len(b) - 1
    n_fft = 1 << (n_out - 1).bit_length()
    spectrum = np.fft.rfft(a, n_fft) * np.fft.rfft(b, n_fft)
    result: FloatArray = np.fft.irfft(spectrum, n_fft)[:n_out]
    return result


@dataclass(frozen=True)
class LogSweep:
    """Barrido senoidal exponencial (ESS) de Farina con fundidos de entrada y salida.

    Attributes:
        f_start: Frecuencia inicial en Hz.
        f_stop: Frecuencia final en Hz; debe quedar bajo Nyquist.
        duration_s: Duración del barrido en segundos.
        fade_s: Duración de cada fundido (medio Hann) en segundos.
        amplitude: Amplitud pico del barrido (0 a 1).
    """

    f_start: float = 20.0
    f_stop: float = 20_000.0
    duration_s: float = 2.0
    fade_s: float = 0.01
    amplitude: float = 0.5

    def __post_init__(self) -> None:
        """Valida los parámetros.

        Raises:
            ValueError: Si las frecuencias, la duración, el fundido o la amplitud no son válidos.
        """
        if not 0 < self.f_start < self.f_stop:
            raise ValueError("se requiere 0 < f_start < f_stop")
        if self.duration_s <= 0:
            raise ValueError("la duración debe ser positiva")
        if not 0 <= 2 * self.fade_s < self.duration_s:
            raise ValueError("los fundidos deben ser no negativos y más cortos que el barrido")
        if not 0 < self.amplitude <= 1:
            raise ValueError("la amplitud debe estar en (0, 1]")

    def _rate(self) -> float:
        """Constante de tiempo ``L`` del barrido: ``f(t) = f_start · e^(t/L)``."""
        return self.duration_s / math.log(self.f_stop / self.f_start)

    def _check_sample_rate(self, sample_rate: int) -> None:
        """Comprueba que ``f_stop`` quede bajo la frecuencia de Nyquist.

        Raises:
            ValueError: Si ``f_stop >= sample_rate / 2``.
        """
        if self.f_stop >= sample_rate / 2:
            raise ValueError(
                f"f_stop={self.f_stop:g} Hz no cabe bajo Nyquist con fs={sample_rate} Hz"
            )

    def generate(self, sample_rate: int) -> Signal:
        """Genera el barrido.

        Args:
            sample_rate: Frecuencia de muestreo en Hz.

        Returns:
            El barrido, con fundidos en los extremos.

        Raises:
            ValueError: Si ``f_stop`` no cabe bajo Nyquist.
        """
        self._check_sample_rate(sample_rate)
        n = round(self.duration_s * sample_rate)
        t = np.arange(n) / sample_rate
        rate = self._rate()
        phase = 2 * np.pi * self.f_start * rate * (np.exp(t / rate) - 1)
        sweep = self.amplitude * np.sin(phase)
        n_fade = round(self.fade_s * sample_rate)
        if n_fade > 0:
            ramp = 0.5 * (1 - np.cos(np.pi * np.arange(n_fade) / n_fade))
            sweep[:n_fade] *= ramp
            sweep[n - n_fade :] *= ramp[::-1]
        return Signal(sweep, sample_rate)

    def inverse_filter(self, sample_rate: int) -> Signal:
        """Filtro inverso: barrido invertido en el tiempo con envolvente de +6 dB/octava.

        Está normalizado para que convolucionarlo con el barrido dé un pico de valor 1.

        Args:
            sample_rate: Frecuencia de muestreo en Hz.

        Returns:
            El filtro inverso, de la misma longitud que el barrido.
        """
        sweep = self.generate(sample_rate).samples
        t = np.arange(len(sweep)) / sample_rate
        inverse = sweep[::-1] * np.exp(-t / self._rate())
        inverse /= np.max(np.abs(linear_convolve(sweep, inverse)))
        return Signal(inverse, sample_rate)

    def deconvolve(self, recording: Signal) -> Signal:
        """Obtiene la IR convolucionando la grabación con el filtro inverso.

        La parte de tiempo negativo (donde Farina deja la distorsión armónica) se descarta.

        Args:
            recording: Grabación hecha a la misma frecuencia de muestreo.

        Returns:
            La IR, con el instante 0 en la muestra 0 y la misma longitud que la grabación.

        Raises:
            ValueError: Si ``f_stop`` no cabe bajo Nyquist a la frecuencia de la grabación.
        """
        inverse = self.inverse_filter(recording.sample_rate).samples
        full = linear_convolve(recording.samples, inverse)
        zero_lag = len(inverse) - 1
        return recording.with_samples(full[zero_lag : zero_lag + len(recording)])
