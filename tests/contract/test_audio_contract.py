"""Corre el contrato de ``AudioIO`` contra los adaptadores disponibles."""

from __future__ import annotations

import pytest

from directivity.adapters.simulated_audio import SimulatedAudio, cardioid
from directivity.ports import AudioIO
from tests.contract.audio_contract import AudioContract


@pytest.fixture(params=["simulated-omni", "simulated-cardioid-noisy-44k"])
def audio(request: pytest.FixtureRequest) -> AudioIO:
    """Interfaz de audio bajo prueba."""
    if request.param == "simulated-omni":
        return SimulatedAudio(angle_provider=lambda: 0.0, noise_db=None)
    return SimulatedAudio(
        angle_provider=lambda: 90.0,
        directivity=cardioid,
        sample_rate=44_100,
        reflections=[(0.008, 0.3)],
        noise_db=-60.0,
    )


class TestAudioContract(AudioContract):
    """Aplica la batería de contrato a cada adaptador del fixture ``audio``."""
