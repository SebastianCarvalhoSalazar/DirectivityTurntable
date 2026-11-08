"""Tests de ``directivity.domain.pipeline``."""

from __future__ import annotations

import numpy as np
import pytest

from directivity.domain.pipeline import Deconvolve, Gate, Pipeline, Stage, default_pipeline
from directivity.domain.signals import LogSweep, Signal, linear_convolve
from directivity.domain.windows import RectangularWindow, TukeyWindow

FS = 16_000


def spy(name: str, calls: list[str]) -> Stage:
    """Etapa espía: registra su nombre y agrega una muestra para dejar huella."""

    def stage(signal: Signal) -> Signal:
        calls.append(name)
        return signal.with_samples(np.append(signal.samples, len(calls)))

    return stage


class TestPipeline:
    def test_empty_pipeline_is_identity(self) -> None:
        signal = Signal(np.arange(5.0), FS)
        assert Pipeline([])(signal) is signal

    def test_stages_run_in_order(self) -> None:
        calls: list[str] = []
        pipeline = Pipeline([spy("a", calls), spy("b", calls), spy("c", calls)])
        out = pipeline(Signal(np.zeros(1), FS))
        assert calls == ["a", "b", "c"]
        np.testing.assert_array_equal(out.samples, [0.0, 1.0, 2.0, 3.0])

    def test_stages_are_exposed_and_immutable(self) -> None:
        stages: list[Stage] = [spy("a", [])]
        pipeline = Pipeline(stages)
        stages.clear()
        assert len(pipeline.stages) == 1


class TestGate:
    def test_converts_seconds_to_samples(self) -> None:
        ir = Signal(np.ones(1600), FS)
        out = Gate(RectangularWindow(), start_s=0.001, length_s=0.005)(ir).samples
        assert not out[:16].any()
        assert out[16:96].all()
        assert not out[96:].any()

    def test_low_frequency_limit(self) -> None:
        assert Gate(length_s=0.005).low_frequency_limit_hz == pytest.approx(200.0)

    @pytest.mark.parametrize(("start", "length"), [(-0.001, 0.01), (0.0, 0.0)])
    def test_invalid(self, start: float, length: float) -> None:
        with pytest.raises(ValueError):
            Gate(start_s=start, length_s=length)


class TestDefaultPipeline:
    def test_recording_to_gated_ir_removes_reflection(self) -> None:
        sweep = LogSweep(f_start=50.0, f_stop=7000.0, duration_s=0.5)
        x = sweep.generate(FS)
        room = np.zeros(400)
        room[48], room[300] = 1.0, 0.5  # directo a 3 ms, reflexión a 18.75 ms
        recording = Signal(np.concatenate([linear_convolve(x.samples, room), np.zeros(800)]), FS)

        pipeline = default_pipeline(sweep, TukeyWindow(0.25), gate_start_s=0.0, gate_length_s=0.01)
        ir = pipeline(recording)

        assert isinstance(pipeline.stages[0], Deconvolve)
        assert isinstance(pipeline.stages[1], Gate)
        assert len(ir) == len(recording)
        assert ir.samples[48] == pytest.approx(1.0, abs=0.02)
        assert np.max(np.abs(ir.samples[160:])) == 0.0  # fuera de la compuerta de 10 ms

    def test_default_window_is_tukey(self) -> None:
        gate = default_pipeline(LogSweep()).stages[1]
        assert isinstance(gate, Gate)
        assert gate.window == TukeyWindow()
