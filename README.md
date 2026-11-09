# DirectivityTurntable

Mesa giratoria automatizada para medir el **patrón de directividad (diagramas polares)** de parlantes y fuentes sonoras. Un Arduino gira el plato a ángulos precisos y el paquete de Python `directivity` coordina el giro con la medición: reproduce un barrido senoidal, obtiene la respuesta al impulso en cada ángulo y calcula el patrón polar por banda de octava.

## Hardware

| Componente | Función |
|---|---|
| Arduino Leonardo | Controla el motor y recibe órdenes por USB/serial |
| Puente H L298N | Driver de potencia del motor |
| Motor paso a paso 24BYJ48A (12 V) | Gira el plato |
| Fuente de 12 V | Alimenta el driver y el motor |
| Plato de acrílico con base de madera e imán de neodimio | Sostiene la fuente sonora |

**Conexión:** los pines 8, 9, 10 y 11 del Arduino van a las entradas IN1, IN2, IN3 e IN4 del L298N, en ese orden.

## Estructura

```
DirectivityTurntable/
├── firmware/DirectivityTurntable/DirectivityTurntable.ino   # firmware del Arduino
├── src/directivity/
│   ├── domain/        # ángulos, señales, ventanas, pipeline y patrón polar (sin I/O)
│   ├── application/   # caso de uso MeasureDirectivity
│   ├── ports.py       # contratos: Turntable, AudioIO, ResultStore
│   └── adapters/      # mesa serial y simulada, audio simulado, CSV y CLI
├── tests/             # unitarios, de contrato, de integración y de arquitectura
├── Makefile           # atajos de pruebas, placa y firmware (make help)
└── docs/MIGRATION_PLAN.md
```

La arquitectura es hexagonal liviana. El dominio no sabe nada del puerto serial ni del audio, y el caso de uso solo conoce los puertos (`typing.Protocol`). Los adaptadores reales y los simulados cumplen el mismo contrato, verificado por los mismos tests. El detalle y el registro de la migración están en [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md).

## Firmware

Usa un protocolo de texto: una orden por línea a 115200 baudios.

| Orden | Acción | Respuesta |
|---|---|---|
| `PING` | Verifica la conexión | `OK PONG` |
| `HOME` | Toma la posición actual como 0° | `OK 0.00` |
| `GOTO <grados>` | Va a un ángulo absoluto (−360° a 360°) | `OK <ángulo real>` |
| `MOVE <grados>` | Gira de forma relativa | `OK <ángulo real>` |
| `POS` | Devuelve el ángulo actual | `OK <ángulo>` |
| `RELEASE` | Apaga las bobinas para que no se calienten | `OK RELEASED` |

Si una orden falla, la respuesta empieza con `ERR`:
- `ERR angulo invalido`: el argumento no es un número, es `NaN` o tiene texto sobrante (`GOTO 5abc`).
- `ERR fuera de rango (-360 a 360)`: el destino supera el límite de ±360°, que evita que el cable del parlante se enrolle.
- `ERR comando desconocido` o `ERR linea demasiado larga` (las órdenes admiten hasta 31 caracteres).

No se distinguen mayúsculas de minúsculas, se ignora `\r` y las líneas vacías no tienen respuesta.

**Resolución:** con 2048 pasos por vuelta, cada paso equivale a unos 0,18°. Por eso `GOTO 5` puede devolver `OK 4.92`. El software registra siempre el ángulo real que devuelve la mesa, no el que se pidió.

### Posición absoluta

Un giro de 5° equivale a 28,44 pasos, que no es un número entero. Si cada giro se redondeara por separado, el error se acumularía: en una vuelta completa de 72 giros de 5° se perderían unos 5,6°.

Para evitarlo, el firmware lleva la posición absoluta en pasos y redondea cada destino contra el ángulo absoluto, así que el error nunca supera medio paso. El emulador del firmware que usan los tests confirma que 72 giros de 5° suman exactamente 2048 pasos (360,00°). Usa aritmética `float32`, igual que el AVR. Falta probarlo en la placa (ver [Tests con la placa](#tests-con-la-placa)).

### Calibración

El firmware usa 2048 pasos por vuelta como valor nominal (`STEPS_PER_REV`). Para verificarlo en tu motor:

1. Marca una referencia en el plato.
2. Envía `HOME` y luego `GOTO 360`.
3. Si la marca no vuelve exactamente al inicio, ajusta `STEPS_PER_REV`.

### Cargar el firmware

1. Abre `firmware/DirectivityTurntable/DirectivityTurntable.ino` en el Arduino IDE.
2. Selecciona **Arduino Leonardo** y el puerto correcto.
3. Sube el sketch. La librería `Stepper` viene incluida con el IDE.

La CI también compila el sketch para Leonardo en cada push.

### Pruebas del firmware sin placa

`tests/firmware/` compila el mismo `.ino` en el computador, con un sustituto de `Arduino.h` que imita la aritmética del AVR. Le envía órdenes por un "serial" simulado y compara la traza completa con el emulador de Python que usan los tests del cliente: respuestas, pasos del motor y pines. Así el firmware, el emulador y el cliente no pueden separarse sin que fallen los tests. Basta con tener un compilador de C++ (`c++`); si no hay, esos tests se saltan. Cómo correrlos y el resto de las pruebas: ver [Pruebas](#pruebas).

La versión 2.0.0 del firmware corrige un giro indefinido con `GOTO NAN` y rechaza texto sobrante después del número. Fuera de eso, responde y gira exactamente igual que la versión 1: lo verificó una prueba diferencial entre ambas versiones, descrita en `docs/MIGRATION_PLAN.md`.

## Instalación

Requiere Python 3.11 o superior. En macOS el `python3` del sistema es 3.9, así que crea un entorno virtual con uno más nuevo (por ejemplo `brew install python@3.13`):

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Uso

### Barrido de prueba de la mesa

Recorre los ángulos sin medir audio y muestra el ángulo pedido junto al real. Al final la mesa vuelve a 0° y apaga las bobinas.

```bash
directivity sweep --simulate --step 5                    # sin placa
directivity sweep --port /dev/tty.usbmodemXXXX --step 5  # con la placa
```

En macOS el puerto aparece como `/dev/tty.usbmodem...`. Puedes listarlo con `ls /dev/tty.usbmodem*`.

### Medición de directividad

Antes de empezar, la fuente debe apuntar al micrófono: esa posición se toma como 0°. En cada ángulo se reproduce un barrido senoidal exponencial. La grabación se deconvoluciona para obtener la respuesta al impulso, que pasa por una compuerta temporal para quedarse con el sonido directo y descartar las reflexiones. Por último se calcula el nivel por banda de octava.

```bash
directivity measure --simulate --source cardioid --step 10 --out data/raw
```

Por ahora la medición solo funciona en simulación: falta el adaptador de audio real, y con `--port` el comando termina con un error que lo explica. La fuente simulada puede ser `omni` o `cardioid`; está a ~1 m del micrófono, con una reflexión de piso y ruido de fondo. Cada sesión se guarda en `data/raw/<fecha>T<hora>/`:

| Archivo | Contenido |
|---|---|
| `measurements.csv` | Una fila por ángulo: ángulo pedido, ángulo real, frecuencia de muestreo, archivo de la IR y nivel por banda. Se escribe apenas se mide cada punto. |
| `ir_000.npy`, … | Respuesta al impulso procesada de cada ángulo (`float32`) |
| `pattern.csv` | Niveles relativos al eje (dB): una fila por banda y una columna por ángulo real |

Opciones útiles:
- `--start`, `--stop` y `--step` definen el barrido angular.
- `--sweep-duration` fija la duración del barrido senoidal.
- `--gate-start` y `--gate-length` definen la compuerta. La frecuencia mínima confiable es ≈ 1/duración: 100 Hz con la compuerta de 10 ms por defecto.

`directivity measure --help` las lista todas.

### Como librería

La mesa real, con `SerialTurntable.open(...)` como reemplazo del antiguo `Turntable(...)`:

```python
from directivity.adapters.serial_turntable import SerialTurntable

with SerialTurntable.open("/dev/tty.usbmodemXXXX") as table:
    table.home()
    for angle in range(0, 361, 5):
        real = table.goto(angle)
        # aquí va la medición acústica en el ángulo `real`
    table.goto(0)
    table.release()
```

Una medición completa en simulación, armando el caso de uso a mano:

```python
from pathlib import Path

from directivity.adapters.csv_store import CsvResultStore
from directivity.adapters.simulated_audio import SimulatedAudio, cardioid
from directivity.adapters.simulated_turntable import SimulatedTurntable
from directivity.application.measure_directivity import MeasureDirectivity
from directivity.domain.angles import SweepPlan
from directivity.domain.pipeline import default_pipeline
from directivity.domain.signals import LogSweep

table = SimulatedTurntable()
audio = SimulatedAudio(angle_provider=table.position, directivity=cardioid)
sweep = LogSweep(duration_s=1.0)
measure = MeasureDirectivity(
    turntable=table,
    audio=audio,
    store=CsvResultStore(Path("data/raw/ejemplo")),  # la carpeta no debe tener otra sesión
    excitation=sweep,
    pipeline=default_pipeline(sweep, gate_length_s=0.010),
    settle_s=0.0,
)
pattern = measure.execute(SweepPlan(0, 180, 30))
for angle, level in zip(pattern.angles(), pattern.normalized(1000.0)):
    print(f"{angle:7.2f}°  {level:6.2f} dB")
```

## Pruebas

### Preparación

```bash
cd ~/Projects/DirectivityTurntable
source .venv/bin/activate
```

Con el venv activado, el prompt muestra `(.venv)` y los comandos `directivity`, `pytest`, `mypy` y `ruff` funcionan directamente. Si no lo activas, tienes dos alternativas:
- `make`, que usa `.venv/bin` por dentro.
- La ruta completa: `.venv/bin/directivity …`, `.venv/bin/pytest …`.

En Claude Code cada comando con `!` abre una terminal nueva, así que la activación no se conserva. Usa `! make …` o `! .venv/bin/…`.

### Atajos con `make`

`make help` lista todos los atajos y el puerto detectado. Los de la placa encuentran el puerto solos (`/dev/tty.usbmodem*`); para usar otro, por ejemplo `make hw PORT=/dev/tty.usbmodem1101`. Antes de empezar comprueban que haya una placa conectada y que el puerto no esté ocupado.

| Atajo | Qué hace | Duración |
|---|---|---|
| `make check` | Estilo, formato, tipos y todos los tests sin placa (lo mismo que la CI) | ~5 s |
| `make test` / `make cov` | Solo los tests, sin y con reporte de cobertura | ~3 s |
| `make format` | Corrige formato y lint automáticamente | ~1 s |
| `make sim` | Barrido y medición con la mesa simulada | ~2 s |
| `make ports` | Muestra el puerto de la placa y quién lo usa | — |
| `make sweep` | Prueba corta con la placa: de 0° a 90° cada 30° | ~9 s |
| `make selftest-quick` | Autoprueba rápida con la placa | ~20 s |
| `make selftest` | Autoprueba completa con la placa | ~55 s |
| `make hw` | Contrato de la mesa contra la placa | ~2.5 min |
| `make firmware` / `make upload` | Compila el firmware, o lo compila y lo carga en la placa | ~10 s |

### 1. Sin placa

```bash
make check
```

Equivale a correr estos cuatro comandos:

```bash
ruff check .            # estilo y docstrings (Google)
ruff format --check .   # formato
mypy                    # tipos, modo estricto
pytest                  # todos los tests sin placa
```

Resultado esperado: `All checks passed!`, `Success: no issues found` y todos los tests en `passed`, con **16 `deselected`**. Esos 16 son los tests de hardware, que se excluyen por defecto.

Para correr solo una parte:

| Comando | Qué prueba |
|---|---|
| `pytest tests/domain` | Ángulos, señales, ventanas, procesamiento y patrón polar |
| `pytest tests/application` | El caso de uso de medición y la autoprueba, con adaptadores simulados |
| `pytest tests/adapters` | Cliente serial, CSV, audio simulado y la CLI |
| `pytest tests/contract` | Contratos: la mesa simulada y el firmware emulado deben comportarse igual |
| `pytest tests/firmware` | Compila el `.ino` en el computador y lo compara con el emulador |
| `pytest tests/test_architecture.py` | Que ninguna capa importe lo que no debe |
| `pytest tests/test_docs.py` | Que los comandos de esta guía sigan siendo válidos |

Opciones útiles de pytest:

| Opción | Para qué |
|---|---|
| `-v` | Muestra cada test por nombre |
| `-x` | Se detiene en el primer fallo |
| `-k nan` | Corre solo los tests cuyo nombre contiene "nan" |
| `pytest tests/domain/test_angles.py::TestSweepPlan` | Corre una sola clase o un solo test |

### 2. CLI en simulación

```bash
directivity sweep --simulate --step 5
directivity measure --simulate --source cardioid --step 10 --out data/raw
directivity selftest --simulate
```

Resultado esperado:
- `sweep` imprime 73 líneas que terminan en `real 360.00°`.
- `measure` muestra −6.02 dB a 90° y crea `data/raw/<fecha>/`.
- `selftest` termina en `RESULTADO: OK (4/5 ok, 1 no aplica)`. El check que no aplica es el del tiempo por paso, porque la mesa simulada gira de forma instantánea.

### 3. Con la placa

Antes de empezar, revisa:
- [ ] El Arduino está conectado por USB y el adaptador de 12 V enchufado. El LED POWER del L298N debe estar encendido.
- [ ] **El Monitor Serie del Arduino IDE está cerrado**, porque ocupa el puerto. `make ports` muestra si algo lo está usando.
- [ ] El plato gira libre y no hay cables que se puedan enredar.

```bash
make ports                                                   # puerto y estado
directivity sweep --port /dev/tty.usbmodemXXXX --stop 90 --step 30
directivity selftest --port /dev/tty.usbmodemXXXX --quick
directivity selftest --port /dev/tty.usbmodemXXXX
pytest -m hardware --port=/dev/tty.usbmodemXXXX -v
```

| Prueba | Atajo | Resultado esperado |
|---|---|---|
| Prueba corta | `make sweep` | 4 puntos (30.06°, 59.94°, 90.00°) y regreso a 0° |
| Autoprueba | `make selftest-quick` / `make selftest` | `RESULTADO: OK (5/5 ok)` |
| Contrato | `make hw` | `16 passed` |

**En el contrato, `--port=` va con el signo igual.** Si escribes el puerto separado por un espacio, pytest lo toma como la carpeta de los tests y responde `unrecognized arguments: --port`. También funciona indicando la carpeta: `pytest tests -m hardware --port /dev/…`. En `directivity` sirven las dos formas.

**Qué revisa la autoprueba (`selftest`):**

| Check | Qué mide | Umbral |
|---|---|---|
| Latencia serial (p95) | Tiempo de ida y vuelta de `POS`, sin mover el motor | < 20 ms |
| Tiempo por paso | Ajuste lineal de la duración de 8 giros contra sus pasos; esperado 60 / (2048 × 5 RPM) | ±2 % |
| Ángulo reportado contra el modelo | Lo que responde la mesa frente a la cuantización del cliente de Python | ≤ 0.011° |
| Medición completa | Medición de 0° a 360° cada 10° (0° a 180° cada 45° en la versión rápida) con audio simulado cardioide; error a 1 kHz | < 0.5 dB |
| Regreso a 0° y RELEASE | Posición al terminar | 0.00° |

Cada ejecución guarda en `data/selftest/<fecha>/` el reporte `selftest.json` (métricas, umbrales y versión) junto con la medición (`measurements.csv`, `pattern.csv` y las IRs). Termina con código 0 si ningún check falla y con 1 si alguno falla. La autoprueba valida la mesa y el software, no la acústica: el audio es simulado y no comprueba la calibración física de los pasos por vuelta.

### 4. Firmware

```bash
make firmware      # compila para Leonardo (debe decir "Sketch uses 9130 bytes")
make upload        # compila y carga en la placa (con el Monitor Serie cerrado)
```

Si cambias el firmware, actualiza también `tests/support/firmware_emulator.py`; si no, `pytest tests/firmware` falla ([ver arriba](#pruebas-del-firmware-sin-placa)).

Para probarlo a mano, abre el Monitor Serie del Arduino IDE con *New Line* y 115200 baud:

| Escribes | Responde |
|---|---|
| `PING` | `OK PONG` |
| `HOME` | `OK 0.00` |
| `GOTO 10` | `OK 10.02` (gira ~10°) |
| `GOTO NAN` | `ERR angulo invalido` (no se mueve) |
| `GOTO 0` y luego `RELEASE` | `OK 0.00` y `OK RELEASED` |

Al terminar, **cierra el Monitor Serie**, para que Python pueda usar el puerto.

### 5. CI

Se ejecuta sola en cada `git push` (`make check` con Python 3.11, 3.12 y 3.13, más la compilación del firmware). Para revisarla desde la terminal:

```bash
gh run list --limit 3       # últimas ejecuciones
gh run view --log-failed    # detalle si alguna falló
```

### Orden recomendado

| Si cambiaste… | Corre |
|---|---|
| Código Python | `make check` |
| El firmware | `pytest tests/firmware`, luego `make upload`, luego `make selftest-quick` y `make hw` |
| Nada, pero vas a medir | `make ports` y luego `make selftest-quick` |

### Registro de pruebas con la placa

| Fecha | Montaje | Prueba | Resultado |
|---|---|---|---|
| 2026-11-08 | Leonardo con firmware 2.0.0, L298N, motor 24BYJ48A de 12 V y adaptador de 12 V 1.5 A | Contrato (`make hw`) | **16/16 pasan** (140 s). Vuelta completa en 12.00 s |
| 2026-11-08 | Igual | `directivity sweep` hasta 90° con pasos de 30°, 10°, 5° y 1° | Ángulos idénticos a la simulación; diferencia ≤ 0.086° con lo pedido; regreso a 0° |
| 2026-11-08 | Igual | `selftest` rápido y completo | **5/5 ok** (20.3 s y 54.3 s). Latencia p95 de 1.03 ms; 5.863 ms por paso (+0.07 %); error del patrón de 0.017 dB |

Sigue pendiente la calibración física de los pasos por vuelta.

## Próximos pasos

- [x] Probar el firmware en la placa (`pytest -m hardware`): 16/16 el 2026-11-08.
- [ ] Calibrar `STEPS_PER_REV` con una vuelta física marcada (el motor es un 24BYJ48A, no un 28BYJ-48).
- [x] Automatizar la medición en cada ángulo: barrido senoidal, respuesta al impulso, compuerta y niveles por banda de octava (en simulación).
- [ ] Adaptador de audio real (p. ej. `sounddevice`) para medir con la placa; el contrato de `AudioIO` ya está escrito.
- [ ] Graficar diagramas polares y mapa de directividad, y calcular el índice de directividad (DI).
- [ ] Validar con una fuente de directividad conocida.

## Autor

Sebastian Carvalho Salazar
