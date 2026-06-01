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
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
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


def _next_number(lines, start_idx):
    """Primer número entero (con separador de miles) a partir de start_idx."""
    for ln in lines[start_idx:]:
        if NUM_RE.match(ln):
            return _to_miles(_parse_number(ln))
    return None


def _to_miles(toneladas: float) -> float:
    """Convierte toneladas a miles de toneladas (unidad del xlsx)."""
    return round(toneladas / 1000.0, 3)


def _parse_number(raw: str) -> float:
    """Convierte un número con separadores AR/EN ('730.644' / '730,644') a float
    de toneladas. En estas páginas los valores son enteros de toneladas con
    separador de miles, así que quitamos puntos y comas."""
    cleaned = re.sub(r"[^\d]", "", raw)
    return float(cleaned)


def _anchor_despacho(lines):
    """Índice de la primera fila 'Despacho Nacional' (encabezado de la tabla de
    despacho). Anclamos ahí para no confundirnos con la tabla de CONSUMO."""
    for i, ln in enumerate(lines):
        if re.search(r"despacho\s+nacional", ln, re.IGNORECASE):
            return i
    return 0


def parse_provisorio(html: str, year: int, month: int):
    """Despacho Nacional del Mes (miles de tn) de la página provisoria.

    Layout: tras anclar en la tabla de despacho, la fila del período objetivo está
    etiquetada como "<Mes> <Año>" (ej: "Abril 2026") y el primer número que sigue
    es el Despacho Nacional Mensual.
    """
    lines = _text_lines(html)
    start = _anchor_despacho(lines)
    label = f"{MESES[month]} {year}".lower()
    for i in range(start, len(lines)):
        if lines[i].lower() == label:
            return _next_number(lines, i + 1)
    return None


def parse_definitivo(html: str, year: int, month: int):
    """Despacho Nacional del Mes (miles de tn) de la página definitiva.

    Layout: la página es específica del mes (la URL ya trae YYYYMM) y compara
    "Año <anterior>" vs "Año <actual>". La fila "Año <year>" da el definitivo del
    mes; el primer número que sigue es el Despacho Nacional Del Mes. Matcheamos por
    el año (evitamos depender de la 'ñ' por temas de encoding).
    """
    lines = _text_lines(html)
    start = _anchor_despacho(lines)
    target = str(year)
    for i in range(start, len(lines)):
        ln = lines[i]
        # línea tipo "Año 2026": termina en el año y no contiene otros dígitos.
        if ln.endswith(target) and re.sub(r"\D", "", ln) == target and len(ln) <= 14:
            return _next_number(lines, i + 1)
    return None


def get_provisorio(year: int, month: int):
    """Devuelve (valor_miles_tn, url) del provisorio, o (None, url) si no está publicado."""
    url = url_provisorio(year, month)
    html = fetch(url)
    return (parse_provisorio(html, year, month) if html else None), url


def get_definitivo(year: int, month: int):
    """Devuelve (valor_miles_tn, url) del definitivo, o (None, url) si no está publicado."""
    url = url_definitivo(year, month)
    html = fetch(url)
    return (parse_definitivo(html, year, month) if html else None), url
