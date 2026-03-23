# agent/brain.py — Cerebro Optimizado
import os
import yaml
import logging
import pytz
import anthropic
from datetime import datetime
from anthropic import AsyncAnthropic
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger("agentkit")

def cargar_config_prompts():
    with open("config/prompts.yaml", "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def obtener_conocimiento():
    folder = "knowledge"
    knowledge = ""
    extensiones = (".txt", ".md", ".yaml", ".yml", ".json", ".csv")
    if os.path.exists(folder):
        for f in sorted(os.listdir(folder)):
            if f.endswith(extensiones):
                try:
                    with open(os.path.join(folder, f), "r", encoding="utf-8") as content:
                        knowledge += f"\n--- {f.upper()} ---\n{content.read()}\n"
                except Exception as e:
                    logger.warning(f"No se pudo leer {f}: {e}")
                    continue
    return knowledge

async def generar_respuesta(prompt_usuario, historial):
    config = cargar_config_prompts()
    system_prompt = config.get("system_prompt", "")
    
    # Reloj y Conocimiento
    santiago_tz = pytz.timezone("America/Santiago")
    hora_actual = datetime.now(santiago_tz).strftime("%A %d de %B, %Y a las %H:%M")
    system_prompt = f"HORA ACTUAL: {hora_actual}\n\n{system_prompt}\n\nCONOCIMIENTO:\n{obtener_conocimiento()}"

    # Usar la llave del .env
    api_key = os.getenv("ANTHROPIC_API_KEY")
    client = AsyncAnthropic(api_key=api_key)
    
    messages = [{"role": h["role"], "content": h["content"]} for h in historial]
    messages.append({"role": "user", "content": prompt_usuario})
    
    try:
        # Petición a la IA (claude-sonnet-4-6)
        response = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1024,
            system=system_prompt,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"Error Crítico Cerebro: {e}")
        # Si falla el modelo 3, intentamos un último recurso con Claude 2
        try:
            response = await client.messages.create(
                model="claude-2.1",
                max_tokens=1024,
                system=system_prompt,
                messages=messages
            )
            return response.content[0].text
        except:
            return "Lo siento, tengo un problema de conexión con mi cerebro. ¿Podrías intentar de nuevo en un momento?"
