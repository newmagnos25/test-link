"""
dashboard.py - Servidor web FastAPI com dashboard em tempo real

Servidor que integra WiFiScanner, MotionDetector e fornece:
- API REST para consultas
- WebSocket para updates em tempo real
- Dashboard web interativo
"""

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request

from .collector import WiFiScanner, WiFiNetwork
from .detector import MotionDetector, ZoneMapper, Zone, DetectionEvent
from .utils import (
    setup_logging,
    load_config,
    save_config,
    get_default_config_path,
    format_timestamp,
)


# Gerenciador de conexões WebSocket
class ConnectionManager:
    """Gerencia conexões WebSocket para broadcast de eventos."""

    def __init__(self):
        self.active_connections: Set[WebSocket] = set()
        self.logger = logging.getLogger("wallsense.websocket")

    async def connect(self, websocket: WebSocket):
        """Aceita e registra nova conexão."""
        await websocket.accept()
        self.active_connections.add(websocket)
        self.logger.info(f"Nova conexão WebSocket. Total: {len(self.active_connections)}")

    def disconnect(self, websocket: WebSocket):
        """Remove conexão."""
        self.active_connections.discard(websocket)
        self.logger.info(f"Conexão WebSocket fechada. Total: {len(self.active_connections)}")

    async def broadcast(self, message: dict):
        """Envia mensagem para todas as conexões ativas."""
        if not self.active_connections:
            return

        # Serializa mensagem
        message_json = json.dumps(message)

        # Envia para todas as conexões
        dead_connections = set()
        for connection in self.active_connections:
            try:
                await connection.send_text(message_json)
            except Exception as e:
                self.logger.error(f"Erro ao enviar para WebSocket: {e}")
                dead_connections.add(connection)

        # Remove conexões mortas
        for conn in dead_connections:
            self.disconnect(conn)


# Sistema WallSense
class WallSenseSystem:
    """Sistema principal que integra todos os componentes."""

    def __init__(self):
        self.logger = setup_logging(log_level="INFO", verbose=False)
        self.logger.info("Inicializando WallSense System...")

        # Carrega configurações
        self.config = self._load_configurations()

        # Componentes principais
        self.scanner = WiFiScanner(
            scan_interval=self.config["router"]["scan_interval"],
            history_size=self.config["router"]["history_size"],
            logger=self.logger
        )

        self.detector = MotionDetector(
            threshold=self.config["router"]["detection_threshold"],
            sensitivity=self.config["router"]["sensitivity"],
            logger=self.logger
        )

        self.zone_mapper = ZoneMapper(logger=self.logger)
        self._load_zones()

        # Estado do sistema
        self.is_running = False
        self.is_calibrated = False
        self.monitoring_task: Optional[asyncio.Task] = None

        # Gerenciador de conexões WebSocket
        self.connection_manager = ConnectionManager()

        # Estatísticas
        self.start_time = datetime.now()
        self.total_scans = 0
        self.total_events = 0

        self.logger.info("WallSense System inicializado com sucesso")

    def _load_configurations(self) -> Dict:
        """Carrega todas as configurações."""
        config = {}

        # Router config
        try:
            router_path = get_default_config_path("router_config.json")
            config["router"] = load_config(router_path)
        except FileNotFoundError:
            self.logger.warning("router_config.json não encontrado, usando template")
            template_path = get_default_config_path("router_config.template.json")
            config["router"] = load_config(template_path)

        # Telegram config
        try:
            telegram_path = get_default_config_path("telegram_config.json")
            config["telegram"] = load_config(telegram_path)
        except FileNotFoundError:
            self.logger.warning("telegram_config.json não encontrado")
            config["telegram"] = {"enabled": False}

        return config

    def _load_zones(self):
        """Carrega configuração de zonas."""
        try:
            zones_path = get_default_config_path("zones_config.json")
            zones_config = load_config(zones_path)
        except FileNotFoundError:
            self.logger.warning("zones_config.json não encontrado, usando template")
            template_path = get_default_config_path("zones_config.template.json")
            zones_config = load_config(template_path)

        # Cria zonas
        for zone_data in zones_config.get("zones", []):
            zone = Zone(
                id=zone_data["id"],
                name=zone_data["name"],
                position=tuple(zone_data["position"]),
                devices=zone_data.get("devices", [])
            )
            self.zone_mapper.add_zone(zone)

    async def calibrate(self, duration: int = 30) -> Dict:
        """
        Executa calibração do sistema.

        Args:
            duration: Duração da calibração em segundos

        Returns:
            Resultado da calibração
        """
        self.logger.info(f"Iniciando calibração ({duration}s)...")

        await self.connection_manager.broadcast({
            "type": "calibration_started",
            "duration": duration,
            "message": "Aguarde... Calibrando sistema. Evite movimento!"
        })

        calibration_samples = {}
        start_time = datetime.now()

        # Coleta amostras durante o período de calibração
        while (datetime.now() - start_time).total_seconds() < duration:
            networks = self.scanner.scan_networks()

            for network in networks:
                if network.ssid not in calibration_samples:
                    calibration_samples[network.ssid] = []
                calibration_samples[network.ssid].append(network.rssi)

            await asyncio.sleep(self.config["router"]["scan_interval"])

        # Calibra detector
        self.detector.calibrate(calibration_samples, duration)
        self.is_calibrated = True

        result = {
            "success": True,
            "networks_calibrated": len(calibration_samples),
            "samples_collected": sum(len(v) for v in calibration_samples.values()),
            "duration": duration
        }

        await self.connection_manager.broadcast({
            "type": "calibration_complete",
            "data": result
        })

        self.logger.info(f"Calibração concluída: {result['networks_calibrated']} redes")
        return result

    async def start_monitoring(self):
        """Inicia monitoramento contínuo."""
        if self.is_running:
            self.logger.warning("Monitoramento já está em execução")
            return

        self.is_running = True
        self.logger.info("Iniciando monitoramento contínuo...")

        await self.connection_manager.broadcast({
            "type": "system_status",
            "status": "running"
        })

        try:
            while self.is_running:
                # Escaneia redes
                networks = self.scanner.scan_networks()
                self.total_scans += 1

                if not networks:
                    await asyncio.sleep(self.config["router"]["scan_interval"])
                    continue

                # Adiciona ao histórico do scanner
                self.scanner.add_to_history(networks)

                # Processa cada rede
                readings = {net.ssid: net.rssi for net in networks}
                events = self.detector.process_batch(readings)

                # Se detectou movimento
                if events:
                    self.total_events += len(events)

                    for event in events:
                        # Tenta detectar zona
                        self.zone_mapper.detect_zone(event)

                        # Broadcast via WebSocket
                        await self.connection_manager.broadcast({
                            "type": "motion_detected",
                            "event": event.to_dict()
                        })

                        self.logger.info(
                            f"🚨 Movimento: {event.ssid} | "
                            f"Zona: {event.zone or 'Desconhecida'} | "
                            f"Confiança: {event.confidence:.0f}%"
                        )

                # Envia atualização de status periódica
                if self.total_scans % 10 == 0:
                    await self._send_status_update(networks)

                await asyncio.sleep(self.config["router"]["scan_interval"])

        except asyncio.CancelledError:
            self.logger.info("Monitoramento cancelado")
        except Exception as e:
            self.logger.error(f"Erro no monitoramento: {e}", exc_info=True)
        finally:
            self.is_running = False

    async def stop_monitoring(self):
        """Para monitoramento."""
        if not self.is_running:
            return

        self.logger.info("Parando monitoramento...")
        self.is_running = False

        if self.monitoring_task and not self.monitoring_task.done():
            self.monitoring_task.cancel()

        await self.connection_manager.broadcast({
            "type": "system_status",
            "status": "stopped"
        })

    async def _send_status_update(self, networks: List[WiFiNetwork]):
        """Envia atualização de status para clientes."""
        zones_data = [zone.to_dict() for zone in self.zone_mapper.get_all_zones()]

        await self.connection_manager.broadcast({
            "type": "status_update",
            "data": {
                "networks_detected": len(networks),
                "is_calibrated": self.is_calibrated,
                "total_scans": self.total_scans,
                "total_events": self.total_events,
                "zones": zones_data,
                "uptime": (datetime.now() - self.start_time).total_seconds()
            }
        })

    def get_statistics(self) -> Dict:
        """Retorna estatísticas do sistema."""
        uptime = (datetime.now() - self.start_time).total_seconds()

        return {
            "uptime_seconds": uptime,
            "is_running": self.is_running,
            "is_calibrated": self.is_calibrated,
            "total_scans": self.total_scans,
            "total_events": self.total_events,
            "scanner_stats": self.scanner.get_statistics(),
            "detector_stats": self.detector.get_statistics(),
            "zones_count": len(self.zone_mapper.zones),
            "start_time": format_timestamp(self.start_time),
        }


# Instância global do sistema
wallsense = WallSenseSystem()


# Lifecycle do FastAPI
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gerencia inicialização e shutdown do servidor."""
    logger = logging.getLogger("wallsense")
    logger.info("=== WallSense Server Starting ===")

    yield

    # Shutdown
    logger.info("=== WallSense Server Shutting Down ===")
    if wallsense.is_running:
        await wallsense.stop_monitoring()


# Aplicação FastAPI
app = FastAPI(
    title="WallSense",
    description="Sistema de Detecção de Movimento via WiFi",
    version="1.0.0",
    lifespan=lifespan
)

# Configuração de diretórios
BASE_DIR = Path(__file__).parent.parent
STATIC_DIR = BASE_DIR / "web" / "static"
TEMPLATES_DIR = BASE_DIR / "web" / "templates"

# Monta arquivos estáticos
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Templates Jinja2
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


# === ROTAS WEB ===

@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    """Página principal do dashboard."""
    return templates.TemplateResponse("index.html", {
        "request": request,
        "title": "WallSense Dashboard"
    })


# === API REST ===

@app.get("/api/status")
async def get_status():
    """Retorna status do sistema."""
    return {
        "status": "ok",
        "is_running": wallsense.is_running,
        "is_calibrated": wallsense.is_calibrated,
        "timestamp": format_timestamp()
    }


@app.get("/api/statistics")
async def get_statistics():
    """Retorna estatísticas detalhadas."""
    return wallsense.get_statistics()


@app.get("/api/zones")
async def get_zones():
    """Retorna informações de todas as zonas."""
    zones = wallsense.zone_mapper.get_all_zones()
    return {
        "zones": [zone.to_dict() for zone in zones],
        "total": len(zones)
    }


@app.get("/api/zones/{zone_id}")
async def get_zone(zone_id: str):
    """Retorna informações de uma zona específica."""
    zone = wallsense.zone_mapper.get_zone_by_id(zone_id)

    if not zone:
        raise HTTPException(status_code=404, detail="Zona não encontrada")

    return zone.to_dict()


@app.get("/api/events")
async def get_events(limit: int = 20):
    """Retorna histórico de eventos."""
    events = wallsense.detector.get_recent_events(limit)
    return {
        "events": [event.to_dict() for event in events],
        "total": len(events)
    }


@app.post("/api/calibrate")
async def start_calibration(duration: int = 30):
    """Inicia processo de calibração."""
    if wallsense.is_running:
        return {
            "success": False,
            "message": "Pare o monitoramento antes de calibrar"
        }

    result = await wallsense.calibrate(duration)
    return result


@app.post("/api/start")
async def start_monitoring():
    """Inicia monitoramento."""
    if not wallsense.is_calibrated:
        return {
            "success": False,
            "message": "Sistema não calibrado. Execute /api/calibrate primeiro"
        }

    if wallsense.is_running:
        return {
            "success": False,
            "message": "Monitoramento já está em execução"
        }

    # Inicia em background
    wallsense.monitoring_task = asyncio.create_task(wallsense.start_monitoring())

    return {
        "success": True,
        "message": "Monitoramento iniciado"
    }


@app.post("/api/stop")
async def stop_monitoring():
    """Para monitoramento."""
    await wallsense.stop_monitoring()

    return {
        "success": True,
        "message": "Monitoramento parado"
    }


@app.post("/api/sensitivity")
async def set_sensitivity(sensitivity: float):
    """Ajusta sensibilidade do detector."""
    if sensitivity < 0.5 or sensitivity > 2.0:
        raise HTTPException(
            status_code=400,
            detail="Sensibilidade deve estar entre 0.5 e 2.0"
        )

    wallsense.detector.set_sensitivity(sensitivity)

    return {
        "success": True,
        "sensitivity": sensitivity,
        "threshold": wallsense.detector.threshold
    }


@app.get("/api/networks")
async def get_networks():
    """Retorna redes WiFi detectadas."""
    networks = []

    for ssid, history in wallsense.scanner.history.items():
        if history:
            latest = history[-1]
            networks.append({
                "ssid": ssid,
                "bssid": latest.bssid,
                "rssi": latest.rssi,
                "timestamp": format_timestamp(latest.timestamp),
                "history_size": len(history)
            })

    return {
        "networks": networks,
        "total": len(networks)
    }


# === WEBSOCKET ===

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """Endpoint WebSocket para comunicação em tempo real."""
    await wallsense.connection_manager.connect(websocket)

    try:
        # Envia status inicial
        await websocket.send_json({
            "type": "connected",
            "message": "Conectado ao WallSense",
            "system_status": {
                "is_running": wallsense.is_running,
                "is_calibrated": wallsense.is_calibrated,
            }
        })

        # Loop de recebimento de mensagens
        while True:
            data = await websocket.receive_text()

            try:
                message = json.loads(data)

                # Processa comandos do cliente
                if message.get("command") == "ping":
                    await websocket.send_json({"type": "pong"})

                elif message.get("command") == "get_status":
                    stats = wallsense.get_statistics()
                    await websocket.send_json({
                        "type": "status",
                        "data": stats
                    })

            except json.JSONDecodeError:
                await websocket.send_json({
                    "type": "error",
                    "message": "JSON inválido"
                })

    except WebSocketDisconnect:
        wallsense.connection_manager.disconnect(websocket)
    except Exception as e:
        logging.error(f"Erro no WebSocket: {e}")
        wallsense.connection_manager.disconnect(websocket)


# === HEALTH CHECK ===

@app.get("/health")
async def health_check():
    """Endpoint de health check."""
    return {
        "status": "healthy",
        "service": "wallsense",
        "timestamp": format_timestamp()
    }


# === MAIN ===

def main():
    """Função principal para executar o servidor."""
    import uvicorn

    logger = logging.getLogger("wallsense")
    logger.info("Iniciando WallSense Server...")

    uvicorn.run(
        "src.dashboard:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
        log_level="info"
    )


if __name__ == "__main__":
    main()
