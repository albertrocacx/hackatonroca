# Facetas dinámicas del buscador — Diseño

**Fecha:** 2026-07-01
**Estado:** aprobado (pendiente de plan de implementación)

## Objetivo

Añadir una **barra lateral de facetas dinámica** al buscador. A partir del conjunto
de SKUs que devuelve una búsqueda (sean 100, 1.000 o 5.000), el sistema genera
automáticamente los filtros que tienen sentido para *ese* subconjunto: categorías,
colecciones, colores, rango de precio y rangos de dimensiones — cada uno con su
contador. Al marcar filtros, la parrilla y los contadores se recalculan (drill-down).

Referencia visual: la barra lateral de `roca.es/search`.

## Alcance (v1)

**Incluye** 5 facetas, todas calculables con los datos actuales:

| Faceta | Campo del producto | Control |
|---|---|---|
| Categoría | `category_base` | checkboxes + contador |
| Colecciones | `collection` | checkboxes + contador + "Mostrar más" |
| Colores | `finish` | checkboxes + contador + "Mostrar más" |
| Precio | `price_rrp` | slider de rango |
| Dimensiones | `dims.length_mm/width_mm/height_mm` | 3 sliders de rango |

**Fuera de v1** (requieren datos que no tenemos): miniatura por colección,
círculo de color real en la faceta Colores, y la faceta "Novedad" (no hay flag de
producto nuevo). Se abordarán más adelante si se consiguen esos datos.

## Comportamiento (drill-down)

- **Dentro de una faceta = OR**: "Lavabos" + "Grifería" → lavabo *o* grifería.
- **Entre facetas = AND**: "Lavabos" + color "Negro" → lavabo *y* negro.
- **Contadores leave-one-out**: el contador de cada faceta se calcula aplicando
  todas las *demás* facetas seleccionadas menos la suya. Así, tras marcar una
  categoría, la faceta Categoría sigue mostrando las otras categorías disponibles,
  mientras Colección/Color/Precio ya reflejan solo lo que aplica.
- **Sliders** (precio, dimensiones): sus topes min/máx salen del ámbito, para poder
  volver a ensancharlos.

## Arquitectura y flujo de datos

```
Búsqueda (texto q + concepto del autocompletado) ─► define el SCOPE (todos los SKUs que casan)
        │
        ▼
  El sidebar muestra las facetas calculadas sobre ese SCOPE
        │
  El usuario marca facetas ─► /search se re-llama con q + concepto + facetas
        │
        ▼
  Respuesta { total, results, facets } ─► se repintan parrilla Y sidebar
```

- **Backend**: calcula `facets` y aplica los filtros. Única fuente de verdad,
  compartida por los dos equipos vía la API.
- **Frontend**: guarda la selección del sidebar, la manda y solo pinta.

**Ámbito fijo vs faceta:**
- Fijo (define el SCOPE): `q` (texto), `include_spare`, `subcategory`.
- Facetas (leave-one-out): categoría, colección, color, precio, dimensiones.
- El "concepto" elegido en el autocompletado simplemente pre-marca una faceta
  (p. ej. `category=["Lavabos"]`); no es un caso especial.

Escala: ≤5.000 SKUs × 5 facetas con leave-one-out ⇒ unos pocos miles de
operaciones en memoria (Python) por petición → milisegundos.

## Backend (`backend/main.py`)

### Parámetros de `/search`
- `category`: pasa de valor único a **lista** (`Query(None)`), OR interno.
- `collection`: pasa a **lista**, OR interno.
- `finish`: ya es lista (sin cambios).
- **Nuevos**: `min_length/max_length`, `min_width/max_width`, `min_height/max_height`.
- Se mantienen: `q`, `limit`, `include_spare`, `subcategory`, `min_price/max_price`.

### Cálculo (helper `matches`)
Un helper `matches(p, sel, exclude=None)` comprueba un producto contra la selección
de facetas `sel`, saltándose opcionalmente una faceta (para leave-one-out).

- **SCOPE** = productos que cumplen `q` (tokens de texto), `include_spare` y
  `subcategory`.
- **Parrilla** = `[p ∈ SCOPE if matches(p, sel)]`, ordenada por score de texto,
  recortada a `limit`.
- **Contador categórico** (categoría/colección/color): agregación `valor → nº` sobre
  `[p ∈ SCOPE if matches(p, sel, exclude=esa_faceta)]`. Orden por count desc.
- **Rangos** (precio/dims): min/máx sobre `[p ∈ SCOPE if matches(p, sel, exclude=ese_rango)]`.
- Campos multivalor (`A|B`) cuentan en **cada** segmento (coherente con `_field_has`).
- Categoría se agrega y filtra por `category_base` (mismo campo que ya usa el filtro).
- Productos con `price_rrp`/dimensión nula quedan fuera de esa agregación y de ese
  filtro de rango.

### Forma de la respuesta (se **añade** `facets`; `results`/`total` no cambian)
```jsonc
{
  "query": "lavabo",
  "total": 462,
  "results": [ /* summary[:limit], igual que ahora */ ],
  "facets": {
    "category":   [ {"value":"Lavabos","count":135}, ... ],
    "collection": [ {"value":"Ona","count":49}, ... ],
    "finish":     [ {"value":"Blanco","count":146}, ... ],
    "price": {"min":26, "max":1587},
    "dims":  { "length":{"min":80,"max":1980},
               "width":{"min":32,"max":600},
               "height":{"min":6,"max":1120} }
  }
}
```
Si el SCOPE está vacío: listas vacías y rangos `null`.

## Frontend (`frontend/src/`)

### Layout
`rs-main` pasa a dos columnas: `<Facets/>` (sidebar) + `rs-grid` (parrilla, sin
cambios). En móvil se apila. El pulido visual queda para más adelante.

### Componente `Facets.tsx` (aislado)
- **Props**: `facets` (del backend), `selected`, `onChange`. No conoce red ni
  búsqueda; solo recibe datos y emite cambios → testeable y sustituible sin tocar `App`.
- Secciones colapsables: Categoría (checkboxes+contador), Colecciones
  (checkboxes+contador+"Mostrar más"), Precio (slider rango), Dimensiones (3 sliders),
  Colores (checkboxes+contador+"Mostrar más").

### Estado en `App.tsx`
```ts
baseQuery = { text, concept }          // fija el SCOPE
selected  = { categories:[], collections:[], finishes:[],
              price:[min,max]|null, length:..|null, width:..|null, height:..|null }
```
- **Nueva búsqueda** (Enter o elegir sugerencia): fija `baseQuery` y **resetea**
  `selected`. Si el concepto es categoría/colección, pre-marca esa faceta.
- **Tocar una faceta**: actualiza `selected`, mantiene `baseQuery`, re-llama a `/search`.
- Un único `runSearch(baseQuery, selected)` que actualiza `results` + `facets`.
- Sliders **debounced** (~250 ms); checkboxes inmediatos.
- Un rango en `selected` empieza en `null` (= sin filtrar) hasta que el usuario mueve
  el slider; sus topes visuales salen de `facets.price` / `facets.dims`.
- Si el concepto elegido es de tipo `subcategory`, va como restricción fija
  (`subcategory`, parte del SCOPE), no como faceta del sidebar.

### `api.ts`
- `search()` envía los parámetros de faceta (categorías, colecciones, colores,
  min/max precio, min/max dimensiones).
- La respuesta añade el tipo `facets`. El autocompletado (`/suggest`, teclado, chips)
  se mantiene intacto.

## Pruebas

**Backend (pytest, sin servidor; se llama a `search()` directamente):**
- Los contadores de una faceta coinciden con filtrar a mano esa consulta.
- Leave-one-out: al seleccionar una categoría, las demás siguen apareciendo con su contador.
- OR dentro / AND entre: 2 colores → unión; añadir categoría → intersección.
- Rangos de precio y dimensiones filtran bien; los nulos quedan fuera.
- La respuesta incluye `facets` con la forma acordada.

**Frontend:** `npm run build` (type-check) + prueba manual: buscar "lavabo",
marcar/desmarcar facetas y confirmar que parrilla y contadores se actualizan, que
"Mostrar más" y los sliders funcionan.

## Casos límite

- **0 resultados** → `facets` vacío; el sidebar muestra "Sin filtros disponibles".
- **Rango con min = máx** → slider deshabilitado, sin romper.
- **Precio/medida nulos** → excluidos de agregación y de filtros de rango.
- **Nueva búsqueda** → se resetea la selección de facetas.
- **Multivalor** (`A|B`) → cuenta en cada segmento.

## Fuera de alcance / futuro

- Miniatura por colección, círculo de color real, faceta "Novedad".
- Persistir filtros en la URL.
- Paginación/scroll infinito de la parrilla (hoy se recorta a `limit`).
