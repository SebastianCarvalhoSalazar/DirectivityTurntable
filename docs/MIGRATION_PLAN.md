# Plan de migración a arquitectura hexagonal liviana — DirectivityTurntable

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

Contenedor de inyección de dependencias, clases base abstractas (se usa `Protocol` estructural), bus de eventos, async, plugins, archivos de configuración (bastan los flags de argparse), capa de DTOs y mappers, un `Pipeline` genérico tipado, mocks escritos a mano cuando ya existe un adaptador simulado, y cambios al firmware.

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
  - `directivity sweep --simulate --step 5` imprime 73 líneas y la última muestra `real 360.00°`.
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
  - Con ruido rosa sintético, `octave_band_levels` reparte la energía de forma consistente con el número de bins de cada banda (tolerancia de ±0.5 dB). Un tono de 1 kHz cae en la banda de 1 kHz.
  - Un `PolarPattern` con ganancias de cardioide da −6.02 dB a 90° (±0.05 dB). La normalización usa el **ángulo real**, no el pedido.
- **Riesgos:**
  - **Compuerta inconsistente entre ángulos.** Se mitiga fijando el inicio de `Gate` con un retardo común (distancia constante), no con el pico de cada IR.
  - **Bandas bajas con poca resolución** por una compuerta corta. Se documenta el límite de baja frecuencia ≈ 1/longitud_de_compuerta.

### Fase 4: Puertos `AudioIO` y `ResultStore` con sus adaptadores

- **Archivos:** `ports.py` (agrega `AudioIO` y `ResultStore`), `adapters/simulated_audio.py`, `adapters/csv_store.py`, `tests/contract/test_audio_contract.py` y `tests/adapters/test_csv_store.py`.
- **`SimulatedAudio(sample_rate, directivity: Callable[[float], float], delay_s, noise_db, angle_provider, seed)`:** convoluciona la excitación con una IR sintética, con la ganancia dada por `directivity(angle)`, y agrega ruido reproducible. Trae los modelos `omni` y `cardioid`.
- **`CsvResultStore(directory)`:**
  - `measurements.csv`: índice, ángulo pedido, ángulo real, nivel por banda y archivo de la IR.
  - `ir_000.npy`, …: IRs crudas.
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
  - `grep -r "python/turntable" .` no devuelve nada.
  - Los ejemplos del README se ejecutan tal cual en modo `--simulate`.
  - La CI está en verde.
- **Riesgo:** perder el uso "como librería" del README. Se mitiga documentando `SerialTurntable.open(...)` como reemplazo directo.

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
- **Job `firmware` (opcional, barato):** `arduino/compile-sketches` con `fqbn: arduino:avr:leonardo`. Detecta si el `.ino` deja de compilar.

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
python3.12 -m venv .venv && source .venv/bin/activate
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
