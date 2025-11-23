"""
test_detector.py - Testes unitários para o MotionDetector

Testa funcionalidades principais do sistema de detecção
"""

import unittest
from datetime import datetime

import sys
from pathlib import Path

# Adiciona src ao path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.detector import MotionDetector, ZoneMapper, Zone, DetectionEvent
from src.collector import WiFiNetwork


class TestMotionDetector(unittest.TestCase):
    """Testes para a classe MotionDetector."""

    def setUp(self):
        """Configura detector antes de cada teste."""
        self.detector = MotionDetector(
            threshold=10.0,
            sensitivity=1.0
        )

    def test_initialization(self):
        """Testa inicialização do detector."""
        self.assertIsNotNone(self.detector)
        self.assertEqual(self.detector.threshold, 10.0)
        self.assertEqual(self.detector.sensitivity, 1.0)
        self.assertFalse(self.detector.is_calibrated)

    def test_calibration(self):
        """Testa calibração do detector."""
        # Dados de calibração simulados
        calibration_data = {
            "TestNetwork": [-65, -66, -65, -64, -66, -65, -65, -66, -64, -65] * 3
        }

        self.detector.calibrate(calibration_data, duration=30)

        # Verifica calibração
        self.assertTrue(self.detector.is_calibrated)
        self.assertIn("TestNetwork", self.detector.baselines)
        self.assertAlmostEqual(self.detector.baselines["TestNetwork"], -65.0, delta=1.0)

    def test_auto_calibration(self):
        """Testa auto-calibração."""
        rssi_values = [-70, -71, -70, -69, -70, -71, -70, -70, -69, -70]

        self.detector.auto_calibrate("AutoNetwork", rssi_values)

        self.assertIn("AutoNetwork", self.detector.baselines)
        self.assertAlmostEqual(self.detector.baselines["AutoNetwork"], -70.0, delta=1.0)

    def test_motion_detection_no_motion(self):
        """Testa que não detecta movimento em leituras normais."""
        # Calibra
        calibration_data = {
            "Network1": [-60] * 30
        }
        self.detector.calibrate(calibration_data)

        # Testa leituras normais
        is_motion, event = self.detector.process_reading("Network1", -60)
        self.assertFalse(is_motion)
        self.assertIsNone(event)

        is_motion, event = self.detector.process_reading("Network1", -61)
        self.assertFalse(is_motion)

    def test_motion_detection_with_motion(self):
        """Testa detecção de movimento em variações grandes."""
        # Calibra
        calibration_data = {
            "Network1": [-60] * 30
        }
        self.detector.calibrate(calibration_data)

        # Simula movimento (grande variação)
        is_motion, event = self.detector.process_reading("Network1", -45)

        self.assertTrue(is_motion)
        self.assertIsNotNone(event)
        self.assertIsInstance(event, DetectionEvent)
        self.assertEqual(event.ssid, "Network1")
        self.assertEqual(event.rssi_current, -45)

    def test_sensitivity_adjustment(self):
        """Testa ajuste de sensibilidade."""
        original_threshold = self.detector.threshold

        # Aumenta sensibilidade
        self.detector.set_sensitivity(2.0)
        self.assertEqual(self.detector.sensitivity, 2.0)
        self.assertNotEqual(self.detector.threshold, original_threshold)

        # Reduz sensibilidade
        self.detector.set_sensitivity(0.5)
        self.assertEqual(self.detector.sensitivity, 0.5)

    def test_extract_features(self):
        """Testa extração de features."""
        # Adiciona histórico
        ssid = "TestNetwork"
        for rssi in [-60, -61, -60, -59, -60]:
            self.detector.process_reading(ssid, rssi)

        features = self.detector.extract_features(ssid)

        self.assertIn("mean", features)
        self.assertIn("std", features)
        self.assertIn("variance", features)
        self.assertAlmostEqual(features["mean"], -60.0, delta=1.0)

    def test_batch_processing(self):
        """Testa processamento em lote."""
        # Calibra múltiplas redes
        calibration_data = {
            "Network1": [-60] * 30,
            "Network2": [-70] * 30,
        }
        self.detector.calibrate(calibration_data)

        # Processa batch
        readings = {
            "Network1": -45,  # Movimento
            "Network2": -69,  # Normal
        }

        events = self.detector.process_batch(readings)

        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].ssid, "Network1")

    def test_reset(self):
        """Testa reset do detector."""
        # Calibra
        calibration_data = {"Network1": [-60] * 30}
        self.detector.calibrate(calibration_data)

        # Reset
        self.detector.reset()

        self.assertFalse(self.detector.is_calibrated)
        self.assertEqual(len(self.detector.baselines), 0)
        self.assertEqual(len(self.detector.events), 0)


class TestZoneMapper(unittest.TestCase):
    """Testes para a classe ZoneMapper."""

    def setUp(self):
        """Configura mapper antes de cada teste."""
        self.mapper = ZoneMapper()

    def test_initialization(self):
        """Testa inicialização do mapper."""
        self.assertIsNotNone(self.mapper)
        self.assertEqual(len(self.mapper.zones), 0)

    def test_add_zone(self):
        """Testa adição de zona."""
        zone = Zone(
            id="sala",
            name="Sala de Estar",
            position=(0, 0)
        )

        self.mapper.add_zone(zone)

        self.assertEqual(len(self.mapper.zones), 1)
        self.assertIn("sala", self.mapper.zones)

    def test_remove_zone(self):
        """Testa remoção de zona."""
        zone = Zone(id="quarto", name="Quarto", position=(1, 0))
        self.mapper.add_zone(zone)

        self.mapper.remove_zone("quarto")

        self.assertEqual(len(self.mapper.zones), 0)

    def test_assign_device_to_zone(self):
        """Testa associação de dispositivo a zona."""
        zone = Zone(id="cozinha", name="Cozinha", position=(0, 1))
        self.mapper.add_zone(zone)

        self.mapper.assign_device_to_zone("cozinha", "AA:BB:CC:DD:EE:FF")

        zone = self.mapper.get_zone_by_id("cozinha")
        self.assertIn("AA:BB:CC:DD:EE:FF", zone.devices)

    def test_detect_zone(self):
        """Testa detecção de zona baseada em evento."""
        # Cria zona com dispositivo
        zone = Zone(id="sala", name="Sala", position=(0, 0))
        zone.devices.append("TestNetwork")
        self.mapper.add_zone(zone)

        # Cria evento
        event = DetectionEvent(
            timestamp=datetime.now(),
            ssid="TestNetwork",
            rssi_current=-50,
            rssi_baseline=-60.0,
            deviation=10.0
        )

        # Detecta zona
        zone_id = self.mapper.detect_zone(event)

        self.assertEqual(zone_id, "sala")
        self.assertTrue(self.mapper.zones["sala"].active)

    def test_calculate_zone_by_rssi(self):
        """Testa cálculo de zona por RSSI."""
        # Cria zonas
        zone1 = Zone(id="zona1", name="Zona 1", position=(0, 0))
        zone2 = Zone(id="zona2", name="Zona 2", position=(1, 0))
        self.mapper.add_zone(zone1)
        self.mapper.add_zone(zone2)

        # Leituras RSSI
        rssi_readings = {
            "Network1": -50,  # Forte
            "Network2": -80   # Fraco
        }

        device_zones = {
            "Network1": "zona1",
            "Network2": "zona2"
        }

        zone_id = self.mapper.calculate_zone_by_rssi(rssi_readings, device_zones)

        # Zona 1 deve ter score maior (RSSI mais forte)
        self.assertEqual(zone_id, "zona1")

    def test_get_active_zones(self):
        """Testa obtenção de zonas ativas."""
        zone1 = Zone(id="z1", name="Z1", position=(0, 0))
        zone2 = Zone(id="z2", name="Z2", position=(1, 0))

        zone1.active = True
        zone2.active = False

        self.mapper.add_zone(zone1)
        self.mapper.add_zone(zone2)

        active_zones = self.mapper.get_active_zones()

        self.assertEqual(len(active_zones), 1)
        self.assertEqual(active_zones[0].id, "z1")

    def test_reset_zone_states(self):
        """Testa reset de estados de zonas."""
        zone = Zone(id="test", name="Test", position=(0, 0))
        zone.active = True
        self.mapper.add_zone(zone)

        self.mapper.reset_zone_states()

        self.assertFalse(self.mapper.zones["test"].active)


class TestWiFiNetwork(unittest.TestCase):
    """Testes para a classe WiFiNetwork."""

    def test_creation(self):
        """Testa criação de WiFiNetwork."""
        network = WiFiNetwork(
            ssid="TestSSID",
            bssid="AA:BB:CC:DD:EE:FF",
            rssi=-60,
            channel=6
        )

        self.assertEqual(network.ssid, "TestSSID")
        self.assertEqual(network.bssid, "AA:BB:CC:DD:EE:FF")
        self.assertEqual(network.rssi, -60)
        self.assertEqual(network.channel, 6)
        self.assertIsNotNone(network.timestamp)

    def test_to_dict(self):
        """Testa conversão para dicionário."""
        network = WiFiNetwork(
            ssid="TestNetwork",
            bssid="11:22:33:44:55:66",
            rssi=-70
        )

        data = network.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["ssid"], "TestNetwork")
        self.assertEqual(data["bssid"], "11:22:33:44:55:66")
        self.assertEqual(data["rssi"], -70)
        self.assertIn("timestamp", data)


class TestDetectionEvent(unittest.TestCase):
    """Testes para a classe DetectionEvent."""

    def test_creation(self):
        """Testa criação de DetectionEvent."""
        event = DetectionEvent(
            timestamp=datetime.now(),
            ssid="TestNetwork",
            rssi_current=-50,
            rssi_baseline=-60.0,
            deviation=10.0,
            confidence=85.5
        )

        self.assertEqual(event.ssid, "TestNetwork")
        self.assertEqual(event.rssi_current, -50)
        self.assertEqual(event.rssi_baseline, -60.0)
        self.assertEqual(event.deviation, 10.0)
        self.assertEqual(event.confidence, 85.5)

    def test_to_dict(self):
        """Testa conversão para dicionário."""
        event = DetectionEvent(
            timestamp=datetime.now(),
            ssid="Network",
            rssi_current=-55,
            rssi_baseline=-65.0,
            deviation=10.0,
            zone="sala"
        )

        data = event.to_dict()

        self.assertIsInstance(data, dict)
        self.assertEqual(data["ssid"], "Network")
        self.assertEqual(data["zone"], "sala")
        self.assertIn("timestamp", data)


def run_tests():
    """Executa todos os testes."""
    # Cria test suite
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # Adiciona testes
    suite.addTests(loader.loadTestsFromTestCase(TestMotionDetector))
    suite.addTests(loader.loadTestsFromTestCase(TestZoneMapper))
    suite.addTests(loader.loadTestsFromTestCase(TestWiFiNetwork))
    suite.addTests(loader.loadTestsFromTestCase(TestDetectionEvent))

    # Executa
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    return result.wasSuccessful()


if __name__ == "__main__":
    print("🧪 Executando testes do WallSense...\n")

    success = run_tests()

    if success:
        print("\n✅ Todos os testes passaram!")
        exit(0)
    else:
        print("\n❌ Alguns testes falharam!")
        exit(1)
