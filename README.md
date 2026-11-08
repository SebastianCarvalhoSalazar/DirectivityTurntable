# DirectivityTurntable

Mesa giratoria automatizada para medir el **patrón de directividad (diagramas polares)** de parlantes y fuentes sonoras. Un Arduino gira el plato a ángulos precisos y el paquete de Python `directivity` coordina el giro con la medición: reproduce un barrido senoidal, obtiene la respuesta al impulso en cada ángulo y calcula el patrón polar por banda de octava.

## Hardware

| Componente | Función |
|---|---|
| Arduino Leonardo | Controla el motor y recibe órdenes por USB/serial |
| Puente H L298N | Driver de potencia del motor |
| Motor paso a paso 28BYJ-48 (12 V) | Gira el plato |
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

`tests/firmware/` compila el mismo `.ino` en el computador, con un sustituto de `Arduino.h` que imita la aritmética del AVR. Le envía órdenes por un "serial" simulado y compara la traza completa con el emulador de Python que usan los tests del cliente: respuestas, pasos del motor y pines. Así el firmware, el emulador y el cliente no pueden separarse sin que fallen los tests. Basta con tener un compilador de C++ (`c++`); si no hay, esos tests se saltan.

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

## Desarrollo

```bash
ruff check . && ruff format --check . && mypy && pytest
```

- `ruff` revisa el estilo y exige docstrings en estilo Google.
- `mypy` corre en modo estricto, así que todo el código tiene type hints.
- La CI de GitHub Actions corre lo mismo con Python 3.11, 3.12 y 3.13, y además compila el firmware.

### Tests con la placa

Los tests de contrato de la mesa corren siempre contra la mesa simulada y contra un emulador del firmware. Para correrlos contra la placa real, conéctala y ejecuta:

```bash
pytest -m hardware --port /dev/tty.usbmodemXXXX
```

El plato gira hasta ±360° y siempre termina en 0° con las bobinas apagadas. Tarda alrededor de un minuto.

| Fecha | Placa / firmware | Resultado |
|---|---|---|
| (pendiente) | Leonardo + `DirectivityTurntable.ino` | Aún no se corre en la placa |

## Próximos pasos

- [ ] Probar el firmware en la placa (`pytest -m hardware`) y calibrar `STEPS_PER_REV`.
- [x] Automatizar la medición en cada ángulo: barrido senoidal, respuesta al impulso, compuerta y niveles por banda de octava (en simulación).
- [ ] Adaptador de audio real (p. ej. `sounddevice`) para medir con la placa; el contrato de `AudioIO` ya está escrito.
- [ ] Graficar diagramas polares y mapa de directividad, y calcular el índice de directividad (DI).
- [ ] Validar con una fuente de directividad conocida.

## Autor

Sebastian Carvalho Salazar
