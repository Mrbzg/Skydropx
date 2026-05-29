"""
Modelos de dominio compartidos por todos los agentes y fuentes.

Mantenemos zero deps (solo stdlib) para que se pueda importar desde cualquier lado
sin instalar pydantic.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any


# ---------------- Enums ----------------

class ModeloNegocio(str, Enum):
    B2B = "B2B"
    B2C = "B2C"
    C2C = "C2C"
    D2C = "D2C"
    C2B = "C2B"
    UNKNOWN = "UNKNOWN"


class Canal(str, Enum):
    WEB = "web"
    SOCIAL = "social"
    MARKETPLACE = "marketplace"
    FISICA = "fisica"
    MIXTO = "mixto"


class Estrategia(str, Enum):
    QUICK = "quick"
    STANDARD = "standard"
    DEEP = "deep"
    ENTERPRISE = "enterprise"


class Bucket(str, Enum):
    """Bucket de calidad del contacto."""
    COMPLETO = "COMPLETO"
    SOLO_EMAIL = "SOLO_EMAIL"
    SOLO_TEL = "SOLO_TEL"
    SIN_CONTACTO = "SIN_CONTACTO"
    DESCARTADO = "DESCARTADO"


class SkydropxPlan(str, Enum):
    STARTER = "Starter"
    PYME = "PyME"
    ENTERPRISE = "Enterprise"


class NivelUsuario(str, Enum):
    NOVICIO = "NOVICIO"
    INTERMEDIO = "INTERMEDIO"
    EXPERTO = "EXPERTO"


# ---------------- ResearchPlan (input al pipeline) ----------------

@dataclass
class ResearchPlan:
    """Lo que el usuario pide. Producido por Strategic Discovery Protocol."""
    nicho: str                       # texto libre, normalizable
    meta: int = 100                  # cuántos leads quiere
    zona: str = "nacional"           # estado/ciudad MX o 'nacional'
    modelo: ModeloNegocio = ModeloNegocio.UNKNOWN
    canal: Canal = Canal.WEB
    nivel_usuario: NivelUsuario = NivelUsuario.INTERMEDIO
    estrategia: Estrategia = Estrategia.STANDARD
    scianes: list[str] = field(default_factory=list)
    estados: list[str] = field(default_factory=list)
    estratos: list[str] = field(default_factory=list)  # ["3","4","5","6"]
    sources_enabled: list[str] = field(default_factory=list)
    extras: dict[str, Any] = field(default_factory=dict)

    def auto_estrategia(self) -> Estrategia:
        """Selecciona estrategia según meta si no se especificó."""
        if self.meta <= 50:
            return Estrategia.QUICK
        if self.meta <= 1_000:
            return Estrategia.STANDARD
        if self.meta <= 10_000:
            return Estrategia.DEEP
        return Estrategia.ENTERPRISE

    def to_dict(self) -> dict:
        d = asdict(self)
        # Convertir enums a strings
        for k, v in d.items():
            if isinstance(v, Enum):
                d[k] = v.value
        return d


# ---------------- RawRecord (output de las fuentes, antes de validación) ----------------

@dataclass
class RawRecord:
    """Dato crudo de una fuente, antes de pasar por Verifier."""
    source: str                      # 'denue', 'mercadolibre', 'dorks_shopify', etc.
    empresa: str | None = None       # razón social o nombre comercial
    nombre_comercial: str | None = None
    nombre_persona: str | None = None
    rfc: str | None = None
    email: str | None = None
    telefono: str | None = None
    whatsapp: str | None = None
    sitio_web: str | None = None
    instagram: str | None = None
    linkedin: str | None = None
    facebook: str | None = None
    direccion: str | None = None
    colonia: str | None = None
    cp: str | None = None
    municipio: str | None = None
    estado: str | None = None
    scian: str | None = None
    giro_descripcion: str | None = None
    tamano: str | None = None        # Micro/Pequeña/Mediana/Grande
    longitud: float | None = None
    latitud: float | None = None
    fecha_descubierto: datetime = field(default_factory=datetime.now)
    metadata: dict[str, Any] = field(default_factory=dict)

    def fingerprint(self) -> str:
        """Clave única para dedup. Prioriza email > tel > dominio > nombre+estado."""
        if self.email and "@" in self.email:
            return f"email:{self.email.lower().strip()}"
        if self.telefono:
            digits = "".join(c for c in self.telefono if c.isdigit())[-10:]
            if len(digits) == 10:
                return f"tel:{digits}"
        if self.sitio_web:
            d = self.sitio_web.lower().replace("https://", "").replace("http://", "")
            d = d.lstrip("www.").rstrip("/")
            # quitar path: solo conservar el host
            d = d.split("/")[0]
            if d:
                return f"dom:{d}"
        if self.empresa and self.estado:
            return f"nombre:{self.empresa.lower().strip()}|{self.estado.lower().strip()}"
        return f"id:{uuid.uuid4()}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["fecha_descubierto"] = self.fecha_descubierto.isoformat()
        return d


# ---------------- Lead (post-verificación, listo para exportar) ----------------

@dataclass
class Lead:
    """Lead calificado en formato CSV v4.0 (26 columnas)."""
    lead_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    modelo: str = "UNKNOWN"
    tipo: str = "empresa"                   # empresa|persona|profesionista
    nombre: str = ""
    empresa: str = ""
    rfc: str = ""
    email: str = ""
    email_score: int = 0
    telefono: str = ""
    whatsapp: str = "no"
    instagram: str = ""
    linkedin: str = ""
    facebook: str = ""
    ubicacion: str = ""
    estado: str = ""
    giro: str = ""
    tamano: str = ""
    skydropx_plan: str = ""
    soluciones: str = ""
    value_proposition: str = ""
    priority_score: int = 0
    scoring: int = 0
    tipo_lead: str = "frio"                 # caliente|frio
    fuentes: str = ""                       # csv de fuentes
    first_seen: str = ""
    version: str = "4.0"

    # No exportables (uso interno)
    _bucket: str = "RAW"
    _scoring_breakdown: dict = field(default_factory=dict)
    _raw_records: list[dict] = field(default_factory=list)

    @classmethod
    def csv_columns(cls) -> list[str]:
        """Las 26 columnas exactas del schema v4.0."""
        return [
            "lead_id", "modelo", "tipo", "nombre", "empresa", "rfc",
            "email", "email_score", "telefono", "whatsapp",
            "instagram", "linkedin", "facebook",
            "ubicacion", "estado", "giro", "tamano",
            "skydropx_plan", "soluciones", "value_proposition",
            "priority_score", "scoring", "tipo_lead", "fuentes",
            "first_seen", "version",
        ]

    def to_csv_row(self) -> dict[str, Any]:
        d = asdict(self)
        return {k: d[k] for k in self.csv_columns()}

    def to_full_dict(self) -> dict:
        return asdict(self)


# ---------------- PipelineState (compartido entre agentes) ----------------

@dataclass
class PipelineState:
    plan: ResearchPlan
    job_id: str = field(default_factory=lambda: f"fnx_{uuid.uuid4().hex[:12]}")
    fase_actual: str = "init"
    started_at: datetime = field(default_factory=datetime.now)
    checkpoint_at: datetime | None = None

    # Datos en pipeline
    candidatos: list[RawRecord] = field(default_factory=list)
    leads_hunted: list[RawRecord] = field(default_factory=list)
    leads_verified: list[Lead] = field(default_factory=list)
    leads_enriched: list[Lead] = field(default_factory=list)
    exports: dict[str, str] = field(default_factory=dict)

    # Métricas / errores
    stats: dict[str, Any] = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)

    def add_error(self, code: str, source: str, msg: str) -> None:
        self.errors.append({
            "code": code, "source": source, "msg": msg,
            "ts": datetime.now().isoformat(),
        })

    def to_dict(self) -> dict:
        return {
            "job_id": self.job_id,
            "fase_actual": self.fase_actual,
            "started_at": self.started_at.isoformat(),
            "checkpoint_at": self.checkpoint_at.isoformat() if self.checkpoint_at else None,
            "plan": self.plan.to_dict(),
            "stats": self.stats,
            "errors": self.errors,
            "n_candidatos": len(self.candidatos),
            "n_verified": len(self.leads_verified),
            "n_enriched": len(self.leads_enriched),
            "exports": self.exports,
        }


__all__ = [
    "ModeloNegocio", "Canal", "Estrategia", "Bucket", "SkydropxPlan", "NivelUsuario",
    "ResearchPlan", "RawRecord", "Lead", "PipelineState",
]
