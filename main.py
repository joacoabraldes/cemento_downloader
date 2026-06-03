"""ETL incremental del despacho de cemento (AFCP -> Supabase).

Por defecto recorre los últimos N meses y, para cada uno, intenta traer los valores
provisorio y definitivo de AFCP, insertando un snapshot sólo si es nuevo o cambió
(modelo append-only con dedup). Al terminar corre la desestacionalización (X-13).
Pensado para correr a diario por cron.

Ejemplos:
    python main.py                      # últimos 2 meses + desestacionalización
    python main.py --months-back 6
    python main.py --month 2026-04      # un mes puntual
    python main.py --force              # inserta aunque el valor no haya cambiado
    python main.py --no-desest          # saltea la desestacionalización

La carga histórica inicial (one-off) se hace aparte con load_history.py.
"""
import argparse
from datetime import date

import afcp
import db
import seasonal


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
    # Si el mes ya tiene definitivo, el dato es final: no hace falta volver a
    # bajar las páginas de AFCP (salvo --force).
    if not force and db.has_definitivo(conn, fecha):
        print(f"  {fecha:%Y-%m} ya tiene definitivo -> skip")
        return
    for estado, getter in (("provisorio", afcp.get_provisorio),
                            ("definitivo", afcp.get_definitivo)):
        try:
            fields, url = getter(fecha.year, fecha.month)
        except Exception as e:  # red caída, HTML inesperado, etc.
            print(f"  {fecha:%Y-%m} {estado:10} ERROR {e}")
            continue
        if fields is None:
            print(f"  {fecha:%Y-%m} {estado:10} no publicado")
            continue
        result = db.insert_if_changed(conn, fecha, fields, estado, url, force=force)
        print(f"  {fecha:%Y-%m} {estado:10} dn={fields['despacho_nacional']} -> {result}")


def main():
    ap = argparse.ArgumentParser(description="ETL despacho de cemento AFCP")
    ap.add_argument("--months-back", type=int, default=2,
                    help="cantidad de meses hacia atrás a revisar (default 2)")
    ap.add_argument("--month", help="mes puntual YYYY-MM (ignora --months-back)")
    ap.add_argument("--force", action="store_true",
                    help="inserta snapshot aunque el valor no haya cambiado")
    ap.add_argument("--no-desest", action="store_true",
                    help="saltea la desestacionalización (X-13) al final")
    args = ap.parse_args()

    if args.month:
        y, m = map(int, args.month.split("-"))
        months = [date(y, m, 1)]
    else:
        months = list(month_iter(date.today(), args.months_back))

    conn = db.get_conn()
    try:
        for fecha in months:
            process_month(conn, fecha, force=args.force)
        if not args.no_desest:
            try:
                seasonal.deseasonalize(conn)
            except Exception as e:  # X-13 nunca debe tumbar el ETL
                print(f"  [desest] error inesperado: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
