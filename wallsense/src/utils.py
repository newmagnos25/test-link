"""
utils.py - Funções auxiliares para o sistema WallSense

Contém utilitários para logging, configuração, validação e processamento de dados.
"""

import json
import logging
import os
import platform
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np


# Configuração de logging
def setup_logging(
    log_level: str = "INFO",
    log_file: Optional[str] = None,
    verbose: bool = False
) -> logging.Logger:
    """
    Configura o sistema de logging do WallSense.

    Args:
        log_level: Nível de log (DEBUG, INFO, WARNING, ERROR, CRITICAL)
        log_file: Caminho para arquivo de log (opcional)
        verbose: Se True, mostra logs mais detalhados

    Returns:
        Logger configurado
    """
    logger = logging.getLogger("wallsense")
    logger.setLevel(getattr(logging, log_level.upper()))

    # Formato do log
    if verbose:
        fmt = "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    else:
        fmt = "%(asctime)s - %(levelname)s - %(message)s"

    formatter = logging.Formatter(fmt, datefmt="%Y-%m-%d %H:%M:%S")

    # Handler para console
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # Handler para arquivo (se especificado)
    if log_file:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger


# Gerenciamento de configurações
def load_config(config_path: str) -> Dict[str, Any]:
    """
    Carrega arquivo de configuração JSON.

    Args:
        config_path: Caminho para o arquivo JSON

    Returns:
        Dicionário com configurações

    Raises:
        FileNotFoundError: Se arquivo não existe
        json.JSONDecodeError: Se JSON inválido
    """
    path = Path(config_path)

    if not path.exists():
        raise FileNotFoundError(f"Arquivo de configuração não encontrado: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        config = json.load(f)

    return config


def save_config(config: Dict[str, Any], config_path: str) -> None:
    """
    Salva configuração em arquivo JSON.

    Args:
        config: Dicionário com configurações
        config_path: Caminho para salvar o arquivo
    """
    path = Path(config_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def get_default_config_path(config_name: str) -> str:
    """
    Retorna o caminho padrão para um arquivo de configuração.

    Args:
        config_name: Nome do arquivo (ex: "router_config.json")

    Returns:
        Caminho completo para o arquivo
    """
    project_root = Path(__file__).parent.parent
    return str(project_root / "config" / config_name)


# Detecção de sistema operacional
def get_os_type() -> str:
    """
    Detecta o sistema operacional.

    Returns:
        "windows", "linux" ou "mac"
    """
    system = platform.system().lower()

    if system == "windows":
        return "windows"
    elif system == "linux":
        return "linux"
    elif system == "darwin":
        return "mac"
    else:
        return "unknown"


def is_admin() -> bool:
    """
    Verifica se o script está rodando com privilégios de administrador.

    Returns:
        True se admin/root, False caso contrário
    """
    if get_os_type() == "windows":
        try:
            import ctypes
            return ctypes.windll.shell32.IsUserAnAdmin() != 0
        except:
            return False
    else:
        return os.geteuid() == 0


# Processamento de dados
def moving_average(data: List[float], window_size: int = 5) -> List[float]:
    """
    Calcula média móvel de uma série de dados.

    Args:
        data: Lista de valores
        window_size: Tamanho da janela de média

    Returns:
        Lista com médias móveis
    """
    if len(data) < window_size:
        return data

    weights = np.ones(window_size) / window_size
    return list(np.convolve(data, weights, mode='valid'))


def calculate_variance(data: List[float]) -> float:
    """
    Calcula a variância de uma série de dados.

    Args:
        data: Lista de valores

    Returns:
        Variância dos dados
    """
    if len(data) < 2:
        return 0.0

    return float(np.var(data))


def calculate_std_deviation(data: List[float]) -> float:
    """
    Calcula o desvio padrão de uma série de dados.

    Args:
        data: Lista de valores

    Returns:
        Desvio padrão dos dados
    """
    if len(data) < 2:
        return 0.0

    return float(np.std(data))


def normalize_rssi(rssi: int) -> float:
    """
    Normaliza valor RSSI para escala 0-100.

    RSSI típico: -100 dBm (fraco) a -30 dBm (forte)

    Args:
        rssi: Valor RSSI em dBm

    Returns:
        Valor normalizado (0-100)
    """
    # Limita RSSI entre -100 e -30
    rssi = max(-100, min(-30, rssi))

    # Normaliza para 0-100
    normalized = ((rssi + 100) / 70) * 100

    return round(normalized, 2)


# Formatação e validação
def format_timestamp(dt: Optional[datetime] = None) -> str:
    """
    Formata timestamp para exibição.

    Args:
        dt: Objeto datetime (usa agora se None)

    Returns:
        String formatada (YYYY-MM-DD HH:MM:SS)
    """
    if dt is None:
        dt = datetime.now()

    return dt.strftime("%Y-%m-%d %H:%M:%S")


def validate_ssid(ssid: str) -> bool:
    """
    Valida se SSID é válido.

    Args:
        ssid: Nome da rede WiFi

    Returns:
        True se válido, False caso contrário
    """
    if not ssid or len(ssid) == 0:
        return False

    if len(ssid) > 32:  # SSID máximo é 32 caracteres
        return False

    return True


def validate_bssid(bssid: str) -> bool:
    """
    Valida se BSSID (MAC address) é válido.

    Args:
        bssid: Endereço MAC (formato XX:XX:XX:XX:XX:XX)

    Returns:
        True se válido, False caso contrário
    """
    import re

    # Formato MAC: XX:XX:XX:XX:XX:XX ou XX-XX-XX-XX-XX-XX
    pattern = r'^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$'

    return bool(re.match(pattern, bssid))


# Utilitários de arquivo
def ensure_directory(path: str) -> None:
    """
    Garante que um diretório existe, criando se necessário.

    Args:
        path: Caminho do diretório
    """
    Path(path).mkdir(parents=True, exist_ok=True)


def get_log_file_path(prefix: str = "wallsense") -> str:
    """
    Gera caminho para arquivo de log com timestamp.

    Args:
        prefix: Prefixo do nome do arquivo

    Returns:
        Caminho completo para arquivo de log
    """
    project_root = Path(__file__).parent.parent
    log_dir = project_root / "data" / "logs"
    ensure_directory(str(log_dir))

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{prefix}_{timestamp}.log"

    return str(log_dir / filename)


# Estatísticas
def calculate_signal_strength_category(rssi: int) -> str:
    """
    Categoriza força do sinal WiFi.

    Args:
        rssi: Valor RSSI em dBm

    Returns:
        Categoria: "excelente", "bom", "regular", "fraco"
    """
    if rssi >= -50:
        return "excelente"
    elif rssi >= -60:
        return "bom"
    elif rssi >= -70:
        return "regular"
    else:
        return "fraco"


def detect_anomaly(value: float, baseline: float, threshold: float = 10.0) -> bool:
    """
    Detecta se um valor é uma anomalia comparado ao baseline.

    Args:
        value: Valor atual
        baseline: Valor de referência (baseline)
        threshold: Limite de desvio para considerar anomalia

    Returns:
        True se anomalia detectada, False caso contrário
    """
    deviation = abs(value - baseline)
    return deviation > threshold


# Exportação de módulo
__all__ = [
    "setup_logging",
    "load_config",
    "save_config",
    "get_default_config_path",
    "get_os_type",
    "is_admin",
    "moving_average",
    "calculate_variance",
    "calculate_std_deviation",
    "normalize_rssi",
    "format_timestamp",
    "validate_ssid",
    "validate_bssid",
    "ensure_directory",
    "get_log_file_path",
    "calculate_signal_strength_category",
    "detect_anomaly",
]
