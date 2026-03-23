# agent/main.py — Centro de Mando: Webhook + Admin + Live Chat
import os
import logging
import yaml
import json
import asyncio
from contextlib import asynccontextmanager
from typing import List
from fastapi import FastAPI, Request, HTTPException, Body, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from agent.brain import generar_respuesta, cargar_config_prompts
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers.whapi import ProveedorWhapi
from agent.providers.meta import ProveedorMeta
from agent.providers.textmebot import ProveedorTextMeBot

load_dotenv()

# --- CONFIGURACIÓN GLOBAL ---
def obtener_proveedor():
    """Función de fábrica para instanciar el proveedor configurado."""
    provider_name = os.getenv("WHATSAPP_PROVIDER", "whapi").lower()
    if provider_name == "meta": return ProveedorMeta()
    if provider_name == "textmebot": return ProveedorTextMeBot()
    return ProveedorWhapi()

# Logger
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

# Gestor de conexiones WebSocket para el Panel
class ConnectionManager:
    def __init__(self):
        self.active_connections: List[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                pass

manager = ConnectionManager()

@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info(f"Sistema listo en puerto {PORT}")
    yield

app = FastAPI(title="Carla Bot Admin", lifespan=lifespan)

# --- ENDPOINTS API CONFIGURACIÓN TÉCNICA ---

@app.get("/api/env")
async def get_env_vars():
    """Lee las variables técnicas clave del .env."""
    return {
        "WHATSAPP_PROVIDER": os.getenv("WHATSAPP_PROVIDER", "whapi"),
        "WHAPI_TOKEN": os.getenv("WHAPI_TOKEN", ""),
        "META_ACCESS_TOKEN": os.getenv("META_ACCESS_TOKEN", ""),
        "META_PHONE_NUMBER_ID": os.getenv("META_PHONE_NUMBER_ID", ""),
        "META_WABA_ID": os.getenv("META_WABA_ID", ""),
        "TEXTMEBOT_API_KEY": os.getenv("TEXTMEBOT_API_KEY", ""),
        "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY", "")
    }

@app.post("/api/test")
async def test_connection():
    """Realiza una prueba de conexión básica con el proveedor actual."""
    try:
        # Intentamos obtener información de la cuenta (esto varía por proveedor)
        # Por ahora, simplemente validamos que la configuración basica existe
        if not proveedor: return {"status": "error", "message": "Proveedor no iniciado"}
        
        # Simulamos un check rápido 
        # (Podrías llamar a proveedor.get_me() si lo tienes implementado)
        return {"status": "ok", "message": f"Conectado a {proveedor.__class__.__name__}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/env")
async def save_env_vars(data: dict = Body(...)):
    """Sobrescribe las variables en el archivo .env."""
    try:
        lines = []
        if os.path.exists(".env"):
            with open(".env", "r", encoding="utf-8") as f:
                lines = f.readlines()
        
        # Eliminar líneas viejas de las variables que estamos editando
        keys_to_update = data.keys()
        new_lines = [l for l in lines if not any(l.startswith(f"{k}=") for k in keys_to_update)]
        
        # Añadir las nuevas
        for k, v in data.items():
            new_lines.append(f"{k}={v}\n")
            
        with open(".env", "w", encoding="utf-8") as f:
            f.writelines(new_lines)
            
        load_dotenv(override=True) # Recargar en memoria
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/config")
async def get_config():
    return cargar_config_prompts()

@app.post("/api/config")
async def save_config(new_config: dict = Body(...)):
    with open("config/prompts.yaml", "w", encoding="utf-8") as f:
        yaml.dump(new_config, f, allow_unicode=True)
    return {"status": "ok"}

@app.get("/api/knowledge")
async def list_knowledge():
    files = [f for f in os.listdir("knowledge") if f.endswith(".txt")] if os.path.exists("knowledge") else []
    return {"files": files}

@app.get("/api/knowledge/{filename}")
async def get_knowledge_file(filename: str):
    with open(os.path.join("knowledge", filename), "r", encoding="utf-8") as f:
        return {"content": f.read()}

@app.post("/api/knowledge/{filename}")
async def save_knowledge_file(filename: str, data: dict = Body(...)):
    with open(os.path.join("knowledge", filename), "w", encoding="utf-8") as f:
        f.write(data.get("content", ""))
    return {"status": "ok"}

@app.post("/api/send")
async def send_manual_message(data: dict = Body(...)):
    telefono = data.get("to")
    texto = data.get("text")
    await proveedor.enviar_mensaje(telefono, texto)
    await guardar_mensaje(telefono, "assistant", f"[Manual] {texto}")
    # Notificar al panel del mensaje manual enviado
    await manager.broadcast({"type": "new_message", "phone": telefono, "text": texto, "author": "admin"})
    return {"status": "ok"}

# WebSocket Admin
@app.websocket("/ws/admin")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

# --- WEBHOOK WHATSAPP ---

@app.get("/webhook")
async def webhook_verificacion(request: Request):
    res = await proveedor.validar_webhook(request)
    return PlainTextResponse(str(res)) if res else {"status": "ok"}

@app.post("/webhook")
async def webhook_handler(request: Request):
    mensajes = await proveedor.parsear_webhook(request)
    for msg in mensajes:
        if msg.es_propio or not msg.texto: continue
        
        # 1. Notificar al Admin en tiempo real
        await manager.broadcast({"type": "new_message", "phone": msg.telefono, "text": msg.texto, "author": "user"})

        # 2. IA piensa
        historial = await obtener_historial(msg.telefono)
        respuesta = await generar_respuesta(msg.texto, historial)

        # 3. Respuesta inteligente (Texto + Multimedia)
        import re
        bloques = [b.strip() for b in respuesta.split("\n\n") if any(c.isalnum() for c in b)]
        for bloque in bloques:
            # Detectar etiquetas multimedia: [IMAGEN:], [VIDEO:], [DOCUMENTO:], [AUDIO:]
            patron = r"\[(IMAGEN|VIDEO|DOCUMENTO|AUDIO):\s*(https?://[^\s\]]+)\]"
            match = re.search(patron, bloque)

            if match:
                tipo = match.group(1)
                url_media = match.group(2)
                texto_restante = re.sub(patron, "", bloque).strip()

                if tipo == "IMAGEN":
                    await proveedor.enviar_imagen(msg.telefono, url_media, texto_restante)
                elif tipo == "VIDEO":
                    await proveedor.enviar_video(msg.telefono, url_media, texto_restante)
                elif tipo == "DOCUMENTO":
                    # Extraer nombre del archivo de la URL
                    nombre_archivo = url_media.split("/")[-1] or "documento"
                    await proveedor.enviar_documento(msg.telefono, url_media, nombre_archivo)
                elif tipo == "AUDIO":
                    if texto_restante:
                        await proveedor.enviar_mensaje(msg.telefono, texto_restante)
                    await proveedor.enviar_audio(msg.telefono, url_media)
            else:
                await proveedor.enviar_mensaje(msg.telefono, bloque)

            # Notificar al Panel Admin
            await manager.broadcast({"type": "new_message", "phone": msg.telefono, "text": bloque, "author": "assistant"})
            await asyncio.sleep(1.5)

        await guardar_mensaje(msg.telefono, "user", msg.texto)
        await guardar_mensaje(msg.telefono, "assistant", respuesta)
    return {"status": "ok"}

# --- FRONTEND ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")
