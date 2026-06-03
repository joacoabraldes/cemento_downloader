"""Desestacionalización con Census X-13ARIMA-SEATS.

Diseñado para reutilizarse entre ETLs: toma una serie mensual observada (1 valor por
mes) desde una vista, corre X-13 y hace UPSERT del resultado como un estado aparte
(por defecto 'desestacionalizado'), una fila por mes que se actualiza en cada corrida.

Requisitos para que efectivamente desestacionalice:
  - statsmodels instalado.
  - El binario x13as accesible y la variable de entorno X13PATH apuntándolo
    (típicamente en Kali Linux). Ver README.

Si falta cualquiera de los dos, NO rompe: loguea un aviso y saltea (devuelve "skipped"),
así el ETL y la demo en Windows siguen funcionando.
"""
import os

MIN_MESES = 36  # X-13 necesita varios años de historia para estimar la estacionalidad


def deseasonalize(conn, table="cemento_despacho", *,
                  source_view="cemento_despacho_actual",
                  out_estado="desestacionalizado", fuente="census x13"):
    """Corre X-13 sobre la serie observada y hace UPSERT de la desestacionalizada.

    Devuelve "ok", "skipped" o "error".
    """
    x13path = os.environ.get("X13PATH")
    if not x13path:
        print("  [desest] X13PATH no seteado -> se saltea la desestacionalización")
        return "skipped"

    try:
        import pandas as pd
        from statsmodels.tsa.x13 import x13_arima_analysis
    except ImportError as e:
        print(f"  [desest] statsmodels no disponible ({e}) -> se saltea")
        return "skipped"

    # 1. Serie observada (1 valor por mes) desde la vista.
    with conn.cursor() as cur:
        cur.execute(f"select date, valor from {source_view} order by date")
        rows = cur.fetchall()
    if len(rows) < MIN_MESES:
        print(f"  [desest] serie demasiado corta ({len(rows)} meses) -> se saltea")
        return "skipped"

    dates = [r[0] for r in rows]
    values = [float(r[1]) for r in rows]
    serie = pd.Series(values, index=pd.PeriodIndex(dates, freq="M"))

    # 2. Correr X-13ARIMA-SEATS.
    try:
        res = x13_arima_analysis(serie, x12path=x13path)
    except Exception as e:
        print(f"  [desest] X-13 falló: {e}")
        return "error"
    seasadj = res.seasadj  # serie desestacionalizada (PeriodIndex mensual)

    # 3. UPSERT: 1 fila por mes con estado=out_estado (se actualiza cada corrida).
    predicate = f"estado = '{out_estado}'"  # coincide con el índice parcial único
    sql = (
        f"insert into {table} (date, valor, estado, fuente) "
        f"values (%s, %s, %s, %s) "
        f"on conflict (date) where {predicate} "
        f"do update set valor = excluded.valor, ingested_at = now()"
    )
    n = 0
    with conn.cursor() as cur:
        for period, val in seasadj.items():
            d = period.to_timestamp().date()  # primer día del mes
            cur.execute(sql, (d, round(float(val), 3), out_estado, fuente))
            n += 1
    conn.commit()
    print(f"  [desest] {n} meses desestacionalizados (UPSERT, fuente='{fuente}')")
    return "ok"
