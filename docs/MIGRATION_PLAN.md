# Plan de migración a arquitectura hexagonal liviana — DirectivityTurntable

> **Estado: completado (2026-11-08).** Las fases 0 a 6 están implementadas y validadas, y se agregó una Fase 7 (reescritura del firmware); el detalle de cada una está en la sección 12, *Registro de implementación*. Queda una verificación manual pendiente: correr el contrato de la mesa contra la placa (`pytest -m hardware --port …`).

## 1. Contexto

Hoy el proyecto tiene dos piezas: el firmware (`firmware/DirectivityTurntable/DirectivityTurntable.ino`) y un cliente serial de un solo archivo (`python/turntable.py`, ~100 líneas) que mezcla protocolo, transporte y un barrido de prueba por CLI. El siguiente paso del README es automatizar la medición acústica: barrido senoidal, grabación, respuesta al impulso, diagramas polares por banda de octava. Si ese código crece sobre el script actual, el procesamiento de señales queda atado al puerto serial y no se puede probar sin la placa.

El objetivo es reorganizar el código en `src/directivity/` en cuatro capas (domain, application, ports, adapters) para que:

- el dominio (ángulos, puntos de medición, patrón polar, señales) se pruebe sin hardware;
- la medición completa corra de punta a punta en simulación, en CI;
- el adaptador serial real y el simulado cumplan el mismo contrato, verificado por la misma batería de tests.

**Restricción principal:** es un proyecto de una persona. Cada abstracción tiene que justificarse con un test o con un segundo adaptador real.

## 2. Estado actual y hallazgos

| Elemento | Observación | Qué hace la migración |
|---|---|---|
| `Turntable.__init__` | Abre el puerto y hace `PING`. Si el PING falla, el puerto queda abierto. | `SerialTurntable` cierra el transporte si falla el saludo. |
| `_command` | `"OK"` sin payload termina en `float("")` y lanza `ValueError`, no `TurntableError`. | Envolver el parseo; toda falla del protocolo pasa a ser `TurntableError`. |
| Validación de rango | El límite de ±360° solo existe en el firmware. | Validar también en el cliente (dominio) para dar un error temprano y uniforme entre adaptadores. |
| Transporte | `serial.Serial` se crea dentro de la clase, así que no hay costura para tests. | Inyectar un `LineTransport` mínimo, que puede ser pyserial o el emulador del firmware. |
| `main()` | La lógica del barrido (`n_points = round(stop/step)+1`) vive en la CLI. | Se mueve a `SweepPlan` en el dominio. |
| Firmware | En AVR, `double` es un float de 32 bits. El README dice que falta probar en la placa. | El emulador replica la cuantización en `float32`. El firmware queda **fuera del alcance** de esta migración. |
| Repo | No es un repositorio git y no tiene `pyproject.toml`. El Python del sistema es 3.9.6. | La Fase 0 hace `git init`, crea el remoto en GitHub y fija Python ≥ 3.11. |

## 3. Arquitectura objetivo

### 3.1 Regla de dependencias

```
adapters  ──►  application  ──►  domain
    │               │              ▲
    └──────►  ports ┴──────────────┘
```

- `domain`: solo usa la biblioteca estándar y `numpy`. No hace I/O, no lee el reloj y no conoce `serial` ni el audio.
- `ports`: `typing.Protocol` que pueden usar tipos del dominio.
- `application`: usa domain y ports. Nunca usa adapters.
- `adapters`: implementan ports. La CLI es la **composition root**, el único lugar donde se instancian clases concretas.
- La regla se verifica con un test de arquitectura de unas 30 líneas (`tests/test_architecture.py`) que recorre los `import` con `ast`. Así no se agrega otra dependencia.

### 3.2 Estructura

```
pyproject.toml
src/directivity/
├── __init__.py
├── __main__.py                  # python -m directivity → adapters.cli.main
├── domain/
│   ├── angles.py                # quantize(), validate_angle(), SweepPlan
│   ├── signals.py               # Signal, ExcitationSignal (Strategy), LogSweep
│   ├── windows.py               # Window (Strategy), RectangularWindow, TukeyWindow
│   ├── pipeline.py              # Stage, Pipeline, Deconvolve, Gate, default_pipeline()
│   └── polar.py                 # MeasurementPoint, octave_band_levels(), PolarPattern
├── application/
│   └── measure_directivity.py   # MeasureDirectivity
├── ports.py                     # Turntable, AudioIO, ResultStore, TurntableError
└── adapters/
    ├── serial_turntable.py      # SerialTurntable + LineTransport + PySerialTransport
    ├── simulated_turntable.py
    ├── simulated_audio.py       # fuente simulada (omni/cardioide) + ruido
    ├── csv_store.py
    └── cli.py                   # argparse; subcomandos `sweep` y `measure`
tests/
├── conftest.py                  # opción --port, marcador `hardware`
├── test_architecture.py
├── support/firmware_emulator.py # réplica en Python del protocolo del .ino
├── contract/
│   ├── turntable_contract.py    # batería reutilizable
│   ├── test_turntable_contract.py
│   └── test_audio_contract.py
├── domain/…  application/…  adapters/…
.github/workflows/ci.yml
```

`ports` es un solo módulo porque son tres Protocols. Se separa en paquete solo si crece.

### 3.3 Contratos clave (borrador)

```python
# ports.py
class TurntableError(RuntimeError): ...

class Turntable(Protocol):
    def home(self) -> float: ...
    def goto(self, degrees: float) -> float: ...   # devuelve el ángulo REAL
    def position(self) -> float: ...
    def release(self) -> None: ...
    def close(self) -> None: ...

class AudioIO(Protocol):
    @property
    def sample_rate(self) -> int: ...
    def play_and_record(self, excitation: Signal) -> Signal: ...

class ResultStore(Protocol):
    def append(self, point: MeasurementPoint) -> None: ...   # incremental: si se corta la medición, no se pierde lo medido
    def save_pattern(self, pattern: PolarPattern) -> None: ...
```

```python
# domain
@dataclass(frozen=True)
class Signal:                       # sirve para la grabación y para la IR
    samples: npt.NDArray[np.float64]
    sample_rate: int

class ExcitationSignal(Protocol):  # Strategy
    def generate(self, sample_rate: int) -> Signal: ...
    def deconvolve(self, recording: Signal) -> Signal: ...   # recording → IR

class Window(Protocol):            # Strategy
    def apply(self, ir: Signal, start: int, length: int) -> Signal: ...

Stage = Callable[[Signal], Signal]
class Pipeline:                    # Pipeline: composición ordenada de Stage
    def __init__(self, stages: Sequence[Stage]) -> None: ...
    def __call__(self, signal: Signal) -> Signal: ...
```

**Decisiones:**

- Las **Strategies viven en el dominio, no en ports.** Son algoritmos puros sin I/O. Un port se reserva para lo que cruza la frontera del sistema: motor, audio y disco.
- **Pipeline homogéneo** `Signal → Signal`. `Deconvolve(excitation)` es una etapa más, y le siguen `Gate(window, …)` y otras. El análisis por bandas (`octave_band_levels`) es una función terminal aparte. Se evita un `Pipeline[In, Out]` genérico.
- `Turntable` **no expone `move()`**: el caso de uso no lo necesita (ISP). `SerialTurntable` puede seguir ofreciéndolo como método extra.
- `SimulatedAudio` recibe `angle_provider: Callable[[], float]`. La CLI le pasa `table.position`, y así el audio simulado "sabe" hacia dónde apunta la fuente sin acoplarse a la mesa.
- El caso de uso recibe `sleep: Callable[[float], None] = time.sleep` para el tiempo de asentamiento mecánico. En los tests se inyecta un no-op.
- `TurntableError` está en `ports.py` porque forma parte del contrato: ambos adaptadores lanzan la misma excepción.

## 4. Convenciones transversales

- **Python ≥ 3.11.** 3.9 y 3.10 ya terminaron su ciclo de vida. Hay que crear un venv con un Python 3.11+ (uv, brew o python.org), no el del sistema.
- **Type hints completos:** `mypy --strict` sobre `src/`. En `tests/` basta con anotar los fixtures.
- **Docstrings estilo Google** en todos los módulos, clases y funciones públicas. Los encabezados `Args:`, `Returns:` y `Raises:` van en inglés porque las herramientas los reconocen; el texto, en español como el código actual. Los identificadores siguen en inglés. Lo hace cumplir `ruff` con `select = ["D", …]` y `convention = "google"`.
- **Herramientas:** `ruff` (lint y formato), `mypy`, `pytest` y `pytest-cov`. Todo va en `[project.optional-dependencies].dev`.
- **Dependencias de ejecución:** `numpy` y `pyserial`. No se usan scipy ni matplotlib en esta migración: la ventana de Tukey y la FFT por bandas se escriben con numpy.
- **Entrada de consola:** `directivity = "directivity.adapters.cli:main"` en `[project.scripts]`.

## 5. Lo que deliberadamente NO se hace

Contenedor de inyección de dependencias, clases base abstractas (se usa `Protocol` estructural), bus de eventos, async, plugins, archivos de configuración (bastan los flags de argparse), capa de DTOs y mappers, un `Pipeline` genérico tipado, mocks escritos a mano cuando ya existe un adaptador simulado, y cambios al firmware. *(El firmware se reescribió después, a pedido, en la Fase 7.)*

## 6. Fases

Cada fase deja `main` en verde: ruff, mypy y pytest pasan en CI. Además se puede probar por sí sola, con un comando.

### Fase 0: Repositorio, empaquetado y CI

**Objetivo:** tener el esqueleto instalable y la CI corriendo antes de mover lógica.

- **Archivos:** `pyproject.toml`, `src/directivity/{__init__,__main__}.py`, paquetes vacíos de cada capa, `tests/test_architecture.py`, `tests/conftest.py`, `.github/workflows/ci.yml` y `.gitignore` actualizado (`.mypy_cache/`, `.pytest_cache/`, `.coverage`, `*.egg-info/`).
- **Pasos:** `git init`, un commit base con el estado actual (antes de cambiar nada) y la creación del remoto en GitHub.
- **Criterios de aceptación:**
  - `pip install -e ".[dev]"` funciona en un venv con 3.11+.
  - `ruff check`, `ruff format --check`, `mypy` y `pytest` pasan localmente.
  - El test de arquitectura falla si se agrega a propósito `import serial` en `domain/`. Se prueba y se revierte.
  - El primer push deja la CI en verde.
- **Riesgos:** Python 3.9 del sistema, que se mitiga documentando el venv en el README. Repositorio de GitHub sin crear, que es un paso manual del usuario.

### Fase 1: Puerto `Turntable`, adaptadores y tests de contrato

**Objetivo:** reemplazar `python/turntable.py` por `SerialTurntable` y `SimulatedTurntable`, ambos verificados por la misma batería.

- **Archivos:** `domain/angles.py`, `ports.py` (solo `Turntable` y `TurntableError`), `adapters/serial_turntable.py`, `adapters/simulated_turntable.py`, `adapters/cli.py` (subcomando `sweep`, equivalente al `main()` actual, con `--simulate`), `tests/support/firmware_emulator.py`, `tests/contract/*` y `tests/domain/test_angles.py`.
- **Contenido:**
  - **Dominio.** `quantize(deg, steps_per_rev=2048) -> float` aplica el redondeo contra la posición absoluta. `validate_angle()` controla el límite de ±360°. `SweepPlan(start, stop, step).targets()` reemplaza el cálculo de `n_points`.
  - **`SerialTurntable`.** Porta `_command` con las correcciones de la sección 2. Recibe un `LineTransport` (Protocol interno del adaptador con `write_line`, `read_line` y `close`) y tiene una `classmethod open(port, baudrate, timeout)` que crea un `PySerialTransport`. Conserva el `sleep(0.5)` y el `reset_input_buffer` del saludo inicial.
  - **`SimulatedTurntable`.** Usa `quantize` del dominio, aplica el mismo límite y lanza `TurntableError`.
  - **`FirmwareEmulator`.** Implementa `LineTransport` replicando el `.ino` línea por línea: mayúsculas, `strtod`, `lround` en `float32`, formato de 2 decimales, `ERR` y línea demasiado larga.
- **Tests de contrato** (`turntable_contract.py`, una clase base parametrizada por fixture):
  1. Después de `home()`, `position() == 0`.
  2. `goto(x)` devuelve un valor a menos de medio paso (más 0.005 por el redondeo a 2 decimales) de `x`, e igual a `position()`.
  3. 72 × `goto(5·i)` terminan en exactamente `360.00`: el error no se acumula, y queda probada la afirmación del README.
  4. `goto(±361)` lanza `TurntableError` y no cambia la posición.
  5. `release()` seguido de `goto()` sigue funcionando y conserva la posición.
  6. La batería siempre termina con `goto(0)` y `release()`, por seguridad del cable.
  - Parámetros del fixture: `simulated` (siempre), `serial-emulated` (`SerialTurntable` + `FirmwareEmulator`, siempre) y `serial-hardware` (`SerialTurntable.open(--port)`, marcado `hardware`).
  - `addopts = "-m 'not hardware'"`. Si se pide `-m hardware` sin `--port`, el test se marca como *skip* con un mensaje claro.
- **Tests de adaptador** (`tests/adapters/test_serial_turntable.py`): `ERR` pasa a `TurntableError`, una respuesta vacía se trata como timeout, una respuesta inesperada lanza error, un PING fallido cierra el transporte y `"OK"` sin payload da `TurntableError`.
- **Criterios de aceptación:**
  - El contrato pasa con `simulated` y `serial-emulated` en CI.
  - `directivity sweep --simulate --step 5` imprime 73 líneas de puntos (más el mensaje final) y la última muestra `real 360.00°`.
  - Con la placa conectada, `pytest -m hardware --port /dev/tty.usbmodemXXXX` pasa. Esta verificación es manual y queda registrada en el README.
- **Riesgos:**
  - **El emulador se aparta del firmware.** Se mitiga corriendo el contrato contra el hardware y comentando en el emulador las líneas del `.ino` que replica.
  - **El test de hardware mueve el plato hasta 360°**, unos 12 s a 5 RPM. Se mitiga quedándose dentro de ±360°, cerrando siempre en `goto(0)` y usando un timeout de 30 s.
  - **Arranque del Leonardo** por USB nativo y `while(!Serial)`. Se mitiga conservando el retardo inicial.

### Fase 2: Señales — Strategy de excitación y de ventana

**Objetivo:** tener la parte de procesamiento puro, probada con señales sintéticas.

- **Archivos:** `domain/signals.py` (`Signal`, `ExcitationSignal`, `LogSweep` con barrido exponencial de Farina, *fade-in/out* e inverso), `domain/windows.py` (`RectangularWindow`, `TukeyWindow(alpha)`) y sus tests.
- **Criterios de aceptación:**
  - Se convoluciona el `LogSweep` con una IR conocida (retardo de N muestras y ganancia g). `deconvolve()` debe recuperar el pico en N ± 1 muestra y g ± 0.1 dB.
  - `TukeyWindow(alpha=0)` equivale a la rectangular y `alpha=1` a la de Hann.
  - Las ventanas conservan la longitud y el `sample_rate`.
  - Cambiar de ventana o de excitación no modifica ningún otro módulo: así se demuestra OCP.
- **Riesgos:** problemas numéricos, como la convolución circular, el ruido fuera de banda o el hecho de que `inv` no sea exacto en los bordes. Se mitigan con tolerancias explícitas y deconvolución lineal (con *padding*), no circular.

### Fase 3: Pipeline de procesamiento y patrón polar

**Objetivo:** pasar de la grabación al nivel por banda y de ahí al patrón polar.

- **Archivos:** `domain/pipeline.py` (`Pipeline`, `Deconvolve`, `Gate(window, start_s, length_s)`, `default_pipeline(excitation, window)`), `domain/polar.py` (`MeasurementPoint(target_deg, actual_deg, ir)`, `octave_band_levels(ir, centers)`, `PolarPattern` con `angles()`, `levels(band)` y `normalized(band)` relativo a 0°) y sus tests.
- **Criterios de aceptación:**
  - `Pipeline([])` es la identidad, y las etapas corren en orden (se verifica con etapas espía).
  - Con ruido rosa sintético, `octave_band_levels` da la misma energía en todas las octavas (rango < 0.5 dB). Con ruido blanco, la energía es proporcional al número de bins, es decir +3.01 dB por octava (±0.1 dB). Un tono de 1 kHz cae en la banda de 1 kHz. *(Criterio corregido en la Fase 3: la versión original atribuía al ruido rosa el comportamiento del blanco.)*
  - Un `PolarPattern` con ganancias de cardioide da −6.02 dB a 90° (±0.05 dB). La normalización usa el **ángulo real**, no el pedido.
- **Riesgos:**
  - **Compuerta inconsistente entre ángulos.** Se mitiga fijando el inicio de `Gate` con un retardo común (distancia constante), no con el pico de cada IR.
  - **El borde suavizado de la ventana atenúa el sonido directo.** Si la compuerta abre a menos de `alpha·(length−1)/2` muestras del directo, este cae en el borde de subida. `Gate` debe abrir con un margen previo mayor que ese borde. *(Agregado tras la Fase 2.)*
  - **Bandas bajas con poca resolución** por una compuerta corta. Se documenta el límite de baja frecuencia ≈ 1/longitud_de_compuerta.

### Fase 4: Puertos `AudioIO` y `ResultStore` con sus adaptadores

- **Archivos:** `ports.py` (agrega `AudioIO` y `ResultStore`), `adapters/simulated_audio.py`, `adapters/csv_store.py`, `tests/contract/test_audio_contract.py` y `tests/adapters/test_csv_store.py`.
- **`SimulatedAudio(sample_rate, directivity: Callable[[float], float], delay_s, noise_db, angle_provider, seed)`:** convoluciona la excitación con una IR sintética, con la ganancia dada por `directivity(angle)`, y agrega ruido reproducible. Trae los modelos `omni` y `cardioid`.
- **`CsvResultStore(directory)`:**
  - `measurements.csv`: índice, ángulo pedido, ángulo real, nivel por banda y archivo de la IR.
  - `ir_000.npy`, …: IRs tal como salen del pipeline (deconvolucionadas y compuertadas), en float32. *(Corregido en la Fase 4: el plan decía "crudas", pero `MeasurementPoint.ir` es la IR procesada.)*
  - `pattern.csv`: matriz banda × ángulo normalizada.
  - El nombre de la sesión, con su timestamp, lo decide la CLI, no el store. Así el store queda determinista. Por defecto se escribe en `data/raw/<timestamp>/`, que ya está en `.gitignore`.
- **Contrato de `AudioIO`:** escrito ahora, aunque solo corre contra `SimulatedAudio`. Verifica que `sample_rate > 0`, que la salida tenga el mismo `sample_rate`, que la longitud sea ≥ la de la entrada y que todo sea finito. Queda listo para un futuro adaptador real.
- **Criterios de aceptación:**
  - El contrato de audio pasa.
  - El CSV se puede leer de vuelta: `csv.DictReader` y `np.load` reconstruyen los mismos valores.
  - `append` va escribiendo de a una fila: si se interrumpe después de k puntos, quedan k filas.
- **Riesgos:** el tamaño de las IRs en disco. Se mitiga guardando `.npy` en float32, no CSV.

### Fase 5: Caso de uso `MeasureDirectivity` y CLI `measure`

- **Archivos:** `application/measure_directivity.py`, `adapters/cli.py` (subcomando `measure`), `tests/application/test_measure_directivity.py` y `tests/adapters/test_cli.py`.
- **Flujo:**
  1. `home()`.
  2. Para cada objetivo de `SweepPlan`: `actual = goto()`, `sleep(settle_s)`, `play_and_record()`, `pipeline()`, `MeasurementPoint`, `store.append()` y el callback de progreso.
  3. Se arma el `PolarPattern` y se llama a `save_pattern()`.
  4. En un **`finally`**: `goto(0)` y `release()`. Esto corre aunque falle el audio o el usuario interrumpa con Ctrl-C.
- **CLI:**
  - `directivity measure --simulate --source cardioid --step 10 --out data/raw`.
  - `directivity measure --port /dev/tty.usbmodemXXXX …` (sin audio real, por ahora termina con error claro).
  - La CLI es la única que instancia adaptadores. Usa `contextlib.ExitStack` para cerrarlos.
- **Criterios de aceptación:**
  - En una integración totalmente simulada (`SimulatedTurntable`, `SimulatedAudio` cardioide y `CsvResultStore(tmp_path)`), el patrón a 1 kHz coincide con el cardioide teórico a ±0.5 dB entre 0° y 150°.
  - Si `AudioIO` lanza una excepción en el punto 3, la mesa termina en 0° y liberada, y el store tiene 2 filas.
  - `directivity measure --simulate` crea la carpeta de la sesión con los tres tipos de archivo.
  - La cobertura de `domain` y `application` es ≥ 90 %.
- **Riesgos:** pruebas lentas por el `sleep`. Se mitiga con el `sleep` inyectado y señales cortas en los tests (0.2 s a 16 kHz).

### Fase 6: Retiro del código heredado y documentación

- **Archivos:**
  - Se eliminan `python/turntable.py` y `python/requirements.txt`.
  - Se actualiza `README.md`: instalación con `pip install -e .`, comandos `directivity sweep` y `directivity measure`, nueva estructura y cómo correr los tests de hardware.
  - Se marca este plan como completado.
- **Criterios de aceptación:**
  - `grep -r "python/turntable" . --exclude-dir=.git --exclude-dir=.venv --exclude=MIGRATION_PLAN.md` no devuelve nada. *(Corregido en la Fase 6: este plan y su registro mencionan el archivo como historia, y el historial de git y el venv no cuentan.)*
  - Los ejemplos del README se ejecutan tal cual en modo `--simulate`.
  - La CI está en verde.
- **Riesgo:** perder el uso "como librería" del README. Se mitiga documentando `SerialTurntable.open(...)` como reemplazo directo.

### Fase 7 (agregada): Reescritura del firmware

**Objetivo:** reescribir `DirectivityTurntable.ino` con buenas prácticas y documentación en cada función, corrigiendo los errores encontrados, y demostrar que en todo lo demás se comporta igual que la versión 1.

- **Archivos:** `firmware/DirectivityTurntable/DirectivityTurntable.ino`, `tests/firmware/host/` (sustituto de `Arduino.h` y `Stepper.h`, y `host_main.cpp`), `tests/firmware/test_firmware_host.py`, `tests/support/firmware_emulator.py` y la CI (`--warnings all`).
- **Criterios de aceptación:**
  - Compila sin advertencias, en la Mac con `-Wall -Wextra -Wpedantic -Werror` y para Leonardo en la CI.
  - **Prueba diferencial contra la versión 1** (ambas compiladas en la Mac con el mismo entorno de pruebas): trazas idénticas en todas las posiciones y empates de medio paso, y en miles de órdenes aleatorias. Las únicas diferencias son las correcciones.
  - Test permanente firmware ↔ emulador en CI.
  - El uso de flash y de RAM no empeora de forma significativa.
- **Riesgos:**
  - El entorno de la Mac no es el AVR. Se mitiga imitando `float32` en los literales, `Print::printFloat` y la gramática de `strtod` de avr-libc. La prueba en la placa sigue pendiente.

## 7. Estrategia de tests

| Nivel | Qué | Dónde corre |
|---|---|---|
| Unitario de dominio | Ángulos, señales, ventanas, pipeline, polar | CI |
| Contrato | `Turntable` (simulado, serial + emulador, serial + hardware) y `AudioIO` (simulado) | CI, salvo el de hardware, que es manual con `-m hardware --port` |
| Adaptador | Errores del protocolo serial, ida y vuelta del CSV | CI |
| Integración | `MeasureDirectivity` totalmente simulado y la CLI con `--simulate` | CI |
| Arquitectura | Regla de dependencias entre capas | CI |

Los tests de contrato son la verificación concreta de **LSP**: cualquier adaptador nuevo se agrega como un parámetro más del fixture.

## 8. CI (`.github/workflows/ci.yml`)

- **Disparadores:** `push` y `pull_request`.
- **Job `python`:** `ubuntu-latest` con matriz de Python 3.11, 3.12 y 3.13; `actions/setup-python` con caché de pip; `pip install -e ".[dev]"`; luego `ruff check`, `ruff format --check`, `mypy` y `pytest --cov=directivity --cov-report=term-missing`. Los tests `hardware` se excluyen por `addopts`.
- **Job `firmware`:** `arduino/setup-arduino-cli` y luego `arduino-cli compile --fqbn arduino:avr:leonardo` (con la librería `Stepper`). Detecta si el `.ino` deja de compilar. *(Corregido en la Fase 0: `arduino/compile-sketches` falla en Ubuntu 26.04; ver el registro.)*

## 9. SOLID y patrones: dónde se aplican

| Principio o patrón | Aplicación concreta |
|---|---|
| S | `SerialTurntable` habla el protocolo y `LineTransport` mueve bytes. `MeasureDirectivity` orquesta. `CsvResultStore` persiste. |
| O | Nueva excitación (MLS), ventana, etapa o store sin tocar el caso de uso. |
| L | Tests de contrato compartidos entre el adaptador real y el simulado. |
| I | `Turntable` sin `move()`; `AudioIO` con un solo método. |
| D | El caso de uso depende de Protocols; la CLI conecta las clases concretas. |
| Strategy | `ExcitationSignal` (`LogSweep`) y `Window` (`Rectangular`, `Tukey`). |
| Pipeline | `Pipeline([Deconvolve(exc), Gate(window, …)])` |

## 10. Verificación de punta a punta (al terminar la Fase 5)

```bash
python3.13 -m venv .venv   # cualquier Python ≥ 3.11 (corregido en la Fase 6: decía 3.12) && source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest --cov=directivity
directivity sweep --simulate --step 5                      # 73 líneas, termina en 360.00°
directivity measure --simulate --source cardioid --step 10 --out data/raw
pytest -m hardware --port /dev/tty.usbmodemXXXX            # con la placa conectada
```

## 11. Fuera de alcance (siguientes pasos)

Adaptador de audio real (`SoundDeviceAudio`, que se conecta al contrato de `AudioIO` ya escrito), gráficos polares y mapa de directividad (matplotlib), índice de directividad (DI) y validación con una fuente conocida, y calibración de `STEPS_PER_REV` en la placa.

## 12. Registro de implementación

### Fase 0: Repositorio, empaquetado y CI (2026-11-08)

**Qué se hizo**
- `git init`, un commit base (`5a35d7e`) y el repositorio público en GitHub: <https://github.com/SebastianCarvalhoSalazar/DirectivityTurntable>.
- Python 3.13.18 instalado con Homebrew y un entorno virtual en `.venv/`.
- `pyproject.toml`:
  - Construcción con hatchling.
  - Dependencias de ejecución: `numpy` y `pyserial`. Dependencias de desarrollo (`dev`): pytest, pytest-cov, ruff, mypy y `types-pyserial`.
  - Comando `directivity` registrado.
  - Configuración de ruff (con reglas `D` en convención Google), de mypy en modo estricto y de pytest (marcador `hardware`, excluido por defecto).
- Esqueleto de `src/directivity/` (`domain/`, `application/`, `ports.py`, `adapters/`), `__main__.py` y una CLI mínima (`--version`).
- `tests/test_architecture.py`, `tests/conftest.py` (opción `--port` y fixture `hardware_port`) y `tests/adapters/test_cli.py`.
- `.github/workflows/ci.yml`:
  - Job `python` con matriz 3.11, 3.12 y 3.13 que corre ruff, mypy y pytest con cobertura.
  - Job `firmware` que compila el `.ino` para Leonardo.
- README: sección "Desarrollo" que explica el entorno virtual (era la mitigación del riesgo de Python 3.9).

**Validación**
- Comandos: `pip install -e ".[dev]"`, `ruff check .`, `ruff format --check .`, `mypy` (12 archivos, sin errores) y `pytest` (9 pasan). `directivity --version` y `python -m directivity --version` funcionan.
- Se agregó `import serial` en `domain/` y el test de arquitectura falló con `domain/_probe.py (domain) importa serial`. Después se revirtió.

**Desviaciones y decisiones**
- **CLI mínima desde la Fase 0.** El plan solo pedía paquetes vacíos, pero `__main__.py` y `[project.scripts]` apuntan a `adapters.cli:main`. Sin ese módulo, mypy y la instalación fallarían.
- **El código heredado queda fuera de ruff.** `python/` y `docs/` se excluyen con `extend-exclude`: `python/turntable.py` no cumple las reglas `D` y se elimina en la Fase 6; ruff, además, intentaba formatear los bloques de código del Markdown.
- **El test de arquitectura se prueba a sí mismo.** Además del ensayo manual, `test_checker_detects_violations` verifica de forma permanente que el verificador detecta 5 tipos de violación, incluidas las importaciones relativas.
- **Clasificación de módulos.** El test de arquitectura usa `sys.stdlib_module_names` para distinguir la biblioteca estándar de las dependencias externas. Esa API existe desde Python 3.10, y el proyecto exige 3.11 o superior.
- **Docstrings en los tests.** Ahí se relajan las reglas de docstring obligatorio (`D100`–`D106`): los nombres de las funciones `test_*` ya describen qué prueban. Las funciones auxiliares y los fixtures sí llevan docstring.
- **CI de firmware.** `arduino/compile-sketches@v1` falló en el runner (`ubuntu-latest` ya es Ubuntu 26.04): instala internamente Python 3.12.4, que no está disponible para esa versión. Se reemplazó por `arduino-cli` directo y se corrigió la sección 8. Además se subió a `actions/checkout@v5` y `actions/setup-python@v6`, que usan Node 24 en vez de Node 20, ya obsoleto.

**Resultado en CI**
- Run 42524203541 en verde: `python` pasa en 3.11, 3.12 y 3.13, y `firmware` compila.
- **Hallazgo:** el firmware compila sin errores para `arduino:avr:leonardo`. Sigue pendiente probarlo en la placa.
- Queda solo un aviso: `arduino/setup-arduino-cli@v2` todavía usa Node 20. No bloquea.

### Fase 1: Puerto `Turntable`, adaptadores y tests de contrato (2026-11-08)

**Qué se hizo**
- `domain/angles.py`: `validate_angle` (además rechaza valores no finitos), `degrees_to_steps` y `steps_to_degrees` con redondeo de empates alejándose de cero, como `lround`, `quantize`, `half_step_deg` y `SweepPlan`.
- `ports.py`: `Turntable` y `TurntableError`.
- `adapters/serial_turntable.py`:
  - `LineTransport` (Protocol interno del adaptador).
  - `PySerialTransport`: usa `serial_for_url`, así que acepta `loop://` en los tests, y traduce `OSError` y `SerialException` a `TurntableError`.
  - `SerialTurntable`: verifica `PING` y cierra el transporte si falla; acepta `OK` y `ERR` solo como prefijos exactos; un ángulo no numérico da `TurntableError`; valida el ángulo antes de enviarlo; conserva `move()` como método extra.
- `adapters/simulated_turntable.py`: usa la aritmética del dominio y expone `is_released` e `is_closed` para los tests.
- `adapters/cli.py`:
  - Subcomando `sweep --port | --simulate` con `--start`, `--stop`, `--step` y `--dwell` (por defecto 1 s con la placa y 0 s simulado).
  - `run_sweep` vuelve a 0° y libera las bobinas en un `finally`. Los errores se informan por stderr con código de salida 1.
- `tests/support/firmware_emulator.py`: replica el `.ino` con `np.float32`, incluido `Print::printFloat` del core de Arduino.
- Tests:
  - Contrato de 16 casos (`tests/contract/`) corrido contra `simulated` y `serial-emulated`, más `serial-hardware` con el marcador `hardware`.
  - `tests/domain/test_angles.py`, `tests/adapters/test_serial_turntable.py`, `tests/support/test_firmware_emulator.py` y tests de `sweep` en `tests/adapters/test_cli.py`.
- README: cómo correr el contrato contra la placa, con una tabla para registrar las ejecuciones.

**Validación**
- `ruff check`, `ruff format --check` y `mypy --strict` (24 archivos) pasan sin errores.
- `pytest`: 115 tests pasan y 16 de hardware quedan excluidos. Cobertura total del 98 %; `angles.py`, `serial_turntable.py` y `cli.py` al 100 %.
- Contrato: 16/16 con `simulated` y 16/16 con `serial-emulated`.
- `directivity sweep --simulate --step 5` imprime 73 puntos (`0.00 → 0.00`, `5.00 → 4.92`, `10.00 → 10.02` … `360.00 → 360.00`) y el mensaje final.
- `pytest -m hardware` sin `--port` da 16 tests saltados con el mensaje "indica la placa con --port <puerto>".
- **Pendiente (manual):** correr `pytest -m hardware --port …` con la placa. No hay ninguna conectada (`/dev/tty.usbmodem*` no existe). Queda anotado en el README. No bloquea porque el plan lo define como verificación manual.

**Hallazgos**
- **Error del cliente heredado: `SweepPlan` podía pasarse del límite.** `round(stop/step)` podía superar `stop`: con `--step 11` daba 33 pasos y llegaba a 363°, que el firmware rechaza con `ERR`. `SweepPlan` usa `floor` con tolerancia y lo cubre un test.
- **Errores de pyserial fuera del contrato.** Un puerto inexistente o un USB desconectado lanzaban `serial.SerialException`, no `TurntableError`. Ahora se traducen en el transporte.
- **El emulador confirma la afirmación del README** en aritmética `float32`, como en AVR: 72 giros de 5° suman exactamente 2048 pasos (360.00°).
- **Divergencia conocida del emulador.** `strtod` de avr-libc acepta `INF` y `NAN`; el emulador responde `ERR angulo invalido`. No importa en la práctica porque el cliente nunca los envía.
- **Línea vacía.** El firmware no responde nada; para el cliente se ve como un timeout. Lo cubre un test del emulador.

**Desviaciones y decisiones**
- **`AngleOutOfRangeError(ValueError)` en el dominio.** El dominio no puede importar `ports`, y los adaptadores la traducen a `TurntableError`.
- **El punto 6 del contrato es el teardown del fixture `table`**, no un test: `goto(0)`, `release()` y `close()`.
- **Doble de pruebas guionado.** Se usa `ScriptedTransport` porque el emulador solo produce respuestas válidas. La sección 5 lo permite: no existe un adaptador simulado capaz de generar respuestas malformadas.
- **Criterio del barrido aclarado en el plan:** son 73 líneas de puntos más un mensaje final.

### Fase 2: Señales — Strategy de excitación y de ventana (2026-11-08)

**Qué se hizo**
- `domain/signals.py`:
  - `Signal`: dataclass inmutable con `eq=False`. Copia las muestras a `float64`, las deja de solo lectura y valida que sean 1-D y que `sample_rate` sea mayor que 0. Expone `len`, `duration_s` y `with_samples()`.
  - `ExcitationSignal`: Protocol (Strategy).
  - `linear_convolve`: convolución lineal por FFT, con relleno a potencia de 2 y sin solapamiento circular.
  - `LogSweep`: barrido exponencial de Farina con fundidos de medio Hann y validación de parámetros y de Nyquist. Su `inverse_filter` es el barrido invertido con envolvente `e^(−t/L)` (+6 dB/octava), normalizado para que `deconvolve(generate())` dé pico 1. En `deconvolve` se descarta el tiempo negativo y la salida tiene la misma longitud que la grabación.
- `domain/windows.py`:
  - `Window`: Protocol (Strategy).
  - `RectangularWindow` y `TukeyWindow(alpha)`, con la misma definición que `scipy.signal.windows.tukey` pero en numpy.
  - Función común `_gate`: conserva la longitud, anula lo que queda fuera de la compuerta y rechaza compuertas que no caben en la señal.
- Tests:
  - `tests/domain/test_signals.py`, que incluye la clase de propiedades `TestExcitationStrategyProperties`, corrida con `LogSweep` y con `DiracExcitation`, esta última definida solo en el test.
  - `tests/domain/test_windows.py`, que incluye `TestWindowStrategyProperties`, corrida con cuatro ventanas.

**Validación**
- `ruff check`, `ruff format --check` y `mypy --strict` (28 archivos) pasan sin errores.
- `pytest`: 185 tests pasan y 16 de hardware quedan excluidos. Cobertura del 98 %; `signals.py` y `windows.py` al 100 %.
- **Criterios de aceptación:**
  - Retardo y ganancia `(N, g)` ∈ {(0, 1), (480, 0.5), (1234, −0.1), (7, 2)}: el pico se recupera en N exacto (dentro de ±1), con g a ±0.1 dB y con el signo correcto. También con ruido a −40 dB.
  - `TukeyWindow(0)` es idéntica a la rectangular, y `TukeyWindow(1)` coincide con `np.hanning` (tolerancia 1e-12) para N ∈ {2, 11, 400, 401}.
  - Todas las ventanas conservan la longitud y el `sample_rate`.
  - OCP: `git status` muestra solo archivos nuevos (`signals.py`, `windows.py` y sus tests); ningún módulo existente cambió. Una excitación nueva (`DiracExcitation`) se definió en el test y pasa las mismas propiedades que `LogSweep`.

**Hallazgos**
- **Distorsión armónica.** Con un segundo armónico fuerte (`x + 0.2·x²`), la parte causal de la IR queda por debajo de −40 dB a partir de los 5 ms. La deconvolución de Farina efectivamente empuja la distorsión a tiempo negativo.
- **El borde de la Tukey simétrica atenúa el directo.** Una compuerta de 600 muestras con `alpha=0.25` que abre 50 muestras antes del directo lo deja en 0.75 (−2.5 dB). Hay un test que lo documenta y se agregó el riesgo a la Fase 3: `Gate` debe abrir con un margen mayor que el borde.
- **Instante cero de la IR.** El barrido invertido desplaza el instante 0 a la muestra `len(barrido) − 1` de la convolución completa. Lo verifica `test_self_deconvolution_is_unit_impulse_at_zero`.

**Desviaciones y decisiones**
- **Normalización por pico.** Se eligió que el inverso dé pico 1 contra el propio barrido, en vez de ganancia unitaria en la banda pasante. Así una IR `g·δ[n−N]` se recupera con pico `g` exacto, que es lo que pide el criterio. Para el patrón polar la constante da igual, porque se normaliza contra 0°.
- **Más tests que los pedidos:** separación de directo y reflexión, robustez al ruido, frecuencia instantánea y distorsión.
- **Ventana asimétrica descartada.** Se mantuvo la Tukey simétrica del plan en vez de agregar una "media ventana" (plana al inicio). Con un margen previo adecuado, la simétrica basta, y una ventana más sería una abstracción sin necesidad demostrada.

### Fase 3: Pipeline de procesamiento y patrón polar (2026-11-08)

**Qué se hizo**
- `domain/pipeline.py`:
  - `Stage = Callable[[Signal], Signal]`.
  - `Pipeline(stages)`: guarda una tupla inmutable, la expone en `stages` y una lista vacía equivale a la identidad.
  - `Deconvolve(excitation)`.
  - `Gate(window, start_s, length_s)`: valida los tiempos y expone `low_frequency_limit_hz = 1/length_s`. Su docstring advierte sobre el retardo común y el margen respecto del borde de la ventana.
  - `default_pipeline(excitation, window=None, gate_start_s=0.0, gate_length_s=0.010)`.
- `domain/polar.py`:
  - `exact_center` y `band_edges` (octavas en base 2: `fc/√2` a `fc·√2`).
  - `octave_band_levels(ir, centers)`: energía FFT por banda en dB; `-inf` si la banda está en silencio; `ValueError` si la banda supera Nyquist o no contiene bins.
  - `MeasurementPoint(target_deg, actual_deg, ir)`.
  - `PolarPattern(points, centers)`: calcula los niveles al construirse y expone `angles()` (reales), `levels(band)`, `normalized(band)`, `bands`, `points` y `reference_index`. La referencia es el punto cuyo ángulo **real**, llevado a (−180°, 180°], queda más cerca de 0°.
- Tests: `tests/domain/test_pipeline.py` y `tests/domain/test_polar.py`.

**Validación**
- `ruff check`, `ruff format --check` y `mypy --strict` (32 archivos) pasan sin errores.
- `pytest`: 217 tests pasan y 16 de hardware quedan excluidos. Cobertura del 99 %; todo `domain/` al 100 %.
- **Criterios de aceptación:**
  - `Pipeline([])` devuelve la misma señal (`is`). Tres etapas espía corren en orden `a, b, c` y cada una deja su huella.
  - Ruido rosa determinista (magnitud exacta `1/√k`, fases aleatorias): rango de **0.039 dB** entre 63 Hz y 16 kHz.
  - Ruido blanco: pasos de **2.99 a 3.05 dB** por octava.
  - Un tono de 1 kHz cae en la banda de 1 kHz, más de 60 dB por encima de las demás.
  - Cardioide: **−6.0206 dB** a 90° en todas las bandas, y −2.50 dB a 60°.
  - El patrón usa el ángulo real en `angles()` y en la elección de la referencia. Hay tests con pedido ≠ real, con barridos que empiezan en −90° y con 360° y ±350° equivalentes a 0° o −10°.
- Integración en el dominio: `default_pipeline` sobre una grabación sintética (directo a 3 ms y reflexión a 18.75 ms) recupera el directo (1.00 ± 0.02) y anula todo lo que queda después de la compuerta de 10 ms.

**Hallazgos**
- **El criterio del plan estaba mal.** Atribuía al ruido rosa la energía proporcional al número de bins, que es la propiedad del ruido blanco. Se corrigió en la Fase 3 y se prueban ambos casos.
- **Pruebas espectrales deterministas.** Generar el ruido "de magnitud exacta y fase aleatoria" en vez de ruido aleatorio en el tiempo deja las tolerancias en centésimas de dB.

**Desviaciones y decisiones**
- **`OCTAVE_CENTERS` por defecto va de 63 Hz a 8 kHz**, sin 16 kHz: el borde superior de esa banda (22.6 kHz) supera Nyquist a 44.1 kHz. Quien mida a 48 kHz puede pedir la banda de 16 kHz explícitamente.
- **Centros exactos en base 2** (`1000·2^n`) para los bordes, con los nombres nominales (63, 125…) como claves. Así 63 Hz usa 62.5 Hz como centro.
- **El nivel es relativo (dB re 1):** solo importan las diferencias entre ángulos.
- **Cálculo en el constructor.** `PolarPattern` calcula los niveles al construirse, en vez de hacerlo de forma diferida: es más simple y el costo es bajo (una FFT por punto).
- **Parámetros explícitos de la compuerta.** `default_pipeline` recibe `gate_start_s` y `gate_length_s`, no solo `(excitation, window)`, porque dependen de la geometría de la medición. Los valores por defecto son 0 y 10 ms.

### Fase 4: Puertos `AudioIO` y `ResultStore` con sus adaptadores (2026-11-08)

**Qué se hizo**
- `ports.py`: agrega `AudioIO` (con la propiedad `sample_rate` y `play_and_record`) y `ResultStore` (`append` y `save_pattern`), que usan los tipos del dominio.
- `adapters/simulated_audio.py`:
  - Modelos `omni` y `cardioid`, y el registro `MODELS` para la CLI.
  - `SimulatedAudio(angle_provider, directivity, sample_rate, delay_s, reflections, noise_db, tail_s, seed)`, con `impulse_response(angle)` público.
  - La grabación es la excitación convolucionada con la IR, más una cola de silencio y ruido gaussiano reproducible. Si la excitación llega a otra frecuencia de muestreo, lanza `ValueError`.
- `adapters/csv_store.py`:
  - `CsvResultStore(directory, bands)`: crea el directorio y se niega a escribir sobre una sesión existente.
  - `append` guarda `ir_NNN.npy` en float32 y agrega una fila a `measurements.csv` (abre y cierra el archivo en cada llamada). Las columnas son `index`, `target_deg`, `actual_deg`, `sample_rate`, `ir_file` y `level_<banda>_hz`.
  - `save_pattern` escribe `pattern.csv`.
  - Los valores se guardan con `repr()` para que la ida y vuelta sea exacta, incluido `-inf`.
- Tests: `tests/contract/audio_contract.py` y `test_audio_contract.py` (5 casos × 2 configuraciones), `tests/adapters/test_simulated_audio.py` y `tests/adapters/test_csv_store.py`.

**Validación**
- `ruff check`, `ruff format --check` y `mypy --strict` (38 archivos) pasan sin errores.
- `pytest`: 251 tests pasan y 16 de hardware quedan excluidos. Cobertura del 99 %; `simulated_audio.py`, `csv_store.py` y `ports.py` al 100 %.
- El test de arquitectura sigue en verde: `ports` importa solo del dominio.
- **Criterios de aceptación:**
  - El contrato de audio pasa 5/5 con `simulated-omni` y 5/5 con `simulated-cardioid-noisy-44k` (cardioide a 44.1 kHz, con reflexión y ruido a −60 dB).
  - Ida y vuelta: `csv.DictReader` recupera los ángulos y los niveles por banda exactos. `np.load` recupera las IRs en float32 (rtol 1e-6). `pattern.csv` se lee igual a `pattern.normalized()`, `-inf` incluido.
  - Escritura incremental: después de cada `append` k (sin cerrar ni finalizar), el disco tiene k filas y k archivos `.npy`.
- El adaptador simulado recupera −6.02 dB (±0.05) entre 0° y 90° con la cardioide, a través de `LogSweep`. El ruido queda a −40 dB (±0.5) y es reproducible con la misma semilla.

**Desviaciones y decisiones**
- **Las IRs se guardan procesadas, no crudas.** Se corrigió el plan, porque el caso de uso guarda la salida del pipeline. Si más adelante se quiere recompuertar sin volver a medir, habría que guardar también la IR sin compuerta. Por ahora no hay necesidad demostrada.
- **Columna `sample_rate` en `measurements.csv`.** No estaba en el plan, pero sin ella las IRs `.npy` no se pueden reinterpretar.
- **El store calcula los niveles por banda** con `octave_band_levels` del dominio y recibe las bandas en el constructor. Repite el cálculo de `PolarPattern`, pero son FFTs baratas y así `append` funciona antes de tener el patrón completo.
- **`SimulatedAudio` acepta reflexiones de sala** (`reflections`), que no estaban en el plan. Así la integración de la Fase 5 prueba también que la compuerta las elimina.
- **El contrato de `AudioIO` exige `ValueError`** ante una frecuencia de muestreo distinta: es un requisito razonable para cualquier adaptador real.
- **`pattern.csv` usa `csv.reader`**, no `DictReader`, porque dos ángulos iguales repetirían el nombre de columna.
- **Caracteres Unicode en docstrings.** Ruff (`RUF002`) rechaza `−` y `∗` porque se confunden con `-` y `*`. En el código se usa ASCII; en este documento no hay problema.

### Fase 5: Caso de uso `MeasureDirectivity` y CLI `measure` (2026-11-08)

**Qué se hizo**
- `application/measure_directivity.py`: `MeasureDirectivity`, una dataclass con `turntable`, `audio`, `store`, `excitation`, `pipeline: Stage`, `bands`, `settle_s` y `sleep`.
  - `execute(plan, on_point)` genera la excitación una sola vez, hace `home()` y en cada ángulo ejecuta `goto`, `sleep(settle_s)`, `play_and_record`, `pipeline`, crea el `MeasurementPoint` y llama a `store.append` y a `on_point`. Al final arma el `PolarPattern` y llama a `save_pattern`.
  - En el `finally`, `_park()` hace `goto(0)` y luego `release()`, este último aunque el regreso falle.
- `adapters/cli.py`:
  - Subcomando `measure (--port | --simulate)` con `--source omni|cardioid`, `--start`, `--stop`, `--step` (por defecto 10°), `--out` (por defecto `data/raw`), `--sample-rate`, `--sweep-duration`, `--gate-start`, `--gate-length` y `--settle`.
  - Arma `LogSweep`, `default_pipeline`, `SimulatedTurntable`, `SimulatedAudio` y `CsvResultStore` con `ExitStack` y `closing`.
  - Geometría simulada: directo a 3 ms (≈1 m), reflexión de piso a 12 ms (ganancia 0.4) y ruido a −80 dB.
  - Durante la medición muestra el avance con L(1 kHz) y al final imprime la tabla del patrón a 1 kHz.
  - `--port` termina con un error claro: falta el adaptador de audio real.
  - Los errores (`TurntableError`, `ValueError`, `FileExistsError`) salen con código 1 y Ctrl-C con código 130.
  - Despacho por `set_defaults(handler=...)`; `sweep` pasó a `_cmd_sweep`.
  - Funciones auxiliares: `bands_for(fs)` (bandas bajo Nyquist), `session_name()` (fecha y hora, sustituible en tests) y `format_db()` (sin `-0.00`).
- Tests: `tests/application/test_measure_directivity.py` (integración totalmente simulada y fallas) y la clase `TestMeasure` en `tests/adapters/test_cli.py`.

**Validación**
- `ruff check`, `ruff format --check` y `mypy --strict` (41 archivos) pasan sin errores.
- `pytest`: 269 tests pasan y 16 de hardware quedan excluidos, en 1.05 s.
- **Criterios de aceptación:**
  - Integración totalmente simulada (`SimulatedTurntable`, `SimulatedAudio` cardioide a 16 kHz con reflexión y ruido, y `CsvResultStore`): el patrón a 1 kHz coincide con la cardioide teórica, evaluada en el ángulo real, con un error máximo de **0.0058 dB** entre 0° y 150° (tolerancia ±0.5 dB).
  - Si `AudioIO` lanza un error en el tercer punto (se probó con `RuntimeError` y con `KeyboardInterrupt`), la mesa termina en 0° y con las bobinas apagadas, `measurements.csv` tiene **2 filas** y no se escribe `pattern.csv`.
  - `directivity measure --simulate --source cardioid --step 10 --out <dir>` crea la sesión con 37 archivos `ir_NNN.npy`, `measurements.csv` y `pattern.csv`. A 90° muestra −6.02 dB.
  - Cobertura de `domain/` y `application/` al **100 %** (el umbral era ≥ 90 %). Cobertura total del 99 %.
- También se verificó que la compuerta elimina la reflexión de sala (nada después de los 10 ms), que el regreso a 0° fallido igual apaga las bobinas y que `sleep(settle_s)` se llama una vez por punto.

**Hallazgos**
- **Ruido y formato.** Con ruido, la normalización produce valores como −0.001 dB, que se imprimían como `-0.00`. Se agregó `format_db`.
- **La sesión puede chocar.** Dos mediciones en el mismo segundo comparten nombre de sesión. `CsvResultStore` lo detecta (`FileExistsError`) y la CLI lo informa sin sobrescribir.

**Desviaciones y decisiones**
- **El caso de uso recibe el pipeline como `Stage`**, no como `Pipeline`, para depender de la abstracción (DIP). `Pipeline` la cumple.
- **La excitación se pasa dos veces**: al caso de uso (para `generate`) y a `Deconvolve`. Las arma la CLI con el mismo objeto y el docstring lo advierte. Se descartó que el caso de uso construya el pipeline, porque dejaría de ser inyectable.
- **`--settle` por defecto** vale 0 en simulación y 0.5 s con la placa. Con la placa, `measure` todavía termina en error hasta que exista un adaptador de audio real (sección 11).
- **Funciones extra** que no estaban en el plan: `session_name()` y `format_db()`, para poder probar choques de sesión y por la presentación.

### Fase 6: Retiro del código heredado y documentación (2026-11-08)

**Qué se hizo**
- Se eliminaron `python/turntable.py` y `python/requirements.txt` (con `git rm`). `python/` también se quitó del `extend-exclude` de ruff.
- `README.md` reescrito:
  - Estructura nueva y resumen de la arquitectura.
  - Instalación con `pip install -e ".[dev]"`.
  - `directivity sweep` y `directivity measure`, con la tabla de archivos de la sesión y las opciones útiles.
  - Uso como librería: `SerialTurntable.open(...)` como reemplazo directo del antiguo `Turntable(...)` y una medición simulada armada a mano con `MeasureDirectivity`.
  - Desarrollo y tests con la placa, con la tabla de ejecuciones.
  - Próximos pasos actualizados. Se marcó como hecha la automatización en simulación y se agregó el adaptador de audio real.
  - En la sección del firmware, la afirmación "72 × 5° = 2048 pasos" ahora cita el emulador `float32`.
- Plan: estado "completado" al inicio, criterio del `grep` corregido y sección 10 corregida (`python3.12` → `python3.13`, cualquier versión ≥ 3.11).

**Validación**
- `grep -rn "python/turntable" . --exclude-dir=.git --exclude-dir=.venv --exclude=MIGRATION_PLAN.md` no devuelve coincidencias (rc=1).
- Ejemplos del README: un script del scratchpad extrae los bloques de código y los ejecuta tal cual en una carpeta temporal.
  - `directivity sweep --simulate --step 5`: rc=0.
  - `directivity measure --simulate --source cardioid --step 10 --out data/raw`: rc=0, con la sesión creada.
  - Medición simulada en Python: rc=0, con la sesión `data/raw/ejemplo` creada.
  - Ejemplo con la placa: rc=0, ejecutado contra el emulador del firmware sustituyendo solo `SerialTurntable.open`.
- `ruff check`, `ruff format --check` (42 archivos, incluido el README) y `mypy --strict` (41 archivos) pasan sin errores. `pytest`: 269 tests pasan y 16 de hardware quedan excluidos. Cobertura total del 99 %.
- CI: el run 42526016376 está en verde (`python` en 3.11, 3.12 y 3.13, y `firmware`).

**Desviaciones y decisiones**
- **El criterio del `grep` estaba mal planteado.** Tal como estaba escrito, era imposible de cumplir sin borrar la historia del propio plan. Se corrigió para excluir `MIGRATION_PLAN.md`, `.git/` y `.venv/`.
- **El ejemplo con la placa no se puede ejecutar en la Mac sin hardware.** Se validó contra el emulador, el mismo que usan los tests de contrato.

### Fase 7 (agregada): Reescritura del firmware (2026-11-08)

**Análisis de la versión 1**

| # | Problema | Severidad | Qué hace la v2 |
|---|---|---|---|
| 1 | `GOTO NAN` y `MOVE NAN` pasan la verificación de rango (`x > 360 \|\| x < -360` es falso con NaN), y `lround(NaN)` da un número de pasos indefinido. En la compilación de la Mac la v1 **giró 541 pasos** y respondió `OK 0.00`. | Alta: giro descontrolado que puede enrollar el cable | `parseAngle` rechaza NaN con `ERR angulo invalido`. Además, `isWithinLimits` está escrito como `x >= -L && x <= L`, que también deja fuera a NaN. |
| 2 | Se ignoraba el texto después del número: `GOTO 5abc` iba a 5°, un error de tipeo como `GOTO 9O` iba a 9°, y `GOTO 5 10` iba a 5°. | Media | Solo se permiten espacios después del número; si no, `ERR angulo invalido`. |
| 3 | `toupper(char)` recibía valores negativos con bytes no ASCII: comportamiento indefinido. | Baja | `toupper((unsigned char)c)`. |
| 4 | `setup()` esperaba la conexión USB antes de apagar las bobinas. | Baja (al arrancar los pines ya están en LOW) | Primero `setSpeed` y `releaseCoils`, después la espera. |
| 5 | Estado y funciones globales sin `static`, números mágicos (el `5` de `strncmp`) y funciones sin documentar. | Calidad | Enlace interno (`static`), constantes con nombre (`GOTO_PREFIX`, `ARG_PREFIX_LENGTH`, `ANGLE_DECIMALS`, `SERIAL_BAUD`), un `static_assert` y comentarios Doxygen en cada función. Las órdenes se separan en funciones (`handleGoto`, `handleMove`, `processChar`, `replyError`). |

Se mantuvieron a propósito:
- Las mismas respuestas y mensajes.
- Las líneas vacías sin respuesta.
- Las bobinas energizadas después de cada giro (torque de retención).
- Los giros bloqueantes.
- El orden de pines 8–11 y la librería `Stepper`.

**Verificación**
- **Entorno de pruebas en la Mac** (`tests/firmware/host/`), que imita la semántica del AVR:
  - `Print::printFloat` en `float`.
  - `strtod` con la gramática de avr-libc: INF, INFINITY y NAN, sin hexadecimales, con el resultado en float.
  - `digitalWrite` y `Stepper::step` se registran como eventos `@dw` y `@step`.
- **La v1 se compiló en la Mac adaptando sus literales:** `360.0` pasa a `360.0f`, que es su significado exacto en AVR, donde `double` es `float`. `clang` no acepta `-fsingle-precision-constant`.
- **Prueba diferencial v1 contra v2** (script en el scratchpad de la sesión, no versionado):
  - Barrido exhaustivo de **20 482 órdenes**: todas las posiciones de −4096 a +4096 pasos, más cada empate de medio paso exacto y a ±1e-6. Las trazas son **idénticas**, línea por línea.
  - **3042 casos aleatorios y de borde** (formatos enteros, decimales, con exponente, con signo y con espacios; minúsculas; `\r\n`; líneas largas; INF; 1e400; hexadecimales). Hay 2423 idénticos. Los 619 restantes son exactamente las correcciones (4 de NaN y 615 con texto sobrante), y en todos la v2 responde `ERR angulo invalido` sin girar. **No hubo diferencias inesperadas.**
- **Test permanente** `tests/firmware/test_firmware_host.py` (27 casos, corre en la CI):
  - Compila el `.ino` real con `-Wall -Wextra -Wpedantic -Werror` y compara la traza completa con `FirmwareEmulator`.
  - Cubre una sesión de protocolo, NaN y texto sobrante, casos límite, `\r\n`, bytes no ASCII, un barrido exhaustivo de ±2 vueltas y 2000 órdenes aleatorias.
- **Prueba de mutación:**
  - Si se cambia el límite del firmware a 359.9°, fallan 2 tests.
  - Si se quita el rechazo de NaN del emulador, fallan 4 tests.
  - Revertidas las dos mutaciones, todo vuelve a verde.
- **AVR (CI, run 42527234592):** compila sin advertencias del sketch. La v1 usaba 9014 bytes de flash y 263 de RAM; la v2 usa **9130 bytes de flash (+116, +1.3 %) y 263 de RAM (igual)**.
- Suite completa: 304 tests pasan y 16 de hardware quedan excluidos. `ruff` y `mypy --strict` pasan sin errores.

**Hallazgos**
- **Error en mi primera versión del formato.** La primera v2 imprimía los ángulos con aritmética entera, porque yo suponía que el resultado era idéntico al de `Serial.print(float, 2)`. La prueba diferencial lo desmintió. En los empates exactos de medio centésimo, por ejemplo −118.125° (−672 pasos), el `printFloat` de la v1 a veces redondea hacia abajo por el error de `float32` (`-118.12`) y a veces hacia arriba (`5.625` da `5.63`). Ninguno es un error, porque ambos valores están a la misma distancia del exacto, pero el texto cambiaba. Como el pedido era comportarse igual, se volvió a `Serial.print(float, 2)`.
- **No se puede compilar para AVR en esta Mac.** El `avr-gcc` del core de Arduino es x86_64 y necesitaría Rosetta. La compilación para AVR queda en la CI; `arduino-cli` 1.5.1 sí quedó instalado con Homebrew.

**Desviaciones y decisiones**
- **Cambio de alcance.** La sección 5 excluía el firmware; se reescribió a pedido y se documenta como Fase 7.
- **El emulador ahora replica la v2.** Acepta INF y NAN como avr-libc, rechaza NaN y texto sobrante, convierte a mayúsculas solo en ASCII y registra la traza. Con eso desaparece la "divergencia conocida" de la Fase 1.
- **El script diferencial no se versionó**, porque depende de la v1 sacada del historial de git. Lo permanente es la comparación firmware ↔ emulador.
- **Sigue pendiente la prueba en la placa:** `pytest -m hardware --port …`.

## 13. Resumen de la migración

| Fase | Resultado | Tests (acumulados) |
|---|---|---|
| 0 | Empaquetado, herramientas, test de arquitectura y CI (Python 3.11–3.13 + firmware) | 9 |
| 1 | Puerto `Turntable`, `SerialTurntable`, `SimulatedTurntable`, emulador del firmware y contrato | 115 |
| 2 | `Signal`, `LogSweep` (Strategy) y `RectangularWindow`/`TukeyWindow` (Strategy) | 185 |
| 3 | `Pipeline`, `Deconvolve`, `Gate`, niveles por octava y `PolarPattern` | 217 |
| 4 | `AudioIO`, `ResultStore`, `SimulatedAudio` y `CsvResultStore` | 251 |
| 5 | `MeasureDirectivity` y `directivity measure` | 269 |
| 6 | Retiro del cliente heredado y README | 269 |
| 7 | Firmware v2.0.0 (NaN, texto sobrante y calidad) verificado contra la v1; test firmware ↔ emulador | 304 |

**Pendiente fuera del alcance:**
- Correr `pytest -m hardware --port …` con la placa y anotarlo en el README.
- Adaptador de audio real.
- Gráficos polares y DI (sección 11).

