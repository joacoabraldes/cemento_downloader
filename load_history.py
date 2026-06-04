"""Carga histórica de la serie de despacho de cemento desde cemento.xlsx.

Inserta todas las filas del xlsx con estado=NULL (no tienen estado provisorio/
definitivo), excluyendo abril 2026, que se vuelve a cargar vía scraping para
corregir el error de carga manual (quedó con el provisorio en vez del definitivo).

Idempotente: no reinserta una fecha histórica que ya esté cargada.
"""
import argparse
from datetime import date

import openpyxl

import db

DEFAULT_XLSX = "cemento.xlsx"
EXCLUDE = {date(2026, 4, 1)}  # abril 2026 se carga vía scraping


def read_rows(path: str):
    """Lee (fecha, valor) del xlsx. La hoja no tiene header."""
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    rows = []
    for fecha, valor in ws.iter_rows(values_only=True):
        if fecha is None or valor is None:
            continue
        d = fecha.date() if hasattr(fecha, "date") else fecha
        rows.append((d, float(valor)))
    return rows


def main():
    ap = argparse.ArgumentParser(description="Carga histórica desde cemento.xlsx")
    ap.add_argument("--file", default=DEFAULT_XLSX, help="ruta al xlsx")
    args = ap.parse_args()

    rows = read_rows(args.file)
    conn = db.get_conn()
    inserted = skipped_existing = excluded = 0
    try:
        for fecha, valor in rows:
            if fecha in EXCLUDE:
                excluded += 1
                continue
            # estado NULL para el histórico; idempotencia por (fecha, estado IS NULL)
            if db.latest_valor(conn, fecha, None) is not None:
                skipped_existing += 1
                continue
            db.insert_snapshot(conn, fecha, valor, estado=None, fuente=None)
            inserted += 1
    finally:
        conn.close()

    print(f"insertadas={inserted} ya_existian={skipped_existing} "
          f"excluidas(abril2026)={excluded} total_xlsx={len(rows)}")


if __name__ == "__main__":
    main()
