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
from agent.providers import obtener_proveedor

load_dotenv()

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

# --- ENDPOINTS API ADMIN ---

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

        # 3. Respuesta dividida con delay
        bloques = [b.strip() for b in respuesta.split("\n\n") if any(c.isalnum() for c in b)]
        for bloque in bloques:
            await proveedor.enviar_mensaje(msg.telefono, bloque)
            # Notificar respuesta al Admin
            await manager.broadcast({"type": "new_message", "phone": msg.telefono, "text": bloque, "author": "assistant"})
            await asyncio.sleep(2.0)

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
