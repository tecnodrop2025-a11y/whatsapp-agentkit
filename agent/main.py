# agent/main.py — Servidor FastAPI + Webhook + Admin Panel
import os
import logging
import yaml
import json
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException, Body
from fastapi.responses import PlainTextResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

from agent.brain import generar_respuesta, cargar_config_prompts
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial
from agent.providers import obtener_proveedor

load_dotenv()

# Configuración de logging
ENVIRONMENT = os.getenv("ENVIRONMENT", "development")
log_level = logging.DEBUG if ENVIRONMENT == "development" else logging.INFO
logging.basicConfig(level=log_level)
logger = logging.getLogger("agentkit")

proveedor = obtener_proveedor()
PORT = int(os.getenv("PORT", 8000))

@asynccontextmanager
async def lifespan(app: FastAPI):
    await inicializar_db()
    logger.info("Sistema listo")
    yield

app = FastAPI(title="Carla Bot Admin", lifespan=lifespan)

# --- ENDPOINTS DEL PANEL ADMINISTRATIVO ---

@app.get("/api/config")
async def get_config():
    """Obtiene la configuración actual de prompts.yaml."""
    return cargar_config_prompts()

@app.post("/api/config")
async def save_config(new_config: dict = Body(...)):
    """Guarda la nueva configuración en prompts.yaml."""
    try:
        with open("config/prompts.yaml", "w", encoding="utf-8") as f:
            yaml.dump(new_config, f, allow_unicode=True)
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/knowledge")
async def list_knowledge():
    """Lista los archivos en la carpeta knowledge."""
    files = []
    folder = "knowledge"
    if os.path.exists(folder):
        for f in os.listdir(folder):
            if f.endswith(".txt"):
                files.append(f)
    return {"files": files}

@app.get("/api/knowledge/{filename}")
async def get_knowledge_file(filename: str):
    """Lee el contenido de un archivo de conocimiento."""
    path = os.path.join("knowledge", filename)
    if not os.path.exists(path):
        raise HTTPException(status_code=404)
    with open(path, "r", encoding="utf-8") as f:
        return {"content": f.read()}

@app.post("/api/knowledge/{filename}")
async def save_knowledge_file(filename: str, data: dict = Body(...)):
    """Guarda el contenido de un archivo de conocimiento."""
    path = os.path.join("knowledge", filename)
    try:
        with open(path, "w", encoding="utf-8") as f:
            f.write(data.get("content", ""))
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- WEBHOOK DE WHATSAPP ---

@app.get("/webhook")
async def webhook_verificacion(request: Request):
    resultado = await proveedor.validar_webhook(request)
    if resultado is not None:
        return PlainTextResponse(str(resultado))
    return {"status": "ok"}

@app.post("/webhook")
async def webhook_handler(request: Request):
    try:
        mensajes = await proveedor.parsear_webhook(request)
        for msg in mensajes:
            if msg.es_propio or not msg.texto: continue
            
            historial = await obtener_historial(msg.telefono)
            respuesta = await generar_respuesta(msg.texto, historial)

            # Lógica de envío humano con delay
            import asyncio
            bloques_raw = [b.strip() for b in respuesta.split("\n\n") if b.strip()]
            bloques = [b for b in bloques_raw if any(c.isalnum() for c in b)]
            
            for index, bloque in enumerate(bloques):
                await proveedor.enviar_mensaje(msg.telefono, bloque)
                if index < len(bloques) - 1:
                    await asyncio.sleep(2.0)

            await guardar_mensaje(msg.telefono, "user", msg.texto)
            await guardar_mensaje(msg.telefono, "assistant", respuesta)

        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error: {e}")
        return {"status": "error"}

# --- DASHBOARD FRONTEND ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    """Sirve la página principal del panel."""
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()

# Servir archivos estáticos (JS, CSS, Imágenes)
if not os.path.exists("static"): os.makedirs("static")
app.mount("/static", StaticFiles(directory="static"), name="static")
