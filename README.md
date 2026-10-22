# Robert V2

Robot diferencial de 4 ruedas. Arquitectura de dos capas:

```
PWA (navegador) ──WS──▶ Raspberry Pi (pi/, FastAPI) ──WS──▶ ESP32 (firmware/) ──▶ MDD10A ──▶ motores
```

- **ESP32**: único que toca el driver. Watchdog 500 ms, parada por desconexión/Wi‑Fi,
  veto local de avance con HC‑SR04 (<15 cm), arranque siempre en STOP.
- **Pi**: árbitro de movimiento (dead‑man 300 ms, tope de velocidad), estado central, sirve la PWA.
- **PWA**: mantener pulsado para mover, soltar = parar. STOP grande.

> STOP (PWA/software) es una parada **lógica**. El corte físico es desconectar el **XT60**.

## Mapa de hardware validado (no cambiar sin medir)

| ESP32 | Destino |
|---|---|
| GPIO25 | MDD10A PWM1 (M1 = ruedas derechas) |
| GPIO26 | MDD10A DIR1 |
| GPIO27 | MDD10A PWM2 (M2 = ruedas izquierdas) |
| GPIO33 | MDD10A DIR2 |
| GPIO32 | HC‑SR04 TRIG |
| GPIO35 | HC‑SR04 ECHO **vía divisor 1k/2k** |

**Frente = lado del HC‑SR04.** Visto desde ahí: M2 = izquierda, M1 = derecha, adelante = DIR LOW en ambos
(validado en banco el 2026‑10‑22; el handoff original describía el robot desde el otro extremo).
Todo esto vive en `firmware/robert_motor/config.h`.

## Firmware ESP32

```sh
cp firmware/robert_motor/secrets.example.h firmware/robert_motor/secrets.h   # Wi-Fi + token
arduino-cli lib install WebSockets ArduinoJson
arduino-cli compile --fqbn esp32:esp32:esp32 firmware/robert_motor
# Flashear: LiPo DESCONECTADA, solo USB
arduino-cli upload  --fqbn esp32:esp32:esp32 -p /dev/cu.usbserial-0001 firmware/robert_motor
arduino-cli monitor -p /dev/cu.usbserial-0001 -c baudrate=115200
```

Actualizar por Wi‑Fi (OTA, sin USB; los motores se paran durante la subida):

```sh
arduino-cli compile --fqbn esp32:esp32:esp32 --output-dir build firmware/robert_motor
python3 ~/Library/Arduino15/packages/esp32/hardware/esp32/3.3.12/tools/espota.py \
  -i robert-motor.local -p 3232 -a "$ESP32_TOKEN" -f build/robert_motor.ino.bin
```

Operación: USB desconectado, LiPo conectada. El ESP32 anuncia `robert-motor.local`, WebSocket en el puerto 81.

## Backend Pi

```sh
cd pi && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
cp .env.example .env            # ESP32_TOKEN = CONTROL_TOKEN de secrets.h
.venv/bin/python -m pytest      # tests (incluye ESP32 simulado)
.venv/bin/python -m robert.main # http://localhost:8000
```

Sin hardware: `python -m robert.sim.fake_esp32 --port 8181 --token dev` y
`ESP32_HOST=127.0.0.1 ESP32_PORT=8181 ESP32_TOKEN=dev python -m robert.main`.

En la Pi corre como servicio systemd `robert` (`pi/deploy/robert.service`). Desplegar desde el Mac:

```sh
pi/scripts/deploy.sh     # rsync + pip + restart (sin sudo); PWA en http://robert.local:8000
ssh pi@robert.local journalctl -u robert -f
```

## Pruebas de banco (RUEDAS EN EL AIRE)

Desde el Mac, sin Pi (`export ESP32_TOKEN=...`):

```sh
pi/.venv/bin/python tools/esp32_cli.py monitor         # telemetría; motores quietos
pi/.venv/bin/python tools/esp32_cli.py bench           # adelante/atrás/giros a 80
pi/.venv/bin/python tools/esp32_cli.py watchdog        # deja de enviar -> STOP ≤500 ms
pi/.venv/bin/python tools/esp32_cli.py garbage         # basura -> no se mueve
pi/.venv/bin/python tools/esp32_cli.py drive 80 80 --seconds 5   # + objeto a <15 cm: para
pi/.venv/bin/python tools/esp32_cli.py drive -80 -80 --seconds 2 # con objeto delante: retrocede
```

Checklist:
1. Arranque: motores quietos, log `[BOOT] ready, motors STOPPED`.
2. Secuencia `bench`: las 4 ruedas en el sentido correcto.
3. `watchdog`: parada ≤500 ms con log `[SAFE] watchdog -> STOP`.
4. Apagar Wi‑Fi del Mac / matar el script durante `drive`: parada.
5. Obstáculo <15 cm: avance bloqueado; marcha atrás y giro en sitio siguen permitidos.
6. `garbage`: ningún movimiento.
7. Con la Pi: PWA, cerrar pestaña mientras se conduce → parada; `sudo systemctl stop robert` → parada por watchdog del ESP32.

## Protocolo Pi ↔ ESP32 (v1, JSON sobre WebSocket)

Pi→ESP32: `hello{v,token}`, `move{left,right}` (enteros −255..255, enviado a 10 Hz = heartbeat), `stop`, `heartbeat`.
ESP32→Pi: `hello_ack`, `telemetry` (5 Hz), `event` (cambios de failsafe), `error`.
Detalles en `firmware/robert_motor/protocol.h`.

## Pendiente / decisiones abiertas

- HC‑SR04 sin eco se trata como "despejado" (también si el sensor se desconecta); `us_timeouts` en telemetría lo hace visible.
- PWA por HTTP en LAN: no instalable como app (requiere HTTPS); funciona como página.
- Red autónoma (hotspot en la Pi), LiDAR, GPS, voz: después de validar el banco.
