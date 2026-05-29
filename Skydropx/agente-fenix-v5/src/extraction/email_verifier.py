"""
Verificador de emails en cascada (sintaxis → DNS MX → SMTP).

Estrategia tiered con librerías opcionales:
1. Sintaxis básica (regex stdlib, siempre disponible)
2. python-email-validator (sintaxis robusta + IDNA) si está instalado
3. dnspython (MX lookup) si está instalado
4. SMTP handshake (verifica si el destinatario existe sin mandar correo)

Caché en memoria + flag para persistir en DB.

Status posibles:
- 'invalid_syntax'    → falla regex
- 'disposable'        → dominio en blacklist
- 'mx_missing'        → no hay registros MX en DNS
- 'mx_ok'             → MX existe pero no se hizo SMTP check
- 'smtp_ok'           → servidor SMTP confirmó que el mailbox existe
- 'smtp_rejected'     → servidor rechazó el destinatario
- 'smtp_unverifiable' → servidor no responde / catch-all / greylist
- 'verified_personal' → smtp_ok + dominio no es genérico (oro)
"""
from __future__ import annotations

import logging
import re
import smtplib
import socket
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

# ---------------- Librerías opcionales ----------------

try:
    from email_validator import validate_email, EmailNotValidError
    HAS_EMAIL_VALIDATOR = True
except ImportError:
    HAS_EMAIL_VALIDATOR = False

try:
    import dns.resolver
    HAS_DNSPYTHON = True
except ImportError:
    HAS_DNSPYTHON = False


# ---------------- Constantes ----------------

EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# Dominios desechables comunes (extender desde fuentes públicas)
DISPOSABLE_DOMAINS: set[str] = {
    "mailinator.com", "guerrillamail.com", "tempmail.com", "10minutemail.com",
    "throwawaymail.com", "yopmail.com", "trashmail.com", "fakeinbox.com",
    "tempinbox.com", "maildrop.cc", "getnada.com", "discard.email",
    "dispostable.com", "mailcatch.com", "spam4.me", "spamgourmet.com",
    "0wnd.net", "0wnd.org", "ml1.net", "mt2009.com", "thankyou2010.com",
}

# Dominios "personales" (no corporativos) — para clasificar pero NO descartar
PERSONAL_DOMAINS: set[str] = {
    "gmail.com", "yahoo.com", "yahoo.com.mx", "hotmail.com", "hotmail.com.mx",
    "outlook.com", "live.com", "live.com.mx", "icloud.com", "me.com",
    "aol.com", "msn.com", "prodigy.net.mx", "att.net.mx", "telmexmail.com",
}

# Dominios que aceptan catch-all conocidos → SMTP unreliable
CATCH_ALL_KNOWN: set[str] = set()  # poblamos dinámicamente


# ---------------- Resultado ----------------

@dataclass
class EmailValidation:
    email: str
    is_valid: bool = False
    status: str = "unknown"
    domain: str = ""
    is_personal: bool = False
    is_disposable: bool = False
    mx_records: list[str] = field(default_factory=list)
    checked_smtp: bool = False
    smtp_message: str = ""
    duration_ms: int = 0


# ---------------- Verificador ----------------

class EmailVerifier:
    """
    Verificador con políticas configurables:
    - check_mx=True       → consulta DNS MX (rápido, ~50ms por dominio nuevo)
    - check_smtp=True     → handshake SMTP (lento, 1-5s por email, puede ser bloqueado)
    - cache_ttl_sec       → reutiliza resultados recientes
    """

    def __init__(
        self,
        check_mx: bool = True,
        check_smtp: bool = False,
        smtp_timeout: int = 5,
        smtp_from_email: str = "no-reply@skydropx.com",
        cache_ttl_sec: int = 86400,  # 24h
    ):
        self.check_mx = check_mx and HAS_DNSPYTHON
        self.check_smtp = check_smtp
        self.smtp_timeout = smtp_timeout
        self.smtp_from = smtp_from_email
        self.cache_ttl = cache_ttl_sec
        # Caché por email completo
        self._email_cache: dict[str, tuple[float, EmailValidation]] = {}
        # Caché por dominio (MX)
        self._mx_cache: dict[str, tuple[float, list[str]]] = {}

    def verify(self, email: str) -> EmailValidation:
        t0 = time.time()
        result = EmailValidation(email=email)
        email_norm = email.strip().lower()

        # Cache hit
        cached = self._email_cache.get(email_norm)
        if cached and time.time() - cached[0] < self.cache_ttl:
            return cached[1]

        # 1. Sintaxis
        if not self._check_syntax(email_norm, result):
            self._cache(email_norm, result, t0)
            return result

        result.domain = email_norm.rsplit("@", 1)[1]
        result.is_personal = result.domain in PERSONAL_DOMAINS
        result.is_disposable = result.domain in DISPOSABLE_DOMAINS

        if result.is_disposable:
            result.status = "disposable"
            self._cache(email_norm, result, t0)
            return result

        # 2. MX records
        if self.check_mx:
            mx = self._check_mx_records(result.domain)
            if mx:
                result.mx_records = mx
                result.status = "mx_ok"
                result.is_valid = True
            else:
                result.status = "mx_missing"
                self._cache(email_norm, result, t0)
                return result
        else:
            # Sin DNS, consideramos sintaxis OK = valido tentativo
            result.status = "syntax_ok"
            result.is_valid = True

        # 3. SMTP handshake
        if self.check_smtp and result.mx_records:
            smtp_status, smtp_msg = self._smtp_probe(
                email_norm, result.mx_records[0]
            )
            result.checked_smtp = True
            result.smtp_message = smtp_msg
            if smtp_status == "ok":
                result.status = "verified_personal" if not result.is_personal else "smtp_ok"
                result.is_valid = True
            elif smtp_status == "rejected":
                result.status = "smtp_rejected"
                result.is_valid = False
            else:
                result.status = "smtp_unverifiable"
                # Mantenemos is_valid=True por MX OK

        result.duration_ms = int((time.time() - t0) * 1000)
        self._cache(email_norm, result, t0)
        return result

    # ---------- Cascada interna ----------

    def _check_syntax(self, email: str, result: EmailValidation) -> bool:
        if HAS_EMAIL_VALIDATOR:
            try:
                # check_deliverability=False → solo sintaxis (rápido)
                validate_email(email, check_deliverability=False)
                return True
            except EmailNotValidError as e:
                result.status = "invalid_syntax"
                result.smtp_message = str(e)
                return False
        else:
            if EMAIL_REGEX.match(email):
                return True
            result.status = "invalid_syntax"
            return False

    def _check_mx_records(self, domain: str) -> list[str]:
        cached = self._mx_cache.get(domain)
        if cached and time.time() - cached[0] < self.cache_ttl:
            return cached[1]

        try:
            answers = dns.resolver.resolve(domain, "MX", lifetime=5)
            mx = sorted(
                [str(r.exchange).rstrip(".") for r in answers],
                key=lambda x: x,
            )
            self._mx_cache[domain] = (time.time(), mx)
            return mx
        except Exception as e:  # noqa: BLE001
            logger.debug("MX lookup fail %s: %s", domain, e)
            self._mx_cache[domain] = (time.time(), [])
            return []

    def _smtp_probe(self, email: str, mx_host: str) -> tuple[str, str]:
        """
        Conecta al MX, hace HELO + MAIL FROM + RCPT TO. NUNCA envía DATA.
        Retorna ('ok'|'rejected'|'unreliable', mensaje_servidor).
        """
        try:
            smtp = smtplib.SMTP(mx_host, 25, timeout=self.smtp_timeout)
            smtp.set_debuglevel(0)
            try:
                # EHLO/HELO + STARTTLS si disponible
                code, _ = smtp.ehlo()
                if code != 250:
                    smtp.helo()
                if smtp.has_extn("STARTTLS"):
                    try:
                        smtp.starttls()
                        smtp.ehlo()
                    except Exception:  # noqa: BLE001
                        pass  # caemos a SMTP sin cifrar

                code, _ = smtp.mail(self.smtp_from)
                if code != 250:
                    return "unreliable", f"MAIL FROM rechazado: {code}"

                code, msg = smtp.rcpt(email)
                if code in (250, 251):
                    return "ok", "RCPT TO aceptado"
                if code in (550, 551, 553):
                    return "rejected", msg.decode() if isinstance(msg, bytes) else str(msg)
                return "unreliable", f"código {code}: {msg}"
            finally:
                try:
                    smtp.quit()
                except Exception:  # noqa: BLE001
                    pass
        except (smtplib.SMTPException, socket.error, OSError) as e:
            return "unreliable", f"SMTP err: {e}"

    def _cache(self, email: str, result: EmailValidation, t0: float) -> None:
        result.duration_ms = result.duration_ms or int((time.time() - t0) * 1000)
        self._email_cache[email] = (time.time(), result)

    # ---------- Bulk ----------

    def verify_many(self, emails: list[str]) -> list[EmailValidation]:
        return [self.verify(e) for e in emails]


# Singleton de conveniencia (sin SMTP por default — más rápido)
_default = EmailVerifier(check_mx=True, check_smtp=False)


def verify_email_quick(email: str) -> EmailValidation:
    """API rápida para uso casual: sintaxis + MX, sin SMTP."""
    return _default.verify(email)


__all__ = [
    "EmailVerifier", "EmailValidation",
    "verify_email_quick",
    "DISPOSABLE_DOMAINS", "PERSONAL_DOMAINS",
    "HAS_EMAIL_VALIDATOR", "HAS_DNSPYTHON",
]
