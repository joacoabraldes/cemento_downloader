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


def insert_snapshot(conn, fecha: date, valor: float, estado, fuente=None):
    """Inserta un snapshot. Devuelve el id insertado."""
    with conn.cursor() as cur:
        cur.execute(
            "insert into cemento_despacho (date, valor, estado, fuente) "
            "values (%s, %s, %s, %s) returning id",
            (fecha, valor, estado, fuente),
        )
        new_id = cur.fetchone()[0]
    conn.commit()
    return new_id


def insert_if_changed(conn, fecha: date, valor: float, estado, fuente=None,
                      *, force=False, tol=1e-6):
    """Inserta un snapshot sólo si es nuevo o el valor cambió (salvo force=True).

    Devuelve "inserted", "unchanged" o "skipped".
    """
    if valor is None:
        return "skipped"
    prev = latest_valor(conn, fecha, estado)
    if not force and prev is not None and abs(prev - valor) <= tol:
        return "unchanged"
    insert_snapshot(conn, fecha, valor, estado, fuente)
    return "inserted"
