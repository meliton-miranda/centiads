# Runbook de ingesta diaria — NetUs Ads Cockpit

Instrucciones que sigue el **agente Claude programado** cada mañana (~07:00 CDMX).
La comunicación con Meta Ads es **solo por MCP**. El agente FETCHEA por MCP, arma un
payload JSON y lo carga con `node scripts/load.mjs`. El dashboard (Vercel) solo lee.

> Zona horaria de referencia: America/Mexico_City. "Ayer" = el día anterior en esa TZ.
> Las cuentas Meta con `is_ads_mcp_enabled:false` o no consultables se **omiten** (se
> reportan como "pendiente de habilitar", no como error).

## 0. Prerrequisitos (entorno del agente)
- `DATABASE_URL` apuntando al Postgres de Hostinger.
- Esquema ya aplicado: `psql "$DATABASE_URL" -f db/schema.sql`.
- El mapa `cockpit.clients` cargado (cuentas Meta ↔ subcuenta GHL + PIT + moneda + segment).
- Conector **MCP Meta Ads** disponible en el contexto de la corrida (ver riesgo #1 del plan).

## 1. Determinar el universo de cuentas (con AUTO-INTEGRACIÓN)
1. `ads_get_ad_accounts` (paginar si hay `next_cursor`) → lista completa de cuentas con acceso.
2. Leer las ya registradas: `select meta_ad_account_id from cockpit.clients`.
3. **Auto-integrar las que YA se pueden leer y NO estén registradas:** para cada cuenta con
   `is_queryable = true` **y** `is_ads_mcp_enabled = true` que no esté en `clients`, insertarla
   automáticamente (name = `ad_account_name` o `"Cuenta {id}"`, `currency`, `segment = netus` si
   `business_id = 2340785922946682` (NetUs), si no `client`) y jalar sus métricas ese día.
4. **Re-checar las bloqueadas:** las cuentas con `is_ads_mcp_enabled = false` (rollout de Meta) o
   `is_queryable = false` (UNSETTLED/cerradas) aún NO se pueden leer. En cuanto Meta las habilite o
   se liquide el saldo, el paso 3 **las integra solas** (no requiere intervención).
5. Ingestar solo las cuentas `is_queryable && is_ads_mcp_enabled` (activas del mapa).
6. Reportar en el resumen: **cuentas nuevas integradas hoy** + cuántas **siguen pendientes** y por qué.

## 2. Por cada cuenta Meta (loop) — vía MCP
Usar `date_preset: YESTERDAY` (o `time_range` con la fecha de ayer). Herramientas y mapeo:

### 2a. Campañas + insights → `campaigns_daily[]`
- Tool: **`ads_get_ad_entities`** con `level: campaign`, `date_preset: yesterday`, e insights.
- **Nombres de campo REALES** (verificados con `ads_get_field_context`):
  `id→campaign_id, name→campaign_name, objective, effective_status→status,
  amount_spent (alias spend), impressions, clicks, ctr, cpm, lead→leads, results, cost_per_result`.
- ⚠️ `leads`, `cost_per_lead` y `actions` **NO son campos válidos**. Los leads salen del campo **`lead`**.
- **5 eventos del pixel** (pedirlos como campos; confirmar nombre exacto con `ads_get_field_context`):
  `lead→leads` (Lead / clientes potenciales), `submit_application→ev_solicitud` (SubmitApplication),
  `complete_registration→ev_registro` (CompleteRegistration), `schedule→ev_cita` (Schedule / citas programadas),
  `purchase→ev_venta` (Purchase / ventas). El pixel de Meliton usa los **eventos estándar**.
- `cpl`: **no existe** campo directo → dejarlo nulo y el loader calcula `spend/leads`
  (o usar `cost_per_result` para campañas de objetivo LEADS).
- ⚠️ Los valores llegan **formateados como texto** (`"$1,386.46 MXN"`, `"2.13%"`, `"13,578"`).
  El loader (`numify`) los limpia; aun así conviene emitirlos limpios cuando se pueda.
- `date` = fecha de ayer. `currency` = la del mapa.

### 2b. Eventos del pixel → `pixel_events_daily[]`
- ⚠️ `action_type` **NO es un breakdown válido** en `ads_get_ad_entities` (el API lo rechaza),
  así que no se desglosa por tipo de acción por esa vía.
- **Por campaña:** pedir como **campos** los eventos de conversión nombrados que exponga el
  catálogo — listarlos con `ads_get_field_context()` (sin args) y elegir los int de conversión
  (p.ej. `lead`, `results`, `link_click`, `landing_page_view`, `purchase`,
  `messaging_conversation_started`…). Cada campo → un renglón en `pixel_events_daily`
  (`action_type` = nombre del campo, `count` = su valor).
- **Detalle completo del pixel a nivel cuenta:** `ads_get_datasets` → `ads_get_dataset_stats`,
  o `ads_pixel_event_read`; y `ads_get_customconversions` para conversiones personalizadas.

### 2c. Estado de anuncios (rechazos / errores) → `ad_status[]`
- Tool: **`ads_get_ad_entities`** con `level: ad`, campos
  `id→ad_id, name→ad_name, campaign_id, adset_id, effective_status,
  ad_review_feedback→review_feedback, created_time` (verificar nombres con `ads_get_field_context`).
- Tool: **`ads_get_errors`** para la cuenta → adjuntar en `delivery_issues` los errores de
  entrega por ad (error_summary/issues).
- `effective_status` en `DISAPPROVED`/`WITH_ISSUES` marca la cuenta en rojo en el dashboard.

### 2d. Intereses de targeting → `adset_interests[]`
- Tool: **`ads_get_ad_entities`** con `level: adset`, leer `targeting.flexible_spec[].interests[]`
  (y `targeting.interests[]` si aplica). Emitir `{adset_id, adset_name, interest_id, interest_name}`.

### 2e. Métricas por ANUNCIO → `ads_daily[]`  (para ver qué anuncio genera conversiones)
- Tool: **`ads_get_ad_entities`** con `level: ad`, `date_preset: yesterday`, e insights.
- Mismos campos que 2a **más** `id→ad_id, name→ad_name, adset_id`, incluyendo los **5 eventos del pixel**
  y `effective_status→status`. Cada anuncio activo = un renglón en `meta_ad_daily`.
- Se puede combinar con 2c (rechazos): el `effective_status`/`ad_review_feedback` marca el anuncio en rojo.

> Nota: los schemas exactos de cada tool MCP están diferidos; cargarlos en runtime con
> ToolSearch (`select:ads_get_ad_entities,ads_get_errors,ads_get_customconversions,...`)
> y ajustar nombres de campos si Meta cambió algo. Ante duda, `ads_get_field_context`.

## 3. Por cada subcuenta GHL (loop) — vía REST con el PIT
No es por MCP: usar el PIT del cliente contra la API de GHL (ver `lib/ghl.ts`).
- **Embudo del día** → `funnel_daily[]`: contar oportunidades que llegaron a cada stage
  del pipeline "01 Lead Machine" ayer (`{date, pipeline_id, stage_id, stage_name, opp_count}`).
- **Atribución** → `attribution[]`: por oportunidad, leer el contacto y su custom field de
  UTM (`ghl_utm_field`) → `campaign_id`. Marcar `is_appointment` (Agendó/Confirmó/Asistió),
  `is_sale` (Contrató), fechas y `monetary_value`. Mapear al `meta_ad_account_id` del cliente.
- El join es **dentro de la subcuenta** (UTM → campaña de esa misma cuenta Meta).

## 4. Tipos de cambio → `fx[]`
- Obtener rate_to_usd de MXN e INR (WebSearch/una API FX) para `as_of_date`. USD = 1.
- El loader calcula `spend_usd`/`cpl_usd`.

## 5. Cargar a Postgres
- Escribir el payload a `ingest/staging/<as_of_date>.json` y correr:
  `node scripts/load.mjs ingest/staging/<as_of_date>.json`
- El loader hace UPSERT idempotente (se puede re-correr el mismo día sin duplicar).

## 6. Resumen "ayer" (retroalimentación diaria)
Después de cargar, consultar y publicar (Google Chat / email) un resumen con:
- ⚠️ **Cuentas sin leads ayer** (de `v_account_health` where `leads_asof = 0 and active`) — prioridad urgente.
- 🔴 **Rechazos / errores de entrega** (where `has_rejection`).
- 🆕 **Cuentas sin anuncio nuevo esta semana** (where not `nuevo_anuncio_semana`).
- 📊 Totales: gasto, leads, CPL promedio, **citas** y **ventas** (de `attribution`) — por vista (clientes / NetUs).

## Shape del payload JSON (contrato con scripts/load.mjs)
```json
{
  "as_of_date": "2026-07-05",
  "kind": "daily",
  "fx": [{ "currency": "MXN", "rate_to_usd": 0.058 }, { "currency": "INR", "rate_to_usd": 0.012 }],
  "accounts": [
    {
      "meta_ad_account_id": "2206188576811692",
      "campaigns_daily": [
        { "campaign_id": "…", "campaign_name": "…", "objective": "OUTCOME_LEADS", "status": "ACTIVE",
          "date": "2026-07-05", "spend": 350.0, "impressions": 12000, "clicks": 230,
          "ctr": 1.92, "cpm": 29.16, "leads": 8, "cpl": 43.75, "currency": "MXN" }
      ],
      "pixel_events_daily": [
        { "campaign_id": "…", "date": "2026-07-05", "action_type": "offsite_conversion.fb_pixel_lead", "count": 8, "value": 0 }
      ],
      "ad_status": [
        { "ad_id": "…", "ad_name": "…", "campaign_id": "…", "adset_id": "…",
          "effective_status": "ACTIVE", "review_feedback": null, "delivery_issues": null,
          "created_time": "2026-07-01T10:00:00-06:00" }
      ],
      "adset_interests": [
        { "adset_id": "…", "adset_name": "…", "interest_id": "6003…", "interest_name": "Seguros" }
      ]
    }
  ],
  "ghl": [
    {
      "ghl_location_id": "n1Jw67thJsyVKq49ly4n",
      "funnel_daily": [
        { "date": "2026-07-05", "pipeline_id": "t5Bl9NQXcCogNMSgwT1d",
          "stage_id": "1c6d905a-7f19-42ad-9dd1-3d1e29c7538c", "stage_name": "Agendó cita", "opp_count": 3 }
      ],
      "attribution": [
        { "opportunity_id": "…", "contact_id": "…", "meta_ad_account_id": "2206188576811692",
          "campaign_id": "…", "utm_raw": "…", "current_stage": "Contrató",
          "is_appointment": true, "is_sale": true,
          "lead_date": "2026-07-01", "appointment_date": "2026-07-03", "sale_date": "2026-07-05",
          "monetary_value": 12000, "currency": "MXN" }
      ]
    }
  ]
}
```

## Manejo de errores
- Una cuenta/subcuenta que falle no aborta el resto (el loader hace commit por bloque).
- Reintentos de MCP con backoff; si tras 2 intentos falla, registrar en el resumen y seguir.
- `cockpit.sync_runs` guarda el resultado de cada corrida (`ok`/`error`, conteos).
