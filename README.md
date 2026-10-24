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

## LiDAR (WitMotion D6 = Guoke COIN‑D6)

USB (adaptador CH340, `/dev/serial/by-id/usb-1a86_USB_Serial-if00-port0`), 230400 8N1. Gira y emite
al recibir corriente. El backend lo lee en un hilo (`pi/robert/robot/lidar.py`, formato de trama documentado
ahí), agrupa cada vuelta en 360 sectores de 1° en el marco del robot (0° = frente, sentido horario) y la PWA
la dibuja como radar. `GET /api/lidar/scan` devuelve la última vuelta. ROS 2 y cómo trabajar con el sensor: `docs/lidar-y-ros.md`.

Calibración guiada (máscara de la carcasa, frente y sentido): `tools/calibrate_lidar.py` (ver su ayuda).
La máscara se guarda en `pi/lidar_mask.json`; Explorar nunca conduce hacia un sector que el LiDAR no ve.
Solo un proceso puede abrir el puerto: para usarlo desde ROS, `LIDAR_ENABLED=false` y reiniciar el servicio.

## Navegación: frenada asistida y modo Explorar (`pi/robert/nav/`)

Control **reactivo** (sin mapa ni odometría: no hay encoders). Cada vuelta del LiDAR se convierte en puntos
del marco del robot (`scan.py`) y se barre la huella del robot + margen a lo largo del camino (`geometry.py`).

- **Guard** (`guard.py`): todo comando, manual o autónomo, pasa por `MotionController.current_target()`, que lo
  limita: frena entre la distancia de "frenar" y la de "parar" y corta solo la dirección peligrosa
  (marcha atrás o giro libres siguen permitidos). Sin LiDAR reciente: manual limitado a 80, autónomo parado.
- **Explorar** (`explore.py` + `planner.py` + `autonomy.py`): elige el pasillo más largo con poco giro e histéresis,
  y entre ~26 arcos + giros en el sitio escoge el mejor que puede parar a tiempo. Solo funciona mientras la PWA
  lo mantiene armado (botón Explorar, re‑enviado cada 100 ms); STOP, mover a mano, cerrar la pestaña, LiDAR
  ciego o atasco (>8 s sin avanzar) lo desactivan. Debajo siguen el dead‑man de la Pi y el ESP32.
- Coste medido en la Pi 4: ~7 ms por ciclo a 10 Hz.

Calibración (robot en el suelo, pared delante a ≥2 m, mano cerca del XT60):

```sh
python tools/calibrate_motion.py --host robert.local:8000 offset             # objeto justo delante
python tools/calibrate_motion.py --host robert.local:8000 forward --pwm 100  # -> MPS_PER_PWM
python tools/calibrate_motion.py --host robert.local:8000 spin --pwm 100 --mps-per-pwm <valor>  # -> TRACK_EFF_M
```
Medidas (2026‑10‑22): 325 × 160 mm, LiDAR en el centro (valores por defecto en `config.py`).
`tools/record_scans.py` graba vueltas reales (los tests reproducen `pi/tests/data/room.jsonl`).

**El LiDAR solo ve en su plano**: bordillos, escalones hacia abajo y objetos bajos quedan para el HC‑SR04
(fase de calle: inclinarlo hacia el suelo, ver plan).

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
- Reinstalar una Pi recién grabada: `pi/scripts/setup_pi.sh` (y tras reiniciar, `setup_pi.sh audio`).
- Pendiente: calibración en el suelo, red propia (hotspot), HC‑SR04 inclinado para bordillos, Seguirme (cámara + LiDAR; la cámara aún no se detecta), GPS, voz.
