"""Caso de uso: autoprueba de punta a punta de la mesa, el procesamiento y el almacenamiento.

Mide la latencia del protocolo, el tiempo por paso del motor y la exactitud del ángulo
reportado. Después hace una medición completa con ``MeasureDirectivity`` contra una
fuente de directividad conocida (normalmente simulada) y compara cada check con su umbral.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

import numpy as np

from directivity.application.measure_directivity import MeasureDirectivity
from directivity.domain.angles import STEPS_PER_REV, SweepPlan, degrees_to_steps, quantize
from directivity.domain.pipeline import Stage
from directivity.domain.polar import OCTAVE_CENTERS, PolarPattern
from directivity.domain.signals import ExcitationSignal
from directivity.ports import AudioIO, ResultStore, Turntable

Status = Literal["ok", "fail", "skip"]
"""Resultado de un check: dentro del umbral, fuera del umbral o no aplicable."""

TIMING_TARGETS: tuple[float, ...] = (5.0, 15.0, 45.0, 90.0, 180.0, 0.0, -90.0, 0.0)
"""Giros para medir el tiempo por paso (540° en total, ~18 s con la placa)."""

QUICK_TIMING_TARGETS: tuple[float, ...] = (5.0, 15.0, 45.0, 90.0, 0.0)
"""Giros de la variante rápida (180° en total, ~6 s con la placa)."""

LATENCY_SAMPLES = 100
"""Consultas ``position()`` para estimar la latencia del protocolo."""


@dataclass(frozen=True)
class Thresholds:
    """Umbrales de aceptación de la autoprueba.

    Attributes:
        latency_p95_ms: Latencia máxima (percentil 95) de una consulta sin giro, en ms.
        step_time_tolerance: Desvío relativo máximo del tiempo por paso respecto del esperado.
        angle_tolerance_deg: Diferencia máxima entre el ángulo reportado y el modelo, en
            grados. Admite 0.01° de diferencia por cómo redondea ``Serial.print`` los empates.
        pattern_error_db: Error máximo del patrón medido contra la referencia, en dB.
        min_reference_gain: Ganancia mínima de la referencia para evaluar un ángulo (evita
            los nulos, donde domina el ruido).
        band_hz: Banda de octava evaluada, en Hz.
    """

    latency_p95_ms: float = 20.0
    step_time_tolerance: float = 0.02
    angle_tolerance_deg: float = 0.011
    pattern_error_db: float = 0.5
    min_reference_gain: float = 0.05
    band_hz: float = 1000.0


@dataclass(frozen=True)
class Check:
    """Resultado de un check de la autoprueba.

    Attributes:
        name: Nombre legible del check.
        measured: Valor medido (``None`` si no aplica).
        threshold: Umbral en texto legible, p. ej. ``"< 20 ms"``.
        unit: Unidad del valor medido.
        status: ``"ok"``, ``"fail"`` o ``"skip"``.
        detail: Aclaración opcional (motivo de un ``skip``, valor esperado…).
    """

    name: str
    measured: float | None
    threshold: str
    unit: str
    status: Status
    detail: str = ""


@dataclass(frozen=True)
class SelfTestReport:
    """Resultado completo de la autoprueba.

    Attributes:
        checks: Checks en el orden en que se ejecutaron.
        metrics: Métricas detalladas (para el reporte JSON).
        thresholds: Umbrales usados.
    """

    checks: tuple[Check, ...]
    metrics: dict[str, Any]
    thresholds: Thresholds

    @property
    def passed(self) -> bool:
        """Indica si ningún check falló (los ``skip`` no cuentan como falla)."""
        return all(check.status != "fail" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        """Convierte el reporte a tipos simples para serializarlo a JSON.

        Returns:
            Diccionario con ``passed``, ``checks``, ``metrics`` y ``thresholds``.
        """
        return {
            "passed": self.passed,
            "checks": [asdict(check) for check in self.checks],
            "metrics": self.metrics,
            "thresholds": asdict(self.thresholds),
        }


def _within(value: float, limit: float) -> Status:
    """Devuelve ``"ok"`` si ``value <= limit`` y ``"fail"`` en caso contrario."""
    return "ok" if value <= limit else "fail"


@dataclass
class SelfTest:
    """Autoprueba de punta a punta: mesa → audio → procesamiento → almacenamiento.

    Attributes:
        turntable: Mesa giratoria (real o simulada).
        audio: Interfaz de audio; para evaluar el patrón, su fuente debe seguir
            ``reference_gain``.
        store: Almacenamiento de la medición.
        excitation: Estrategia de excitación.
        pipeline: Procesamiento de la grabación hasta la IR.
        reference_gain: Ganancia de amplitud esperada de la fuente según el ángulo.
        bands: Bandas de octava de la medición; debe incluir ``thresholds.band_hz``.
        settle_s: Pausa tras cada giro durante la medición, en segundos.
        thresholds: Umbrales de aceptación.
        clock: Reloj monotónico en segundos (se inyecta uno falso en los tests).
        sleep: Función de espera (se inyecta un no-op en los tests).
        progress: Recibe mensajes de avance para mostrar en pantalla.
    """

    turntable: Turntable
    audio: AudioIO
    store: ResultStore
    excitation: ExcitationSignal
    pipeline: Stage
    reference_gain: Callable[[float], float]
    bands: Sequence[float] = OCTAVE_CENTERS
    settle_s: float = 0.3
    thresholds: Thresholds = field(default_factory=Thresholds)
    clock: Callable[[], float] = field(default=time.perf_counter)
    sleep: Callable[[float], None] = field(default=time.sleep)
    progress: Callable[[str], None] = field(default=lambda _message: None)

    def __post_init__(self) -> None:
        """Valida que la banda evaluada forme parte de la medición.

        Raises:
            ValueError: Si ``thresholds.band_hz`` no está en ``bands``.
        """
        if self.thresholds.band_hz not in self.bands:
            raise ValueError(f"la banda {self.thresholds.band_hz:g} Hz no está en {self.bands}")

    def run(
        self,
        plan: SweepPlan,
        expected_step_s: float | None,
        timing_targets: Sequence[float] = TIMING_TARGETS,
    ) -> SelfTestReport:
        """Ejecuta todos los checks y devuelve el reporte.

        La mesa termina siempre en 0° con las bobinas apagadas, aunque algo falle.

        Args:
            plan: Ángulos de la medición completa.
            expected_step_s: Tiempo esperado por paso del motor, en segundos; ``None``
                marca el check como ``skip`` (p. ej. con la mesa simulada).
            timing_targets: Ángulos visitados para medir el tiempo por paso.

        Returns:
            El reporte con los checks y las métricas.

        Raises:
            TurntableError: Si la mesa falla.
        """
        self.progress("Midiendo latencia del protocolo…")
        self.turntable.home()
        try:
            latency = self._measure_latency()
            self.progress("Midiendo tiempo por paso y exactitud del ángulo…")
            stepping = self._measure_stepping(timing_targets)
        finally:
            self._park()

        self.progress(f"Medición completa: {len(plan.targets())} puntos…")
        started = self.clock()
        pattern = MeasureDirectivity(
            self.turntable,
            self.audio,
            self.store,
            self.excitation,
            self.pipeline,
            self.bands,
            settle_s=self.settle_s,
            sleep=self.sleep,
        ).execute(plan)
        duration = self.clock() - started
        pattern_error = self._pattern_error(pattern)
        final_position = self.turntable.position()

        metrics: dict[str, Any] = {
            "latency_ms": latency,
            "stepping": stepping,
            "measurement": {
                "points": len(pattern.points),
                "duration_s": duration,
                "band_hz": self.thresholds.band_hz,
                "max_error_db": pattern_error,
            },
            "final_position_deg": final_position,
        }
        checks = (
            self._latency_check(latency),
            self._step_check(stepping, expected_step_s),
            self._angle_check(stepping),
            self._pattern_check(pattern_error, len(plan.targets())),
            Check(
                "Regreso a 0° y RELEASE",
                final_position,
                "= 0.00°",
                "°",
                "ok" if final_position == 0.0 else "fail",
            ),
        )
        return SelfTestReport(checks, metrics, self.thresholds)

    # ------------------------------------------------------------ mediciones
    def _measure_latency(self) -> dict[str, float]:
        """Mide el tiempo de ida y vuelta de ``position()``, que no mueve el motor."""
        samples = []
        for _ in range(LATENCY_SAMPLES):
            started = self.clock()
            self.turntable.position()
            samples.append((self.clock() - started) * 1000.0)
        values = np.array(samples)
        return {
            "mean": float(np.mean(values)),
            "p50": float(np.percentile(values, 50)),
            "p95": float(np.percentile(values, 95)),
            "max": float(np.max(values)),
        }

    def _measure_stepping(self, targets: Sequence[float]) -> dict[str, Any]:
        """Mide la duración de cada giro y la compara con el número de pasos.

        Returns:
            Métricas del ajuste lineal duración = sobrecosto + pasos · tiempo_por_paso y la
            diferencia máxima entre el ángulo reportado y el modelo del dominio.
        """
        steps, durations, angle_errors = [], [], []
        previous = 0.0
        for target in targets:
            started = self.clock()
            reached = self.turntable.goto(target)
            durations.append(self.clock() - started)
            steps.append(abs(degrees_to_steps(target) - degrees_to_steps(previous)))
            angle_errors.append(abs(reached - round(quantize(target), 2)))
            previous = target
        slope, intercept = np.polyfit(np.array(steps, float), np.array(durations), 1)
        residuals = np.array(durations) - (slope * np.array(steps, float) + intercept)
        return {
            "moves": len(targets),
            "s_per_step": float(slope),
            "overhead_s": float(intercept),
            "max_residual_s": float(np.max(np.abs(residuals))),
            "rpm_effective": 60.0 / (slope * STEPS_PER_REV) if slope > 0 else None,
            "max_angle_error_deg": float(max(angle_errors)),
        }

    def _pattern_error(self, pattern: PolarPattern) -> float:
        """Error máximo, en dB, entre el patrón medido y la referencia en los ángulos reales."""
        band = self.thresholds.band_hz
        reference_angle = pattern.angles()[pattern.reference_index]
        reference_gain = self.reference_gain(reference_angle)
        errors = []
        for angle, level in zip(pattern.angles(), pattern.normalized(band), strict=True):
            gain = self.reference_gain(angle)
            if gain > self.thresholds.min_reference_gain:
                expected = 20.0 * math.log10(gain / reference_gain)
                errors.append(abs(level - expected))
        return max(errors) if errors else math.inf

    def _park(self) -> None:
        """Vuelve a 0° y apaga las bobinas; las apaga aunque el regreso falle."""
        try:
            self.turntable.goto(0.0)
        finally:
            self.turntable.release()

    # ------------------------------------------------------------ evaluación
    def _latency_check(self, latency: dict[str, float]) -> Check:
        limit = self.thresholds.latency_p95_ms
        return Check(
            "Latencia serial (p95)",
            latency["p95"],
            f"< {limit:g} ms",
            "ms",
            _within(latency["p95"], limit),
        )

    def _step_check(self, stepping: dict[str, Any], expected_s: float | None) -> Check:
        measured_ms = stepping["s_per_step"] * 1000.0
        tolerance = self.thresholds.step_time_tolerance
        if expected_s is None:
            return Check(
                "Tiempo por paso",
                None,
                "n/a",
                "ms",
                "skip",
                "la mesa simulada gira de forma instantánea",
            )
        expected_ms = expected_s * 1000.0
        deviation = abs(measured_ms / expected_ms - 1.0)
        return Check(
            "Tiempo por paso",
            measured_ms,
            f"{expected_ms:.3f} ±{tolerance:.0%}",
            "ms",
            _within(deviation, tolerance),
            f"desvío {deviation:+.2%}",
        )

    def _angle_check(self, stepping: dict[str, Any]) -> Check:
        limit = self.thresholds.angle_tolerance_deg
        return Check(
            "Ángulo reportado vs modelo",
            stepping["max_angle_error_deg"],
            f"≤ {limit:g}°",
            "°",
            _within(stepping["max_angle_error_deg"], limit),
        )

    def _pattern_check(self, error_db: float, points: int) -> Check:
        limit = self.thresholds.pattern_error_db
        return Check(
            f"Medición completa ({points} puntos)",
            error_db,
            f"< {limit:g} dB",
            "dB",
            _within(error_db, limit),
            f"banda {self.thresholds.band_hz:g} Hz",
        )
