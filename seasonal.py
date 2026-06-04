"""Desestacionalización con Census X-13ARIMA-SEATS, llamando al binario directo.

Diseñado para reutilizarse entre ETLs: toma una serie mensual observada (1 valor por
mes) desde una vista, corre X-13 y hace UPSERT del resultado como un estado aparte
(por defecto 'desestacionalizado'), una fila por mes que se actualiza en cada corrida.

No depende de statsmodels: arma el archivo .spc, ejecuta x13as y lee la tabla d11
(serie desestacionalizada por X-11). Funciona con el binario "html" (x13ashtml)
renombrado a x13as, que es el que se consigue precompilado para Linux.

Requisitos:
  - El binario x13as accesible y la variable X13PATH apuntando a la carpeta que lo
    contiene (o directamente al binario). Ver README.

Si falta X13PATH/el binario, NO rompe: loguea un aviso y saltea (devuelve "skipped"),
así el ETL y la demo en Windows siguen funcionando.
"""
import os
import shutil
import subprocess
import tempfile
from datetime import date

MIN_MESES = 36           # X-13 necesita varios años de historia
VALORES_POR_LINEA = 10   # X-13 corta líneas de input a ~132 chars


def _x13_binary():
    """Ruta al binario x13as a partir de X13PATH (carpeta o archivo), o None."""
    x13path = os.environ.get("X13PATH")
    if not x13path:
        return None
    if os.path.isfile(x13path) and os.access(x13path, os.X_OK):
        return x13path
    cand = os.path.join(x13path, "x13as")
    if os.path.isfile(cand) and os.access(cand, os.X_OK):
        return cand
    return None


def _es_contigua(dates):
    """True si la lista de fechas (primer día de mes) es mensual sin huecos."""
    for a, b in zip(dates, dates[1:]):
        esperado_mes = a.month % 12 + 1
        esperado_anio = a.year + (1 if a.month == 12 else 0)
        if (b.year, b.month) != (esperado_anio, esperado_mes):
            return False
    return True


def _write_spc(path, dates, values):
    """Escribe el .spc de X-13 con los datos wrapeados a VALORES_POR_LINEA por línea."""
    y, m = dates[0].year, dates[0].month
    nums = [f"{v:.3f}" for v in values]
    bloques = ["  " + " ".join(nums[i:i + VALORES_POR_LINEA])
               for i in range(0, len(nums), VALORES_POR_LINEA)]
    data = "\n".join(bloques)
    spc = (
        f'series{{ title="serie" start={y}.{m:02d} period=12\n'
        f' data=(\n{data}\n ) }}\n'
        f'x11{{ save=(d11) }}\n'
    )
    with open(path, "w") as f:
        f.write(spc)


def _parse_d11(path):
    """Lee la tabla d11 -> lista de (date primer-día-de-mes, valor)."""
    out = []
    with open(path) as f:
        for ln in f:
            parts = ln.split()
            if len(parts) != 2:
                continue
            ym, val = parts
            if not (len(ym) == 6 and ym.isdigit()):
                continue  # saltea header y separador
            out.append((date(int(ym[:4]), int(ym[4:6]), 1), round(float(val), 3)))
    return out


def deseasonalize(conn, table="cemento_despacho", *,
                  source_view="cemento_despacho_actual",
                  out_estado="desestacionalizado", fuente="census x13"):
    """Corre X-13 sobre la serie observada y hace UPSERT de la desestacionalizada.

    Devuelve "ok", "skipped" o "error".
    """
    x13bin = _x13_binary()
    if not x13bin:
        print("  [desest] X13PATH no seteado o binario x13as no encontrado -> se saltea")
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
    if not _es_contigua(dates):
        print("  [desest] la serie tiene huecos mensuales -> se saltea")
        return "skipped"

    # 2. Correr x13as en un directorio temporal.
    workdir = tempfile.mkdtemp(prefix="x13_")
    base = "serie"
    _write_spc(os.path.join(workdir, base + ".spc"), dates, values)
    try:
        subprocess.run([x13bin, base], cwd=workdir, capture_output=True,
                       text=True, timeout=120)
    except Exception as e:
        print(f"  [desest] no se pudo ejecutar x13as: {e}")
        return "error"

    d11 = os.path.join(workdir, base + ".d11")
    if not os.path.isfile(d11):
        print(f"  [desest] X-13 no produjo la tabla d11. Revisar {workdir}/{base}_err.html")
        return "error"
    series = _parse_d11(d11)

    # 3. UPSERT: 1 fila por mes con estado=out_estado (se actualiza cada corrida).
    predicate = f"estado = '{out_estado}'"  # coincide con el índice parcial único
    sql = (
        f"insert into {table} (date, valor, estado, fuente) "
        f"values (%s, %s, %s, %s) "
        f"on conflict (date) where {predicate} "
        f"do update set valor = excluded.valor, ingested_at = now()"
    )
    with conn.cursor() as cur:
        for d, val in series:
            cur.execute(sql, (d, val, out_estado, fuente))
    conn.commit()

    shutil.rmtree(workdir, ignore_errors=True)
    print(f"  [desest] {len(series)} meses desestacionalizados (UPSERT, fuente='{fuente}')")
    return "ok"
