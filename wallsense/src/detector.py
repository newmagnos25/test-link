"""
detector.py - Algoritmo de detecção de movimento via WiFi

Implementa detecção de movimento baseada em variações de RSSI com:
- Calibração automática de baseline
- Filtros de suavização (Butterworth)
- Detecção de anomalias
- Mapeamento de zonas
"""

import logging
from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy import signal

from .utils import (
    calculate_std_deviation,
    calculate_variance,
    detect_anomaly,
    format_timestamp,
    moving_average,
)


@dataclass
class DetectionEvent:
    """Representa um evento de movimento detectado."""

    timestamp: datetime
    ssid: str
    rssi_current: int
    rssi_baseline: float
    deviation: float
    zone: Optional[str] = None
    confidence: float = 0.0

    def to_dict(self) -> dict:
        """Converte para dicionário."""
        return {
            "timestamp": format_timestamp(self.timestamp),
            "ssid": self.ssid,
            "rssi_current": self.rssi_current,
            "rssi_baseline": round(self.rssi_baseline, 2),
            "deviation": round(self.deviation, 2),
            "zone": self.zone,
            "confidence": round(self.confidence, 2),
        }


@dataclass
class Zone:
    """Representa uma zona física da casa."""

    id: str
    name: str
    position: Tuple[float, float]  # Coordenadas (x, y)
    devices: List[str] = field(default_factory=list)  # Lista de BSSIDs
    active: bool = False
    last_motion: Optional[datetime] = None

    def to_dict(self) -> dict:
        """Converte para dicionário."""
        return {
            "id": self.id,
            "name": self.name,
            "position": list(self.position),
            "devices": self.devices,
            "active": self.active,
            "last_motion": format_timestamp(self.last_motion) if self.last_motion else None,
        }


class MotionDetector:
    """
    Detector de movimento baseado em variações de RSSI.

    Usa algoritmo de detecção de anomalias com baseline calibrado
    e filtros de suavização para reduzir falsos positivos.
    """

    def __init__(
        self,
        threshold: float = 10.0,
        sensitivity: float = 1.0,
        filter_order: int = 3,
        filter_cutoff: float = 0.1,
        history_size: int = 60,
        logger: Optional[logging.Logger] = None
    ):
        """
        Inicializa o detector de movimento.

        Args:
            threshold: Desvio mínimo para considerar movimento (dBm)
            sensitivity: Multiplicador de sensibilidade (0.5-2.0)
            filter_order: Ordem do filtro Butterworth
            filter_cutoff: Frequência de corte do filtro (normalizada)
            history_size: Tamanho do histórico para análise
            logger: Logger personalizado
        """
        self.threshold = threshold * sensitivity
        self.sensitivity = sensitivity
        self.filter_order = filter_order
        self.filter_cutoff = filter_cutoff
        self.history_size = history_size
        self.logger = logger or logging.getLogger("wallsense.detector")

        # Baselines calibrados: {ssid: baseline_rssi}
        self.baselines: Dict[str, float] = {}

        # Histórico de leituras: {ssid: deque([rssi, ...])}
        self.rssi_history: Dict[str, deque] = {}

        # Estado de calibração
        self.is_calibrated = False
        self.calibration_samples: Dict[str, List[int]] = defaultdict(list)

        # Eventos detectados
        self.events: deque = deque(maxlen=100)

        # Estatísticas
        self.total_detections = 0
        self.false_positives = 0

        # Filtro Butterworth
        self.butter_b, self.butter_a = signal.butter(
            self.filter_order,
            self.filter_cutoff,
            btype='low',
            analog=False
        )

        self.logger.info(f"MotionDetector inicializado (threshold={self.threshold:.1f} dBm)")

    def calibrate(self, samples: Dict[str, List[int]], duration: int = 30) -> None:
        """
        Calibra o detector com amostras de ambiente sem movimento.

        Args:
            samples: Dicionário {ssid: [rssi1, rssi2, ...]}
            duration: Duração da calibração em segundos
        """
        self.logger.info(f"Iniciando calibração com {duration}s de amostras...")

        for ssid, rssi_values in samples.items():
            if len(rssi_values) < 10:
                self.logger.warning(f"Poucas amostras para {ssid}, ignorando")
                continue

            # Calcula baseline (média)
            baseline = float(np.mean(rssi_values))
            self.baselines[ssid] = baseline

            # Inicializa histórico
            if ssid not in self.rssi_history:
                self.rssi_history[ssid] = deque(maxlen=self.history_size)

            # Calcula estatísticas
            std_dev = calculate_std_deviation(rssi_values)
            variance = calculate_variance(rssi_values)

            self.logger.info(
                f"  {ssid}: baseline={baseline:.2f} dBm, "
                f"std={std_dev:.2f}, var={variance:.2f}"
            )

        self.is_calibrated = True
        self.logger.info(f"Calibração concluída: {len(self.baselines)} redes configuradas")

    def auto_calibrate(self, ssid: str, rssi_values: List[int]) -> None:
        """
        Calibração automática para um SSID específico.

        Args:
            ssid: Nome da rede
            rssi_values: Lista de valores RSSI para calibração
        """
        if len(rssi_values) < 5:
            return

        baseline = float(np.mean(rssi_values))
        self.baselines[ssid] = baseline

        self.logger.info(f"Auto-calibração para {ssid}: baseline={baseline:.2f} dBm")

    def process_reading(self, ssid: str, rssi: int) -> Tuple[bool, Optional[DetectionEvent]]:
        """
        Processa uma leitura de RSSI e detecta movimento.

        Args:
            ssid: Nome da rede
            rssi: Valor RSSI atual

        Returns:
            Tupla (movimento_detectado, evento)
        """
        # Adiciona ao histórico
        if ssid not in self.rssi_history:
            self.rssi_history[ssid] = deque(maxlen=self.history_size)
        self.rssi_history[ssid].append(rssi)

        # Se não calibrado para este SSID, faz auto-calibração
        if ssid not in self.baselines:
            if len(self.rssi_history[ssid]) >= 10:
                self.auto_calibrate(ssid, list(self.rssi_history[ssid]))
            return False, None

        baseline = self.baselines[ssid]

        # Aplica filtro se temos histórico suficiente
        filtered_rssi = rssi
        if len(self.rssi_history[ssid]) >= self.filter_order * 2:
            try:
                filtered_data = self.apply_filter(list(self.rssi_history[ssid]))
                if filtered_data:
                    filtered_rssi = int(filtered_data[-1])
            except Exception as e:
                self.logger.debug(f"Erro ao aplicar filtro: {e}")

        # Detecta anomalia
        deviation = abs(filtered_rssi - baseline)
        is_motion = detect_anomaly(filtered_rssi, baseline, self.threshold)

        if is_motion:
            # Calcula confiança baseada no desvio
            confidence = min(100.0, (deviation / self.threshold) * 100)

            event = DetectionEvent(
                timestamp=datetime.now(),
                ssid=ssid,
                rssi_current=rssi,
                rssi_baseline=baseline,
                deviation=deviation,
                confidence=confidence,
            )

            self.events.append(event)
            self.total_detections += 1

            self.logger.info(
                f"🚨 MOVIMENTO DETECTADO: {ssid} | "
                f"RSSI={rssi} dBm | Baseline={baseline:.1f} | "
                f"Desvio={deviation:.1f} | Confiança={confidence:.0f}%"
            )

            return True, event

        return False, None

    def process_batch(self, readings: Dict[str, int]) -> List[DetectionEvent]:
        """
        Processa múltiplas leituras de uma vez.

        Args:
            readings: Dicionário {ssid: rssi}

        Returns:
            Lista de eventos detectados
        """
        events = []

        for ssid, rssi in readings.items():
            is_motion, event = self.process_reading(ssid, rssi)
            if is_motion and event:
                events.append(event)

        return events

    def apply_filter(self, data: List[float]) -> List[float]:
        """
        Aplica filtro Butterworth para suavização.

        Args:
            data: Lista de valores RSSI

        Returns:
            Dados filtrados
        """
        if len(data) < self.filter_order * 3:
            return data

        try:
            # Aplica filtro Butterworth
            filtered = signal.filtfilt(self.butter_b, self.butter_a, data)
            return filtered.tolist()
        except Exception as e:
            self.logger.debug(f"Erro no filtro Butterworth: {e}")
            return data

    def extract_features(self, ssid: str) -> Dict:
        """
        Extrai features estatísticas do histórico de um SSID.

        Args:
            ssid: Nome da rede

        Returns:
            Dicionário com features extraídas
        """
        if ssid not in self.rssi_history or len(self.rssi_history[ssid]) < 5:
            return {}

        history = list(self.rssi_history[ssid])

        features = {
            "mean": float(np.mean(history)),
            "std": calculate_std_deviation(history),
            "variance": calculate_variance(history),
            "min": float(np.min(history)),
            "max": float(np.max(history)),
            "range": float(np.max(history) - np.min(history)),
            "median": float(np.median(history)),
        }

        # Adiciona baseline se disponível
        if ssid in self.baselines:
            features["baseline"] = self.baselines[ssid]
            features["deviation_from_baseline"] = abs(features["mean"] - self.baselines[ssid])

        return features

    def update_baseline(self, ssid: str, new_baseline: float) -> None:
        """
        Atualiza manualmente o baseline de um SSID.

        Args:
            ssid: Nome da rede
            new_baseline: Novo valor de baseline
        """
        old_baseline = self.baselines.get(ssid, 0)
        self.baselines[ssid] = new_baseline

        self.logger.info(
            f"Baseline atualizado para {ssid}: {old_baseline:.2f} → {new_baseline:.2f} dBm"
        )

    def set_sensitivity(self, sensitivity: float) -> None:
        """
        Ajusta a sensibilidade do detector.

        Args:
            sensitivity: Novo valor de sensibilidade (0.5-2.0)
                        0.5 = menos sensível, 2.0 = mais sensível
        """
        sensitivity = max(0.5, min(2.0, sensitivity))
        old_threshold = self.threshold

        self.sensitivity = sensitivity
        self.threshold = (self.threshold / self.sensitivity) * sensitivity

        self.logger.info(
            f"Sensibilidade ajustada: {self.sensitivity:.2f} "
            f"(threshold: {old_threshold:.1f} → {self.threshold:.1f} dBm)"
        )

    def get_recent_events(self, limit: int = 10) -> List[DetectionEvent]:
        """
        Retorna eventos recentes.

        Args:
            limit: Número máximo de eventos

        Returns:
            Lista de eventos mais recentes
        """
        events = list(self.events)
        return events[-limit:]

    def get_statistics(self) -> Dict:
        """
        Retorna estatísticas do detector.

        Returns:
            Dicionário com estatísticas
        """
        return {
            "is_calibrated": self.is_calibrated,
            "total_detections": self.total_detections,
            "false_positives": self.false_positives,
            "networks_tracked": len(self.baselines),
            "threshold": self.threshold,
            "sensitivity": self.sensitivity,
            "recent_events": len(self.events),
        }

    def reset(self) -> None:
        """Reseta o detector para estado inicial."""
        self.baselines.clear()
        self.rssi_history.clear()
        self.calibration_samples.clear()
        self.events.clear()
        self.is_calibrated = False
        self.total_detections = 0

        self.logger.info("Detector resetado")


class ZoneMapper:
    """
    Mapeia movimento para zonas físicas da casa.

    Usa triangulação simples baseada em RSSI de múltiplos
    pontos de acesso para determinar localização aproximada.
    """

    def __init__(
        self,
        zones: Optional[List[Zone]] = None,
        logger: Optional[logging.Logger] = None
    ):
        """
        Inicializa o mapeador de zonas.

        Args:
            zones: Lista de zonas configuradas
            logger: Logger personalizado
        """
        self.zones: Dict[str, Zone] = {}
        self.logger = logger or logging.getLogger("wallsense.zonemapper")

        if zones:
            for zone in zones:
                self.add_zone(zone)

        self.logger.info(f"ZoneMapper inicializado com {len(self.zones)} zonas")

    def add_zone(self, zone: Zone) -> None:
        """
        Adiciona uma zona ao mapeador.

        Args:
            zone: Zona a ser adicionada
        """
        self.zones[zone.id] = zone
        self.logger.info(f"Zona adicionada: {zone.name} ({zone.id})")

    def remove_zone(self, zone_id: str) -> None:
        """
        Remove uma zona do mapeador.

        Args:
            zone_id: ID da zona a remover
        """
        if zone_id in self.zones:
            zone = self.zones.pop(zone_id)
            self.logger.info(f"Zona removida: {zone.name}")

    def assign_device_to_zone(self, zone_id: str, bssid: str) -> None:
        """
        Associa um dispositivo (BSSID) a uma zona.

        Args:
            zone_id: ID da zona
            bssid: BSSID do dispositivo
        """
        if zone_id not in self.zones:
            self.logger.warning(f"Zona não encontrada: {zone_id}")
            return

        zone = self.zones[zone_id]
        if bssid not in zone.devices:
            zone.devices.append(bssid)
            self.logger.info(f"Dispositivo {bssid} associado à zona {zone.name}")

    def detect_zone(self, event: DetectionEvent) -> Optional[str]:
        """
        Detecta em qual zona ocorreu o movimento.

        Args:
            event: Evento de detecção

        Returns:
            ID da zona detectada ou None
        """
        # Procura zona por SSID associado
        for zone_id, zone in self.zones.items():
            if event.ssid in zone.devices:
                zone.active = True
                zone.last_motion = event.timestamp
                event.zone = zone_id

                self.logger.info(f"Movimento detectado na zona: {zone.name}")
                return zone_id

        return None

    def calculate_zone_by_rssi(
        self,
        rssi_readings: Dict[str, int],
        device_zones: Dict[str, str]
    ) -> Optional[str]:
        """
        Calcula zona mais provável baseada em múltiplas leituras RSSI.

        Usa triangulação simples: a zona com RSSI mais forte é a mais provável.

        Args:
            rssi_readings: Dicionário {ssid: rssi}
            device_zones: Mapeamento {ssid: zone_id}

        Returns:
            ID da zona mais provável
        """
        zone_scores: Dict[str, float] = defaultdict(float)

        for ssid, rssi in rssi_readings.items():
            if ssid in device_zones:
                zone_id = device_zones[ssid]
                # RSSI mais alto (menos negativo) = mais próximo
                # Normaliza para score positivo
                score = rssi + 100  # -30 dBm → 70, -80 dBm → 20
                zone_scores[zone_id] += score

        if not zone_scores:
            return None

        # Retorna zona com maior score
        best_zone = max(zone_scores.items(), key=lambda x: x[1])
        return best_zone[0]

    def get_active_zones(self) -> List[Zone]:
        """
        Retorna zonas com movimento ativo.

        Returns:
            Lista de zonas ativas
        """
        return [zone for zone in self.zones.values() if zone.active]

    def reset_zone_states(self) -> None:
        """Reseta estado de todas as zonas."""
        for zone in self.zones.values():
            zone.active = False

        self.logger.info("Estados de zonas resetados")

    def get_zone_by_id(self, zone_id: str) -> Optional[Zone]:
        """
        Obtém zona por ID.

        Args:
            zone_id: ID da zona

        Returns:
            Zona encontrada ou None
        """
        return self.zones.get(zone_id)

    def get_all_zones(self) -> List[Zone]:
        """
        Retorna todas as zonas.

        Returns:
            Lista de todas as zonas
        """
        return list(self.zones.values())

    def to_dict(self) -> Dict:
        """
        Converte todas as zonas para dicionário.

        Returns:
            Dicionário com todas as zonas
        """
        return {
            zone_id: zone.to_dict()
            for zone_id, zone in self.zones.items()
        }


# Função auxiliar para teste
def test_detector():
    """Função de teste do detector."""
    from .utils import setup_logging

    logger = setup_logging(log_level="INFO", verbose=True)

    # Cria detector
    detector = MotionDetector(threshold=8.0, logger=logger)

    # Simula calibração
    calibration_data = {
        "MyNetwork": [-65, -66, -65, -64, -66, -65, -65, -66, -64, -65] * 3
    }
    detector.calibrate(calibration_data)

    # Simula leituras normais
    print("\n=== Leituras Normais ===")
    for rssi in [-65, -66, -65, -64, -66]:
        detector.process_reading("MyNetwork", rssi)

    # Simula movimento (variação grande)
    print("\n=== Simulando Movimento ===")
    for rssi in [-55, -50, -48, -52, -54]:
        is_motion, event = detector.process_reading("MyNetwork", rssi)
        if is_motion:
            print(f"  ⚡ Evento: {event.to_dict()}")

    # Mostra estatísticas
    print(f"\n=== Estatísticas ===")
    stats = detector.get_statistics()
    for key, value in stats.items():
        print(f"  {key}: {value}")


if __name__ == "__main__":
    test_detector()
