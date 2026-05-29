"""
Strategic Discovery Protocol — interactivo CLI + estructurado MCP.

Implementa la fase 0 del workflow Fénix:
  1. Parsea texto libre del usuario ("quiero leads de ropa")
  2. Detecta intent + extrae campos disponibles (nicho, zona, modelo)
  3. Detecta campos faltantes
  4. CLI: pregunta interactivamente con prompts
  5. MCP: devuelve estructura con `needs_input` y `next_question`
  6. Cuando todo está completo → devuelve ResearchPlan listo para run_pipeline

Sin IA: regex + diccionarios + heurísticas. La "inteligencia natural" la pone
el asistente (Claude/opencode) que llama a este endpoint vía MCP.
"""
from __future__ import annotations

import json
import re
import sys
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

from src.core.models import (
    ResearchPlan, ModeloNegocio, Canal, Estrategia, NivelUsuario,
)

# Catálogo de nichos (para resolver SCIAN)
NICHO_PATH = Path(__file__).resolve().parents[2] / "data" / "nicho_scian.json"


def _load_nichos() -> dict:
    if NICHO_PATH.exists():
        return json.loads(NICHO_PATH.read_text(encoding="utf-8")).get("nichos", {})
    return {}


NICHOS_CATALOG = _load_nichos()


# ---------------- Detección de campos en texto libre ----------------

# Estados/ciudades comunes MX
ZONAS_PATTERNS = {
    "CDMX": [r"\bcdmx\b", r"ciudad\s+de\s+m[ée]xico", r"\bdf\b", r"distrito\s+federal"],
    "Jalisco": [r"jalisco", r"\bgdl\b", r"guadalajara", r"zapopan", r"tlaquepaque"],
    "Nuevo Leon": [r"nuevo\s+le[oó]n", r"\bnl\b", r"\bmty\b", r"monterrey", r"san\s+pedro"],
    "Puebla": [r"puebla"],
    "Queretaro": [r"quer[ée]taro", r"\bqro\b"],
    "Estado de Mexico": [r"edomex", r"estado\s+de\s+m[ée]xico", r"toluca", r"naucalpan"],
    "Yucatan": [r"yucat[áa]n", r"m[ée]rida"],
    "Veracruz": [r"veracruz", r"xalapa"],
    "Guanajuato": [r"guanajuato", r"\ble[oó]n\b", r"irapuato"],
    "Coahuila": [r"coahuila", r"saltillo", r"torre[óo]n"],
    "Chihuahua": [r"chihuahua", r"ciudad\s+ju[áa]rez"],
    "Baja California": [r"baja\s+california", r"tijuana", r"mexicali"],
    "Sinaloa": [r"sinaloa", r"culiac[áa]n", r"mazatl[áa]n"],
    "Sonora": [r"sonora", r"hermosillo"],
    "Quintana Roo": [r"quintana\s+roo", r"canc[úu]n", r"playa\s+del\s+carmen"],
    "nacional": [r"\bnacional\b", r"todo\s+m[ée]xico", r"toda\s+la\s+rep[úu]blica"],
}

MODELOS_PATTERNS = {
    "B2B": [r"\bb2b\b", r"business\s+to\s+business", r"empresarial", r"corporativo",
            r"mayorist[ao]s?", r"distribuidor[ae]s?", r"3pl", r"fulfillment"],
    "B2C": [r"\bb2c\b", r"business\s+to\s+consumer", r"venta\s+al\s+p[úu]blico",
            r"consumidor\s+final", r"retail"],
    "C2C": [r"\bc2c\b", r"consumer\s+to\s+consumer", r"reventa", r"mercado\s+libre"],
    "D2C": [r"\bd2c\b", r"direct\s+to\s+consumer", r"directo\s+al\s+consumidor",
            r"marca\s+propia", r"emprendedor[ae]s?"],
    "C2B": [r"\bc2b\b", r"consumer\s+to\s+business", r"freelance",
            r"creador[ae]s?\s+de\s+contenido", r"influencer"],
}

CANALES_PATTERNS = {
    "web": [r"\bweb\b", r"sitio\s+web", r"p[áa]gina\s+web", r"tienda\s+online",
            r"shopify", r"tiendanube", r"woocommerce"],
    "social": [r"redes\s+sociales", r"instagram", r"facebook", r"tiktok",
                r"ig\s+shop"],
    "marketplace": [r"marketplace", r"mercado\s+libre", r"\bml\b", r"amazon"],
    "fisica": [r"tienda\s+f[íi]sica", r"local\s+f[íi]sico", r"sucursal"],
    "mixto": [r"omnichannel", r"omnicanal"],
}


# ---------------- Detectores ----------------

def detect_nicho(text: str) -> tuple[str | None, list[str]]:
    """Busca el nicho en el catálogo de aliases. Devuelve (nicho_canónico, [scianes])."""
    t = text.lower()
    for key, data in NICHOS_CATALOG.items():
        if key == "_meta":
            continue
        aliases = [key] + (data.get("aliases", []) if isinstance(data, dict) else [])
        for alias in aliases:
            if not alias:
                continue
            # word boundary match
            if re.search(rf"\b{re.escape(alias.lower())}\b", t):
                scianes = data.get("scianes", []) if isinstance(data, dict) else []
                return key, scianes
    return None, []


def detect_zona(text: str) -> str | None:
    t = text.lower()
    for zona, patterns in ZONAS_PATTERNS.items():
        for p in patterns:
            if re.search(p, t):
                return zona
    return None


def detect_modelo(text: str) -> str | None:
    t = text.lower()
    for modelo, patterns in MODELOS_PATTERNS.items():
        for p in patterns:
            if re.search(p, t):
                return modelo
    return None


def detect_canal(text: str) -> str | None:
    t = text.lower()
    for canal, patterns in CANALES_PATTERNS.items():
        for p in patterns:
            if re.search(p, t):
                return canal
    return None


def detect_meta(text: str) -> int | None:
    """Busca un número que pueda ser meta de leads."""
    t = text.lower()
    # "100 leads", "500 prospectos", "5 mil", "1k", "10K"
    m = re.search(r"(\d+(?:[\.,]\d+)?)\s*(?:mil|k\b)", t)
    if m:
        n = float(m.group(1).replace(",", "."))
        return int(n * 1000)
    m = re.search(r"(\d{2,7})\s*(?:leads?|prospectos?|empresas?|clientes?|contactos?)", t)
    if m:
        return int(m.group(1))
    m = re.search(r"\b(\d{2,7})\b", t)
    if m:
        n = int(m.group(1))
        if 10 <= n <= 1_000_000:
            return n
    return None


# ---------------- Sesión de Discovery ----------------

SESSIONS_DIR = Path("data/discovery_sessions")
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class DiscoverySession:
    session_id: str = field(default_factory=lambda: f"disc_{uuid.uuid4().hex[:10]}")
    original_text: str = ""
    nicho: str | None = None
    scianes: list[str] = field(default_factory=list)
    zona: str = "nacional"
    modelo: str | None = None
    canal: str | None = None
    meta: int | None = None
    nivel_usuario: str = "INTERMEDIO"
    extra_keywords: list[str] = field(default_factory=list)
    confirmed: dict[str, bool] = field(default_factory=dict)
    next_question: str | None = None
    status: str = "needs_input"     # needs_input | ready | confirmed

    REQUIRED_FIELDS = ("nicho", "zona", "modelo", "meta")

    def missing_fields(self) -> list[str]:
        out = []
        for f in self.REQUIRED_FIELDS:
            v = getattr(self, f, None)
            if not v:
                out.append(f)
        return out

    def is_complete(self) -> bool:
        return not self.missing_fields()

    def to_research_plan(self) -> ResearchPlan:
        try:
            modelo = ModeloNegocio(self.modelo) if self.modelo else ModeloNegocio.UNKNOWN
        except ValueError:
            modelo = ModeloNegocio.UNKNOWN
        try:
            canal = Canal(self.canal) if self.canal else Canal.WEB
        except ValueError:
            canal = Canal.WEB
        try:
            nivel = NivelUsuario(self.nivel_usuario)
        except ValueError:
            nivel = NivelUsuario.INTERMEDIO
        plan = ResearchPlan(
            nicho=self.nicho or "",
            meta=self.meta or 100,
            zona=self.zona,
            modelo=modelo,
            canal=canal,
            nivel_usuario=nivel,
            scianes=self.scianes,
        )
        plan.estrategia = plan.auto_estrategia()
        return plan

    def save(self) -> Path:
        p = SESSIONS_DIR / f"{self.session_id}.json"
        p.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2),
                     encoding="utf-8")
        return p

    @classmethod
    def load(cls, session_id: str) -> "DiscoverySession | None":
        p = SESSIONS_DIR / f"{session_id}.json"
        if not p.exists():
            return None
        return cls(**json.loads(p.read_text(encoding="utf-8")))


# ---------------- Parsing inicial desde texto libre ----------------

def parse_user_input(text: str) -> DiscoverySession:
    """
    'quiero 500 leads de ropa femenina en CDMX para B2B'
    → DiscoverySession(nicho='ropa', zona='CDMX', modelo='B2B', meta=500)
    """
    session = DiscoverySession(original_text=text)

    nicho, scianes = detect_nicho(text)
    if nicho:
        session.nicho = nicho
        session.scianes = scianes

    zona = detect_zona(text)
    if zona:
        session.zona = zona

    modelo = detect_modelo(text)
    if modelo:
        session.modelo = modelo

    canal = detect_canal(text)
    if canal:
        session.canal = canal

    meta = detect_meta(text)
    if meta:
        session.meta = meta

    _next_question(session)
    if session.is_complete():
        session.status = "ready"
    return session


def _next_question(session: DiscoverySession) -> None:
    """Establece la próxima pregunta a hacer al usuario."""
    missing = session.missing_fields()
    if not missing:
        session.next_question = None
        return
    f = missing[0]
    questions = {
        "nicho":  "¿Qué nicho o producto buscas? (ej: ropa, calzado, joyería, belleza, restaurantes)",
        "zona":   "¿Zona geográfica? (ej: CDMX, Jalisco, nacional)",
        "modelo": "¿Qué modelo de negocio? B2B (empresarial), B2C (retail), D2C (marca propia), C2C (reventa), C2B (freelance/creadores)",
        "meta":   "¿Cuántos leads necesitas? (ej: 100, 500, 5000)",
        "canal":  "¿Por qué canal venden? (web, social, marketplace, fisica, mixto)",
    }
    session.next_question = questions.get(f, f"Falta el campo: {f}")


# ---------------- Apply answer (CLI o MCP) ----------------

def apply_answer(session: DiscoverySession, field_name: str,
                  value: str) -> DiscoverySession:
    """Aplica una respuesta del usuario a una pregunta específica."""
    val = (value or "").strip()
    if field_name == "nicho":
        nicho, scianes = detect_nicho(val) if val else (None, [])
        session.nicho = nicho or val.lower()  # acepta nicho libre si no está en catálogo
        session.scianes = scianes
        if not nicho:
            session.extra_keywords.append(val)
    elif field_name == "zona":
        z = detect_zona(val)
        session.zona = z or val
    elif field_name == "modelo":
        m = detect_modelo(val)
        if not m:
            v_up = val.upper()
            if v_up in ("B2B", "B2C", "C2C", "D2C", "C2B"):
                m = v_up
        session.modelo = m
    elif field_name == "canal":
        c = detect_canal(val)
        session.canal = c or val.lower()
    elif field_name == "meta":
        n = detect_meta(val)
        if not n:
            try:
                n = int(val.replace(",", "").replace(".", ""))
            except ValueError:
                pass
        session.meta = n
    elif field_name == "nivel_usuario":
        if val.upper() in ("NOVICIO", "INTERMEDIO", "EXPERTO"):
            session.nivel_usuario = val.upper()

    _next_question(session)
    if session.is_complete():
        session.status = "ready"
    return session


# ---------------- CLI interactivo ----------------

def run_cli_discovery(initial_text: str = "") -> DiscoverySession:
    """
    Modo CLI interactivo. Usa input() en terminal.
    Si no hay TTY (ej: pipe), retorna sesión incompleta sin preguntar.
    """
    if initial_text:
        session = parse_user_input(initial_text)
    else:
        session = DiscoverySession()
        _next_question(session)

    # Sin TTY → no preguntar
    if not sys.stdin.isatty():
        return session

    print(f"\n🧭 Strategic Discovery Protocol (session: {session.session_id})")
    if initial_text:
        print(f"   Input inicial: {initial_text}")
        print(f"   Detectado: nicho={session.nicho!r} zona={session.zona!r} "
              f"modelo={session.modelo!r} meta={session.meta!r}")
        print()

    while not session.is_complete():
        q = session.next_question
        if not q:
            break
        print(f"\n❓ {q}")
        try:
            answer = input("→ ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n(cancelado)")
            return session
        if not answer:
            print("   (vacío — saltando, sesión quedará incompleta)")
            break
        missing = session.missing_fields()
        if missing:
            apply_answer(session, missing[0], answer)

    if session.is_complete():
        print(f"\n✓ Discovery completo:")
        print(f"   nicho={session.nicho}  scianes={session.scianes}")
        print(f"   zona={session.zona}  modelo={session.modelo}  canal={session.canal}")
        print(f"   meta={session.meta}  estrategia={session.to_research_plan().estrategia.value}")
        session.save()
    return session


__all__ = [
    "DiscoverySession", "parse_user_input", "apply_answer",
    "run_cli_discovery", "detect_nicho", "detect_zona",
    "detect_modelo", "detect_canal", "detect_meta",
]
