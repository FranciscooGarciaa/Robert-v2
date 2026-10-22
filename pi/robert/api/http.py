from fastapi import APIRouter, Request

router = APIRouter(prefix="/api")


@router.get("/state")
def get_state(request: Request) -> dict:
    return request.app.state.robot.snapshot()


@router.post("/stop")
def post_stop(request: Request) -> dict:
    request.app.state.motion.stop("http_stop")
    return {"ok": True}


@router.get("/health")
def health(request: Request) -> dict:
    return {"ok": True, "esp32_connected": request.app.state.robot.esp32.connected}


@router.get("/lidar/scan")
def lidar_scan(request: Request) -> dict:
    lidar = request.app.state.robot.lidar
    return {"connected": lidar.connected, "bins": lidar.bins}
