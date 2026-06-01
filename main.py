"""ETL incremental del despacho de cemento (AFCP -> Supabase).

Por defecto recorre los últimos N meses y, para cada uno, intenta traer el valor
provisorio y el definitivo de AFCP, insertando un snapshot sólo si es nuevo o
cambió (modelo append-only con dedup). Pensado para correr a diario por cron.

Ejemplos:
    python main.py                      # últimos 3 meses, provisorio + definitivo
    python main.py --months-back 6
    python main.py --month 2026-04      # un mes puntual
    python main.py --force              # inserta aunque el valor no haya cambiado
    python main.py --file cemento.xlsx  # delega en la carga histórica
"""
import argparse
import sys
from datetime import date

import afcp
import db
import load_history


def month_iter(end: date, n: int):
    """Genera los últimos n meses (primer día) hacia atrás desde `end` (inclusive)."""
    y, m = end.year, end.month
    for _ in range(n):
        yield date(y, m, 1)
        m -= 1
        if m == 0:
            y, m = y - 1, 12


def process_month(conn, fecha: date, *, force: bool):
    """Trae provisorio y definitivo del mes y los snapshotea si corresponde."""
    for estado, getter in (("provisorio", afcp.get_provisorio),
                            ("definitivo", afcp.get_definitivo)):
        try:
            valor, url = getter(fecha.year, fecha.month)
        except Exception as e:  # red caída, HTML inesperado, etc.
            print(f"  {fecha:%Y-%m} {estado:10} ERROR {e}")
            continue
        if valor is None:
            print(f"  {fecha:%Y-%m} {estado:10} no publicado")
            continue
        result = db.insert_if_changed(conn, fecha, valor, estado, url, force=force)
        print(f"  {fecha:%Y-%m} {estado:10} valor={valor} -> {result}")


def main():
    ap = argparse.ArgumentParser(description="ETL despacho de cemento AFCP")
    ap.add_argument("--months-back", type=int, default=3,
                    help="cantidad de meses hacia atrás a revisar (default 3)")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--force", action="store_true",
                    help="inserta snapshot aunque el valor no haya cambiado")
    ap.add_argument("--file", help="cargar histórico desde xlsx en vez de scrapear")
    args = ap.parse_args()

    if args.file:
        sys.argv = ["load_history.py", "--file", args.file]
        return load_history.main()

    if args.month:
        y, m = map(int, args.month.split("-"))
        months = [date(y, m, 1)]
    else:
        months = list(month_iter(date.today(), args.months_back))

    conn = db.get_conn()
    try:
        for fecha in months:
            process_month(conn, fecha, force=args.force)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
