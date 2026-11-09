# Atajos de desarrollo y pruebas de DirectivityTurntable.
# Usa siempre las herramientas de $(VENV)/bin, así que no hace falta activar el venv.
# Los targets de la placa autodetectan el puerto; para otro: make hw PORT=/dev/tty.usbmodemXXXX

VENV ?= .venv
BIN  := $(VENV)/bin
PORT ?= $(firstword $(wildcard /dev/tty.usbmodem*))
CU_PORT = $(subst /dev/tty.,/dev/cu.,$(PORT))
# lsof devuelve 1 si alguno de los archivos no está abierto, así que se mira su salida.
FQBN := arduino:avr:leonardo
SKETCH := firmware/DirectivityTurntable

.DEFAULT_GOAL := help
.PHONY: help check lint typecheck test cov format sim ports sweep hw selftest selftest-quick \
        firmware upload _port

help: ## Lista los targets disponibles
	@grep -E '^[a-zA-Z_-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "} {printf "  make %-16s %s\n", $$1, $$2}'
	@echo "  Puerto detectado: $(if $(PORT),$(PORT),(ninguno))"

# ---------------------------------------------------------------- sin placa
check: lint typecheck test ## Estilo, formato, tipos y tests (lo mismo que la CI)

lint: ## ruff: estilo, docstrings y formato
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

typecheck: ## mypy en modo estricto
	$(BIN)/mypy

test: ## Todos los tests sin placa
	$(BIN)/pytest

cov: ## Tests con reporte de cobertura
	$(BIN)/pytest --cov=directivity --cov-report=term-missing

format: ## Corrige formato y lint automáticamente
	$(BIN)/ruff format .
	$(BIN)/ruff check . --fix

sim: ## Barrido y medición con la mesa simulada
	$(BIN)/directivity sweep --simulate --step 5
	$(BIN)/directivity measure --simulate --source cardioid --step 10 --out data/raw

# ---------------------------------------------------------------- con placa
ports: ## Lista los puertos de la placa y quién los usa
	@ls /dev/tty.usbmodem* 2>/dev/null || echo "No hay ninguna placa conectada."
	@if [ -n "$(PORT)" ] && [ -n "$$(lsof "$(PORT)" "$(CU_PORT)" 2>/dev/null)" ]; then \
	  echo "Puerto OCUPADO por:"; lsof "$(PORT)" "$(CU_PORT)" 2>/dev/null | awk 'NR>1 {print "  " $$1 " (PID " $$2 ")"}'; \
	  echo "Si es serial-monitor, cierra el Monitor Serie del Arduino IDE."; \
	elif [ -n "$(PORT)" ]; then echo "Puerto libre: $(PORT)"; fi

_port:
	@if [ -z "$(PORT)" ]; then \
	  echo "error: no hay placa conectada; conéctala o usa PORT=/dev/tty.usbmodemXXXX"; exit 1; fi
	@if [ -n "$$(lsof "$(PORT)" "$(CU_PORT)" 2>/dev/null)" ]; then \
	  echo "error: el puerto $(PORT) está ocupado (¿Monitor Serie del Arduino IDE abierto?). Revisa con: make ports"; \
	  exit 1; fi

sweep: _port ## Prueba corta con la placa: 0° a 90° cada 30° (~9 s)
	$(BIN)/directivity sweep --port $(PORT) --stop 90 --step 30

selftest-quick: _port ## Autoprueba rápida con la placa (~20 s)
	$(BIN)/directivity selftest --port $(PORT) --quick

selftest: _port ## Autoprueba completa con la placa (~55 s)
	$(BIN)/directivity selftest --port $(PORT)

hw: _port ## Contrato de la mesa contra la placa (~2.5 min)
	$(BIN)/pytest -m hardware --port=$(PORT) -v

# ---------------------------------------------------------------- firmware
firmware: ## Compila el firmware para Leonardo
	arduino-cli compile --fqbn $(FQBN) --warnings all $(SKETCH)

upload: _port ## Compila y carga el firmware en la placa
	arduino-cli compile --fqbn $(FQBN) $(SKETCH)
	arduino-cli upload -p $(CU_PORT) --fqbn $(FQBN) $(SKETCH)
