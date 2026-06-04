"""Builders de URL y parsers para las estadísticas de despacho de cemento de AFCP.

El dato que nos interesa es el "Despacho Nacional - Del Mes" (toneladas), que
guardamos en miles de toneladas para igualar las unidades de la serie histórica
del xlsx (ej: 730644 toneladas -> 730.644).

Hay dos fuentes:
  - Provisorio: página de "Despacho Mensual" (texto preformateado).
  - Definitivo: página de "Datos Definitivos" (tablas HTML).

NOTA: los selectores/regex se afinan contra el HTML real. Las funciones de parsing
reciben el HTML como string para poder testearlas con fixtures.
"""
import re

import requests
from bs4 import BeautifulSoup

BASE = "https://afcp.info/ESTADISTICAS"
HEADERS = {"User-Agent": "Mozilla/5.0 (cemento_downloader ETL)"}
TIMEOUT = 30

# Sesión compartida: reutiliza la conexión TCP/TLS (keep-alive) entre meses.
SESSION = requests.Session()
SESSION.headers.update(HEADERS)

MESES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre",
    12: "Diciembre",
}

# Número entero con separador de miles por puntos: "730.644", "3.041.356".
NUM_RE = re.compile(r"^\d{1,3}(\.\d{3})*$")


def url_provisorio(year: int, month: int) -> str:
    ym = f"{year:04d}{month:02d}"
    return f"{BASE}/DESPACHO-MENSUAL/P{ym}/P{ym}.html"


def url_definitivo(year: int, month: int) -> str:
    ym = f"{year:04d}{month:02d}"
    return f"{BASE}/DATOS-DEFINITIVOS/{ym}-ProDesp/estadistica02.html"


def fetch(url: str):
    """GET de la url. Devuelve el texto (HTML) o None si no existe (404) aún."""
    resp = SESSION.get(url, timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    # Las páginas declaran ISO-8859-1 pero son Windows-1252.
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def _text_lines(html: str):
    """Texto de la página como lista de líneas no vacías."""
    text = BeautifulSoup(html, "lxml").get_text("\n")
    return [ln.strip() for ln in text.splitlines() if ln.strip()]


def _value_as_shown(raw: str) -> float:
    """Valor tal como aparece en la tabla de AFCP.

    En estas páginas el "." es separador de miles. Un número con separador
    (ej. "730.432" = 730432 t) se interpreta como se ve, o sea en miles (730.432);
    un número sin separador (ej. "212") se deja tal cual (212). Así el dato queda
    idéntico al mostrado en la planilla."""
    digits = re.sub(r"[^\d]", "", raw)
    value = float(digits)
    if "." in raw:
        value /= 1000.0
    return round(value, 3)


def _collect_numbers(lines, start_idx, n):
    """Primeros n números desde start_idx, tal como se muestran en la tabla."""
    out = []
    for ln in lines[start_idx:]:
        if NUM_RE.match(ln):
            out.append(_value_as_shown(ln))
            if len(out) >= n:
                break
    return out


def _anchor_despacho(lines):
    """Índice de la primera fila 'Despacho Nacional' (encabezado de la tabla de
    Despacho de Cemento). Anclamos ahí para saltar el título de la página."""
    for i, ln in enumerate(lines):
        if re.search(r"despacho\s+nacional", ln, re.IGNORECASE):
            return i
    return 0


def _anchor_consumo(lines):
    """Índice donde empieza la tabla de Consumo del Mercado Interno. La fila de
    'Importaciones' (Propias) aparece en ambas páginas y marca esa sección."""
    for i, ln in enumerate(lines):
        if re.search(r"importaciones", ln, re.IGNORECASE):
            return i
    return len(lines)


def _find_label(lines, matcher, start):
    """Primera línea >= start que cumple matcher (la etiqueta de período)."""
    for i in range(start, len(lines)):
        if matcher(lines[i]):
            return i
    return None


def _extract_fields(lines, matcher):
    """Extrae los 4 valores 'Del Mes' (tal como se muestran) usando el matcher de la
    etiqueta de período. Tras cada etiqueta los números van en el orden
    [nacional_mes, nacional_acum, otro_mes, otro_acum, total_mes, total_acum], así que
    tomamos los índices 0 (nacional) y 2 (exportación / importaciones)."""
    desp_i = _find_label(lines, matcher, _anchor_despacho(lines))
    cons_i = _find_label(lines, matcher, _anchor_consumo(lines))
    desp = _collect_numbers(lines, desp_i + 1, 3) if desp_i is not None else []
    cons = _collect_numbers(lines, cons_i + 1, 3) if cons_i is not None else []

    def pick(seq, idx):
        return seq[idx] if len(seq) > idx else None

    fields = {
        "despacho_nacional": pick(desp, 0),
        "exportacion": pick(desp, 2),
        "consumo_despacho_nacional": pick(cons, 0),
        "importaciones_propias": pick(cons, 2),
    }
    # Si no encontramos ni el dato principal, el mes no está publicado en esa página.
    return fields if fields["despacho_nacional"] is not None else None


def parse_provisorio(html: str, year: int, month: int):
    """Dict con SOLO el Despacho Nacional de la página provisoria.

    Los campos adicionales (exportación, consumo, importaciones) se toman únicamente
    del definitivo; en el provisorio quedan en None. La fila del período se etiqueta
    como "<Mes> <Año>" (ej: "Abril 2026").
    """
    lines = _text_lines(html)
    label = f"{MESES[month]} {year}".lower()
    desp_i = _find_label(lines, lambda ln: ln.lower() == label, _anchor_despacho(lines))
    nums = _collect_numbers(lines, desp_i + 1, 1) if desp_i is not None else []
    if not nums:
        return None
    return {"despacho_nacional": nums[0], "exportacion": None,
            "consumo_despacho_nacional": None, "importaciones_propias": None}


def parse_definitivo(html: str, year: int, month: int):
    """Dict de campos (miles de tn) de la página definitiva.

    La página compara "Año <anterior>" vs "Año <actual>"; matcheamos la fila del año
    objetivo (por el año, no por la palabra "Año", para no depender de la 'ñ').
    """
    lines = _text_lines(html)
    target = str(year)

    def matcher(ln):
        # "Año 2026": termina en el año y no contiene otros dígitos.
        return ln.endswith(target) and re.sub(r"\D", "", ln) == target and len(ln) <= 14

    return _extract_fields(lines, matcher)


def get_provisorio(year: int, month: int):
    """Devuelve (dict_campos, url) del provisorio, o (None, url) si no está publicado."""
    url = url_provisorio(year, month)
    html = fetch(url)
    return (parse_provisorio(html, year, month) if html else None), url


def get_definitivo(year: int, month: int):
    """Devuelve (dict_campos, url) del definitivo, o (None, url) si no está publicado."""
    url = url_definitivo(year, month)
    html = fetch(url)
    return (parse_definitivo(html, year, month) if html else None), url
