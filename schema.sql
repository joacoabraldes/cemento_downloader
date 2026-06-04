-- Serie histórica de despacho de cemento (AFCP).
-- Modelo append-only: cada corrida del ETL inserta un snapshot nuevo con su
-- ingested_at, conservando provisorio y definitivo. La "vista actual" toma el
-- último snapshot por fecha (priorizando definitivo). La serie desestacionalizada
-- (Census X-13) se guarda como estado='desestacionalizado' con UPSERT (1 fila/mes).

create table if not exists cemento_despacho (
  id          bigint generated always as identity primary key,
  date        date    not null,              -- primer día del mes
  valor       double precision not null,     -- Despacho Nacional del mes, miles de toneladas
  estado      text    check (estado in ('provisorio','definitivo','desestacionalizado')),  -- null = histórico xlsx
  fuente      text,                           -- url de origen / 'census x13' (null para histórico)
  ingested_at timestamptz not null default now(),
  -- campos adicionales (solo se llenan en filas 'definitivo'; valores tal como se
  -- muestran en AFCP):
  exportacion               double precision, -- Exportación del mes
  consumo_despacho_nacional double precision, -- Despacho Nacional del Consumo del Mercado Interno
  importaciones_propias     double precision  -- Importaciones Propias del mes
);

create index if not exists cemento_despacho_date_ingested_idx
  on cemento_despacho (date, ingested_at desc);

-- UPSERT de la serie desestacionalizada: a lo sumo 1 fila por mes con ese estado.
create unique index if not exists cemento_despacho_desest_uniq
  on cemento_despacho (date) where estado = 'desestacionalizado';

-- Valor "actual" por mes de la serie OBSERVADA (excluye desestacionalizado):
-- último snapshot, priorizando el definitivo sobre el provisorio.
create or replace view cemento_despacho_actual as
select distinct on (date) date, valor, estado, ingested_at
from cemento_despacho
where estado is distinct from 'desestacionalizado'
order by date, (estado = 'definitivo') desc, ingested_at desc;

-- Serie desestacionalizada (Census X-13).
create or replace view cemento_despacho_desest as
select date, valor, ingested_at from cemento_despacho
where estado = 'desestacionalizado' order by date;
