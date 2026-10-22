# LiDAR y ROS 2 con Robert — guía práctica

## 1. Cómo funciona un LiDAR 2D giratorio (el D6)

- **Medida dTOF** (direct Time of Flight): un láser infrarrojo emite un pulso y un
  fotodetector mide cuánto tarda en volver. `distancia = c · t / 2`. A 1 m el viaje de ida y
  vuelta dura ~6,7 ns, por eso el chip mide tiempos de picosegundos.
- **Giro**: el cabezal gira a ~10 vueltas/s y mide ~4000 veces/s → ~400 puntos por vuelta
  (≈0,9° entre puntos). A más distancia, más separación entre puntos: a 4 m, ~6 cm.
- **Salida**: cada medida es un par **(ángulo, distancia)**, coordenadas **polares**, más una
  **intensidad** (cuánta luz volvió; superficies negras o cristal devuelven poco o nada).

Limitaciones reales: no ve cristales ni espejos bien, tiene distancia mínima (~5 cm), solo ve
un **plano** (mesas y escalones por debajo/encima del plano son invisibles) y hay ruido de ±1–2 cm.

## 2. Del cable a los datos (lo que hace `pi/robert/robot/lidar.py`)

Es el patrón de casi cualquier sensor serie:

1. **Transporte**: UART 230400 baudios 8N1, convertido a USB por un CH340 → `/dev/ttyUSB0`.
   Llega un *chorro de bytes* sin separaciones.
2. **Encuadre (framing)**: buscar la cabecera `AA 55`, leer la longitud (`LSN` muestras × 3 bytes
   + 10 de cabecera) y cortar el paquete.
3. **Integridad**: comprobar el **checksum** (XOR de palabras de 16 bits). Si falla, era una
   falsa cabecera o un byte corrupto: se descarta y se **resincroniza** buscando la siguiente.
4. **Decodificación**: campos *little‑endian*; ángulo = `(raw >> 1) / 64` grados; distancia =
   `(b2 << 6) | (b1 >> 2)` mm. Solo vienen los ángulos inicial y final: los intermedios se
   **interpolan**.
5. **Agrupación**: un paquete especial (bit 0 de `CT` = 1) marca el inicio de cada vuelta.
   Se juntan los paquetes de una vuelta completa = un **scan**.
6. **Marco de referencia**: el 0° del sensor no tiene por qué ser el frente del robot. Se
   aplica giro (`LIDAR_OFFSET_DEG`) y sentido (`LIDAR_CLOCKWISE`).

Ejercicio: lee bytes en crudo tú mismo en la Pi (con el servicio parado o `LIDAR_ENABLED=false`):

```sh
stty -F /dev/ttyUSB0 230400 raw -echo
timeout 1 cat /dev/ttyUSB0 | od -An -tx1 | head      # verás 'aa 55 00 19 ...'
```

## 3. Conceptos de ROS 2 que necesitas

- **Nodo**: un proceso con una tarea (leer el LiDAR, hacer un mapa, decidir la ruta…).
- **Topic**: canal con nombre por el que los nodos publican/suscriben mensajes tipados.
  El LiDAR se publica en `/scan` con el tipo `sensor_msgs/msg/LaserScan`:
  `angle_min`, `angle_increment`, `ranges[]` (metros; `inf` = sin eco), `header.frame_id`, `header.stamp`.
- **TF**: árbol de marcos de coordenadas (`map → odom → base_link → laser`). Dice dónde está
  montado el sensor respecto al robot y dónde está el robot en el mundo.
- **Convenciones (REP 103)**: metros, radianes, x adelante, y izquierda, ángulos **antihorarios**.
  Nuestro backend usa grados horarios; el nodo puente hace la conversión.
- Herramientas: `ros2 topic list/echo/hz`, `ros2 node list`, visualización con **Foxglove** o RViz.

## 4. Práctica: ver el LiDAR de Robert en ROS 2

El backend sigue siendo el dueño del puerto serie; `ros/robert_laser_bridge.py` lee
`/api/lidar/scan` y publica `/scan`. Así no hay conflictos y ROS puede correr en la Pi o en el Mac.

La Pi usa Debian 13; ROS 2 solo da paquetes para Ubuntu, así que lo ejecutamos en **Docker**
(imagen oficial `ros:jazzy-ros-base`, ~1 GB en disco).

**Una vez** (en la Pi, pide tu contraseña de sudo):

```sh
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER      # luego sal y vuelve a entrar por SSH
```

**Cada sesión**:

```sh
docker run -it --rm --net=host -v ~/robert/ros:/ros --name ros ros:jazzy-ros-base bash
# dentro del contenedor:
apt update && apt install -y ros-jazzy-foxglove-bridge      # solo la primera vez por contenedor
python3 /ros/robert_laser_bridge.py &
ros2 run tf2_ros static_transform_publisher --frame-id base_link --child-frame-id laser &
ros2 launch foxglove_bridge foxglove_bridge_launch.xml
```

En otra terminal (`docker exec -it ros bash`, y dentro `source /opt/ros/jazzy/setup.bash`):

```sh
ros2 topic list            # /scan, /tf_static, ...
ros2 topic hz /scan        # ~10 Hz
ros2 topic echo /scan --once
```

Visualizar desde el Mac: abre <https://app.foxglove.dev> (o Lichtblick, alternativa libre),
"Open connection" → `ws://<ip-de-la-pi>:8765`, añade un panel **3D** y activa `/scan`.

## 5. Programar con los datos: ideas para practicar

Escribe un nodo que se suscriba a `/scan`:

```python
import math, rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan

class Nearest(Node):
    def __init__(self):
        super().__init__("nearest")
        self.create_subscription(LaserScan, "scan", self.on_scan, 10)

    def on_scan(self, s: LaserScan):
        best = min(((r, i) for i, r in enumerate(s.ranges) if math.isfinite(r)), default=None)
        if best:
            r, i = best
            ang = math.degrees(s.angle_min + i * s.angle_increment)
            self.get_logger().info(f"más cercano: {r:.2f} m a {ang:.0f}° (antihorario)")

rclpy.init(); rclpy.spin(Nearest())
```

Siguientes ejercicios, de menos a más:

1. **Polar → cartesiano**: `x = r·cos(θ)`, `y = r·sin(θ)`. Dibuja los puntos.
2. **Sectores**: distancia mínima delante (±30°), izquierda, derecha y detrás.
3. **Filtrado**: mediana entre vueltas para quitar puntos sueltos; descarta intensidad baja.
4. **Detectar paredes**: ajuste de rectas (RANSAC) a los puntos.
5. **Evitar obstáculos**: si el sector frontal < 40 cm, girar hacia el sector más libre.
6. **SLAM** (mapa + localización): `slam_toolbox`. Necesita **odometría** (cuánto se ha movido
   el robot); Robert no tiene encoders, así que habría que estimarla con el propio LiDAR
   (p. ej. `rf2o_laser_odometry`) o añadir encoders/IMU. Con 1 GB de RAM en la Pi, el SLAM
   conviene ejecutarlo en el Mac.

## 6. Seguridad

Ahora el LiDAR **solo se visualiza**: no frena al robot. Si en el futuro un nodo ROS envía
movimiento, debe pasar por el backend (dead‑man de 300 ms) y el ESP32 sigue teniendo la
última palabra (watchdog de 500 ms y veto del HC‑SR04). Pruebas de autonomía: ruedas en el aire.
