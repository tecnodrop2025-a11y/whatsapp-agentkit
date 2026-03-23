# agent/providers/evolution.py — Adaptador para Evolution API v2
import os
import logging
import httpx
from fastapi import Request
from agent.providers.base import ProveedorWhatsApp, MensajeEntrante

logger = logging.getLogger("agentkit")

class ProveedorEvolution(ProveedorWhatsApp):
    """Proveedor de WhatsApp usando Evolution API v2 (Self-hosted)."""

    def __init__(self):
        self.api_url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
        self.api_key = os.getenv("EVOLUTION_API_KEY", "")
        self.instance = os.getenv("EVOLUTION_INSTANCE_NAME", "")

    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Parsea el payload de Evolution API v2 (event: messages.upsert)."""
        try:
            body = await request.json()
            event = body.get("event")
            data = body.get("data", {})
            
            if event != "messages.upsert":
                return []

            key = data.get("key", {})
            from_me = key.get("fromMe", False)
            remote_jid = key.get("remoteJid", "")
            
            if from_me or "@g.us" in remote_jid: # Ignorar propios y grupos
                return []

            # Extraer número limpio
            telefono = remote_jid.split("@")[0]
            
            # Extraer texto del mensaje (soporta conversation y extendedTextMessage)
            message = data.get("message", {})
            texto = ""
            if "conversation" in message:
                texto = message["conversation"]
            elif "extendedTextMessage" in message:
                texto = message["extendedTextMessage"].get("text", "")
            
            if not texto: return []

            return [MensajeEntrante(
                telefono=telefono,
                texto=texto,
                mensaje_id=key.get("id", ""),
                es_propio=False
            )]
        except Exception as e:
            logger.error(f"Error parseando webhook Evolution: {e}")
            return []

    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía mensaje de texto via Evolution API."""
        if not self.api_url or not self.api_key or not self.instance:
            logger.warning("Configuración de Evolution API incompleta")
            return False
            
        url = f"{self.api_url}/message/sendText/{self.instance}"
        headers = {"apikey": self.api_key, "Content-Type": "application/json"}
        payload = {
            "number": telefono,
            "text": mensaje,
            "delay": 1200,
            "linkPreview": True
        }
        
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(url, json=payload, headers=headers)
                return r.status_code in (200, 201)
            except Exception as e:
                logger.error(f"Error enviando mensaje Evolution: {e}")
                return False

    async def enviar_imagen(self, telefono: str, url: str, leyenda: str = "") -> bool:
        """Envía imagen via Evolution API."""
        return await self._enviar_media(telefono, "image", url, leyenda)

    async def enviar_video(self, telefono: str, url: str, leyenda: str = "") -> bool:
        """Envía video via Evolution API."""
        return await self._enviar_media(telefono, "video", url, leyenda)

    async def enviar_documento(self, telefono: str, url: str, nombre: str = "archivo") -> bool:
        """Envía documento via Evolution API."""
        return await self._enviar_media(telefono, "document", url, "", nombre)

    async def enviar_audio(self, telefono: str, url: str) -> bool:
        """Envía audio via Evolution API."""
        return await self._enviar_media(telefono, "audio", url)

    async def _enviar_media(self, telefono: str, tipo: str, url_media: str, caption: str = "", filename: str = "") -> bool:
        """Método genérico para enviar media en Evolution API."""
        if not self.api_url or not self.instance: return False
        
        endpoint = f"{self.api_url}/message/sendMedia/{self.instance}"
        headers = {"apikey": self.api_key, "Content-Type": "application/json"}
        
        payload = {
            "number": telefono,
            "mediaMessage": {
                "mediaType": tipo,
                "media": url_media,
                "caption": caption
            },
            "delay": 1500
        }
        
        if filename:
            payload["mediaMessage"]["fileName"] = filename
            
        async with httpx.AsyncClient() as client:
            try:
                r = await client.post(endpoint, json=payload, headers=headers)
                return r.status_code in (200, 201)
            except Exception as e:
                logger.error(f"Error enviando media Evolution ({tipo}): {e}")
                return False

    async def verificar_conexion(self) -> dict:
        """Verifica si el servidor responde y la instancia existe."""
        if not self.api_url or not self.api_key or not self.instance:
            return {"status": "error", "message": "Faltan datos de configuración"}
        
        url = f"{self.api_url}/instance/connectionState/{self.instance}"
        headers = {"apikey": self.api_key}
        
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url, headers=headers)
                if r.status_code == 200:
                    data = r.json()
                    status = data.get("instance", {}).get("state", "unknown")
                    return {"status": "ok", "message": f"Conectado: Instancia '{self.instance}' está {status}"}
                elif r.status_code == 404:
                    return {"status": "error", "message": f"Instancia '{self.instance}' no encontrada"}
                else:
                    return {"status": "error", "message": f"Error {r.status_code}: {r.text[:50]}"}
            except Exception as e:
                return {"status": "error", "message": f"No se pudo conectar al servidor: {str(e)[:50]}"}
