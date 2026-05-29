"""
Plans YAML — "campañas reusables" manuales.

NO es scheduling automático. Es un atajo para no escribir 10 flags cada vez.
El usuario corre el plan A VOLUNTAD: `fenix run --plan archivo.yaml`.

Formato del plan YAML:
    name: "Mi campaña"
    description: "Descripción opcional"
    nicho: "ropa"
    zona: "CDMX"
    modelo: "B2C"
    canal: "web"
    meta: 500
    mode: "standard"          # quick|standard|deep|enterprise
    sources:                   # opcional
      - denue
      - dorks
      - camaras
    scianes:                   # opcional, sobreescribe el catálogo
      - "4632"
      - "4633"
    estratos:                  # opcional, filtra tamaño DENUE
      - "3"
      - "4"
    enrich_max: 100
    deep_enrich_max: 50        # opcional, deep_enrich top-N
    deep_enrich_tools:         # opcional
      - holehe
      - maigret
    format: "csv,json"
    include_large: false       # permitir estrato 7
    include_medianas_grandes: false

    # Avanzado: dorks customizados
    extras:
      dork_categorias: ["envios_mx", "shopify"]
      dork_queries_extra:
        - '"compra y gana" "mundial" site:.mx'

    # Tags para tracking (opcional)
    tags:
      - "skydropx"
      - "recurrente"
      - "semana1"

Zero deps: usa parser YAML mínimo propio si PyYAML no está instalado.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

PLANS_DIR = Path("plans")
PLANS_HISTORY_PATH = Path("data/plans_history.json")


# ---------------- YAML parser opcional ----------------

try:
    import yaml as _yaml
    HAS_PYYAML = True
except ImportError:
    HAS_PYYAML = False


def _minimal_yaml_parse(text: str) -> dict:
    """
    Parser YAML mínimo (zero deps) para los casos simples que usa Fénix:
    - claves planas: `nicho: ropa`
    - listas: `- item`
    - comentarios con #

    NO soporta: anchors, multi-document, multiline strings, flow style.
    Para YAML complejo instalar `pip install pyyaml`.
    """
    if HAS_PYYAML:
        return _yaml.safe_load(text) or {}

    out: dict[str, Any] = {}
    stack: list[tuple[int, Any, str]] = [(-1, out, "")]
    current_list: list | None = None
    current_list_indent = -1
    current_list_key = ""

    for raw_line in text.splitlines():
        # Strip comentarios al final de línea (cuidado con # dentro de strings)
        if "#" in raw_line and not raw_line.strip().startswith('"'):
            # quitar comentario que no esté entre comillas
            in_str = False
            for i, c in enumerate(raw_line):
                if c in ('"', "'"):
                    in_str = not in_str
                if c == "#" and not in_str:
                    raw_line = raw_line[:i]
                    break

        if not raw_line.strip():
            current_list = None
            continue

        # Indentación
        indent = len(raw_line) - len(raw_line.lstrip())
        line = raw_line.strip()

        # Item de lista
        if line.startswith("- "):
            value = line[2:].strip()
            value = _coerce_scalar(value)
            if current_list is None or indent <= current_list_indent:
                # Lista nueva (no se anidaron bien) - intentar adjuntarla al último dict
                pass
            if current_list is not None:
                current_list.append(value)
            continue

        # key: value
        if ":" in line:
            key, _, val = line.partition(":")
            key = key.strip()
            val = val.strip()
            if not val:
                # Empieza una lista o dict anidado
                # Asumimos lista (caso común en plans)
                new_list: list = []
                out[key] = new_list
                current_list = new_list
                current_list_indent = indent
                current_list_key = key
            else:
                out[key] = _coerce_scalar(val)
                current_list = None
        # Si no contiene `:`, se ignora silenciosamente
    return out


def _coerce_scalar(v: str) -> Any:
    """Convierte string YAML a tipo Python correcto."""
    v = v.strip()
    if v.startswith(('"', "'")) and v.endswith(('"', "'")):
        return v[1:-1]
    if v.lower() in ("true", "yes"):
        return True
    if v.lower() in ("false", "no"):
        return False
    if v.lower() in ("null", "~", ""):
        return None
    # Número entero
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    if re.fullmatch(r"-?\d+\.\d+", v):
        return float(v)
    return v


# ---------------- Plan model ----------------

@dataclass
class Plan:
    """Representación tipada de un plan YAML."""
    name: str = ""
    description: str = ""
    nicho: str = ""
    zona: str = "nacional"
    modelo: str = ""
    canal: str = "web"
    meta: int = 100
    mode: str = ""                                   # auto si vacío
    sources: list[str] = field(default_factory=list)
    scianes: list[str] = field(default_factory=list)
    estratos: list[str] = field(default_factory=list)
    enrich_max: int = 50
    deep_enrich_max: int = 0
    deep_enrich_tools: list[str] = field(default_factory=lambda: ["holehe"])
    format: str = "csv,json"
    include_large: bool = False
    include_medianas_grandes: bool = False
    extras: dict[str, Any] = field(default_factory=dict)
    tags: list[str] = field(default_factory=list)
    source_file: str = ""

    @classmethod
    def from_dict(cls, d: dict, source_file: str = "") -> "Plan":
        valid_fields = {f for f in cls.__dataclass_fields__}
        kwargs = {k: v for k, v in d.items() if k in valid_fields}
        # Validar tipos básicos
        if "sources" in kwargs and isinstance(kwargs["sources"], str):
            kwargs["sources"] = [s.strip() for s in kwargs["sources"].split(",")]
        if "scianes" in kwargs and isinstance(kwargs["scianes"], (str, int)):
            kwargs["scianes"] = [str(kwargs["scianes"])]
        if "estratos" in kwargs and isinstance(kwargs["estratos"], (str, int)):
            kwargs["estratos"] = [str(kwargs["estratos"])]
        if "deep_enrich_tools" in kwargs and isinstance(kwargs["deep_enrich_tools"], str):
            kwargs["deep_enrich_tools"] = [s.strip() for s in kwargs["deep_enrich_tools"].split(",")]
        kwargs["source_file"] = source_file
        return cls(**kwargs)

    @classmethod
    def load(cls, path: str | Path) -> "Plan":
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Plan no encontrado: {p}")
        text = p.read_text(encoding="utf-8")
        if p.suffix in (".json",):
            data = json.loads(text)
        else:
            data = _minimal_yaml_parse(text)
        plan = cls.from_dict(data, source_file=str(p))
        plan.validate()
        return plan

    def validate(self) -> list[str]:
        """Devuelve lista de errores. Vacía si todo OK."""
        errors = []
        if not self.nicho:
            errors.append("falta 'nicho'")
        if not self.zona:
            errors.append("falta 'zona'")
        if self.meta <= 0:
            errors.append("'meta' debe ser > 0")
        if self.mode and self.mode not in ("quick", "standard", "deep", "enterprise"):
            errors.append(f"'mode' inválido: {self.mode}")
        if self.modelo and self.modelo not in ("B2B", "B2C", "C2C", "D2C", "C2B"):
            errors.append(f"'modelo' inválido: {self.modelo}")
        if errors:
            raise ValueError(f"Plan {self.source_file} inválido: {', '.join(errors)}")
        return errors

    def to_research_plan(self):
        """Convierte a ResearchPlan para correr el pipeline."""
        from src.core.models import (
            ResearchPlan, ModeloNegocio, Canal, Estrategia, NivelUsuario,
        )
        try:
            modelo = ModeloNegocio(self.modelo) if self.modelo else ModeloNegocio.UNKNOWN
        except ValueError:
            modelo = ModeloNegocio.UNKNOWN
        try:
            canal = Canal(self.canal)
        except ValueError:
            canal = Canal.WEB
        plan = ResearchPlan(
            nicho=self.nicho, meta=self.meta, zona=self.zona,
            modelo=modelo, canal=canal,
            nivel_usuario=NivelUsuario.INTERMEDIO,
            scianes=self.scianes, estratos=self.estratos,
            sources_enabled=self.sources,
        )
        # Estrategia: si el plan la trae, úsala; sino auto por meta
        if self.mode:
            plan.estrategia = Estrategia(self.mode)
        else:
            plan.estrategia = plan.auto_estrategia()
        # Pasar extras (dorks customizados, etc.)
        plan.extras.update(self.extras or {})
        return plan


# ---------------- Plans discovery + listing ----------------

def list_plans(plans_dir: Path | str = PLANS_DIR) -> list[dict]:
    """Lista plans disponibles en el directorio."""
    d = Path(plans_dir)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")) + sorted(d.glob("*.json")):
        try:
            plan = Plan.load(p)
            out.append({
                "file": str(p),
                "name": plan.name or p.stem,
                "description": plan.description,
                "nicho": plan.nicho, "zona": plan.zona,
                "modelo": plan.modelo, "meta": plan.meta,
                "mode": plan.mode or "(auto)",
                "tags": plan.tags,
            })
        except Exception as e:  # noqa: BLE001
            logger.warning("Plan inválido %s: %s", p, e)
            out.append({"file": str(p), "error": str(e)})
    return out


# ---------------- History (cuándo se ha corrido cada plan) ----------------

def _load_history() -> list[dict]:
    if not PLANS_HISTORY_PATH.exists():
        return []
    try:
        return json.loads(PLANS_HISTORY_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return []


def _save_history(entries: list[dict]) -> None:
    PLANS_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLANS_HISTORY_PATH.write_text(
        json.dumps(entries[-200:], indent=2, ensure_ascii=False),  # cap a 200
        encoding="utf-8",
    )


def record_plan_run(plan: Plan, job_id: str, stats: dict) -> None:
    """Registra que el usuario corrió este plan."""
    history = _load_history()
    history.append({
        "ts": datetime.now().isoformat(),
        "plan_file": plan.source_file,
        "plan_name": plan.name,
        "job_id": job_id,
        "nicho": plan.nicho, "zona": plan.zona, "meta": plan.meta,
        "n_leads": stats.get("profiler", {}).get("n_leads", 0)
                    or stats.get("dispatcher", {}).get("n_exportados", 0),
        "n_completo": stats.get("profiler", {}).get("n_completo", 0),
        "duration_sec": stats.get("pipeline_duration_sec", 0),
        "tags": plan.tags,
    })
    _save_history(history)


def history(plan_file: str | None = None, limit: int = 20) -> list[dict]:
    """Devuelve historial de corridas. Si plan_file, filtra por ese plan."""
    h = _load_history()
    if plan_file:
        h = [e for e in h if plan_file in e.get("plan_file", "")]
    return list(reversed(h))[:limit]


# ---------------- Run helper ----------------

def run_plan(plan_path: str | Path, **overrides) -> dict:
    """
    Carga un plan, lo ejecuta, registra en historial.
    overrides permite sobreescribir campos desde CLI (--meta 200, etc.).
    """
    plan = Plan.load(plan_path)

    # Aplicar overrides
    for k, v in overrides.items():
        if v is not None and hasattr(plan, k):
            setattr(plan, k, v)

    research_plan = plan.to_research_plan()

    from src.agents.pipeline import run_pipeline
    state = run_pipeline(
        research_plan,
        enrich_max=plan.enrich_max,
        deep_enrich_max=plan.deep_enrich_max,
        deep_enrich_tools=tuple(plan.deep_enrich_tools),
        formats=plan.format.split(",") if plan.format else ["csv", "json"],
    )

    record_plan_run(plan, state.job_id, state.stats)

    return {
        "plan_file": str(plan_path),
        "plan_name": plan.name,
        "job_id": state.job_id,
        "duration_sec": state.stats.get("pipeline_duration_sec"),
        "n_leads": len(state.leads_enriched),
        "stats": state.stats,
        "exports": state.exports,
    }


__all__ = [
    "Plan", "load_plans", "list_plans", "history",
    "record_plan_run", "run_plan",
    "HAS_PYYAML", "PLANS_DIR", "PLANS_HISTORY_PATH",
]


def load_plans(plans_dir: Path | str = PLANS_DIR) -> list[Plan]:
    """Alias para devolver objetos Plan directamente."""
    d = Path(plans_dir)
    if not d.exists():
        return []
    out: list[Plan] = []
    for p in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")) + sorted(d.glob("*.json")):
        try:
            out.append(Plan.load(p))
        except Exception:  # noqa: BLE001
            continue
    return out
