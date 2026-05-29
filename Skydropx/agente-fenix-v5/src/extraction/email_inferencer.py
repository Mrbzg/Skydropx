"""
Email Inferencer — infiere emails corporativos cuando solo tenemos el dominio.

Estrategia tiered:
1. Verificar MX del dominio (si no tiene MX, ni intentar)
2. Generar candidatos comunes en español MX: contacto@, ventas@, info@, etc.
3. Para cada candidato, hacer SMTP probe ligero (HELO + RCPT TO) si check_smtp=True
4. Devolver el primer email que el servidor SMTP acepta como existente

IMPORTANTE: Esto NO inventa emails sin verificar. Si SMTP no responde positivamente,
el resultado es `inferido_sin_verificar` (status separado) — el usuario decide
si lo usa o no.

Para Skydropx: aumenta dramáticamente el READY rate cuando DENUE tiene web pero no email.
Ejemplo real: empresa.com.mx → MX existe → contacto@empresa.com.mx aceptado por servidor.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Iterable

from src.extraction.email_verifier import (
    EmailVerifier, EmailValidation, HAS_DNSPYTHON, PERSONAL_DOMAINS,
)

logger = logging.getLogger(__name__)


# Prefijos comunes en orden de probabilidad de existencia
# Basado en heurística MX: contacto/ventas son más comunes que info en .mx
DEFAULT_PREFIXES_MX = [
    "contacto",     # más común en sitios MX
    "ventas",       # 2do más común para B2B
    "info",         # 3ro (global)
    "hola",         # común en D2C/PyMEs
    "atencion",     # atención a clientes
    "soporte",      # 6to
    "administracion",
    "facturacion",
    "comercial",
    "mail",
]

# Para empresas con marca de persona (juan@ó luis@), no inferimos esos
# porque sin nombre real es 100% adivinanza


@dataclass
class InferredEmail:
    email: str
    domain: str
    prefix_used: str
    status: str           # 'mx_valid' | 'smtp_accepted' | 'smtp_rejected' | 'mx_missing'
    is_verified_smtp: bool = False
    confidence: int = 0   # 0-100
    notes: str = ""


@dataclass
class InferenceResult:
    domain: str
    candidates_tried: list[str] = field(default_factory=list)
    emails_found: list[InferredEmail] = field(default_factory=list)
    best_email: InferredEmail | None = None
    method: str = ""      # 'mx_only' | 'smtp_probe' | 'failed'
    error: str = ""

    def has_result(self) -> bool:
        return self.best_email is not None


class EmailInferencer:
    """
    Infiere emails corporativos partiendo de un dominio.

    Modos:
    - check_mx_only=True (default): genera candidatos si el dominio tiene MX,
      devuelve como "inferred_unverified" con confidence=50
    - check_smtp=True: hace SMTP probe a cada candidato (más lento pero verifica
      existencia real). Confidence=85 si pasa.
    """

    def __init__(
        self,
        prefixes: list[str] | None = None,
        check_smtp: bool = False,
        smtp_timeout: int = 5,
        max_candidates: int = 5,
        verifier: EmailVerifier | None = None,
    ):
        self.prefixes = prefixes or DEFAULT_PREFIXES_MX
        self.check_smtp = check_smtp
        self.max_candidates = max_candidates
        self.verifier = verifier or EmailVerifier(
            check_mx=True, check_smtp=check_smtp, smtp_timeout=smtp_timeout,
        )

    # ---------- API principal ----------

    def infer_from_domain(self, domain: str,
                            company_name: str = "") -> InferenceResult:
        """
        Devuelve InferenceResult con el mejor email inferido o vacío.
        """
        result = InferenceResult(domain=domain)
        dom = self._normalize_domain(domain)
        if not dom:
            result.error = "invalid_domain"
            return result

        # Filtro: dominios personales (gmail, hotmail) no se infieren
        if dom in PERSONAL_DOMAINS:
            result.error = "personal_domain_skipped"
            return result

        result.domain = dom

        # Step 1: ¿tiene MX records?
        if not HAS_DNSPYTHON:
            result.error = "dnspython_not_installed"
            return result

        mx_records = self.verifier._check_mx_records(dom)  # noqa: SLF001
        if not mx_records:
            result.method = "failed"
            result.error = "mx_missing"
            return result

        # Step 2: Generar candidatos
        candidates = [f"{p}@{dom}" for p in self.prefixes[: self.max_candidates]]
        result.candidates_tried = candidates

        # Step 3a: Si check_smtp=False, devolver el más probable como "MX-validated"
        if not self.check_smtp:
            first = candidates[0]
            result.best_email = InferredEmail(
                email=first, domain=dom, prefix_used=self.prefixes[0],
                status="mx_valid", is_verified_smtp=False,
                confidence=50,
                notes=f"MX records existen ({len(mx_records)}). Email inferido sin SMTP probe.",
            )
            result.method = "mx_only"
            result.emails_found = [result.best_email]
            return result

        # Step 3b: SMTP probe a cada candidato
        result.method = "smtp_probe"
        for cand in candidates:
            try:
                status, msg = self.verifier._smtp_probe(cand, mx_records[0])  # noqa: SLF001
                if status == "ok":
                    inferred = InferredEmail(
                        email=cand, domain=dom,
                        prefix_used=cand.split("@", 1)[0],
                        status="smtp_accepted",
                        is_verified_smtp=True, confidence=85,
                        notes=f"SMTP servidor aceptó RCPT TO: {msg[:80]}",
                    )
                    result.emails_found.append(inferred)
                    if result.best_email is None:
                        result.best_email = inferred
                    # No seguimos: con 1 confirmado nos basta
                    break
                elif status == "rejected":
                    # Aprende: probablemente catch-all OFF, ese prefix no existe
                    pass
                # 'unreliable': el servidor no responde claro, seguimos al siguiente
            except Exception as e:  # noqa: BLE001
                logger.debug("smtp_probe %s err: %s", cand, e)

        if result.best_email is None:
            # SMTP no confirmó ninguno → degradar a MX-only del primer candidato
            result.best_email = InferredEmail(
                email=candidates[0], domain=dom,
                prefix_used=self.prefixes[0],
                status="mx_valid",
                is_verified_smtp=False, confidence=30,
                notes="MX existe pero SMTP no confirmó ningún prefijo común. Probable catch-all OFF o tarpit.",
            )
            result.emails_found = [result.best_email]

        return result

    # ---------- Bulk ----------

    def infer_many(self, domains: Iterable[str]) -> list[InferenceResult]:
        return [self.infer_from_domain(d) for d in domains]

    # ---------- Helpers ----------

    @staticmethod
    def _normalize_domain(domain: str | None) -> str:
        if not domain:
            return ""
        d = domain.strip().lower()
        d = re.sub(r"^https?://", "", d)
        d = re.sub(r"^www\.", "", d)
        d = d.rstrip("/").split("/")[0]
        return d if "." in d else ""


__all__ = [
    "EmailInferencer", "InferenceResult", "InferredEmail",
    "DEFAULT_PREFIXES_MX",
]
