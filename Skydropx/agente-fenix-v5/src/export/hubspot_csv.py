"""
HubSpot CSV Exporter — genera archivos listos para import manual.

HubSpot acepta CSVs con headers específicos para Contacts y Companies. Si los
headers coinciden EXACTO, HubSpot los mapea automáticamente al importar (sin
necesidad de mapeo manual columna por columna).

Genera 3 archivos:
1. <run_id>_contacts.csv      → personas (con email/phone)
2. <run_id>_companies.csv     → empresas (con domain/industry)
3. README_import.txt          → instrucciones paso a paso

Validaciones aplicadas antes de exportar:
- Email regex válido
- Teléfono normalizado E.164
- Sin DATO_NO_VERIFICABLE en campos requeridos
- Dedup de contactos por email
"""
from __future__ import annotations

import csv
import json
import logging
from src.scoring.tiered_filter import (
    Tier, classify_tier, filter_by_tier, get_tier_summary,
)
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------- Headers de HubSpot (oficiales) ----------------

# Headers reconocidos automáticamente por HubSpot al importar Contacts CSV
HUBSPOT_CONTACT_HEADERS = [
    "First Name",
    "Last Name",
    "Email",
    "Phone Number",
    "Mobile Phone Number",
    "Job Title",
    "Company Name",
    "Industry",
    "City",
    "State/Region",
    "Country/Region",
    "Website URL",
    "Lifecycle Stage",
    "Lead Status",
    "HubSpot Score",
    "Original Source",
    "LinkedIn Bio",
    "Facebook URL",
    "Instagram URL",
    "Notes",
]

# Headers reconocidos para Companies CSV
HUBSPOT_COMPANY_HEADERS = [
    "Company Name",
    "Company Domain Name",
    "Phone Number",
    "Industry",
    "Type",
    "City",
    "State/Region",
    "Country/Region",
    "Number of Employees",
    "Annual Revenue",
    "Description",
    "Lifecycle Stage",
    "Lead Status",
    "Original Source",
    "Website URL",
    "LinkedIn Company Page",
    "Facebook Company Page",
]

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$")


# ---------------- Helpers ----------------

def _split_name(full: str) -> tuple[str, str]:
    """'Juan García López' → ('Juan', 'García López'). Si no hay espacio, va todo al last name (empresa)."""
    if not full:
        return ("", "")
    parts = full.strip().split(maxsplit=1)
    if len(parts) == 1:
        return ("", parts[0])
    return (parts[0], parts[1])


def _is_valid_email(e: str) -> bool:
    if not e or e == "DATO_NO_VERIFICABLE":
        return False
    return bool(EMAIL_RE.match(e.strip()))


def _normalize_phone_e164(p: str) -> str:
    if not p or p == "DATO_NO_VERIFICABLE":
        return ""
    # Si ya viene +52, mantener
    if p.startswith("+"):
        return p
    digits = re.sub(r"\D", "", p)
    if len(digits) == 10:
        return f"+52{digits}"
    if len(digits) >= 10:
        return f"+52{digits[-10:]}"
    return ""


def _modelo_to_lifecycle(modelo: str, bucket: str) -> str:
    """Mapea bucket/modelo Fénix → lifecycle stage HubSpot."""
    if bucket == "COMPLETO":
        return "Lead"
    if bucket in ("PARCIAL", "SOLO_EMAIL", "SOLO_TEL"):
        return "Subscriber"
    return "Other"


def _icp_to_lead_status(icp_segment: str, tipo_lead: str) -> str:
    """Mapea ICP + tipo_lead → lead status HubSpot."""
    if tipo_lead == "caliente":
        return "New"
    if icp_segment == "ICP_1_PYME":
        return "Open"
    if icp_segment == "ICP_2_ENTERPRISE":
        return "Open Deal"
    return "Attempted to Contact"


# ---------------- Lead → Contact ----------------

def lead_to_hubspot_contact(lead: dict, run_id: str = "") -> dict | None:
    """Convierte un Lead Fénix v4.0 a row HubSpot Contacts. None si inválido."""
    email = (lead.get("email") or "").strip()
    if not _is_valid_email(email):
        return None  # sin email, no es un contact válido en HubSpot

    nombre = lead.get("nombre") or lead.get("empresa") or ""
    nombre_persona = lead.get("nombre_persona") or ""
    # Si hay nombre_persona específico, usarlo. Sino, usar empresa como last_name.
    if nombre_persona:
        first, last = _split_name(nombre_persona)
    else:
        first, last = "", nombre

    phone = _normalize_phone_e164(lead.get("telefono", ""))
    wa = _normalize_phone_e164(lead.get("whatsapp", ""))
    # Si el principal es el whatsapp y el phone está vacío, usar wa como mobile
    mobile = wa if wa else (phone if "yes" == lead.get("whatsapp", "").lower() else "")

    meta = lead.get("_metadata") or lead.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}

    icp_segment = meta.get("icp_segment", "")
    icp_vertical = meta.get("icp_vertical", "")
    envios_est = meta.get("envios_estimados", "")
    tech_stack = meta.get("tech_stack", [])
    if isinstance(tech_stack, str):
        try:
            tech_stack = json.loads(tech_stack)
        except Exception:  # noqa: BLE001
            tech_stack = []

    notes_parts = []
    if icp_segment:
        notes_parts.append(f"ICP: {icp_segment} ({icp_vertical})")
    if envios_est:
        notes_parts.append(f"Envíos estimados/mes: {envios_est}")
    if tech_stack:
        notes_parts.append(f"Tech detectado: {', '.join(tech_stack[:5])}")
    if lead.get("value_proposition"):
        notes_parts.append(f"VP: {lead['value_proposition']}")
    if lead.get("skydropx_plan"):
        notes_parts.append(f"Plan sugerido: {lead['skydropx_plan']}")
    if meta.get("paqueterias_mencionadas"):
        notes_parts.append(f"Ya usa: {', '.join(meta['paqueterias_mencionadas'])}")

    return {
        "First Name": first,
        "Last Name": last,
        "Email": email.lower(),
        "Phone Number": phone,
        "Mobile Phone Number": mobile,
        "Job Title": "",
        "Company Name": lead.get("empresa") or lead.get("nombre") or "",
        "Industry": lead.get("giro", ""),
        "City": lead.get("ubicacion", ""),
        "State/Region": lead.get("estado", ""),
        "Country/Region": "México",
        "Website URL": meta.get("sitio_web", "") or lead.get("sitio_web", ""),
        "Lifecycle Stage": _modelo_to_lifecycle(lead.get("modelo", ""), lead.get("_bucket", "")),
        "Lead Status": _icp_to_lead_status(icp_segment, lead.get("tipo_lead", "")),
        "HubSpot Score": lead.get("scoring", 0),
        "Original Source": f"Agente Fénix - {lead.get('fuentes','')} (run {run_id})",
        "LinkedIn Bio": lead.get("linkedin", ""),
        "Facebook URL": lead.get("facebook", ""),
        "Instagram URL": lead.get("instagram", ""),
        "Notes": " | ".join(notes_parts),
    }


# ---------------- Lead → Company ----------------

def lead_to_hubspot_company(lead: dict, run_id: str = "") -> dict | None:
    """Convierte un Lead Fénix v4.0 a row HubSpot Companies."""
    empresa = (lead.get("empresa") or lead.get("nombre") or "").strip()
    if not empresa or empresa == "(sin nombre)":
        return None

    meta = lead.get("_metadata") or lead.get("metadata") or {}
    if isinstance(meta, str):
        try:
            meta = json.loads(meta)
        except Exception:  # noqa: BLE001
            meta = {}

    website = meta.get("sitio_web") or lead.get("sitio_web", "")
    domain = ""
    if website:
        domain = re.sub(r"^https?://", "", website).lstrip("www.").rstrip("/").split("/")[0]

    phone = _normalize_phone_e164(lead.get("telefono", ""))
    tamano_to_employees = {
        "Micro": "1-10", "Pequeña": "11-50",
        "Mediana": "51-250", "Grande": "251+",
    }
    employees = tamano_to_employees.get(lead.get("tamano", ""), "")

    icp_segment = meta.get("icp_segment", "")
    icp_vertical = meta.get("icp_vertical", "")
    envios = meta.get("envios_estimados", "")

    desc_parts = [
        f"ICP Skydropx: {icp_segment}" if icp_segment else "",
        f"Vertical: {icp_vertical}" if icp_vertical else "",
        f"Envíos est/mes: {envios}" if envios else "",
        f"Plan sugerido: {lead.get('skydropx_plan','')}" if lead.get("skydropx_plan") else "",
        f"VP: {lead.get('value_proposition','')}" if lead.get("value_proposition") else "",
    ]
    description = " · ".join([p for p in desc_parts if p])

    return {
        "Company Name": empresa,
        "Company Domain Name": domain,
        "Phone Number": phone,
        "Industry": lead.get("giro", ""),
        "Type": icp_vertical or "",
        "City": lead.get("ubicacion", ""),
        "State/Region": lead.get("estado", ""),
        "Country/Region": "México",
        "Number of Employees": employees,
        "Annual Revenue": "",
        "Description": description,
        "Lifecycle Stage": _modelo_to_lifecycle(lead.get("modelo", ""), lead.get("_bucket", "")),
        "Lead Status": _icp_to_lead_status(icp_segment, lead.get("tipo_lead", "")),
        "Original Source": f"Agente Fénix - run {run_id}",
        "Website URL": website,
        "LinkedIn Company Page": lead.get("linkedin", ""),
        "Facebook Company Page": lead.get("facebook", ""),
    }


# ---------------- Export ----------------

@dataclass
class HubSpotExport:
    contacts_csv: str = ""
    companies_csv: str = ""
    readme: str = ""
    n_contacts: int = 0
    n_companies: int = 0
    n_contacts_skipped: int = 0
    n_companies_skipped: int = 0
    tier_summary: dict = field(default_factory=dict)
    min_tier_applied: str | None = None

    def to_dict(self) -> dict:
        return {
            "contacts_csv": self.contacts_csv,
            "companies_csv": self.companies_csv,
            "readme": self.readme,
            "n_contacts": self.n_contacts,
            "n_companies": self.n_companies,
            "n_contacts_skipped": self.n_contacts_skipped,
            "n_companies_skipped": self.n_companies_skipped,
        }


def export_hubspot_csvs(
    leads: list[dict],
    output_dir: str = "output",
    run_id: str = "",
    filename_prefix: str = "fenix_hubspot",
    only_bucket: tuple[str, ...] = ("COMPLETO", "PARCIAL", "SOLO_EMAIL"),
    min_tier: str | None = None,
) -> HubSpotExport:
    """
    Exporta leads a 2 CSVs HubSpot-ready (contacts + companies) + README.

    Args:
        leads: lista de dicts (Lead.to_full_dict() o resultado de db.fetch_all)
        output_dir: directorio destino
        run_id: identificador del job (se incluye en el nombre del archivo)
        only_bucket: solo exporta leads con bucket en esta lista
    """
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_id_short = (run_id or "manual")[-12:]

    contacts_path = out_dir / f"{filename_prefix}_{run_id_short}_{fecha}_contacts.csv"
    companies_path = out_dir / f"{filename_prefix}_{run_id_short}_{fecha}_companies.csv"
    readme_path = out_dir / f"{filename_prefix}_{run_id_short}_{fecha}_README.txt"

    # Filtro: si min_tier definido, ese tiene prioridad sobre only_bucket
    tier_summary = get_tier_summary(leads)
    if min_tier:
        try:
            tier_enum = Tier(min_tier.upper())
        except ValueError:
            tier_enum = Tier.SILVER
        leads_filt, _counts = filter_by_tier(leads, min_tier=tier_enum)
    elif only_bucket:
        leads_filt = [
            l for l in leads
            if l.get("_bucket", l.get("bucket", "")) in only_bucket
        ]
    else:
        leads_filt = leads

    # Generar contacts (dedup por email)
    contacts_rows = []
    contacts_seen: set[str] = set()
    n_contacts_skipped = 0
    for l in leads_filt:
        row = lead_to_hubspot_contact(l, run_id=run_id_short)
        if row is None:
            n_contacts_skipped += 1
            continue
        email_key = row["Email"].lower()
        if email_key in contacts_seen:
            n_contacts_skipped += 1
            continue
        contacts_seen.add(email_key)
        contacts_rows.append(row)

    # Generar companies (dedup por domain o name)
    companies_rows = []
    companies_seen: set[str] = set()
    n_companies_skipped = 0
    for l in leads_filt:
        row = lead_to_hubspot_company(l, run_id=run_id_short)
        if row is None:
            n_companies_skipped += 1
            continue
        key = (row["Company Domain Name"] or row["Company Name"]).lower()
        if key in companies_seen:
            n_companies_skipped += 1
            continue
        companies_seen.add(key)
        companies_rows.append(row)

    # Escribir CSVs (UTF-8 con BOM para que Excel/HubSpot lo lean bien)
    with contacts_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HUBSPOT_CONTACT_HEADERS)
        w.writeheader()
        for r in contacts_rows:
            w.writerow(r)

    with companies_path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=HUBSPOT_COMPANY_HEADERS)
        w.writeheader()
        for r in companies_rows:
            w.writerow(r)

    # README con instrucciones paso a paso
    readme = _build_readme(
        contacts_path=contacts_path.name,
        companies_path=companies_path.name,
        n_contacts=len(contacts_rows),
        n_companies=len(companies_rows),
        n_contacts_skipped=n_contacts_skipped,
        n_companies_skipped=n_companies_skipped,
        run_id=run_id_short,
        fecha=fecha,
    )
    readme_path.write_text(readme, encoding="utf-8")

    export_result = HubSpotExport(
        contacts_csv=str(contacts_path),
        companies_csv=str(companies_path),
        readme=str(readme_path),
        n_contacts=len(contacts_rows),
        n_companies=len(companies_rows),
        n_contacts_skipped=n_contacts_skipped,
        n_companies_skipped=n_companies_skipped,
    )
    export_result.tier_summary = tier_summary
    export_result.min_tier_applied = min_tier
    return export_result


def _build_readme(**kw) -> str:
    return f"""
============================================================
 HUBSPOT IMPORT - Agente Fenix v5
 Job: {kw['run_id']}  -  Fecha: {kw['fecha']}
============================================================

ARCHIVOS GENERADOS
------------------
1. {kw['contacts_path']}     ({kw['n_contacts']} contactos)
2. {kw['companies_path']}    ({kw['n_companies']} empresas)

Saltados por validacion:
  - Contacts:  {kw['n_contacts_skipped']} (email invalido o duplicado)
  - Companies: {kw['n_companies_skipped']} (sin nombre o duplicada por dominio)

============================================================
COMO IMPORTAR EN HUBSPOT (manual, sin permisos API)
============================================================

PASO 1 - Importar Companies PRIMERO (orden importante)
------------------------------------------------------
1. En HubSpot, ve a:  Contactos > Importaciones > Iniciar una importacion
2. Selecciona: "Archivo del equipo"
3. Tipo de objeto: "Empresas"
4. Sube: {kw['companies_path']}
5. HubSpot detectara automaticamente las columnas (los headers ya estan
   alineados con los nombres oficiales de HubSpot).
6. Verifica que las columnas se mapeen al campo correcto:
   - "Company Name"          -> Nombre
   - "Company Domain Name"   -> Dominio (clave de dedup en HubSpot)
   - "Phone Number"          -> Numero de telefono
   - "Industry"              -> Industria
   - "City" / "State/Region" -> Ciudad / Estado
   - "Description"           -> Descripcion (incluye ICP/vertical/envios)
7. En "Configurar la importacion":
   - Activa: "Actualizar registros existentes" (matching por dominio)
   - Tipo de duplicados: "Mantener el mas reciente"
8. Iniciar importacion. Espera el correo de confirmacion (~5-15 min).


PASO 2 - Importar Contacts (DESPUES de Companies)
-------------------------------------------------
1. Nueva importacion > "Archivo del equipo"
2. Tipo: "Contactos"
3. Sube: {kw['contacts_path']}
4. Columnas se mapean automatico:
   - "Email"                 -> Email (clave de dedup)
   - "First Name"/"Last Name"-> Nombre
   - "Phone Number"          -> Telefono
   - "Mobile Phone Number"   -> Whatsapp (campo movil)
   - "Company Name"          -> ENLAZA al Company importado en Paso 1
   - "Lifecycle Stage"       -> Estado del ciclo (Lead/Subscriber)
   - "Lead Status"           -> Status del lead (New/Open/Open Deal)
   - "Notes"                 -> Nota inicial (incluye ICP + tech + plan)
5. HubSpot vinculara automaticamente cada contacto a su company por el
   campo "Company Name" si ya existe.
6. Iniciar importacion.


PASO 3 - Crear vistas (recomendado)
-----------------------------------
Una vez importado, crea estas vistas guardadas:

a) "Fenix - SKYDROPX READY (calientes)"
   Filtros: Lead Status = "New"
            AND Original Source contiene "Agente Fenix"

b) "Fenix - ICP PyME 50-100 envios"
   Filtros: Notes contiene "ICP_1_PYME"

c) "Fenix - ICP Enterprise (3PL / Agencias)"
   Filtros: Notes contiene "ICP_2_ENTERPRISE"

d) "Fenix - Ya usa competencia (oportunidad)"
   Filtros: Notes contiene "Ya usa: estafeta"  OR
            Notes contiene "Ya usa: dhl"  OR
            Notes contiene "Ya usa: 99minutos"


COMPLIANCE LFPDPPP
==================
Estos contactos fueron descubiertos via fuentes publicas
(DENUE/INEGI, Google Dorks, scraping de sitios web propios).
Antes de enviar comunicacion outbound:
  - Verifica que el contacto no haya pedido opt-out (REPEP)
  - Incluye aviso de privacidad en el primer correo
  - Da opcion clara de baja desde el primer contacto

Si un contacto pide ser eliminado, registra el opt-out con:
  fenix db opt-out --kind email --value <email> --reason "user_request"


SOPORTE
=======
- Documentacion: references/integracion-crm.md
- Issues: revisa logs en logs/fenix.log
"""
