# agent/providers/base.py — Clase base para proveedores de WhatsApp
# Generado por AgentKit

"""
Define la interfaz común que todos los proveedores de WhatsApp deben implementar.
Esto permite cambiar de proveedor sin modificar el resto del código.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from fastapi import Request


@dataclass
class MensajeEntrante:
    """Mensaje normalizado — mismo formato sin importar el proveedor."""
    telefono: str       # Número del remitente
    texto: str          # Contenido del mensaje
    mensaje_id: str     # ID único del mensaje
    es_propio: bool     # True si lo envió el agente (se ignora)


class ProveedorWhatsApp(ABC):
    """Interfaz que cada proveedor de WhatsApp debe implementar."""

    @abstractmethod
    async def parsear_webhook(self, request: Request) -> list[MensajeEntrante]:
        """Extrae y normaliza mensajes del payload del webhook."""
        ...

    @abstractmethod
    async def enviar_mensaje(self, telefono: str, mensaje: str) -> bool:
        """Envía un mensaje de texto. Retorna True si fue exitoso."""
        ...

    @abstractmethod
    async def enviar_imagen(self, telefono: str, url: str, leyenda: str = "") -> bool:
        """Envía una imagen (jpg, png, gif, webp) con leyenda opcional."""
        ...

    async def enviar_video(self, telefono: str, url: str, leyenda: str = "") -> bool:
        """Envía un video (mp4, mov). Implementación opcional por proveedor."""
        return False

    async def enviar_documento(self, telefono: str, url: str, nombre: str = "archivo") -> bool:
        """Envía un documento (pdf, docx, xlsx, etc.). Implementación opcional."""
        return False

    async def enviar_audio(self, telefono: str, url: str) -> bool:
        """Envía un audio (mp3, ogg, wav). Implementación opcional."""
        return False

    async def validar_webhook(self, request: Request) -> dict | int | None:
        """Verificación GET del webhook (solo Meta la requiere). Retorna respuesta o None."""
        return None
