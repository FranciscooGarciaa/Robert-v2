"""ROS 2 node: republishes Robert's LiDAR as sensor_msgs/LaserScan on /scan.

The Robert backend owns the serial port; this node polls its HTTP API, so
both can run at once and ROS can live on the Pi or on another machine.

    python3 robert_laser_bridge.py --url http://localhost:8000/api/lidar/scan

Conventions (ROS REP 103): x forward, y left, angles counter-clockwise,
distances in metres. The backend's bins are clockwise from the front, so
they are mirrored here.
"""

import argparse
import json
import math
import urllib.request

import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan


class LaserBridge(Node):
    def __init__(self, url: str, frame_id: str, hz: float):
        super().__init__("robert_laser_bridge")
        self.url = url
        self.frame_id = frame_id
        self.pub = self.create_publisher(LaserScan, "scan", 10)
        self.last = None
        self.create_timer(1 / hz, self.tick)

    def tick(self) -> None:
        try:
            with urllib.request.urlopen(self.url, timeout=0.5) as r:
                bins = json.load(r).get("bins")
        except OSError as exc:
            self.get_logger().warn(f"backend unreachable: {exc}", throttle_duration_sec=5)
            return
        if not bins or bins == self.last:
            return  # no LiDAR, or the same revolution as last time
        self.last = bins

        msg = LaserScan()
        msg.header.stamp = self.get_clock().now().to_msg()
        msg.header.frame_id = self.frame_id
        msg.angle_min = 0.0
        msg.angle_increment = math.radians(1)
        msg.angle_max = math.radians(359)
        msg.scan_time = 0.1        # 10 Hz rotation
        msg.time_increment = 0.1 / 360
        msg.range_min = 0.05
        msg.range_max = 12.0
        # ROS angle i (CCW) == backend bin (360 - i) (CW); 0 mm means no return.
        msg.ranges = [
            bins[(360 - i) % 360] / 1000 if bins[(360 - i) % 360] else math.inf
            for i in range(360)
        ]
        self.pub.publish(msg)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://localhost:8000/api/lidar/scan")
    ap.add_argument("--frame-id", default="laser")
    ap.add_argument("--hz", type=float, default=20.0, help="poll rate (> scan rate)")
    args = ap.parse_args()
    rclpy.init()
    node = LaserBridge(args.url, args.frame_id, args.hz)
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.try_shutdown()


if __name__ == "__main__":
    main()
