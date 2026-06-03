"""Conexión a Postgres (Supabase) e inserción de snapshots con dedup.

Modelo append-only: cada snapshot es una fila nueva. Para no duplicar en cada
corrida del cron, sólo insertamos un (fecha, estado) si no existe o si su valor
cambió respecto del último snapshot.
"""
import os
from datetime import date

import psycopg2
from dotenv import load_dotenv

load_dotenv()


def get_conn():
    """Abre una conexión Postgres.

    Prioriza DATABASE_URL (la cadena que ofrece el botón "Connect" de Supabase);
    si no está, arma la conexión desde las variables POSTGRES_*.
    """
    url = os.environ.get("DATABASE_URL")
    if url:
        return psycopg2.connect(url)
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "postgres"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ["POSTGRES_PASSWORD"],
        sslmode=os.environ.get("POSTGRES_SSLMODE", "require"),
    )


def latest_valor(conn, fecha: date, estado):
    """Devuelve el valor del último snapshot para (fecha, estado), o None si no hay.

    `estado` puede ser None para consultar las filas históricas (estado IS NULL).
    """
    with conn.cursor() as cur:
        if estado is None:
            cur.execute(
                "select valor from cemento_despacho "
                "where date = %s and estado is null "
                "order by ingested_at desc limit 1",
                (fecha,),
            )
        else:
            cur.execute(
                "select valor from cemento_despacho "
                "where date = %s and estado = %s "
                "order by ingested_at desc limit 1",
                (fecha, estado),
            )
        row = cur.fetchone()
        return row[0] if row else None


def has_definitivo(conn, fecha: date) -> bool:
    """True si el mes ya tiene un snapshot definitivo (dato final, no vuelve a cambiar)."""
    with conn.cursor() as cur:
        cur.execute(
            "select 1 from cemento_despacho "
            "where date = %s and estado = 'definitivo' limit 1",
            (fecha,),
        )
        return cur.fetchone() is not None


def latest_fields(conn, fecha: date, estado):
    """Tupla (valor, exportacion, consumo_despacho_nacional, importaciones_propias)
    del último snapshot para (fecha, estado), o None si no hay."""
    with conn.cursor() as cur:
        cur.execute(
            "select valor, exportacion, consumo_despacho_nacional, importaciones_propias "
            "from cemento_despacho where date = %s and estado = %s "
            "order by ingested_at desc limit 1",
            (fecha, estado),
        )
        return cur.fetchone()


def insert_snapshot(conn, fecha: date, valor: float, estado, fuente=None, *,
                    exportacion=None, consumo_despacho_nacional=None,
                    importaciones_propias=None):
    """Inserta un snapshot. Devuelve el id insertado."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into cemento_despacho "
            "(date, valor, estado, fuente, exportacion, consumo_despacho_nacional, "
            " importaciones_propias) "
            "values (%s, %s, %s, %s, %s, %s, %s) returning id",
            (fecha, valor, estado, fuente, exportacion, consumo_despacho_nacional,
             importaciones_propias),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def _same(prev, new, tol):
    """True si dos tuplas de valores (con posibles None) son iguales dentro de tol."""
    for a, b in zip(prev, new):
        if a is None and b is None:
            continue
        if a is None or b is None or abs(a - b) > tol:
            return False
    return True


def insert_if_changed(conn, fecha: date, fields: dict, estado, fuente=None,
                      *, force=False, tol=1e-6):
    """Inserta un snapshot sólo si es nuevo o cambió algún campo (salvo force=True).

    `fields` es el dict del parser (despacho_nacional + 3 campos). Devuelve
    "inserted", "unchanged" o "skipped".
    """
    valor = fields.get("despacho_nacional")
    if valor is None:
        return "skipped"
    new = (
        valor,
        fields.get("exportacion"),
        fields.get("consumo_despacho_nacional"),
        fields.get("importaciones_propias"),
    )
    prev = latest_fields(conn, fecha, estado)
    if not force and prev is not None and _same(prev, new, tol):
        return "unchanged"
    insert_snapshot(conn, fecha, valor, estado, fuente,
                    exportacion=new[1], consumo_despacho_nacional=new[2],
                    importaciones_propias=new[3])
    return "inserted"
