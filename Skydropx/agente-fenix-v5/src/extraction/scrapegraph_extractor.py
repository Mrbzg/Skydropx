"""
Wrapper opcional para ScrapeGraphAI.

Permite extracción guiada por lenguaje natural sobre URLs/HTML,
usando un LLM local (Ollama) o cloud.

Si ScrapeGraphAI o Ollama no están instalados, este módulo se omite
silenciosamente y el pipeline cae al extractor regex tradicional.

Setup:
  pip install scrapegraphai
  # Para Ollama local:
  curl -fsSL https://ollama.com/install.sh | sh
  ollama pull llama3.2:3b   # modelo pequeño y rápido
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def _try_import(name: str) -> bool:
    try:
        __import__(name)
        return True
    except ImportError:
        return False


HAS_SCRAPEGRAPH = _try_import("scrapegraphai")


def is_available() -> bool:
    return HAS_SCRAPEGRAPH


def extract_with_prompt(
    url_or_html: str,
    prompt: str,
    model: str = "ollama/llama3.2:3b",
    ollama_base_url: str = "http://localhost:11434",
) -> dict | None:
    """
    Extrae datos estructurados usando ScrapeGraphAI + un LLM.

    Args:
        url_or_html: URL o HTML directo (auto-detecta)
        prompt: descripción en lenguaje natural de qué extraer
                Ej: "Extract company name, email, phone, owner name, business hours"
        model: "ollama/<modelo>" o "openai/gpt-4o-mini", etc.
        ollama_base_url: para Ollama local

    Returns:
        Dict con los campos extraídos, o None si falla.
    """
    if not HAS_SCRAPEGRAPH:
        logger.debug("scrapegraphai no instalado; saltando NLP extraction")
        return None

    try:
        from scrapegraphai.graphs import SmartScraperGraph  # type: ignore

        config = {
            "llm": {"model": model, "base_url": ollama_base_url},
            "verbose": False,
            "headless": True,
        }

        smart_scraper = SmartScraperGraph(
            prompt=prompt,
            source=url_or_html,
            config=config,
        )
        return smart_scraper.run()
    except Exception as e:  # noqa: BLE001
        logger.warning("ScrapeGraphAI err: %s", e)
        return None


def extract_skydropx_lead(url: str, model: str = "ollama/llama3.2:3b") -> dict | None:
    """
    Prompt curado para extraer datos de un sitio comercial MX
    en el contexto de leads para Skydropx.
    """
    prompt = """
    Extract the following information from this Mexican business website,
    in Spanish. Return a JSON object with these keys:

    - empresa: nombre comercial completo
    - nombre_persona: nombre completo del dueño/fundador/CEO si se menciona
    - emails: lista de todos los emails de contacto visibles
    - telefonos: lista de teléfonos con formato (lada + número)
    - whatsapp: número de WhatsApp si lo mencionan
    - direccion: dirección física en México
    - ciudad: ciudad y estado mexicano
    - giro: a qué se dedica el negocio (ej: "venta de ropa femenina")
    - envia_a_toda_mx: true si menciona "envíos a toda la república" o similar
    - paqueterias_usa: lista de paqueterías que ya usan (DHL, Estafeta, FedEx, etc.)
    - plataforma_ecommerce: si detectas que usa Shopify/Tiendanube/WooCommerce/etc.
    - redes_sociales: dict con instagram, facebook, tiktok si existen

    Si algún dato no aparece en la página, omítelo o pon null.
    """
    return extract_with_prompt(url, prompt, model=model)


__all__ = [
    "is_available", "extract_with_prompt", "extract_skydropx_lead",
    "HAS_SCRAPEGRAPH",
]
