"""
telegram_bot.py - Bot Telegram para notificações do WallSense

Envia notificações de movimento detectado e responde a comandos
de controle do sistema via Telegram.
"""

import asyncio
import logging
from datetime import datetime, time
from typing import Optional, Dict

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    filters,
)

from .detector import DetectionEvent
from .utils import load_config, get_default_config_path, format_timestamp


class RateLimiter:
    """Limitador de taxa para notificações."""

    def __init__(self, max_per_minute: int = 5, cooldown_seconds: int = 30):
        self.max_per_minute = max_per_minute
        self.cooldown_seconds = cooldown_seconds
        self.notifications = []
        self.in_cooldown = False
        self.cooldown_until: Optional[datetime] = None

    def can_send(self) -> bool:
        """Verifica se pode enviar notificação."""
        now = datetime.now()

        # Remove notificações antigas (mais de 1 minuto)
        self.notifications = [
            ts for ts in self.notifications
            if (now - ts).total_seconds() < 60
        ]

        # Verifica cooldown
        if self.in_cooldown and self.cooldown_until:
            if now < self.cooldown_until:
                return False
            else:
                self.in_cooldown = False
                self.cooldown_until = None

        # Verifica limite de taxa
        if len(self.notifications) >= self.max_per_minute:
            self.in_cooldown = True
            self.cooldown_until = now.replace(
                second=now.second + self.cooldown_seconds
            )
            return False

        return True

    def record_notification(self):
        """Registra que uma notificação foi enviada."""
        self.notifications.append(datetime.now())


class TelegramNotifier:
    """
    Gerenciador de notificações via Telegram.

    Envia alertas de movimento e responde a comandos de controle.
    """

    def __init__(self, config: Optional[Dict] = None, logger: Optional[logging.Logger] = None):
        """
        Inicializa o notificador Telegram.

        Args:
            config: Configuração do Telegram
            logger: Logger personalizado
        """
        self.logger = logger or logging.getLogger("wallsense.telegram")

        # Carrega configuração
        if config is None:
            try:
                config_path = get_default_config_path("telegram_config.json")
                config = load_config(config_path)
            except FileNotFoundError:
                self.logger.warning("telegram_config.json não encontrado")
                config = {"enabled": False}

        self.config = config
        self.enabled = config.get("enabled", False)

        if not self.enabled:
            self.logger.info("Notificações Telegram desabilitadas")
            return

        self.token = config.get("token")
        self.admin_chat_id = config.get("admin_chat_id")

        if not self.token or not self.admin_chat_id:
            self.logger.error("Token ou chat_id não configurado")
            self.enabled = False
            return

        # Configurações de notificação
        self.notifications_config = config.get("notifications", {})
        self.quiet_hours_config = config.get("quiet_hours", {})
        self.rate_limit_config = config.get("rate_limit", {})
        self.message_format = config.get("message_format", {})

        # Rate limiter
        self.rate_limiter = RateLimiter(
            max_per_minute=self.rate_limit_config.get("max_notifications_per_minute", 5),
            cooldown_seconds=self.rate_limit_config.get("cooldown_seconds", 30)
        )

        # Aplicação Telegram
        self.application: Optional[Application] = None

        # Referência ao sistema WallSense (será injetada)
        self.wallsense_system = None

        self.logger.info("TelegramNotifier inicializado")

    async def start(self):
        """Inicia o bot Telegram."""
        if not self.enabled:
            return

        try:
            self.application = Application.builder().token(self.token).build()

            # Registra handlers de comandos
            self.application.add_handler(CommandHandler("start", self.cmd_start))
            self.application.add_handler(CommandHandler("status", self.cmd_status))
            self.application.add_handler(CommandHandler("calibrar", self.cmd_calibrate))
            self.application.add_handler(CommandHandler("sensibilidade", self.cmd_sensitivity))
            self.application.add_handler(CommandHandler("help", self.cmd_help))

            # Inicia bot
            await self.application.initialize()
            await self.application.start()
            await self.application.updater.start_polling()

            self.logger.info("✅ Bot Telegram iniciado")

            # Envia notificação de startup
            if self.notifications_config.get("system_startup", True):
                await self.send_message("🚀 *WallSense Online*\n\nSistema iniciado com sucesso!")

        except Exception as e:
            self.logger.error(f"Erro ao iniciar bot Telegram: {e}")
            self.enabled = False

    async def stop(self):
        """Para o bot Telegram."""
        if not self.enabled or not self.application:
            return

        try:
            if self.notifications_config.get("system_shutdown", False):
                await self.send_message("🛑 *WallSense Offline*\n\nSistema sendo desligado...")

            await self.application.updater.stop()
            await self.application.stop()
            await self.application.shutdown()

            self.logger.info("Bot Telegram parado")

        except Exception as e:
            self.logger.error(f"Erro ao parar bot Telegram: {e}")

    async def send_message(self, text: str, parse_mode: str = "Markdown") -> bool:
        """
        Envia mensagem para o admin.

        Args:
            text: Texto da mensagem
            parse_mode: Modo de parse (Markdown ou HTML)

        Returns:
            True se enviou com sucesso
        """
        if not self.enabled or not self.application:
            return False

        try:
            await self.application.bot.send_message(
                chat_id=self.admin_chat_id,
                text=text,
                parse_mode=parse_mode
            )
            return True

        except Exception as e:
            self.logger.error(f"Erro ao enviar mensagem Telegram: {e}")
            return False

    async def notify_motion(self, event: DetectionEvent):
        """
        Envia notificação de movimento detectado.

        Args:
            event: Evento de detecção
        """
        if not self.enabled:
            return

        # Verifica se notificações de movimento estão habilitadas
        if not self.notifications_config.get("motion_detected", True):
            return

        # Verifica quiet hours
        if self._is_quiet_hours():
            self.logger.debug("Em horário silencioso, notificação ignorada")
            return

        # Verifica rate limit
        if not self.rate_limiter.can_send():
            self.logger.debug("Rate limit atingido, notificação ignorada")
            return

        # Formata mensagem
        message = self._format_motion_message(event)

        # Envia
        if await self.send_message(message):
            self.rate_limiter.record_notification()
            self.logger.info("Notificação de movimento enviada via Telegram")

    def _format_motion_message(self, event: DetectionEvent) -> str:
        """Formata mensagem de movimento detectado."""
        message = "🚨 *MOVIMENTO DETECTADO!*\n\n"

        # SSID
        message += f"📡 *Rede:* `{event.ssid}`\n"

        # Zona (se configurado)
        if self.message_format.get("include_zone", True) and event.zone:
            message += f"📍 *Zona:* {event.zone}\n"

        # RSSI (se configurado)
        if self.message_format.get("include_rssi", False):
            message += f"📶 *RSSI:* {event.rssi_current} dBm\n"
            message += f"📊 *Baseline:* {event.rssi_baseline:.1f} dBm\n"
            message += f"📈 *Desvio:* {event.deviation:.1f} dBm\n"

        # Confiança (se configurado)
        if self.message_format.get("include_confidence", True):
            confidence_emoji = "🔴" if event.confidence > 80 else "🟡" if event.confidence > 60 else "🟢"
            message += f"{confidence_emoji} *Confiança:* {event.confidence:.0f}%\n"

        # Timestamp (se configurado)
        if self.message_format.get("include_timestamp", True):
            message += f"\n🕐 {event.timestamp}"

        return message

    def _is_quiet_hours(self) -> bool:
        """Verifica se está em horário silencioso."""
        if not self.quiet_hours_config.get("enabled", False):
            return False

        now = datetime.now().time()

        start_str = self.quiet_hours_config.get("start", "22:00")
        end_str = self.quiet_hours_config.get("end", "07:00")

        start = time(*map(int, start_str.split(":")))
        end = time(*map(int, end_str.split(":")))

        if start <= end:
            # Horário no mesmo dia (ex: 22:00 - 23:59)
            return start <= now <= end
        else:
            # Horário cruza meia-noite (ex: 22:00 - 07:00)
            return now >= start or now <= end

    async def notify_calibration_complete(self, data: Dict):
        """Notifica conclusão de calibração."""
        if not self.enabled or not self.notifications_config.get("calibration_complete", True):
            return

        message = (
            "✅ *Calibração Concluída*\n\n"
            f"🔢 Redes calibradas: {data.get('networks_calibrated', 0)}\n"
            f"📊 Amostras coletadas: {data.get('samples_collected', 0)}\n"
            f"⏱️ Duração: {data.get('duration', 0)}s"
        )

        await self.send_message(message)

    async def notify_error(self, error_message: str):
        """Notifica erro do sistema."""
        if not self.enabled or not self.notifications_config.get("errors", True):
            return

        message = f"❌ *Erro no Sistema*\n\n{error_message}"
        await self.send_message(message)

    # ===== COMANDOS DO BOT =====

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /start."""
        await update.message.reply_text(
            "🎉 *Bem-vindo ao WallSense!*\n\n"
            "Sistema de detecção de movimento via WiFi.\n\n"
            "Use /help para ver comandos disponíveis.",
            parse_mode="Markdown"
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /help."""
        help_text = (
            "📚 *Comandos Disponíveis:*\n\n"
            "/status - Mostra status do sistema\n"
            "/calibrar - Inicia calibração (30s)\n"
            "/sensibilidade <valor> - Ajusta sensibilidade (0.5-2.0)\n"
            "/help - Mostra esta mensagem\n\n"
            "💡 *Dica:* Use /status para verificar se o sistema está ativo."
        )

        await update.message.reply_text(help_text, parse_mode="Markdown")

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /status."""
        if not self.wallsense_system:
            await update.message.reply_text("❌ Sistema não disponível")
            return

        stats = self.wallsense_system.get_statistics()

        status_emoji = "🟢" if stats["is_running"] else "🔴"
        calibrated_emoji = "✅" if stats["is_calibrated"] else "❌"

        message = (
            f"{status_emoji} *Status do Sistema*\n\n"
            f"🔧 Calibrado: {calibrated_emoji}\n"
            f"📡 Redes monitoradas: {stats['detector_stats']['networks_tracked']}\n"
            f"🚨 Eventos detectados: {stats['total_events']}\n"
            f"📊 Scans realizados: {stats['total_scans']}\n"
            f"⏱️ Uptime: {self._format_uptime(stats['uptime_seconds'])}\n"
            f"\n📅 Iniciado em: {stats['start_time']}"
        )

        await update.message.reply_text(message, parse_mode="Markdown")

    async def cmd_calibrate(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /calibrar."""
        if not self.wallsense_system:
            await update.message.reply_text("❌ Sistema não disponível")
            return

        if self.wallsense_system.is_running:
            await update.message.reply_text(
                "⚠️ *Aviso*\n\nPare o monitoramento antes de calibrar.",
                parse_mode="Markdown"
            )
            return

        await update.message.reply_text(
            "🔧 *Calibração Iniciada*\n\n"
            "⏳ Aguarde 30 segundos...\n"
            "⚠️ Evite movimento durante a calibração!",
            parse_mode="Markdown"
        )

        try:
            result = await self.wallsense_system.calibrate(duration=30)

            if result["success"]:
                await update.message.reply_text(
                    f"✅ *Calibração Concluída*\n\n"
                    f"Redes calibradas: {result['networks_calibrated']}",
                    parse_mode="Markdown"
                )
        except Exception as e:
            await update.message.reply_text(
                f"❌ *Erro na Calibração*\n\n{str(e)}",
                parse_mode="Markdown"
            )

    async def cmd_sensitivity(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Comando /sensibilidade."""
        if not self.wallsense_system:
            await update.message.reply_text("❌ Sistema não disponível")
            return

        if not context.args:
            current = self.wallsense_system.detector.sensitivity
            await update.message.reply_text(
                f"ℹ️ Sensibilidade atual: *{current:.1f}x*\n\n"
                f"Use: `/sensibilidade <valor>` (0.5-2.0)",
                parse_mode="Markdown"
            )
            return

        try:
            value = float(context.args[0])

            if value < 0.5 or value > 2.0:
                await update.message.reply_text(
                    "❌ Valor inválido! Use entre 0.5 e 2.0",
                    parse_mode="Markdown"
                )
                return

            self.wallsense_system.detector.set_sensitivity(value)

            await update.message.reply_text(
                f"✅ Sensibilidade ajustada para *{value:.1f}x*",
                parse_mode="Markdown"
            )

        except ValueError:
            await update.message.reply_text(
                "❌ Valor inválido! Use um número (ex: 1.5)",
                parse_mode="Markdown"
            )

    def _format_uptime(self, seconds: float) -> str:
        """Formata uptime em formato legível."""
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = int(seconds % 60)

        if hours > 0:
            return f"{hours}h {minutes}m"
        elif minutes > 0:
            return f"{minutes}m {secs}s"
        else:
            return f"{secs}s"

    def set_wallsense_system(self, system):
        """Define referência ao sistema WallSense."""
        self.wallsense_system = system
        self.logger.info("Sistema WallSense vinculado ao bot Telegram")


# Função auxiliar para teste
async def test_bot():
    """Testa o bot Telegram."""
    from .utils import setup_logging

    logger = setup_logging(log_level="INFO", verbose=True)

    notifier = TelegramNotifier(logger=logger)

    if notifier.enabled:
        await notifier.start()

        # Aguarda comandos
        print("✅ Bot iniciado. Pressione Ctrl+C para parar...")

        try:
            while True:
                await asyncio.sleep(1)
        except KeyboardInterrupt:
            print("\n🛑 Parando bot...")
            await notifier.stop()
    else:
        print("❌ Bot Telegram não está habilitado. Configure telegram_config.json")


if __name__ == "__main__":
    asyncio.run(test_bot())
