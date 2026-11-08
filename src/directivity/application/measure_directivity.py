"""Caso de uso: medir el patrón de directividad recorriendo un plan de ángulos."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from directivity.domain.angles import SweepPlan
from directivity.domain.pipeline import Stage
from directivity.domain.polar import OCTAVE_CENTERS, MeasurementPoint, PolarPattern
from directivity.domain.signals import ExcitationSignal
from directivity.ports import AudioIO, ResultStore, Turntable

ProgressCallback = Callable[[MeasurementPoint], None]
"""Función que recibe cada punto apenas se mide (p. ej. para mostrar el avance)."""


@dataclass
class MeasureDirectivity:
    """Orquesta mesa, audio, procesamiento y almacenamiento para medir un patrón polar.

    Solo depende de los puertos y del dominio; la CLI le entrega los adaptadores.
    ``excitation`` debe ser la misma estrategia que usa la etapa ``Deconvolve`` del
    ``pipeline``.

    Attributes:
        turntable: Mesa giratoria.
        audio: Interfaz de audio (reproduce y graba).
        store: Almacenamiento de resultados.
        excitation: Estrategia de excitación que se reproduce en cada ángulo.
        pipeline: Procesamiento de la grabación hasta la IR final.
        bands: Centros nominales de las bandas de octava del patrón.
        settle_s: Pausa tras cada giro para que el plato deje de vibrar, en segundos.
        sleep: Función de espera (se inyecta un no-op en los tests).
    """

    turntable: Turntable
    audio: AudioIO
    store: ResultStore
    excitation: ExcitationSignal
    pipeline: Stage
    bands: Sequence[float] = OCTAVE_CENTERS
    settle_s: float = 0.5
    sleep: Callable[[float], None] = field(default=time.sleep)

    def execute(self, plan: SweepPlan, on_point: ProgressCallback | None = None) -> PolarPattern:
        """Mide en cada ángulo del plan y devuelve el patrón polar.

        Toma la posición inicial como 0° (la fuente debe apuntar al micrófono). Pase lo
        que pase (error, Ctrl-C), al final la mesa vuelve a 0° y apaga las bobinas para
        no enrollar el cable. Cada punto se guarda apenas se mide.

        Args:
            plan: Ángulos a medir.
            on_point: Función opcional que recibe cada punto medido.

        Returns:
            El patrón polar con todos los puntos, ya guardado en el store.

        Raises:
            TurntableError: Si la mesa falla.
            ValueError: Si el audio o el procesamiento reciben datos inválidos.
        """
        stimulus = self.excitation.generate(self.audio.sample_rate)
        points: list[MeasurementPoint] = []
        self.turntable.home()
        try:
            for target in plan.targets():
                actual = self.turntable.goto(target)
                self.sleep(self.settle_s)
                recording = self.audio.play_and_record(stimulus)
                point = MeasurementPoint(target, actual, self.pipeline(recording))
                self.store.append(point)
                points.append(point)
                if on_point is not None:
                    on_point(point)
            pattern = PolarPattern(points, self.bands)
            self.store.save_pattern(pattern)
            return pattern
        finally:
            self._park()

    def _park(self) -> None:
        """Vuelve a 0° y apaga las bobinas; las apaga aunque el regreso falle."""
        try:
            self.turntable.goto(0.0)
        finally:
            self.turntable.release()
