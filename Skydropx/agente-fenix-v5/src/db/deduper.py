"""
Deduplicador persistente con upsert + merge inteligente.

Política de matching jerárquica (en orden):
1. exact_match(email_norm)           → MISMO LEAD
2. exact_match(phone_norm last 10)   → MISMO LEAD
3. exact_match(dominio_norm)         → MISMA EMPRESA (merge)
4. fuzzy_match(razon_social, 85) + mismo_estado → MISMA EMPRESA

Merge strategy: conservar el lead con MÁS campos llenos, agregar fuentes a array,
incrementar `times_seen`, actualizar `last_seen_at`.
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from src.core.models import RawRecord
from src.db.engine import FenixDB, get_db

logger = logging.getLogger(__name__)


# ---------------- Normalizers ----------------

def normalize_email(s: str | None) -> str | None:
    if not s or "@" not in s:
        return None
    return s.strip().lower()


def normalize_phone(s: str | None) -> str | None:
    if not s:
        return None
    digits = re.sub(r"\D", "", s)
    return digits[-10:] if len(digits) >= 10 else None


def normalize_domain(s: str | None) -> str | None:
    if not s:
        return None
    s = s.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    s = s.rstrip("/")
    s = s.split("/")[0]  # solo el host
    return s or None


def normalize_name(s: str | None) -> str:
    if not s:
        return ""
    # Sin acentos
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = s.lower().strip()
    # Quitar caracteres especiales primero (queda 'mi empresa sa de cv')
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    # Ahora quitar sufijos legales como tokens (s, a, sa, sab, de, cv, srl, sapi)
    tokens = s.split()
    legal_tokens = {"s", "a", "sa", "sab", "de", "cv", "srl", "rl",
                     "sapi", "sc", "en", "c"}
    tokens = [t for t in tokens if t not in legal_tokens]
    return " ".join(tokens).strip()


# ---------------- Fuzzy match opcional ----------------

try:
    from difflib import SequenceMatcher

    def fuzzy_ratio(a: str, b: str) -> int:
        """0-100 score de similitud."""
        if not a or not b:
            return 0
        return int(SequenceMatcher(None, a, b).ratio() * 100)
    HAS_FUZZY = True
except ImportError:
    def fuzzy_ratio(a: str, b: str) -> int:
        return 100 if a == b else 0
    HAS_FUZZY = False


# ---------------- Match strategy ----------------

@dataclass
class MatchResult:
    company_id: int | None
    match_type: str          # 'email'|'phone'|'domain'|'name'|'new'
    confidence: int          # 0-100
    matched_field: str = ""


class Deduper:
    def __init__(self, db: FenixDB | None = None,
                 fuzzy_threshold: int = 85):
        self.db = db or get_db()
        self.fuzzy_threshold = fuzzy_threshold

    # ---------- Match ----------

    def find_match(self, record: RawRecord) -> MatchResult:
        # 1. email exacto
        email_n = normalize_email(record.email)
        if email_n:
            row = self.db.fetch_one(
                "SELECT company_id FROM contacts "
                "WHERE kind='email' AND value_norm = ? LIMIT 1",
                (email_n,),
            )
            if row:
                return MatchResult(row["company_id"], "email", 100, email_n)

        # 2. phone exacto
        for src_phone in (record.telefono, record.whatsapp):
            phone_n = normalize_phone(src_phone)
            if phone_n:
                row = self.db.fetch_one(
                    "SELECT company_id FROM contacts "
                    "WHERE kind IN ('phone','whatsapp') AND value_norm = ? LIMIT 1",
                    (phone_n,),
                )
                if row:
                    return MatchResult(row["company_id"], "phone", 100, phone_n)

        # 3. dominio exacto
        dom_n = normalize_domain(record.sitio_web)
        if dom_n:
            row = self.db.fetch_one(
                "SELECT company_id FROM contacts "
                "WHERE kind='website' AND value_norm = ? LIMIT 1",
                (dom_n,),
            )
            if row:
                return MatchResult(row["company_id"], "domain", 95, dom_n)

        # 4. nombre fuzzy + estado
        name_n = normalize_name(record.empresa or record.nombre_comercial or "")
        if name_n and len(name_n) >= 6 and record.estado:
            # Buscar candidatos en el mismo estado con nombres similares
            estado_q = record.estado.upper().strip()
            candidates = self.db.fetch_all(
                "SELECT id, razon_social, nombre_comercial FROM companies "
                "WHERE UPPER(estado) = ? LIMIT 500",
                (estado_q,),
            )
            best_id = None
            best_score = 0
            for c in candidates:
                c_name = normalize_name(c["razon_social"] or c["nombre_comercial"] or "")
                score = fuzzy_ratio(name_n, c_name)
                if score > best_score:
                    best_score = score
                    best_id = c["id"]
            if best_id and best_score >= self.fuzzy_threshold:
                return MatchResult(best_id, "name", best_score, name_n)

        return MatchResult(None, "new", 0)

    # ---------- Upsert ----------

    def upsert(self, record: RawRecord, job_id: str | None = None) -> tuple[int, bool]:
        """
        Inserta o mergea un RawRecord. Devuelve (company_id, is_new).
        """
        match = self.find_match(record)

        if match.company_id is None:
            company_id = self._insert_new(record)
            is_new = True
        else:
            company_id = match.company_id
            self._merge_into(company_id, record)
            is_new = False

        # Insertar contactos (idempotent vía UNIQUE)
        self._upsert_contacts(company_id, record)

        # Registrar raw_finding para trazabilidad
        self.db.execute(
            "INSERT INTO raw_findings (company_id, job_id, source, fingerprint, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (company_id, job_id, record.source,
             record.fingerprint(),
             json.dumps(record.to_dict(), ensure_ascii=False, default=str)),
        )

        # Asociar con el job
        if job_id:
            try:
                self.db.execute(
                    "INSERT OR IGNORE INTO job_companies (job_id, company_id, is_new) "
                    "VALUES (?, ?, ?)",
                    (job_id, company_id, int(is_new)),
                )
            except Exception:  # noqa: BLE001 (Postgres usa otra sintaxis)
                self.db.execute(
                    "INSERT INTO job_companies (job_id, company_id, is_new) "
                    "VALUES (:j, :c, :n) ON CONFLICT DO NOTHING",
                    {"j": job_id, "c": company_id, "n": int(is_new)},
                )

        return company_id, is_new

    def _insert_new(self, r: RawRecord) -> int:
        sql = """
        INSERT INTO companies (
            fingerprint, razon_social, nombre_comercial, rfc,
            estado, municipio, colonia, cp, direccion,
            scian, giro_descripcion, tamano,
            longitud, latitud, metadata_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        with self.db.connect() as conn:
            cur = conn.execute(sql, (
                r.fingerprint(),
                r.empresa, r.nombre_comercial, r.rfc,
                (r.estado or "").upper() or None,
                r.municipio, r.colonia, r.cp, r.direccion,
                r.scian, r.giro_descripcion, r.tamano,
                r.longitud, r.latitud,
                json.dumps(r.metadata or {}, ensure_ascii=False, default=str),
            ))
            conn.commit()
            return cur.lastrowid

    def _merge_into(self, company_id: int, r: RawRecord) -> None:
        """Actualiza campos vacíos con datos nuevos, incrementa times_seen."""
        existing = self.db.fetch_one(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        )
        if not existing:
            return

        updates: list[str] = []
        params: list[Any] = []

        # Conservar lo no vacío
        for col, new_val in [
            ("razon_social", r.empresa),
            ("nombre_comercial", r.nombre_comercial),
            ("rfc", r.rfc),
            ("colonia", r.colonia),
            ("cp", r.cp),
            ("direccion", r.direccion),
            ("scian", r.scian),
            ("giro_descripcion", r.giro_descripcion),
            ("tamano", r.tamano),
        ]:
            if new_val and not existing.get(col):
                updates.append(f"{col} = ?")
                params.append(new_val)

        # Merge metadata JSON
        try:
            old_meta = json.loads(existing.get("metadata_json") or "{}")
        except Exception:  # noqa: BLE001
            old_meta = {}
        old_meta.update(r.metadata or {})
        updates.append("metadata_json = ?")
        params.append(json.dumps(old_meta, ensure_ascii=False, default=str))

        # Siempre actualizar last_seen y times_seen
        updates.append("last_seen_at = CURRENT_TIMESTAMP")
        updates.append("times_seen = times_seen + 1")

        sql = f"UPDATE companies SET {', '.join(updates)} WHERE id = ?"
        params.append(company_id)
        self.db.execute(sql, tuple(params))

    def _upsert_contacts(self, company_id: int, r: RawRecord) -> None:
        contacts = []

        if r.email:
            v_norm = normalize_email(r.email)
            if v_norm:
                is_personal = not any(
                    v_norm.startswith(p + "@") or v_norm.startswith(p + ".")
                    for p in ("info", "contacto", "contact", "hola", "ventas",
                              "sales", "soporte", "admin", "webmaster", "noreply",
                              "no-reply", "atencion")
                )
                contacts.append(("email", r.email, v_norm, int(is_personal)))

        if r.telefono:
            v_norm = normalize_phone(r.telefono)
            if v_norm:
                contacts.append(("phone", r.telefono, v_norm, 0))

        if r.whatsapp:
            v_norm = normalize_phone(r.whatsapp)
            if v_norm:
                contacts.append(("whatsapp", r.whatsapp, v_norm, 0))

        if r.sitio_web:
            v_norm = normalize_domain(r.sitio_web)
            if v_norm:
                contacts.append(("website", r.sitio_web, v_norm, 0))

        for kind, value, value_norm, is_personal in contacts:
            try:
                self.db.execute(
                    "INSERT OR IGNORE INTO contacts "
                    "(company_id, kind, value, value_norm, is_personal, fuente) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (company_id, kind, value, value_norm, is_personal, r.source),
                )
            except Exception:  # Postgres syntax
                self.db.execute(
                    "INSERT INTO contacts "
                    "(company_id, kind, value, value_norm, is_personal, fuente) "
                    "VALUES (:c, :k, :v, :vn, :ip, :s) "
                    "ON CONFLICT (company_id, kind, value_norm) DO NOTHING",
                    {"c": company_id, "k": kind, "v": value, "vn": value_norm,
                     "ip": is_personal, "s": r.source},
                )

    # ---------- Bulk ----------

    def upsert_many(self, records: list[RawRecord],
                    job_id: str | None = None) -> dict:
        """Procesa una lista. Devuelve stats."""
        n_new = 0
        n_updated = 0
        n_opted_out = 0
        opt_out_set = self._load_opt_outs()

        for r in records:
            # Skip si está en opt-outs
            if self._is_opted_out(r, opt_out_set):
                n_opted_out += 1
                continue
            try:
                _, is_new = self.upsert(r, job_id)
                if is_new:
                    n_new += 1
                else:
                    n_updated += 1
            except Exception as e:  # noqa: BLE001
                logger.warning("Dedup upsert err para %s: %s",
                               r.fingerprint(), e)

        return {
            "n_processed": len(records),
            "n_new": n_new,
            "n_updated": n_updated,
            "n_opted_out": n_opted_out,
        }

    # ---------- Opt-outs (LFPDPPP) ----------

    def _load_opt_outs(self) -> dict[str, set[str]]:
        out: dict[str, set[str]] = {"email": set(), "phone": set(), "company_id": set()}
        for r in self.db.fetch_all("SELECT kind, value_norm FROM opt_outs"):
            out[r["kind"]].add(r["value_norm"])
        return out

    def _is_opted_out(self, r: RawRecord,
                      opt_outs: dict[str, set[str]]) -> bool:
        if r.email and normalize_email(r.email) in opt_outs["email"]:
            return True
        for p in (r.telefono, r.whatsapp):
            if p and normalize_phone(p) in opt_outs["phone"]:
                return True
        return False

    def add_opt_out(self, kind: str, value: str, reason: str = "") -> None:
        norm = (
            normalize_email(value) if kind == "email"
            else normalize_phone(value) if kind == "phone"
            else value
        )
        if not norm:
            return
        try:
            self.db.execute(
                "INSERT OR IGNORE INTO opt_outs (kind, value_norm, reason) VALUES (?, ?, ?)",
                (kind, norm, reason),
            )
        except Exception:
            self.db.execute(
                "INSERT INTO opt_outs (kind, value_norm, reason) VALUES (:k, :v, :r) "
                "ON CONFLICT DO NOTHING",
                {"k": kind, "v": norm, "r": reason},
            )


__all__ = [
    "Deduper", "MatchResult",
    "normalize_email", "normalize_phone", "normalize_domain", "normalize_name",
    "fuzzy_ratio", "HAS_FUZZY",
]
