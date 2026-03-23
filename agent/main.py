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
from dotenv import load_dotenv, set_key

from agent.brain import generar_respuesta, cargar_config_prompts
from agent.memory import inicializar_db, guardar_mensaje, obtener_historial, obtener_config_db, guardar_config_db
from agent.providers.whapi import ProveedorWhapi
from agent.providers.meta import ProveedorMeta
from agent.providers.textmebot import ProveedorTextMeBot
from agent.providers.evolution import ProveedorEvolution
from agent.shopify_client import ShopifyClient

load_dotenv()

# --- CONFIGURACIÓN GLOBAL ---
def obtener_proveedor(name: str = None):
    """Función de fábrica para instanciar el proveedor configurado."""
    if not name:
        name = os.getenv("WHATSAPP_PROVIDER", "whapi").lower()
    
    if name == "meta": return ProveedorMeta()
    if name == "textmebot": return ProveedorTextMeBot()
    if name == "evolution": return ProveedorEvolution()
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
    """Lee las variables técnicas clave (prioriza DB sobre .env)."""
    return {
        "WHATSAPP_PROVIDER": await obtener_config_db("WHATSAPP_PROVIDER", "whapi"),
        "WHAPI_TOKEN": await obtener_config_db("WHAPI_TOKEN", ""),
        "META_ACCESS_TOKEN": await obtener_config_db("META_ACCESS_TOKEN", ""),
        "META_PHONE_NUMBER_ID": await obtener_config_db("META_PHONE_NUMBER_ID", ""),
        "META_WABA_ID": await obtener_config_db("META_WABA_ID", ""),
        "TEXTMEBOT_API_KEY": await obtener_config_db("TEXTMEBOT_API_KEY", ""),
        "EVOLUTION_API_URL": await obtener_config_db("EVOLUTION_API_URL", ""),
        "EVOLUTION_API_KEY": await obtener_config_db("EVOLUTION_API_KEY", ""),
        "EVOLUTION_INSTANCE_NAME": await obtener_config_db("EVOLUTION_INSTANCE_NAME", ""),
        "ANTHROPIC_API_KEY": await obtener_config_db("ANTHROPIC_API_KEY", ""),
        "SHOPIFY_STORE_URL": await obtener_config_db("SHOPIFY_STORE_URL", ""),
        "SHOPIFY_CLIENT_ID": await obtener_config_db("SHOPIFY_CLIENT_ID", ""),
        "SHOPIFY_CLIENT_SECRET": await obtener_config_db("SHOPIFY_CLIENT_SECRET", ""),
        "SHOPIFY_IMPORT_STOCK": await obtener_config_db("SHOPIFY_IMPORT_STOCK", "true"),
        "APP_URL": await obtener_config_db("APP_URL", "")
    }

@app.post("/api/test")
async def test_connection():
    """Realiza una prueba de conexión básica con el proveedor actual."""
    try:
        if not proveedor: return {"status": "error", "message": "Proveedor no iniciado"}
        return {"status": "ok", "message": f"Configurado para usar {os.getenv('WHATSAPP_PROVIDER','').upper()}"}
    except Exception as e:
        return {"status": "error", "message": str(e)}

@app.post("/api/test/evolution")
async def test_evolution_api(request: Request):
    """Prueba específica para Evolution API con valores del formulario."""
    try:
        body = await request.json()
        api_url = body.get("url", "").rstrip("/")
        api_key = body.get("key", "")
        instance = body.get("instance", "")
    except Exception:
        api_url = api_key = instance = ""

    # Si no se pasan desde el form, usar .env
    if not api_url: api_url = os.getenv("EVOLUTION_API_URL", "").rstrip("/")
    if not api_key: api_key = os.getenv("EVOLUTION_API_KEY", "")
    if not instance: instance = os.getenv("EVOLUTION_INSTANCE_NAME", "")

    if not api_url or not api_key or not instance:
        return {"status": "error", "message": "Faltan datos: URL, Token o Nombre de instancia vacíos"}

    import httpx as _httpx
    url = f"{api_url}/instance/connectionState/{instance}"
    headers = {"apikey": api_key}
    try:
        async with _httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 200:
            data = r.json()
            state = data.get("instance", {}).get("state", "unknown")
            emoji = "✅" if state == "open" else "⚠️"
            return {"status": "ok" if state == "open" else "error",
                    "message": f"{emoji} Instancia '{instance}' está: {state}"}
        elif r.status_code == 401:
            return {"status": "error", "message": "❌ Token inválido — verifica el Token de la instancia"}
        elif r.status_code == 404:
            return {"status": "error", "message": f"❌ Instancia '{instance}' no encontrada"}
        else:
            return {"status": "error", "message": f"Error HTTP {r.status_code}: {r.text[:80]}"}
    except _httpx.ConnectError:
        return {"status": "error", "message": "❌ No se pudo conectar al servidor Evolution — verifica la URL"}
    except Exception as e:
        return {"status": "error", "message": f"Error: {str(e)[:80]}"}

@app.post("/api/test/claude")
async def test_claude():
    """Prueba la conexión real con la API de Anthropic Claude."""
    import time
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY", "")
    if not api_key:
        return {"status": "error", "message": "ANTHROPIC_API_KEY no configurada"}
    try:
        client = AsyncAnthropic(api_key=api_key)
        t0 = time.time()
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=10,
            messages=[{"role": "user", "content": "di solo: ok"}]
        )
        ms = round((time.time() - t0) * 1000)
        modelo = response.model
        return {"status": "ok", "message": f"✅ Conectado · {modelo} · {ms}ms"}
    except Exception as e:
        msg = str(e)
        if "401" in msg or "invalid" in msg.lower():
            return {"status": "error", "message": "❌ API Key inválida o sin acceso"}
        if "404" in msg:
            return {"status": "error", "message": "❌ Modelo no encontrado para esta cuenta"}
        return {"status": "error", "message": f"❌ {msg[:120]}"}

@app.post("/api/env")
async def save_env_vars(data: dict = Body(...)):
    """Guarda variables en DB (persistente) y en .env (local)."""
    try:
        # 1. Guardar en Base de Datos (Persistente en Railway)
        for k, v in data.items():
            await guardar_config_db(k, str(v))
            
        # 2. Intentar guardar en .env (solo para desarrollo local o si es permitido)
        try:
            lines = []
            if os.path.exists(".env"):
                with open(".env", "r", encoding="utf-8") as f:
                    lines = f.readlines()
            
            keys_to_update = data.keys()
            new_lines = [l for l in lines if not any(l.startswith(f"{k}=") for l in keys_to_update)]
            for k, v in data.items():
                new_lines.append(f"{k}={v}\n")
                
            with open(".env", "w", encoding="utf-8") as f:
                f.writelines(new_lines)
            load_dotenv(override=True)
        except Exception as env_err:
            logger.warning(f"No se pudo escribir en .env (normal en Railway): {env_err}")

        return {"status": "ok", "message": "Configuración guardada en Base de Datos"}
    except Exception as e:
        logger.error(f"Error guardando config: {e}")
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

@app.get("/api/catalog")
async def get_catalog():
    """Retorna el catálogo multimedia de productos."""
    file_path = "knowledge/catalog.json"
    if os.path.exists(file_path):
        with open(file_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}

@app.post("/api/catalog")
async def save_catalog(data: dict = Body(...)):
    """Guarda el catálogo multimedia completo."""
    with open("knowledge/catalog.json", "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    return {"status": "ok"}

# --- SHOPIFY OAUTH ---

@app.get("/shopify/install")
async def shopify_install(request: Request):
    """Inicia el flujo OAuth de Shopify. Redirige al usuario a Shopify para autorizar."""
    from fastapi.responses import RedirectResponse
    shop = os.getenv("SHOPIFY_STORE_URL", "").strip()
    client_id = os.getenv("SHOPIFY_CLIENT_ID", "").strip()
    # URL base donde está desplegada esta app (Railway u otro)
    app_url = os.getenv("APP_URL", str(request.base_url).rstrip("/"))
    redirect_uri = f"{app_url}/shopify/callback"
    scopes = "read_products,read_inventory"
    auth_url = (
        f"https://{shop}/admin/oauth/authorize"
        f"?client_id={client_id}"
        f"&scope={scopes}"
        f"&redirect_uri={redirect_uri}"
    )
    return RedirectResponse(url=auth_url)

@app.get("/shopify/callback")
async def shopify_callback(request: Request):
    """Recibe el código OAuth de Shopify, lo intercambia por un token permanente y lo guarda."""
    import httpx as _httpx
    code = request.query_params.get("code")
    shop = request.query_params.get("shop", os.getenv("SHOPIFY_STORE_URL", ""))
    if not code:
        return HTMLResponse("<h2>❌ Error: No se recibió el código de autorización de Shopify.</h2>")
    client_id = os.getenv("SHOPIFY_CLIENT_ID", "")
    client_secret = os.getenv("SHOPIFY_CLIENT_SECRET", "")
    try:
        async with _httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                f"https://{shop}/admin/oauth/access_token",
                json={"client_id": client_id, "client_secret": client_secret, "code": code}
            )
            data = r.json()
            access_token = data.get("access_token", "")
            if not access_token:
                return HTMLResponse(f"<h2>❌ Error al obtener token: {data}</h2>")
            # Guardar token en .env automáticamente
            env_path = os.path.join(os.getcwd(), ".env")
            set_key(env_path, "SHOPIFY_ACCESS_TOKEN", access_token)
            set_key(env_path, "SHOPIFY_STORE_URL", shop)
            os.environ["SHOPIFY_ACCESS_TOKEN"] = access_token
            os.environ["SHOPIFY_STORE_URL"] = shop
            logger.info(f"✅ Shopify token guardado correctamente para {shop}")
            return HTMLResponse(f"""
                <html><body style="font-family:sans-serif; padding:3rem; background:#0f172a; color:white; text-align:center;">
                <h1 style="color:#10b981">✅ ¡Shopify conectado!</h1>
                <p>Token guardado para: <strong>{shop}</strong></p>
                <p style="color:#94a3b8">Puedes cerrar esta ventana y volver al panel de Carla.</p>
                </body></html>
            """)
    except Exception as e:
        return HTMLResponse(f"<h2>❌ Error: {e}</h2>")

@app.post("/api/shopify/test")
async def test_shopify():
    """Prueba la conexión con la tienda Shopify."""
    import time
    t0 = time.time()
    client = ShopifyClient()
    result = await client.test_connection()
    ms = round((time.time() - t0) * 1000)
    if result["ok"]:
        return {"status": "ok", "message": f"✅ Conectado · {result.get('shop_name', '')} · {ms}ms"}
    return {"status": "error", "message": f"❌ {result.get('error', 'Error desconocido')}"}

@app.get("/api/shopify/products")
async def list_shopify_products():
    """Devuelve la lista de productos de Shopify para previsualizar (no importa)."""
    client = ShopifyClient()
    if not client.is_configured():
        return {"status": "error", "products": [], "message": "Shopify no configurado"}
    try:
        productos = await client.get_products()
        preview = []
        for p in productos:
            images = p.get("images", [])
            variants = p.get("variants", [])
            preview.append({
                "id": p.get("id"),
                "name": p.get("title", "Sin nombre"),
                "price": variants[0].get("price", "0") if variants else "0",
                "image": images[0]["src"] if images else "",
                "tags": p.get("tags", ""),
                "type": p.get("product_type", "")
            })
        return {"status": "ok", "products": preview}
    except Exception as e:
        return {"status": "error", "products": [], "message": str(e)[:200]}

@app.post("/api/shopify/import")
async def import_shopify_products(data: dict = Body(default={})):
    """Importa productos seleccionados desde Shopify y los fusiona con el catálogo local."""
    client = ShopifyClient()
    if not client.is_configured():
        return {"status": "error", "message": "Shopify no está configurado"}
    try:
        productos = await client.get_products()
        if not productos:
            return {"status": "error", "message": "No se encontraron productos o error de conexión"}

        # Filtrar por IDs si se especificaron
        selected_ids = data.get("ids", [])
        if selected_ids:
            productos = [p for p in productos if p.get("id") in selected_ids]

        nuevos = client.format_for_catalog(productos)

        # Fusionar con catálogo existente (preservar video y documento manual)
        catalog_file = "knowledge/catalog.json"
        existente = {}
        if os.path.exists(catalog_file):
            with open(catalog_file, "r", encoding="utf-8") as f:
                existente = json.load(f)

        for nombre, datos in nuevos.items():
            if nombre in existente:
                datos["video"] = existente[nombre].get("video", "")
                datos["documento"] = existente[nombre].get("documento", "")
                datos["keywords"] = existente[nombre].get("keywords", datos["keywords"])
            existente[nombre] = datos

        with open(catalog_file, "w", encoding="utf-8") as f:
            json.dump(existente, f, indent=2, ensure_ascii=False)

        n = len(nuevos)
        return {"status": "ok", "message": f"✅ {n} producto{'s' if n != 1 else ''} importado{'s' if n != 1 else ''} correctamente", "count": n}
    except Exception as e:
        return {"status": "error", "message": f"❌ {str(e)[:200]}"}

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

@app.post("/webhook/debug")
async def webhook_debug(request: Request):
    """Endpoint de diagnóstico: muestra exactamente lo que llegó del webhook."""
    try:
        body = await request.json()
        logger.info(f"[DEBUG WEBHOOK] Payload recibido: {json.dumps(body, ensure_ascii=False)[:500]}")
        return {"received": body, "provider": os.getenv("WHATSAPP_PROVIDER", "?"),
                "evolution_url": bool(os.getenv("EVOLUTION_API_URL")),
                "evolution_key": bool(os.getenv("EVOLUTION_API_KEY")),
                "evolution_instance": os.getenv("EVOLUTION_INSTANCE_NAME", "?")}
    except Exception as e:
        return {"error": str(e), "raw": await request.body()}

@app.post("/webhook")
async def webhook_handler(request: Request):
    # Cargar proveedor dinámicamente desde la Base de Datos
    provider_name = await obtener_config_db("WHATSAPP_PROVIDER", "whapi")
    proveedor_actual = obtener_proveedor(provider_name.lower())
    
    logger.info(f"[WEBHOOK] POST recibido - Proveedor DB: {provider_name}")
    
    mensajes = await proveedor_actual.parsear_webhook(request)
    logger.info(f"[WEBHOOK] Mensajes parseados: {len(mensajes)}")
    
    for msg in mensajes:
        if msg.es_propio or not msg.texto: continue
        logger.info(f"[WEBHOOK] Procesando mensaje de {msg.telefono}: '{msg.texto[:60]}'")

        # 1. Notificar al Admin en tiempo real
        await manager.broadcast({"type": "new_message", "phone": msg.telefono, "text": msg.texto, "author": "user"})

        # 2. IA piensa
        historial = await obtener_historial(msg.telefono)
        respuesta = await generar_respuesta(msg.texto, historial)

        # 3. Respuesta inteligente (Texto + Multimedia)
        import re
        bloques = [b.strip() for b in respuesta.split("\n\n") if any(c.isalnum() for c in b)]
        
        # Cargar catálogo para disparadores de producto
        catalog = {}
        if os.path.exists("knowledge/catalog.json"):
            try:
                with open("knowledge/catalog.json", "r", encoding="utf-8") as f:
                    catalog = json.load(f)
            except: pass

        for bloque in bloques:
            # 1. Detectar disparador de producto completo: [PRODUCTO: Nombre]
            patron_prod = r"\[PRODUCTO:\s*(.*?)\]"
            match_prod = re.search(patron_prod, bloque)
            
            if match_prod:
                prod_name = match_prod.group(1).strip()
                texto_limpio = re.sub(patron_prod, "", bloque).strip()
                
                # Enviar texto primero si queda algo
                if texto_limpio:
                    await proveedor.enviar_mensaje(msg.telefono, texto_limpio)
                
                # Buscar en catálogo (búsqueda flexible por nombre)
                prod_data = catalog.get(prod_name)
                if not prod_data:
                    # Intento de búsqueda parcial
                    for k in catalog:
                        if prod_name.lower() in k.lower():
                            prod_data = catalog[k]
                            break
                
                if prod_data:
                    # Enviar Imagen
                    if prod_data.get("imagen"):
                        await proveedor.enviar_imagen(msg.telefono, prod_data["imagen"], f"Foto de {prod_name}")
                        await asyncio.sleep(0.5)
                    # Enviar Video
                    if prod_data.get("video"):
                        await proveedor.enviar_video(msg.telefono, prod_data["video"], f"Video de {prod_name}")
                        await asyncio.sleep(0.5)
                    # Enviar PDF
                    if prod_data.get("documento"):
                        await proveedor.enviar_documento(msg.telefono, prod_data["documento"], f"Ficha_{prod_name}.pdf")
                        await asyncio.sleep(0.5)
                continue

            # 2. Detectar etiquetas multimedia unitarias: [IMAGEN:url], [VIDEO:url], etc.
            patron_media = r"\[(IMAGEN|VIDEO|DOCUMENTO|AUDIO):\s*(https?://[^\s\]]+)\]"
            match_media = re.search(patron_media, bloque)

            if match_media:
                tipo = match_media.group(1)
                url_media = match_media.group(2)
                texto_restante = re.sub(patron_media, "", bloque).strip()

                if tipo == "IMAGEN":
                    await proveedor.enviar_imagen(msg.telefono, url_media, texto_restante)
                elif tipo == "VIDEO":
                    await proveedor.enviar_video(msg.telefono, url_media, texto_restante)
                elif tipo == "DOCUMENTO":
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
            await asyncio.sleep(1.0)

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
