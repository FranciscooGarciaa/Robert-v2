# Guía de montaje eléctrico de Robert V2

El esquema de conexiones de Robert V2, pensado para que puedas replicarlo sin tener que
interpretar nada. Junto con las piezas de [`cad/`](../cad/README.md) y el código del repositorio,
es todo lo necesario para construirlo desde cero. Los pines y sentidos de giro de esta guía son
los validados en el robot (`firmware/robert_motor/config.h`); si tu montaje difiere, cambia
`config.h`, no el cableado a ciegas.

## 1. Arquitectura general

```text
                         ┌──────────────────────────────┐
                         │       RASPBERRY PI 4         │
                         │                              │
                         │  ReSpeaker 2-Mics HAT        │
                         │  LiDAR por USB               │
                         │  Cámara (CSI)                │
                         │  Voz / visión / navegación   │
                         └──────────────┬───────────────┘
                                        │
                                      Wi-Fi
                                        │
                         ┌──────────────▼───────────────┐
                         │            ESP32             │
                         │                              │
                         │ GPIO25 → PWM1                │
                         │ GPIO26 → DIR1                │
                         │ GPIO27 → PWM2                │
                         │ GPIO33 → DIR2                │
                         │ GPIO32 → HC-SR04 TRIG        │
                         │ GPIO35 ← HC-SR04 ECHO        │
                         └──────────────┬───────────────┘
                                        │
                         ┌──────────────▼───────────────┐
                         │        CYTRON MDD10A         │
                         │                              │
                         │ M1 → motores derechos        │
                         │ M2 → motores izquierdos      │
                         └──────────────────────────────┘
```

La Raspberry es el **cerebro de alto nivel** y el ESP32 es el **controlador físico** de motores
y del sensor de seguridad.

## 2. Alimentación

```text
                    LiPo 3S
                   11,1 V nominal
                   12,6 V cargada
                         │
             ┌───────────┴────────────┐
             │                        │
             ▼                        ▼
       Cytron MDD10A               LM2596
       alimentación                ajustado
       de motores                  a ~5,0 V
                                      │
                              ┌───────┴────────┐
                              │                │
                              ▼                ▼
                            ESP32           PAM8403
                              │
                              ▼
                           HC-SR04
```

La **Raspberry Pi no se alimenta de esa línea**:

```text
Powerbank 5 V
     │
     ▼
Raspberry Pi 4
```

Así los motores y el amplificador no provocan caídas de tensión en la Raspberry.

> **Lección aprendida:** no alimentes el PAM8403 desde otro puerto del powerbank de la
> Raspberry. Al arrancar, el códec del ReSpeaker sin inicializar hace que el amplificador
> suelte un pico de ruido, el powerbank corta **todas** sus salidas un instante y la Raspberry
> se apaga a mitad de arranque. A nosotros nos corrompió dos tarjetas SD. El PAM8403 va siempre
> desde la LiPo, a través del LM2596.

### LiPo

```text
LiPo +
 ├── MDD10A VIN+  (por el XT60)
 └── LM2596 IN+

LiPo -
 ├── MDD10A VIN-
 └── LM2596 IN-
```

El conector **XT60** es el corte de emergencia físico: el botón STOP de la app es lógico, el
XT60 desconectado es definitivo. La alarma de batería LiPo va directamente al **conector de
balance** de la batería.

## 3. LM2596

Ajústalo con un multímetro **antes** de conectar ninguna electrónica:

```text
OUT+ ≈ 5,0 V
OUT- = GND
```

Conexiones:

```text
LM2596 OUT+
 ├── ESP32 VIN
 └── PAM8403 5V+

LM2596 OUT-
 ├── ESP32 GND
 └── PAM8403 5V-
```

Nuestro ajuste quedó en unos **5,03 V**.

> Para programar el ESP32 por USB, **desconecta la LiPo**: sólo USB. Una vez instalado el
> firmware se actualiza por Wi-Fi (OTA) y no hace falta volver a abrir el robot.

## 4. ESP32 → Cytron MDD10A

El pinout probado físicamente en Robert:

| ESP32 | MDD10A | Función |
|---|---|---|
| GPIO25 | PWM1 | Velocidad lado **derecho** (M1) |
| GPIO26 | DIR1 | Dirección lado derecho |
| GPIO27 | PWM2 | Velocidad lado **izquierdo** (M2) |
| GPIO33 | DIR2 | Dirección lado izquierdo |
| GND | GND | Tierra común |

En la regleta del MDD10A el orden es:

```text
GND | PWM2 | DIR2 | PWM1 | DIR1
```

**El frontal del robot es el lado del HC-SR04.** Mirando el robot desde delante, M2 es el lado
izquierdo y M1 el derecho.

### Dirección que funciona en Robert

```text
ADELANTE:
DIR1 = LOW
DIR2 = LOW

ATRÁS:
DIR1 = HIGH
DIR2 = HIGH
```

El PWM controla la velocidad (0-255). Si en tu montaje un lado gira al revés, intercambia los
dos cables de ese canal del MDD10A, o cambia `config.h`.

## 5. MDD10A → cuatro motores

Robert lleva cuatro motores, pero eléctricamente funcionan como **dos grupos**, uno por lado,
con sus dos motores **en paralelo**:

```text
MDD10A

M1A ─────┬── Motor delantero derecho
         └── Motor trasero derecho

M1B ─────┬── Motor delantero derecho
         └── Motor trasero derecho


M2A ─────┬── Motor delantero izquierdo
         └── Motor trasero izquierdo

M2B ─────┬── Motor delantero izquierdo
         └── Motor trasero izquierdo
```

Es decir:

```text
M1 = lado derecho
M2 = lado izquierdo
```

Si al construirlo uno de los motores gira al revés que su pareja, intercambia sus dos cables.

## 6. HC-SR04 → ESP32

| HC-SR04 | Conexión |
|---|---|
| VCC | 5 V |
| GND | GND |
| TRIG | ESP32 GPIO32 |
| ECHO | ESP32 GPIO35 **mediante divisor resistivo** |

Muy importante: el `ECHO` del HC-SR04 entrega unos **5 V** y el ESP32 trabaja a **3,3 V**.
No se conecta directamente.

```text
HC-SR04 ECHO
      │
     1 kΩ
      │
      ├────────→ GPIO35 ESP32
      │
     2 kΩ
      │
     GND
```

```text
5 V × 2k / (1k + 2k) ≈ 3,33 V
```

El TRIG sí va directo:

```text
GPIO32 → TRIG
```

El GPIO35 es sólo de entrada, perfecto para el ECHO. El propio ESP32 usa este sensor para
**vetar el avance** si hay un obstáculo delante, diga lo que diga la Raspberry (marcha atrás
sigue permitida).

## 7. Raspberry Pi 4

La Raspberry se encarga de:

```text
voz e IA
visión (cámara + detección de personas)
LiDAR y guardián de colisiones
navegación (explorar, seguirte)
interfaz web (la app del móvil)
comunicación con el ESP32
memoria
```

No controla directamente los motores:

```text
Raspberry Pi
      │
    Wi-Fi
      │
      ▼
    ESP32
      │
      ▼
   MDD10A
      │
      ▼
   Motores
```

Así el ESP32 mantiene la seguridad del robot aunque falle la IA, el programa o Internet.

## 8. ReSpeaker 2-Mics Pi HAT V2.0

Va pinchado directamente sobre el GPIO de la Raspberry Pi:

```text
Raspberry Pi GPIO Header
          │
          ▼
ReSpeaker 2-Mics Pi HAT V2.0
```

Por eso **no usamos los GPIO de la Raspberry para hablar con el ESP32**: la comunicación
Raspberry ↔ ESP32 va por Wi-Fi.

El ReSpeaker aporta:

```text
2 micrófonos
entrada de audio
salida de audio (jack 3,5 mm)
```

Necesita su overlay en `/boot/firmware/config.txt`; `pi/scripts/setup_pi.sh` lo compila e
instala, y `setup_pi.sh audio` deja los niveles validados.

## 9. ReSpeaker → PAM8403

Usamos la salida de audio de 3,5 mm del ReSpeaker. En el cable que utilizamos:

```text
TIP        = Left
RING 1     = Right
RING 2     = Ground
SLEEVE     = Mic / sin usar
```

En nuestro cable concreto:

```text
verde  = Left
azul   = Right
cobre  = GND
rojo   = Mic / no conectado
```

Conexión:

```text
ReSpeaker LEFT
      │
      └────────→ PAM8403 L IN

ReSpeaker RIGHT
      │
      └────────→ PAM8403 R IN

ReSpeaker GND
      │
      └────────→ PAM8403 INPUT GND
```

El cable del micrófono se aísla:

```text
MIC → NO CONNECT
```

Importante: **los colores cambian según el cable**. Comprueba TIP/RING/GND con un multímetro
en modo continuidad antes de soldar.

## 10. PAM8403 → altavoces

Robert usa dos altavoces de 3 W:

```text
PAM8403

L OUT+ ─────→ Altavoz izquierdo +
L OUT- ─────→ Altavoz izquierdo -

R OUT+ ─────→ Altavoz derecho +
R OUT- ─────→ Altavoz derecho -
```

MUY IMPORTANTE:

```text
L OUT- ≠ GND
R OUT- ≠ GND
```

El PAM8403 tiene salidas en puente (BTL). Nunca conectes:

```text
L- → GND   ❌
R- → GND   ❌
L- + R-    ❌
```

Cada altavoz lleva sus dos cables independientes.

## 11. LiDAR → Raspberry Pi

El LiDAR va a la Raspberry, no al ESP32:

```text
WitMotion D6 (DTOF LiDAR)
        │
        ▼
Adaptador USB-UART (en el nuestro, un CH340)
        │
        ▼
Raspberry Pi USB
```

Conceptualmente:

```text
LiDAR TX → USB-UART RX
LiDAR RX → USB-UART TX
LiDAR GND → USB-UART GND
LiDAR alimentación → según el adaptador/módulo
```

Funciona a **230400 baudios, 8N1**, y empieza a girar y emitir datos al recibir corriente, sin
ningún comando. En Linux aparece como un puerto serie. No uses `/dev/ttyUSB0` fijo, porque
puede cambiar entre arranques: usa la ruta estable de `/dev/serial/by-id/…` y ponla en
`LIDAR_PORT` (`pi/.env`).

No publicamos el pinout interno del LiDAR (VCC/TX/RX) porque depende del adaptador USB-UART
exacto que uses: sigue la hoja del tuyo.

Después del montaje hay que calibrar hacia dónde mira (`LIDAR_OFFSET_DEG`, `LIDAR_CLOCKWISE`)
con `tools/calibrate_lidar.py`.

## 12. Cámara

Robert usa una **cámara OV5647 (5 MP)** por el conector **CSI** de la Raspberry:

```text
Cámara OV5647
    │
    ▼
Conector CSI (contactos del cable hacia los HDMI)
    │
    ▼
Raspberry Pi
```

No se detecta sola: `config.txt` necesita `camera_auto_detect=0` y `dtoverlay=ov5647`
(`setup_pi.sh` lo hace).

La cámara tiene dos usos:

```text
SEGUIRTE (todo en local, sin Internet)
Cámara → YOLO11n en la Raspberry → "hay una persona ahí"
       → el LiDAR la fija y la sigue → comando de velocidad
       → guardián de colisiones → ESP32 → MDD10A → motores

MIRAR (cuando le preguntas "¿qué ves?")
Cámara → foto → la IA de voz (Gemini o ChatGPT) → te la describe
```

La IA de voz **no conduce**: sólo puede pedir acciones acotadas (parar, un movimiento de un
segundo, seguirte, bailar…) y todas pasan por el guardián de colisiones antes de llegar a los
motores.

## 13. Comunicación Raspberry ↔ ESP32

No hay cable de datos entre ambos:

```text
Raspberry Pi
     │
   Wi-Fi
     │
   Router / hotspot del móvil
     │
   Wi-Fi
     │
    ESP32
```

En casa, los dos en la misma Wi-Fi. En la calle usamos el **hotspot del móvil** (en 2,4 GHz;
en iPhone, "Maximizar compatibilidad"): ESP32, Raspberry y el móvil con la app en la misma red.
Las redes se listan en `firmware/robert_motor/secrets.h`; un hotspot propio de la Raspberry
está previsto, pero todavía no.

El ESP32 escucha en un **WebSocket en el puerto 81** y habla JSON. Lo primero que manda la
Raspberry es un saludo con el token compartido (`CONTROL_TOKEN` en `secrets.h`, `ESP32_TOKEN`
en `pi/.env`):

```json
{ "type": "hello", "v": 1, "token": "..." }
```

Después, la velocidad de cada lado (-255 a 255), diez veces por segundo:

```json
{ "type": "move", "left": 180, "right": 180 }
```

y, para parar en seco:

```json
{ "type": "stop" }
```

El protocolo completo está en el [README](../README.md#protocol-v1-json-over-websocket-pi--esp32).

## 14. Failsafe

Esto es lo más importante si vas a construirlo, y el firmware ya lo hace por ti:

- El ESP32 **arranca siempre con los motores parados**.
- Tiene un **watchdog**: la Raspberry manda `move` cada 100 ms (10 Hz), y si pasan **más de
  500 ms** sin un comando válido, el ESP32 **para los motores**.

```text
Raspberry envía move cada 100 ms
                ↓
ESP32 lo recibe
                ↓
si pasan >500 ms sin comando
                ↓
STOP MOTORS
```

Así, si ocurre cualquiera de estas cosas:

```text
cae el Wi-Fi
la Raspberry se reinicia
el programa se bloquea
la IA falla
la app del móvil se desconecta
```

Robert se detiene. También para si recibe un mensaje mal formado, si se conecta otro
controlador o durante una actualización OTA. Y el veto del HC-SR04 (sección 6) funciona en el
propio ESP32, sin depender de la Raspberry.

## 15. Esquema completo

```text
                             ┌────────────────────┐
                             │     POWERBANK      │
                             │       5 V          │
                             └─────────┬──────────┘
                                       │
                                       ▼
                             ┌────────────────────┐
                             │  RASPBERRY PI 4    │
                             │                    │
                             │ Voz / IA           │
                             │ Visión             │
                             │ Navegación         │
                             └───┬───────┬────┬───┘
                                 │       │    │
                      GPIO HAT   │       │    │ USB
                                 │       │    ▼
                                 ▼       │  LiDAR
                          ┌──────────┐   │
                          │ReSpeaker │   │ CSI
                          │ 2-Mics   │   ▼
                          └────┬─────┘  Cámara
                               │ audio
                               ▼
                            PAM8403
                          ┌────┴────┐
                          ▼         ▼
                       Altavoz   Altavoz
                        izq.      der.

                 Raspberry ── Wi-Fi ──┐
                                      │
                                      ▼
                    ┌──────────────┐
                    │    ESP32     │
                    │              │
                    │25 → PWM1     │
                    │26 → DIR1     │
                    │27 → PWM2     │
                    │33 → DIR2     │
                    │32 → TRIG     │
                    │35 ← ECHO     │
                    └───┬──────┬───┘
                        │      │
                        │      ▼
                        │   HC-SR04
                        │
                        ▼
                  ┌───────────────┐
                  │ Cytron MDD10A │
                  │               │
                  │ M2        M1  │
                  └─┬─────────┬───┘
                    │         │
            ┌───────┴──┐   ┌──┴───────┐
            ▼          ▼   ▼          ▼
          MOTOR      MOTOR MOTOR      MOTOR
          del.izq  tras.izq del.der  tras.der


                  LiPo 3S (12,6 V máx.)
                        │ XT60
              ┌─────────┴────────┐
              │                  │
              ▼                  ▼
           MDD10A             LM2596
                                 │
                               5,0 V
                                 │
                     ┌───────────┴──────────┐
                     ▼                      ▼
                   ESP32                  PAM8403
```

## Lista de materiales eléctricos

| Componente | Función |
|---|---|
| Raspberry Pi 4 (1 GB) | Ordenador principal |
| ESP32 NodeMCU / ESP-WROOM-32 | Control de motores y sensores |
| Cytron MDD10A Rev 2.0 | Driver doble de motores |
| 4× DT25-370 12 V ~300 RPM | Motores |
| LiPo 3S | Alimentación de motores y electrónica |
| LM2596 | 12,6 V → 5 V |
| HC-SR04 | Obstáculos frontales |
| Resistencia 1 kΩ | Divisor del ECHO |
| Resistencia 2 kΩ | Divisor del ECHO |
| WitMotion D6 (DTOF LiDAR) | Escáner de 360° |
| Adaptador USB-UART | LiDAR → Raspberry |
| ReSpeaker 2-Mics Pi HAT V2.0 | Micrófonos y audio |
| PAM8403 | Amplificador estéreo |
| 2× altavoz 3 W | Voz |
| Cámara OV5647 (CSI) | Visión |
| Powerbank 5 V | Raspberry Pi |
| Alarma de LiPo | Protección de la batería |
| XT60 | Conexión de la LiPo y corte de emergencia |
| Cable 16 AWG | Potencia principal |
| Cable 22 AWG | Señal, audio y cargas pequeñas |

## Siguientes pasos

1. Imprime las piezas de [`cad/`](../cad/README.md).
2. Cablea siguiendo esta guía y comprueba cada tensión con el multímetro antes de conectar.
3. Copia `firmware/robert_motor/secrets.example.h` a `secrets.h` y flashea el ESP32 (LiPo
   desconectada).
4. Prepara la Raspberry con `pi/scripts/setup_pi.sh` y rellena `pi/.env` (tu clave de IA
   incluida): ver ["Build your own"](../README.md#build-your-own) en el README.
5. Calibra el LiDAR y el movimiento ([Calibration](../README.md#calibration)).
