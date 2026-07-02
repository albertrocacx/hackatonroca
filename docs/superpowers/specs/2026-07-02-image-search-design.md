# Búsqueda por imagen en el buscador — Diseño

**Fecha:** 2026-07-02 · **Rama:** `img_search`

## Objetivo

Añadir a la barra del buscador la opción de subir una o varias fotos (drag & drop
estilo Google) para identificar productos del catálogo. El backend usa el endpoint
DINOv2 ya deployado en Azure ML (ver `tools/test_dino_embedder.py`), que dado una
imagen devuelve SKUs con similitud coseno. Con varias fotos del mismo objeto la
identificación es más precisa que con una. La búsqueda de texto actual no cambia.

## Decisiones tomadas

| Decisión | Elección |
|---|---|
| Multi-foto | Ambos casos: toggle en la UI — "mismo producto" (fusiona rankings) o "productos distintos" (un ranking por foto) |
| Presentación | Parrilla normal de ProductCards, ordenada por score visual, **sin** mostrar el % |
| Texto + imagen | Combinables: el texto **filtra** los matches visuales, el score visual **ordena** |
| Alcance del índice DINO | Todo el catálogo |
| Arquitectura | Proxy en el backend (`POST /search/image`); la API key nunca llega al navegador |

## UX del buscador (frontend)

**Entrada:**
- Icono de cámara dentro de la caja de búsqueda (junto al botón de borrar).
- Click en la cámara → panel dropzone anclado bajo la barra (mismo patrón visual
  que el panel de sugerencias): "arrastra tus fotos aquí o haz click para elegir"
  (`input file multiple`, accept `image/*`).
- Arrastrar fotos directamente sobre la barra también funciona: drag-over resalta
  la barra, drop añade las fotos y abre el panel.

**Panel:**
- Miniaturas de las fotos añadidas, cada una con botón × para quitarla.
- Con 2+ fotos: toggle "Las fotos son del mismo producto" (ON por defecto)
  → `mode=same` | `mode=distinct`.
- Botón "Buscar por imagen".
- Límite: máx. 6 fotos.

**Preparación en cliente:**
- Cada foto se reescala con canvas a máx. 1024px de lado, JPEG calidad ~0.85
  (~1.5MB → ~150KB) antes de subir.

**Resultados:**
- Las fotos quedan como chips-miniatura dentro de la barra, coexistiendo con el
  texto libre. Editar el texto y re-buscar refina los matches visuales.
- `mode=same`: la parrilla actual muestra las ModelCards ordenadas por score
  fusionado (sin badge de %).
- `mode=distinct`: una sección de parrilla por foto, con la miniatura de la foto
  como encabezado de sección.
- El sidebar de facetas se oculta en modo imagen (pocos resultados; el
  refinamiento es por texto).
- Quitar todas las fotos → la barra vuelve al comportamiento actual solo-texto.
- Si `/health` devuelve `image_ready: false`, el icono de cámara no se muestra.

## Backend: `POST /search/image`

**Request** (multipart/form-data):
- `images`: 1–6 ficheros (jpeg/png/webp)
- `q`: texto opcional de refinamiento
- `mode`: `"same"` (default) | `"distinct"`

**Flujo:**
1. Por cada foto, llamada al endpoint DINO (`{"image_b64", "top_k": 50}`,
   `Authorization: Bearer <IMAGE_SEARCH_API_KEY>`). Llamadas en paralelo.
   `top_k=50` da material suficiente para el filtrado por texto posterior.
2. Fusión de rankings (modo `same`; ver abajo).
3. Mapeo SKU → producto → modelo con los índices en memoria (`BY_SKU`,
   `BY_MODEL`), construyendo `ModelCard`s con el código existente. `default`
   apunta a la variante (SKU) con mejor score del modelo.
4. Si hay `q`: filtrado de texto sobre los candidatos. El orden sigue siendo el
   score visual.

**Response:**
- `mode=same` → mismo shape que `/search`: `{query, total, results: ModelCard[],
  facets: null}` (el frontend reutiliza la parrilla sin adaptación).
- `mode=distinct` → `{groups: [{photo: n, total, results: ModelCard[]}]}`. Si hay
  `q`, el filtro de texto se aplica igual a los candidatos de cada grupo.

**Config (backend/.env, opcional como el resto):**
- `IMAGE_SEARCH_API_KEY` — clave del endpoint Azure ML.
- `IMAGE_SEARCH_SCORING_URI` — default
  `https://dino-embedder-roca.spaincentral.inference.ml.azure.com/score`.
- Sin clave → `/health` expone `image_ready: false` y el frontend oculta la
  cámara. La app nunca se rompe.

## Fusión multi-foto y cruce con texto

**Fusión (`mode=same`):**
- Score por SKU = media de sus scores de similitud sobre el **total** de fotos,
  contando 0 cuando el SKU no aparece en el top-50 de una foto. Premia SKUs
  consistentes entre ángulos y hunde matches espurios de una sola foto.
- Score por modelo = máximo score entre sus SKUs (las variantes de acabado
  comparten geometría).

**Cruce con texto (si `q` no vacío):**
- `parse_query()` existente extrae filtros estructurados (acabado, banda de
  precio) → se aplican sobre los candidatos.
- Los tokens restantes se comprueban por substring contra `SEARCH_INDEX`; los
  candidatos que no matchean se descartan.
- No se re-puntúa: texto filtra, score visual ordena. Si el filtro deja 0
  resultados → `total: 0` y el mensaje de "sin resultados" habitual.

## Errores

| Caso | Respuesta |
|---|---|
| DINO caído / timeout (60s) | `502` con mensaje legible → estado de error existente de la parrilla |
| Foto corrupta o formato no soportado | `400` indicando qué foto |
| Falta API key | `503` "búsqueda por imagen no configurada" (la cámara ya estaría oculta) |
| Más de 6 fotos / >10MB por foto | `400` |

## Testing

- **Unit (backend):** fusión con respuestas DINO mockeadas — 1 foto; varias fotos
  con SKU consistente vs espurio; cruce con texto que filtra; filtro que deja 0.
- **Smoke real:** script contra el endpoint deployado con 2–3 fotos de
  `C:\Users\parand01\Desktop\IA\hackaton_dino\imgtest`, verificando que devuelve
  SKUs conocidos del catálogo.
- **Manual (frontend):** drag&drop, quitar fotos, toggle de modo, texto+foto,
  arranque sin API key (cámara oculta).

## Fuera de alcance

- Facetas sobre resultados de imagen.
- Mostrar el % de confianza en la UI.
- Búsqueda por URL de imagen o cámara en vivo.
- Re-ranking híbrido texto+imagen (el texto solo filtra).
