# DirectivityTurntable

Mesa giratoria automatizada para medir el **patrón de directividad (diagramas polares)** de parlantes y fuentes sonoras. Un Arduino gira el plato a ángulos precisos y un cliente en Python coordina el giro con la medición.

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
└── python/
    ├── turntable.py                                         # cliente serial + barrido de prueba
    └── requirements.txt
```

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

Si una orden falla, la respuesta empieza con `ERR`. El límite de ±360° evita que el cable del parlante se enrolle.

**Resolución:** con 2048 pasos por vuelta, cada paso equivale a unos 0,18°. Por eso `GOTO 5` puede devolver `OK 4.92`. Registra en tu medición el ángulo real que devuelve la mesa, no el ángulo que pediste.

### Posición absoluta

Un giro de 5° equivale a 28,44 pasos, que no es un número entero. Si cada giro se redondeara por separado, el error se acumularía: en una vuelta completa de 72 giros de 5° se perderían unos 5,6°.

Para evitarlo, el firmware lleva la posición absoluta en pasos y redondea cada destino contra el ángulo absoluto, así que el error nunca supera medio paso. En una simulación de escritorio, 72 giros de 5° suman exactamente 2048 pasos (360,00°). Falta probarlo en la placa.

### Calibración

El firmware usa 2048 pasos por vuelta como valor nominal (`STEPS_PER_REV`). Para verificarlo en tu motor:

1. Marca una referencia en el plato.
2. Envía `HOME` y luego `GOTO 360`.
3. Si la marca no vuelve exactamente al inicio, ajusta `STEPS_PER_REV`.

### Cargar el firmware

1. Abre `firmware/DirectivityTurntable/DirectivityTurntable.ino` en el Arduino IDE.
2. Selecciona **Arduino Leonardo** y el puerto correcto.
3. Sube el sketch. La librería `Stepper` viene incluida con el IDE.

## Cliente en Python

```bash
cd python
pip install -r requirements.txt
python turntable.py --port /dev/tty.usbmodemXXXX --step 5      # barrido de prueba de 0° a 360°
```

Como librería:

```python
from turntable import Turntable

with Turntable("/dev/tty.usbmodemXXXX") as table:
    table.home()
    for angle in range(0, 361, 5):
        real = table.goto(angle)
        # aquí va la medición acústica en el ángulo `real`
    table.goto(0)
    table.release()
```

En macOS el puerto aparece como `/dev/tty.usbmodem...`. Puedes listarlo con `ls /dev/tty.usbmodem*`.

## Desarrollo

El código está migrando a `src/directivity/` (ver [docs/MIGRATION_PLAN.md](docs/MIGRATION_PLAN.md)). Requiere Python 3.11 o superior. En macOS el `python3` del sistema es 3.9, así que crea un entorno virtual con uno más nuevo (por ejemplo `brew install python@3.13`):

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
ruff check . && ruff format --check . && mypy && pytest
```

## Próximos pasos

- [ ] Probar el firmware en la placa y calibrar `STEPS_PER_REV`.
- [ ] Automatizar la medición en cada ángulo: barrido senoidal, grabación y respuesta al impulso.
- [ ] Generar diagramas polares por banda de octava, mapa de directividad e índice de directividad (DI).
- [ ] Validar con una fuente de directividad conocida.

## Autor

Sebastian Carvalho Salazar
