"""
Checkpoint + Resume — gap #8.

Permite que el pipeline sobreviva interrupciones:
- Ctrl+C del usuario
- Crash de proceso
- Apagón de la laptop
- Timeout de red

Cómo funciona:
- Cada agente, al finalizar, guarda su `state` actualizado en JSON
- `state` se persiste en `data/checkpoints/<job_id>.json`
- Si llamas `fenix run --resume <job_id>`, se carga el state y se continua
  desde el agente siguiente al último completado
- Si llamas `fenix run --resume-last`, busca el último checkpoint pendiente

Para Skydropx: imprescindible en mega-corridas de 10K+ leads que pueden tomar horas.
Si la laptop se apaga a las 5pm, el lunes siguiente puede reanudar.

NO checkpoint:
- run_quick (≤50 leads) → no vale la pena el overhead
- runs <2 minutos → mejor reiniciar de cero

Activación: `--enable-checkpoint` o automático si meta >= 500.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

CHECKPOINT_DIR = Path("data/checkpoints")
CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)


def _serialize(obj: Any) -> Any:
    """Serializa objetos complejos para JSON."""
    if is_dataclass(obj) and not isinstance(obj, type):
        d = {}
        for k, v in asdict(obj).items():
            d[k] = _serialize(v)
        return d
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_serialize(x) for x in obj]
    if hasattr(obj, "value"):  # Enums
        return obj.value
    return obj


def save_checkpoint(state, agent_completed: str) -> Path:
    """
    Guarda el state después de completar un agente.
    Devuelve la ruta del archivo.
    """
    job_id = state.job_id
    cp_path = CHECKPOINT_DIR / f"{job_id}.json"

    # Serializar el state completo
    try:
        data = {
            "job_id": job_id,
            "agent_completed": agent_completed,
            "checkpointed_at": datetime.now().isoformat(),
            "fase_actual": state.fase_actual,
            "started_at": state.started_at.isoformat() if hasattr(state.started_at, "isoformat") else str(state.started_at),
            "plan": _serialize(state.plan),
            "stats": _serialize(state.stats),
            "errors": _serialize(state.errors),
            "candidatos": [_serialize(c) for c in state.candidatos],
            "leads_hunted": [_serialize(c) for c in state.leads_hunted],
            "n_leads_verified": len(state.leads_verified),
            "n_leads_enriched": len(state.leads_enriched),
            "exports": state.exports,
        }
        cp_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        logger.debug("Checkpoint guardado: %s (después de %s)", cp_path.name, agent_completed)
        return cp_path
    except Exception as e:  # noqa: BLE001
        logger.warning("No se pudo guardar checkpoint: %s", e)
        return cp_path


def load_checkpoint(job_id: str):
    """Carga un state desde checkpoint. Devuelve PipelineState o None."""
    cp_path = CHECKPOINT_DIR / f"{job_id}.json"
    if not cp_path.exists():
        return None
    try:
        data = json.loads(cp_path.read_text(encoding="utf-8"))
        return _reconstruct_state(data)
    except Exception as e:  # noqa: BLE001
        logger.error("No se pudo cargar checkpoint %s: %s", job_id, e)
        return None


def _reconstruct_state(data: dict):
    """Reconstruye PipelineState desde dict checkpoint."""
    from src.core.models import (
        PipelineState, ResearchPlan, RawRecord,
        ModeloNegocio, Canal, NivelUsuario, Estrategia,
    )

    # Reconstruir plan
    plan_data = data.get("plan", {})
    plan = ResearchPlan(
        nicho=plan_data.get("nicho", ""),
        meta=plan_data.get("meta", 100),
        zona=plan_data.get("zona", "nacional"),
        modelo=ModeloNegocio(plan_data.get("modelo", "UNKNOWN")),
        canal=Canal(plan_data.get("canal", "web")),
        nivel_usuario=NivelUsuario(plan_data.get("nivel_usuario", "INTERMEDIO")),
        estrategia=Estrategia(plan_data.get("estrategia", "standard")),
        scianes=plan_data.get("scianes", []),
        estados=plan_data.get("estados", []),
        estratos=plan_data.get("estratos", []),
        sources_enabled=plan_data.get("sources_enabled", []),
        extras=plan_data.get("extras", {}),
    )

    # Reconstruir state
    state = PipelineState(plan=plan, job_id=data.get("job_id"))
    state.fase_actual = data.get("fase_actual", "init")
    state.stats = data.get("stats", {})
    state.errors = data.get("errors", [])
    try:
        state.started_at = datetime.fromisoformat(data["started_at"])
    except Exception:  # noqa: BLE001
        state.started_at = datetime.now()

    # Reconstruir candidatos (RawRecords)
    for c_data in data.get("candidatos", []):
        try:
            # Quitar campos que no son de RawRecord (datetime str, etc.)
            valid_fields = set(RawRecord.__dataclass_fields__.keys())
            clean = {k: v for k, v in c_data.items() if k in valid_fields}
            # fecha_descubierto se queda como str — el código posterior la tolera
            clean.pop("fecha_descubierto", None)
            state.candidatos.append(RawRecord(**clean))
        except Exception as e:  # noqa: BLE001
            logger.debug("Skip candidato malformado: %s", e)

    for c_data in data.get("leads_hunted", []):
        try:
            valid_fields = set(RawRecord.__dataclass_fields__.keys())
            clean = {k: v for k, v in c_data.items() if k in valid_fields}
            clean.pop("fecha_descubierto", None)
            state.leads_hunted.append(RawRecord(**clean))
        except Exception:  # noqa: BLE001
            pass

    state.exports = data.get("exports", {})
    return state


def list_pending() -> list[dict]:
    """Lista checkpoints pendientes (no completados con dispatcher)."""
    out = []
    if not CHECKPOINT_DIR.exists():
        return out
    for cp in CHECKPOINT_DIR.glob("*.json"):
        try:
            data = json.loads(cp.read_text())
            # Si llegó a dispatcher, ya está completo
            if data.get("agent_completed") == "self_improver":
                continue
            out.append({
                "job_id": data.get("job_id"),
                "agent_completed": data.get("agent_completed"),
                "checkpointed_at": data.get("checkpointed_at"),
                "nicho": data.get("plan", {}).get("nicho"),
                "meta": data.get("plan", {}).get("meta"),
                "n_candidatos": len(data.get("candidatos", [])),
                "file": str(cp),
            })
        except Exception:  # noqa: BLE001
            continue
    out.sort(key=lambda x: x.get("checkpointed_at") or "", reverse=True)
    return out


def get_last_pending() -> str | None:
    """Devuelve el job_id del último checkpoint pendiente."""
    pending = list_pending()
    return pending[0]["job_id"] if pending else None


def cleanup_old(days: int = 30) -> int:
    """Borra checkpoints más viejos que N días. Devuelve cuántos borró."""
    import time
    if not CHECKPOINT_DIR.exists():
        return 0
    cutoff = time.time() - (days * 86400)
    n_deleted = 0
    for cp in CHECKPOINT_DIR.glob("*.json"):
        if cp.stat().st_mtime < cutoff:
            try:
                cp.unlink()
                n_deleted += 1
            except OSError:
                pass
    return n_deleted


def delete_checkpoint(job_id: str) -> bool:
    """Borra un checkpoint específico (ej: tras corrida exitosa)."""
    cp = CHECKPOINT_DIR / f"{job_id}.json"
    if cp.exists():
        try:
            cp.unlink()
            return True
        except OSError:
            pass
    return False


__all__ = [
    "save_checkpoint", "load_checkpoint",
    "list_pending", "get_last_pending",
    "cleanup_old", "delete_checkpoint",
    "CHECKPOINT_DIR",
]
