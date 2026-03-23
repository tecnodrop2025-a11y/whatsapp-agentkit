# agent/shopify_client.py — Integración con Shopify REST Admin API
import os
import httpx
import logging

logger = logging.getLogger("agentkit")

class ShopifyClient:
    """Cliente para la API REST de Shopify."""

    def __init__(self):
        self.store_url = os.getenv("SHOPIFY_STORE_URL", "").strip().rstrip("/")
        self.token = os.getenv("SHOPIFY_ACCESS_TOKEN", "").strip()
        self.api_version = "2024-01"
        self.base_url = f"https://{self.store_url}/admin/api/{self.api_version}"
        self.headers = {
            "X-Shopify-Access-Token": self.token,
            "Content-Type": "application/json"
        }

    def is_configured(self) -> bool:
        return bool(self.store_url and self.token)

    async def test_connection(self) -> dict:
        """Prueba la conexión con la tienda Shopify."""
        if not self.is_configured():
            return {"ok": False, "error": "SHOPIFY_STORE_URL o SHOPIFY_ACCESS_TOKEN no configurados"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(f"{self.base_url}/shop.json", headers=self.headers)
                if r.status_code == 200:
                    shop = r.json().get("shop", {})
                    return {"ok": True, "shop_name": shop.get("name", ""), "domain": shop.get("domain", "")}
                return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
        except Exception as e:
            return {"ok": False, "error": str(e)[:200]}

    async def get_products(self, limit: int = 250) -> list:
        """Obtiene todos los productos de la tienda con sus imágenes y variantes."""
        if not self.is_configured():
            return []

        all_products = []
        url = f"{self.base_url}/products.json"
        params = {"limit": limit, "status": "active", "fields": "id,title,body_html,tags,images,variants,product_type"}

        async with httpx.AsyncClient(timeout=30) as client:
            while url:
                r = await client.get(url, headers=self.headers, params=params)
                if r.status_code != 200:
                    logger.error(f"Shopify error {r.status_code}: {r.text[:200]}")
                    break

                data = r.json()
                products = data.get("products", [])
                all_products.extend(products)

                # Paginación via Link header
                link_header = r.headers.get("Link", "")
                url = None
                if 'rel="next"' in link_header:
                    for part in link_header.split(","):
                        if 'rel="next"' in part:
                            url = part.split(";")[0].strip().strip("<>")
                            params = {}  # La URL paginada ya tiene los params
                            break

        return all_products

    def format_for_catalog(self, products: list) -> dict:
        """Convierte los productos de Shopify al formato del catálogo local."""
        catalog = {}
        for p in products:
            name = p.get("title", "Sin nombre")

            # Imagen principal del producto
            images = p.get("images", [])
            imagen_url = images[0]["src"] if images else ""

            # Precio desde la primera variante
            variants = p.get("variants", [])
            precio = variants[0].get("price", "0") if variants else "0"
            
            # Stock (opcional según configuración)
            import_stock = os.getenv("SHOPIFY_IMPORT_STOCK", "true").lower() == "true"
            stock = sum(v.get("inventory_quantity", 0) for v in variants) if import_stock else 0

            # Tags de Shopify como palabras clave
            tags = p.get("tags", "")

            # Descripción limpia (sin HTML)
            descripcion = p.get("body_html", "") or ""
            import re
            descripcion_limpia = re.sub(r"<[^>]+>", "", descripcion).strip()[:300]

            catalog[name] = {
                "shopify_id": p.get("id"),
                "keywords": tags,
                "precio": precio,
                "stock": stock,
                "descripcion": descripcion_limpia,
                "imagen": imagen_url,
                "video": "",
                "documento": ""
            }

        return catalog
