# agent/providers/textmebot.py — Adaptador para TextMeBot.com
# Generado por AgentKit

import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

class ProveedorTextMeBot(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando TextMeBot.com API."""

    def __init__(self):
        self.apikey = os.getenv("TEXTMEBOT_API_KEY")
        self.url_envio = "https://api.textmebot.com/send.php"

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload enviado por TextMeBot via POST JSON."""
        try:
            body = await request.json()
            mensajes = []
            
            # TextMeBot suele enviar un mensaje por cada POST
            from_phone = body.get("from")
            text = body.get("message")
            
            if from_phone and text:
                mensajes.append(MensajeEntrante(
                    telefono=from_phone,
                    texto=text,
                    mensaje_id=f"tmb_{from_phone}", # TextMeBot simplificado no envía un ID único de mensaje
                    es_propio=False
                ))
            return mensajes
        except Exception as e:
            logger.error(f"Error parseando webhook de TextMeBot: {e}")
            return []

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje via TextMeBot.com usando JSON POST."""
        if not self.apikey:
            logger.warning("TEXTMEBOT_API_KEY no configurado — mensaje no enviado")
            return False

        # Limpiar el teléfono (quitar + si existe)
        clean_phone = telefono.replace("+", "")
        
        payload = {
            "recipient": clean_phone,
            "apikey": self.apikey,
            "text": mensaje
        }

        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    self.url_envio,
                    json=payload,
                    headers={"Content-Type": "application/json"}
                )
                if r.status_code != 200:
                    logger.error(f"Error TextMeBot: {r.status_code} — {r.text}")
                return r.status_code == 200
        except Exception as e:
            logger.error(f"Fallo de red al enviar via TextMeBot: {e}")
            return False

    async def enviar_imagen(self, telefono: str, url: str, leyenda: str = "") -> bool:
        """Envía imagen (jpg, png, gif, webp) via TextMeBot."""
        return await self._enviar_archivo(telefono, url, leyenda)

    async def enviar_video(self, telefono: str, url: str, leyenda: str = "") -> bool:
        """Envía video (mp4, mov) via TextMeBot."""
        return await self._enviar_archivo(telefono, url, leyenda)

    async def enviar_documento(self, telefono: str, url: str, nombre: str = "archivo") -> bool:
        """Envía documento (pdf, docx, xlsx) via TextMeBot."""
        return await self._enviar_archivo(telefono, url, nombre)

    async def enviar_audio(self, telefono: str, url: str) -> bool:
        """Envía audio (mp3, ogg) via TextMeBot."""
        return await self._enviar_archivo(telefono, url, "")

    async def _enviar_archivo(self, telefono: str, url: str, texto: str) -> bool:
        """Método interno genérico — TextMeBot usa 'file' para todo tipo de media."""
        if not self.apikey: return False
        payload = {
            "recipient": telefono.replace("+", ""),
            "apikey": self.apikey,
            "text": texto,
            "file": url
        }
        async with httpx.AsyncClient() as client:
            r = await client.post(self.url_envio, json=payload)
            if r.status_code != 200:
                logger.error(f"Error TextMeBot media: {r.status_code} — {r.text}")
            return r.status_code == 200
