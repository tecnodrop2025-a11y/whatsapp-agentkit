import os
import asyncio
from httpx import AsyncClient
from dotenv import load_dotenv

load_dotenv()

async def test():
    api_key = os.getenv("ANTHROPIC_API_KEY")
    print(f"Key starts with: {api_key[:10] if api_key else 'None'}")
    
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }
    
    payload = {
        "model": "claude-3-haiku-20240307",
        "max_tokens": 10,
        "messages": [{"role": "user", "content": "Hola"}]
    }
    
    async with AsyncClient() as client:
        response = await client.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
        print(f"Status: {response.status_code}")
        print(f"Response: {response.text}")

if __name__ == "__main__":
    asyncio.run(test())
