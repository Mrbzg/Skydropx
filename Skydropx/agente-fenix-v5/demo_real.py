#!/usr/bin/env python3
"""
Demo de Agente Fénix v5 — corre el pipeline completo (7 agentes) con DENUE real.

Uso simple:
    cd agente-fenix-v5
    python3 demo_real.py

Uso CLI completo (recomendado):
    python3 -m src.skill.cli fenix healthcheck
    python3 -m src.skill.cli fenix run --nicho "ropa" --zona CDMX --meta 100
    python3 -m src.skill.cli fenix source denue cuantificar --actividad 46 --entidad 09
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Cargar .env si existe
env = Path(__file__).parent / ".env"
if env.exists():
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).parent))

import logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

from src.core.models import ResearchPlan, ModeloNegocio, Estrategia, Canal
from src.agents.pipeline import run_pipeline


def main():
    print("=" * 72)
    print("  Agente Fénix v5 — Demo del pipeline completo (7 agentes)")
    print("  Datos REALES desde DENUE/INEGI. Costo: $0 USD.")
    print("=" * 72)

    plan = ResearchPlan(
        nicho="calzado",
        modelo=ModeloNegocio.B2C,
        canal=Canal.WEB,
        zona="CDMX",
        meta=50,
        estrategia=Estrategia.QUICK,
        sources_enabled=["denue"],
    )

    state = run_pipeline(plan, enrich_max=10, formats=["csv", "json"])

    print()
    print("=" * 72)
    print(f"✓ Pipeline completado en {state.stats.get('pipeline_duration_sec', 0)}s")
    print(f"  Leads exportados: {len(state.leads_enriched)}")
    print(f"  Job ID: {state.job_id}")
    print()
    if state.exports:
        print("📁 Archivos generados:")
        for fmt, path in state.exports.items():
            print(f"   · {fmt.upper()}  {path}")
    print()
    print("🏆 Top 5 leads con datos completos:")
    print("-" * 72)
    ready = [l for l in state.leads_enriched
             if l.email != "DATO_NO_VERIFICABLE" and l.telefono != "DATO_NO_VERIFICABLE"]
    for i, lead in enumerate(sorted(ready, key=lambda l: -l.scoring)[:5], 1):
        print(f"\n  [{i}] {lead.nombre}  (score={lead.scoring}, {lead.tipo_lead})")
        print(f"      Empresa: {lead.empresa}")
        print(f"      Giro:    {lead.giro}")
        print(f"      Email:   {lead.email}")
        print(f"      Tel:     {lead.telefono}   WhatsApp: {lead.whatsapp}")
        print(f"      Ubicación: {lead.ubicacion}, {lead.estado}")
        print(f"      Tamaño:  {lead.tamano}  →  Plan Skydropx: {lead.skydropx_plan}")
        print(f"      Value:   {lead.value_proposition}")
    print()
    print(f"💰 Costo total: $0.00 USD")
    print(f"⚠ Verificación ética LFPDPPP: datos públicos del DENUE/INEGI ({len(state.leads_enriched)} registros).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
