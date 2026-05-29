"""
Cliente DENUE / INEGI - probado contra API real (mayo 2026).

Endpoints reales validados:
  · Buscar              — por coordenadas + radio (max 5000m)
  · Ficha               — por Id de establecimiento
  · Nombre              — por nombre/razón social + entidad
  · BuscarEntidad       — por palabra + entidad
  · BuscarAreaAct       — por área geográfica + actividad
  · BuscarAreaActEstr   — área + actividad + estrato (el más usado)
  · Cuantificar         — conteo por actividad + área + estrato

Base URL: https://www.inegi.org.mx/app/api/denue/v1/consulta/

Token: gratuito en https://www.inegi.org.mx/app/api/denue/v1/tutorial.html
Rate limit: ~100 req/min (no documentado oficialmente, observado).
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Iterable, Iterator
from urllib.parse import quote

import requests

try:
    from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
    HAS_TENACITY = True
except ImportError:
    HAS_TENACITY = False

logger = logging.getLogger(__name__)

DENUE_BASE = "https://www.inegi.org.mx/app/api/denue/v1/consulta"
DEFAULT_TIMEOUT = 30
DEFAULT_DELAY = 0.6  # 100 req/min ≈ 0.6s


# ---------------- Catálogo de Entidades Federativas ----------------

ENTIDADES_MX = {
    "01": "Aguascalientes",   "02": "Baja California", "03": "Baja California Sur",
    "04": "Campeche",         "05": "Coahuila",        "06": "Colima",
    "07": "Chiapas",          "08": "Chihuahua",       "09": "Ciudad de México",
    "10": "Durango",          "11": "Guanajuato",      "12": "Guerrero",
    "13": "Hidalgo",          "14": "Jalisco",         "15": "México",
    "16": "Michoacán",        "17": "Morelos",         "18": "Nayarit",
    "19": "Nuevo León",       "20": "Oaxaca",          "21": "Puebla",
    "22": "Querétaro",        "23": "Quintana Roo",    "24": "San Luis Potosí",
    "25": "Sinaloa",          "26": "Sonora",          "27": "Tabasco",
    "28": "Tamaulipas",       "29": "Tlaxcala",        "30": "Veracruz",
    "31": "Yucatán",          "32": "Zacatecas",
}

# Reverse lookup case-insensitive sin acentos
def _strip_accents(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", s) if unicodedata.category(c) != "Mn")

ENTIDAD_LOOKUP = {_strip_accents(v).lower(): k for k, v in ENTIDADES_MX.items()}
# Aliases comunes
ENTIDAD_LOOKUP.update({
    "cdmx": "09", "df": "09", "ciudad de mexico": "09",
    "edomex": "15", "estado de mexico": "15",
    "gdl": "14", "guadalajara": "14",     # ciudad → estado padre
    "mty": "19", "monterrey": "19",
    "qro": "22", "queretaro": "22",
})


def resolve_entidad(nombre_o_clave: str) -> str:
    """Convierte 'Jalisco', 'JAL', 'CDMX', '09' → clave de 2 dígitos."""
    s = (nombre_o_clave or "").strip()
    if s.isdigit() and len(s) <= 2:
        return s.zfill(2)
    key = _strip_accents(s).lower()
    if key in ENTIDAD_LOOKUP:
        return ENTIDAD_LOOKUP[key]
    raise ValueError(f"Entidad desconocida: {nombre_o_clave!r}")


# ---------------- Estratos (tamaño) ----------------

ESTRATOS = {
    "0": "todos",
    "1": "0 a 5 personas",
    "2": "6 a 10 personas",
    "3": "11 a 30 personas",
    "4": "31 a 50 personas",
    "5": "51 a 100 personas",
    "6": "101 a 250 personas",
    "7": "251 y más personas",
}

# Mapeo a tamaño comercial (para Skydropx)
ESTRATO_TO_TAMANO = {
    "1": "Micro", "2": "Micro",
    "3": "Pequeña", "4": "Pequeña",
    "5": "Mediana", "6": "Mediana",
    "7": "Grande",
}


# ---------------- Cliente ----------------

@dataclass
class DenueRecord:
    """Representación normalizada de un establecimiento DENUE."""
    id: str
    clee: str
    nombre_establecimiento: str
    razon_social: str
    clase_actividad: str
    clase_actividad_id: str | None
    estrato: str
    tamano: str | None
    telefono: str
    email: str
    sitio_web: str
    tipo_vialidad: str
    calle: str
    num_exterior: str
    num_interior: str
    colonia: str
    cp: str
    municipio: str
    estado: str
    longitud: float | None
    latitud: float | None
    tipo_establecimiento: str
    centro_comercial: str
    fecha_alta: str | None
    sector_id: str | None
    subsector_id: str | None
    rama_id: str | None
    subrama_id: str | None
    raw: dict = field(default_factory=dict)

    def has_contact(self) -> bool:
        return bool(self.telefono or self.email or self.sitio_web)


def _parse_ubicacion(ubic: str) -> tuple[str, str]:
    """'GUADALAJARA, Guadalajara, JALISCO' → (municipio, estado)."""
    parts = [p.strip() for p in ubic.split(",")]
    if len(parts) >= 3:
        return parts[1], parts[2]
    if len(parts) == 2:
        return parts[0], parts[1]
    return ubic.strip(), ""


def _to_record(d: dict) -> DenueRecord:
    municipio, estado = _parse_ubicacion(d.get("Ubicacion", ""))
    estrato_id = ""
    for k, v in ESTRATOS.items():
        if v == d.get("Estrato"):
            estrato_id = k
            break

    return DenueRecord(
        id=str(d.get("Id", "")),
        clee=d.get("CLEE", ""),
        nombre_establecimiento=d.get("Nombre", "").strip(),
        razon_social=d.get("Razon_social", "").strip(),
        clase_actividad=d.get("Clase_actividad", "").strip(),
        clase_actividad_id=d.get("CLASE_ACTIVIDAD_ID"),
        estrato=d.get("Estrato", ""),
        tamano=ESTRATO_TO_TAMANO.get(estrato_id),
        telefono=d.get("Telefono", "").strip(),
        email=d.get("Correo_e", "").strip().lower(),
        sitio_web=d.get("Sitio_internet", "").strip(),
        tipo_vialidad=d.get("Tipo_vialidad", ""),
        calle=d.get("Calle", ""),
        num_exterior=d.get("Num_Exterior", ""),
        num_interior=d.get("Num_Interior", ""),
        colonia=d.get("Colonia", ""),
        cp=d.get("CP", ""),
        municipio=municipio,
        estado=estado,
        longitud=float(d["Longitud"]) if d.get("Longitud") else None,
        latitud=float(d["Latitud"]) if d.get("Latitud") else None,
        tipo_establecimiento=d.get("Tipo", ""),
        centro_comercial=d.get("nom_corredor_industrial", ""),
        fecha_alta=d.get("Fecha_Alta"),
        sector_id=d.get("SECTOR_ACTIVIDAD_ID"),
        subsector_id=d.get("SUBSECTOR_ACTIVIDAD_ID"),
        rama_id=d.get("RAMA_ACTIVIDAD_ID"),
        subrama_id=d.get("SUBRAMA_ACTIVIDAD_ID"),
        raw=d,
    )


class DenueClient:
    """Cliente HTTP del API DENUE con todos los endpoints probados."""

    def __init__(
        self,
        token: str | None = None,
        timeout: int = DEFAULT_TIMEOUT,
        delay: float = DEFAULT_DELAY,
        session: requests.Session | None = None,
    ):
        self.token = token or os.environ.get("DENUE_TOKEN")
        if not self.token:
            raise ValueError(
                "DENUE_TOKEN no configurado. Regístrate gratis en "
                "https://www.inegi.org.mx/app/api/denue/v1/tutorial.html"
            )
        self.timeout = timeout
        self.delay = delay
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": "AgenteFenix/5.0 (research; +https://skydropx.com)",
            "Accept": "application/json",
        })

    # ---------- HTTP base con tenacity ----------
    def _get_raw(self, url: str):
        """HTTP GET con manejo de status. Se envuelve con @retry si tenacity disponible."""
        r = self.session.get(url, timeout=self.timeout)
        if r.status_code in (429, 500, 502, 503, 504):
            raise requests.HTTPError(f"DENUE retryable {r.status_code}", response=r)
        return r

    def _get(self, path: str) -> list[dict] | dict:
        url = f"{DENUE_BASE}/{path}"
        try:
            if HAS_TENACITY:
                _retrying = retry(
                    stop=stop_after_attempt(3),
                    wait=wait_exponential(multiplier=1, min=2, max=10),
                    retry=retry_if_exception_type(requests.RequestException),
                    reraise=True,
                )(self._get_raw)
                r = _retrying(url)
            else:
                r = self._get_raw(url)
        except requests.RequestException as e:
            logger.warning("DENUE %s falló tras retries: %s", path[:60], e)
            return []

        if r.status_code != 200:
            logger.error("DENUE %s → HTTP %s body=%s", path[:60], r.status_code, r.text[:200])
            return []
        txt = r.text.strip()
        if not txt:
            return []
        try:
            return r.json()
        except Exception as e:  # noqa: BLE001
            logger.error("DENUE %s → JSON parse err: %s", path[:60], e)
            return []

    # ---------- Cuantificar ----------
    def cuantificar(self, actividad: str, area: str = "0", estrato: str = "0") -> list[dict]:
        """
        Conteo de establecimientos.
        actividad: 2-6 dígitos (sector/subsector/rama/subrama/clase)
        area: 2-9 dígitos (entidad/municipio/localidad) o "0" para todo MX
        estrato: "0" (todos) o "1"-"7"
        """
        path = f"Cuantificar/{actividad}/{area}/{estrato}/{self.token}"
        data = self._get(path)
        return data if isinstance(data, list) else []

    # ---------- Ficha ----------
    def ficha(self, id_establecimiento: str) -> DenueRecord | None:
        path = f"Ficha/{id_establecimiento}/{self.token}"
        data = self._get(path)
        if isinstance(data, list) and data:
            return _to_record(data[0])
        return None

    # ---------- Nombre ----------
    def buscar_por_nombre(
        self, nombre: str, entidad: str = "00",
        registro_inicial: int = 1, registro_final: int = 50,
    ) -> list[DenueRecord]:
        nombre_enc = quote(nombre)
        path = f"Nombre/{nombre_enc}/{entidad}/{registro_inicial}/{registro_final}/{self.token}"
        data = self._get(path)
        return [_to_record(d) for d in (data or [])] if isinstance(data, list) else []

    # ---------- BuscarEntidad ----------
    def buscar_por_entidad(
        self, condicion: str, entidad: str = "00",
        registro_inicial: int = 1, registro_final: int = 50,
    ) -> list[DenueRecord]:
        cond_enc = quote(condicion)
        path = f"BuscarEntidad/{cond_enc}/{entidad}/{registro_inicial}/{registro_final}/{self.token}"
        data = self._get(path)
        return [_to_record(d) for d in (data or [])] if isinstance(data, list) else []

    # ---------- BuscarAreaActEstr (el más usado para volumen) ----------
    def buscar_area_act_estr(
        self,
        entidad: str = "00",
        municipio: str = "0",
        localidad: str = "0",
        ageb: str = "0",
        manzana: str = "0",
        sector: str = "0",
        subsector: str = "0",
        rama: str = "0",
        clase: str = "0",
        nombre_estab: str = "0",
        registro_inicial: int = 1,
        registro_final: int = 50,
        id_estab: str = "0",
        estrato: str = "0",
    ) -> list[DenueRecord]:
        """
        Búsqueda completa por área geográfica + actividad económica + estrato.

        ⚠ Importante: especifica al menos uno de {sector, subsector, rama, clase}
        para no traer TODO el universo de una entidad.
        """
        nombre_enc = quote(nombre_estab) if nombre_estab != "0" else "0"
        path = (
            f"BuscarAreaActEstr/{entidad}/{municipio}/{localidad}/{ageb}/{manzana}"
            f"/{sector}/{subsector}/{rama}/{clase}/{nombre_enc}"
            f"/{registro_inicial}/{registro_final}/{id_estab}/{estrato}/{self.token}"
        )
        data = self._get(path)
        return [_to_record(d) for d in (data or [])] if isinstance(data, list) else []

    # ---------- Buscar (por coordenadas + radio) ----------
    def buscar_por_coordenadas(
        self, condicion: str, latitud: float, longitud: float, radio_m: int = 1000,
    ) -> list[DenueRecord]:
        """Para campañas locales: 'cafeterías a 1km de mi sucursal'."""
        if not 1 <= radio_m <= 5000:
            raise ValueError("radio_m debe estar entre 1 y 5000 metros")
        cond_enc = quote(condicion)
        path = f"Buscar/{cond_enc}/{latitud},{longitud}/{radio_m}/{self.token}"
        data = self._get(path)
        return [_to_record(d) for d in (data or [])] if isinstance(data, list) else []

    # ---------- Iterador paginado de alto volumen ----------
    def iter_area_act_estr(
        self,
        entidad: str,
        actividad_kwargs: dict,
        estrato: str = "0",
        page_size: int = 100,
        max_records: int | None = None,
    ) -> Iterator[DenueRecord]:
        """
        Pagina automáticamente buscar_area_act_estr.
        actividad_kwargs: dict con uno de {sector, subsector, rama, clase}.

        Ejemplo:
            client.iter_area_act_estr(
                entidad="09",
                actividad_kwargs={"sector": "46"},
                estrato="0",
                max_records=10000,
            )
        """
        emitted = 0
        start = 1
        while True:
            end = start + page_size - 1
            batch = self.buscar_area_act_estr(
                entidad=entidad,
                registro_inicial=start,
                registro_final=end,
                estrato=estrato,
                **actividad_kwargs,
            )
            if not batch:
                break
            for rec in batch:
                yield rec
                emitted += 1
                if max_records and emitted >= max_records:
                    return
            if len(batch) < page_size:
                break
            start += page_size
            time.sleep(self.delay)


# ---------------- Helper de alto nivel ----------------

def search_for_skydropx(
    nicho_scian: str | list[str],
    zona: str = "00",
    estrato: str = "0",
    max_per_zone: int = 5000,
    token: str | None = None,
) -> list[DenueRecord]:
    """
    Helper para el pipeline de Fénix v5:
    - acepta 1 o varios códigos SCIAN
    - resuelve zona si viene como nombre ("Jalisco") o clave ("14")
    - itera paginado hasta max_per_zone por zona
    """
    client = DenueClient(token=token)
    entidad = resolve_entidad(zona) if zona not in ("00", "0", "nacional") else "00"

    scianes = [nicho_scian] if isinstance(nicho_scian, str) else nicho_scian
    out: list[DenueRecord] = []

    for scian in scianes:
        # Determinar qué nivel pasar (sector/subsector/rama/clase) por longitud
        if len(scian) == 2:
            kwargs = {"sector": scian}
        elif len(scian) == 3:
            kwargs = {"subsector": scian}
        elif len(scian) == 4:
            kwargs = {"rama": scian}
        elif len(scian) == 5 or len(scian) == 6:
            kwargs = {"clase": scian}
        else:
            logger.warning("SCIAN inválido: %r", scian)
            continue

        if entidad == "00":
            # Iterar todas las entidades para no exceder paginación
            for ent_clave in ENTIDADES_MX.keys():
                for rec in client.iter_area_act_estr(
                    entidad=ent_clave,
                    actividad_kwargs=kwargs,
                    estrato=estrato,
                    max_records=max_per_zone,
                ):
                    out.append(rec)
        else:
            for rec in client.iter_area_act_estr(
                entidad=entidad,
                actividad_kwargs=kwargs,
                estrato=estrato,
                max_records=max_per_zone,
            ):
                out.append(rec)

    logger.info("DENUE: %s establecimientos descubiertos (SCIAN=%s, zona=%s)",
                len(out), scianes, zona)
    return out


__all__ = [
    "DenueClient", "DenueRecord", "search_for_skydropx",
    "resolve_entidad", "ENTIDADES_MX", "ESTRATOS", "ESTRATO_TO_TAMANO",
]
