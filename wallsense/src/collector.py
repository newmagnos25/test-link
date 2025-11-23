"""
collector.py - Coleta dados de WiFi para detecção de movimento

Classe WiFiScanner que escaneia redes WiFi e coleta dados de RSSI
Suporta Windows (netsh) e Linux (iwlist/nmcli)
"""

import asyncio
import logging
import re
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Callable

from .utils import get_os_type, validate_ssid, validate_bssid, format_timestamp


@dataclass
class WiFiNetwork:
    """Representa uma rede WiFi detectada."""

    ssid: str
    bssid: str
    rssi: int
    channel: Optional[int] = None
    frequency: Optional[int] = None
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()

    def to_dict(self) -> dict:
        """Converte para dicionário."""
        return {
            "ssid": self.ssid,
            "bssid": self.bssid,
            "rssi": self.rssi,
            "channel": self.channel,
            "frequency": self.frequency,
            "timestamp": format_timestamp(self.timestamp),
        }


class WiFiScanner:
    """
    Scanner de redes WiFi com suporte multi-plataforma.

    Escaneia redes WiFi disponíveis e mantém histórico de leituras RSSI
    para detecção de variações causadas por movimento.
    """

    def __init__(
        self,
        scan_interval: float = 1.0,
        history_size: int = 60,
        logger: Optional[logging.Logger] = None
    ):
        """
        Inicializa o scanner WiFi.

        Args:
            scan_interval: Intervalo entre scans em segundos
            history_size: Tamanho do buffer de histórico
            logger: Logger personalizado (cria um se None)
        """
        self.scan_interval = scan_interval
        self.history_size = history_size
        self.logger = logger or logging.getLogger("wallsense.collector")

        self.os_type = get_os_type()
        self.is_running = False
        self.scan_task: Optional[asyncio.Task] = None

        # Buffer de histórico: {ssid: deque([WiFiNetwork, ...])}
        self.history: Dict[str, deque] = {}

        # Callback para novos dados
        self.on_scan_callback: Optional[Callable] = None

        # Estatísticas
        self.total_scans = 0
        self.failed_scans = 0
        self.last_scan_time: Optional[datetime] = None

        self.logger.info(f"WiFiScanner inicializado para {self.os_type}")

    def set_callback(self, callback: Callable) -> None:
        """
        Define callback para quando novos dados são coletados.

        Args:
            callback: Função a ser chamada com lista de redes detectadas
        """
        self.on_scan_callback = callback

    def scan_networks(self) -> List[WiFiNetwork]:
        """
        Escaneia redes WiFi disponíveis.

        Returns:
            Lista de redes WiFi detectadas

        Raises:
            RuntimeError: Se não conseguir escanear
        """
        self.logger.debug(f"Iniciando scan WiFi ({self.os_type})...")

        try:
            if self.os_type == "windows":
                networks = self._scan_windows()
            elif self.os_type == "linux":
                networks = self._scan_linux()
            elif self.os_type == "mac":
                networks = self._scan_mac()
            else:
                raise RuntimeError(f"Sistema operacional não suportado: {self.os_type}")

            self.total_scans += 1
            self.last_scan_time = datetime.now()

            self.logger.debug(f"Scan completo: {len(networks)} redes encontradas")
            return networks

        except Exception as e:
            self.failed_scans += 1
            self.logger.error(f"Erro ao escanear redes WiFi: {e}")
            return []

    def _scan_windows(self) -> List[WiFiNetwork]:
        """Escaneia redes WiFi no Windows usando netsh."""
        networks = []

        try:
            # Força um novo scan
            subprocess.run(
                ["netsh", "wlan", "scan"],
                capture_output=True,
                timeout=5,
                check=False
            )

            # Aguarda o scan completar
            time.sleep(0.5)

            # Obtém lista de redes
            result = subprocess.run(
                ["netsh", "wlan", "show", "networks", "mode=bssid"],
                capture_output=True,
                text=True,
                encoding="cp850",  # Windows usa codepage 850
                timeout=5,
                check=True
            )

            output = result.stdout

            # Parse do output
            current_ssid = None
            current_bssid = None
            current_rssi = None
            current_channel = None

            for line in output.split('\n'):
                line = line.strip()

                # SSID
                if line.startswith("SSID"):
                    match = re.search(r"SSID\s+\d+\s+:\s+(.+)", line)
                    if match:
                        current_ssid = match.group(1).strip()

                # BSSID (MAC)
                elif "BSSID" in line and ":" in line:
                    match = re.search(r"BSSID\s+\d+\s+:\s+([0-9a-fA-F:]+)", line)
                    if match:
                        current_bssid = match.group(1).strip()

                # Sinal (%)
                elif "Sinal" in line or "Signal" in line:
                    match = re.search(r"(\d+)%", line)
                    if match:
                        signal_percent = int(match.group(1))
                        # Converte % para dBm aproximado
                        current_rssi = self._percent_to_dbm(signal_percent)

                # Canal
                elif "Canal" in line or "Channel" in line:
                    match = re.search(r":\s+(\d+)", line)
                    if match:
                        current_channel = int(match.group(1))

                # Quando temos todos os dados, cria o objeto
                if current_ssid and current_bssid and current_rssi is not None:
                    if validate_ssid(current_ssid) and validate_bssid(current_bssid):
                        network = WiFiNetwork(
                            ssid=current_ssid,
                            bssid=current_bssid,
                            rssi=current_rssi,
                            channel=current_channel
                        )
                        networks.append(network)

                    # Reset para próxima rede
                    current_ssid = None
                    current_bssid = None
                    current_rssi = None
                    current_channel = None

        except subprocess.TimeoutExpired:
            self.logger.warning("Timeout ao executar netsh")
        except subprocess.CalledProcessError as e:
            self.logger.error(f"Erro ao executar netsh: {e}")
        except Exception as e:
            self.logger.error(f"Erro inesperado no scan Windows: {e}")

        return networks

    def _scan_linux(self) -> List[WiFiNetwork]:
        """Escaneia redes WiFi no Linux usando nmcli ou iwlist."""
        # Tenta nmcli primeiro (mais moderno)
        try:
            result = subprocess.run(
                ["nmcli", "-t", "-f", "SSID,BSSID,SIGNAL,CHAN", "dev", "wifi", "list"],
                capture_output=True,
                text=True,
                timeout=5,
                check=True
            )

            return self._parse_nmcli_output(result.stdout)

        except (FileNotFoundError, subprocess.CalledProcessError):
            self.logger.debug("nmcli não disponível, tentando iwlist...")
            return self._scan_linux_iwlist()

    def _scan_linux_iwlist(self) -> List[WiFiNetwork]:
        """Escaneia usando iwlist (fallback para Linux antigo)."""
        networks = []

        try:
            # Tenta detectar interface WiFi
            interfaces = ["wlan0", "wlp2s0", "wlp3s0", "wifi0"]
            interface = None

            for iface in interfaces:
                try:
                    subprocess.run(
                        ["iwconfig", iface],
                        capture_output=True,
                        timeout=2,
                        check=True
                    )
                    interface = iface
                    break
                except:
                    continue

            if not interface:
                self.logger.warning("Nenhuma interface WiFi encontrada")
                return []

            # Executa scan
            result = subprocess.run(
                ["sudo", "iwlist", interface, "scan"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )

            return self._parse_iwlist_output(result.stdout)

        except Exception as e:
            self.logger.error(f"Erro no scan iwlist: {e}")
            return []

    def _parse_nmcli_output(self, output: str) -> List[WiFiNetwork]:
        """Parse da saída do nmcli."""
        networks = []

        for line in output.strip().split('\n'):
            if not line:
                continue

            parts = line.split(':')
            if len(parts) >= 3:
                ssid = parts[0].strip()
                bssid = parts[1].strip()
                signal = parts[2].strip()

                try:
                    rssi = int(signal)
                    channel = int(parts[3]) if len(parts) > 3 else None

                    # nmcli retorna sinal em % ou dBm dependendo da versão
                    if rssi > 0:  # É porcentagem
                        rssi = self._percent_to_dbm(rssi)

                    if validate_ssid(ssid) and validate_bssid(bssid):
                        network = WiFiNetwork(
                            ssid=ssid,
                            bssid=bssid,
                            rssi=rssi,
                            channel=channel
                        )
                        networks.append(network)

                except ValueError:
                    continue

        return networks

    def _parse_iwlist_output(self, output: str) -> List[WiFiNetwork]:
        """Parse da saída do iwlist."""
        networks = []
        current_network = {}

        for line in output.split('\n'):
            line = line.strip()

            # BSSID/MAC
            if "Address:" in line:
                match = re.search(r"Address:\s+([0-9A-Fa-f:]+)", line)
                if match:
                    current_network["bssid"] = match.group(1)

            # SSID
            elif "ESSID:" in line:
                match = re.search(r'ESSID:"(.+?)"', line)
                if match:
                    current_network["ssid"] = match.group(1)

            # Sinal
            elif "Signal level" in line:
                match = re.search(r"Signal level=(-?\d+)", line)
                if match:
                    current_network["rssi"] = int(match.group(1))

            # Canal
            elif "Channel:" in line:
                match = re.search(r"Channel:(\d+)", line)
                if match:
                    current_network["channel"] = int(match.group(1))

            # Fim de uma célula
            if "Cell" in line and current_network:
                if all(k in current_network for k in ["ssid", "bssid", "rssi"]):
                    network = WiFiNetwork(**current_network)
                    networks.append(network)
                current_network = {}

        # Adiciona última rede
        if all(k in current_network for k in ["ssid", "bssid", "rssi"]):
            network = WiFiNetwork(**current_network)
            networks.append(network)

        return networks

    def _scan_mac(self) -> List[WiFiNetwork]:
        """Escaneia redes WiFi no macOS usando airport."""
        networks = []

        try:
            # Path do utilitário airport no macOS
            airport_path = "/System/Library/PrivateFrameworks/Apple80211.framework/Versions/Current/Resources/airport"

            result = subprocess.run(
                [airport_path, "-s"],
                capture_output=True,
                text=True,
                timeout=10,
                check=True
            )

            # Parse do output
            for line in result.stdout.split('\n')[1:]:  # Pula header
                if not line.strip():
                    continue

                parts = line.split()
                if len(parts) >= 3:
                    ssid = parts[0]
                    bssid = parts[1]
                    rssi = int(parts[2])

                    if validate_ssid(ssid) and validate_bssid(bssid):
                        network = WiFiNetwork(ssid=ssid, bssid=bssid, rssi=rssi)
                        networks.append(network)

        except Exception as e:
            self.logger.error(f"Erro no scan macOS: {e}")

        return networks

    def _percent_to_dbm(self, percent: int) -> int:
        """
        Converte porcentagem de sinal para dBm aproximado.

        Args:
            percent: Sinal em % (0-100)

        Returns:
            RSSI em dBm (-100 a -30)
        """
        # Fórmula aproximada: 0% = -100dBm, 100% = -30dBm
        return int(-100 + (percent * 0.7))

    def get_rssi(self, ssid: str) -> Optional[int]:
        """
        Obtém RSSI mais recente de um SSID específico.

        Args:
            ssid: Nome da rede WiFi

        Returns:
            RSSI em dBm ou None se não encontrado
        """
        if ssid in self.history and len(self.history[ssid]) > 0:
            return self.history[ssid][-1].rssi
        return None

    def get_rssi_history(self, ssid: str, limit: Optional[int] = None) -> List[int]:
        """
        Obtém histórico de RSSI de um SSID.

        Args:
            ssid: Nome da rede WiFi
            limit: Limite de resultados (None = todos)

        Returns:
            Lista de valores RSSI
        """
        if ssid not in self.history:
            return []

        history = list(self.history[ssid])
        if limit:
            history = history[-limit:]

        return [net.rssi for net in history]

    def add_to_history(self, networks: List[WiFiNetwork]) -> None:
        """
        Adiciona redes ao histórico.

        Args:
            networks: Lista de redes para adicionar
        """
        for network in networks:
            if network.ssid not in self.history:
                self.history[network.ssid] = deque(maxlen=self.history_size)

            self.history[network.ssid].append(network)

    async def start_monitoring(self) -> None:
        """Inicia monitoramento contínuo em background."""
        if self.is_running:
            self.logger.warning("Monitoramento já está em execução")
            return

        self.is_running = True
        self.logger.info(f"Iniciando monitoramento WiFi (intervalo: {self.scan_interval}s)")

        try:
            while self.is_running:
                # Escaneia redes
                networks = self.scan_networks()

                # Adiciona ao histórico
                if networks:
                    self.add_to_history(networks)

                    # Chama callback se configurado
                    if self.on_scan_callback:
                        try:
                            if asyncio.iscoroutinefunction(self.on_scan_callback):
                                await self.on_scan_callback(networks)
                            else:
                                self.on_scan_callback(networks)
                        except Exception as e:
                            self.logger.error(f"Erro no callback: {e}")

                # Aguarda próximo scan
                await asyncio.sleep(self.scan_interval)

        except asyncio.CancelledError:
            self.logger.info("Monitoramento cancelado")
        except Exception as e:
            self.logger.error(f"Erro no monitoramento: {e}")
        finally:
            self.is_running = False

    def stop_monitoring(self) -> None:
        """Para monitoramento contínuo."""
        if not self.is_running:
            self.logger.warning("Monitoramento não está em execução")
            return

        self.logger.info("Parando monitoramento WiFi...")
        self.is_running = False

        if self.scan_task and not self.scan_task.done():
            self.scan_task.cancel()

    def get_statistics(self) -> Dict:
        """
        Retorna estatísticas do scanner.

        Returns:
            Dicionário com estatísticas
        """
        return {
            "total_scans": self.total_scans,
            "failed_scans": self.failed_scans,
            "success_rate": (
                (self.total_scans - self.failed_scans) / self.total_scans * 100
                if self.total_scans > 0
                else 0
            ),
            "networks_tracked": len(self.history),
            "last_scan": format_timestamp(self.last_scan_time) if self.last_scan_time else None,
            "is_running": self.is_running,
        }

    def clear_history(self, ssid: Optional[str] = None) -> None:
        """
        Limpa histórico de scans.

        Args:
            ssid: SSID específico para limpar (None = limpa tudo)
        """
        if ssid:
            if ssid in self.history:
                self.history[ssid].clear()
                self.logger.info(f"Histórico limpo para {ssid}")
        else:
            self.history.clear()
            self.logger.info("Todo histórico limpo")


# Função auxiliar para teste rápido
async def test_scanner():
    """Função de teste do scanner."""
    import sys
    from .utils import setup_logging

    logger = setup_logging(log_level="DEBUG", verbose=True)

    scanner = WiFiScanner(scan_interval=2.0, logger=logger)

    # Callback de exemplo
    def on_scan(networks):
        print(f"\n{'='*60}")
        print(f"Scan completo: {len(networks)} redes encontradas")
        print(f"{'='*60}")

        for net in networks[:5]:  # Mostra apenas 5 primeiras
            print(f"  {net.ssid:30} | {net.bssid:17} | {net.rssi:4} dBm")

    scanner.set_callback(on_scan)

    try:
        await scanner.start_monitoring()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário")
        scanner.stop_monitoring()


if __name__ == "__main__":
    # Executa teste
    asyncio.run(test_scanner())
