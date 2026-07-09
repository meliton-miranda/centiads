# NetUs Ads Cockpit

Plataforma que unifica **Meta Ads** (por cliente/campaña/anuncio) con el embudo de **Go High Level**,
con 3 vistas (Clientes · NetUs · Conexiones GHL), 3 niveles de drill-down, semáforos y **filtro por fecha**.

Construida en **Python** (sin Node): un servidor web mínimo (`scripts/server.py`, solo `pg8000`) que lee
Postgres y renderiza el dashboard. Ideal para desplegar en **EasyPanel** al lado de la base.

## Correr local
```bash
cp .env.example .env          # DATABASE_URL de tu Postgres
python3 -m pip install --user pg8000
python3 scripts/pgexec.py db/schema.sql     # aplica el esquema (una vez)
python3 scripts/server.py                    # http://localhost:8080
```

## Ingesta (Meta solo por MCP)
La ingesta la ejecuta un agente Claude siguiendo `ingest/routine.md`: llama al MCP de Meta,
arma un JSON y lo carga con `scripts/load.mjs` (Node) **o** vía `scripts/pgexec.py` (Python).
El `render.py` genera un HTML estático de respaldo desde la base.

## Desplegar en EasyPanel
1. Sube este repo a un Git (GitHub).
2. EasyPanel → proyecto → **+ Service → App** → Source: tu repo → Build: **Dockerfile**.
3. Env var **`DATABASE_URL`** = conexión **interna** a la base
   (`postgres://postgres:PASS@administrador_de_anuncios_cockpit-db:5432/administrador_de_anuncios?sslmode=disable`).
4. **Exponer** el puerto 8080 → obtienes la URL de tu plataforma.

## Archivos
```
scripts/server.py      plataforma web (Python, pg8000)
scripts/render.py      snapshot HTML estático desde la base
scripts/pgexec.py      ejecutar SQL contra Postgres (Python)
scripts/load.mjs       loader del payload de ingesta (Node)
db/schema.sql          esquema Postgres + vistas
ingest/routine.md      runbook del agente (MCP Meta → payload)
Dockerfile             imagen para EasyPanel
```
