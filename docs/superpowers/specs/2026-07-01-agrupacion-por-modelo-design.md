# Agrupación del grid por modelo (variantes de acabado) — Diseño

**Fecha:** 2026-07-01
**Estado:** aprobado (pendiente de implementación)

## Objetivo

Agrupar el grid de resultados **por modelo**: las variantes de acabado del mismo
producto (mismo `model`, distinto SKU) se muestran como **una sola tarjeta** con
**thumbnails de acabados** para navegar entre ellas sin salir del grid. La ficha del
producto muestra esos mismos acabados como swatches.

Ejemplo (captura del usuario): el modelo `851545...` "Unik (mueble base…)" tiene 4
acabados (Roble City, Fresno nórdico, Gris ártico, Blanco brillo) → hoy son 4
tarjetas; pasa a ser 1 tarjeta con 4 thumbnails.

Datos que lo soportan: `images.json` está keyed por **SKU** (cada variante tiene su
foto) y las variantes comparten el campo `model`. 9.501 modelos, 1.754 con >1 variante.

## Decisiones (acordadas)

- **Contar modelos en todo**: el total "(N)" y los contadores de facetas cuentan
  modelos/tarjetas, no SKUs.
- **Faceta Colores + agrupación**: al filtrar por un color, aparecen los modelos que
  tienen ese color; la tarjeta se muestra por defecto en ese color, pero los
  thumbnails siguen mostrando **todos** los acabados del modelo.
- **Agrupación en el backend** (única opción coherente con contar modelos y paginar).

## Backend (`backend/main.py`)

### Agrupación en `/search`
Tras filtrar los SKUs (igual que ahora), se agrupan por `model`. Cada resultado es una
**tarjeta-modelo**:
```jsonc
{
  "model": "851545...",
  "title": "Unik (mueble base de dos cajones y lavabo)",
  "collection": "Domi",
  "category": "Muebles de baño",
  "variants": [
    {"sku":"A851545402","finish":"Roble City texturizado","image":"…","price_rrp":492.0,"dims":"600 x 460 x 578"},
    {"sku":"A851545434","finish":"Fresno nórdico","image":"…","price_rrp":492.0,"dims":"600 x 460 x 578"}
  ],
  "default": 0
}
```

**Reglas:**
- La **tarjeta aparece** si ≥1 variante pasa *todos* los filtros (incluido color).
- Los **`variants`** (thumbnails) = variantes del modelo que pasan los filtros
  *excepto* el de color (reusa el leave-one-out de color). Así se ven todos los
  acabados aunque se filtre por uno.
- **`default`** = índice de la primera variante que además cumple el filtro de color;
  si no hay filtro de color, la primera del grupo.
- **Orden de tarjetas**: por el mejor score de texto entre sus variantes (mantiene
  relevancia). Las variantes dentro de la tarjeta, en orden de catálogo.
- **`total`** = nº de modelos distintos que casan. `limit` pagina **modelos**.

### Contadores de facetas por modelo
El cálculo leave-one-out se mantiene, pero se cuentan **modelos distintos** en vez de
sumar SKUs:
- Categórico (categoría/colección/color): por cada valor, `set()` de `model`; el
  contador es `len(set)`. Un modelo cuenta en cada valor que tenga alguna variante
  (p. ej. un modelo con 4 acabados cuenta en 4 colores).
- Rangos (precio/dims): los topes min/máx se calculan igual (sobre valores). El grid
  incluye un modelo si alguna variante cae en el rango.

### `/products/{sku}`
Añade `variants` del modelo (reusando `BY_MODEL`) para que la ficha muestre los
swatches sin acoplar lógica en el frontend:
```jsonc
{ …campos actuales…, "variants": [ {sku, finish, image, price_rrp, dims} ] }
```

## Frontend (`frontend/src/`)

### Componente `ProductCard.tsx` (aislado)
- **Props**: `card` (tarjeta-modelo) + `onOpen(sku)`.
- Estado local: índice de **variante seleccionada** (inicia en `card.default`).
- Pinta la variante seleccionada (imagen vía `<Tile>`, colección, título, `Ref:`,
  dimensiones, acabado, PVPR) + una **fila de thumbnails** (mini-imagen por variante),
  con el activo resaltado.
- Pulsar un thumbnail → cambia la variante mostrada **en la misma tarjeta**.
- Pulsar la foto/título → `onOpen(sku de la variante seleccionada)`.
- Si el modelo tiene 1 variante → sin fila de thumbnails.

### `App.tsx`
- `results` pasa a `ModelCard[]`; el grid mapea `<ProductCard key={model} card=… onOpen={openProduct} />`.
- No cambia el resto (facetas, autocompletado, chat IA).

### Ficha (overlay)
- Debajo de los datos, una fila de **swatches** desde `detail.variants`; pulsar uno →
  `openProduct(sku)` recarga la ficha en ese acabado; swatch activo resaltado.

### `api.ts`
- Nuevos tipos `Variant` y `ModelCard`; `SearchResponse.results: ModelCard[]`;
  `ProductDetail` añade `variants`.
- Interfaces de facetas/sugerencias sin cambios.

## Pruebas

**Backend (pytest, llamando a `search()`/`product_detail()`):**
- Los resultados son tarjetas-modelo con `model`, `variants` y `default` válido.
- El modelo `851545...` devuelve una tarjeta con sus variantes.
- `total` = nº de modelos distintos (≤ nº de SKUs).
- Contador de una categoría = nº de modelos distintos (≤ el equivalente en SKUs).
- Con `finish=X`: `default` apunta a una variante de acabado X; si el modelo tiene más
  acabados, la tarjeta sigue listándolos todos.
- `product_detail` incluye `variants`.

**Frontend:** `npm run build` + prueba manual: buscar "Unik" → una tarjeta con
thumbnails; pulsar un thumbnail cambia imagen/ref/precio; pulsar abre la ficha; en la
ficha, los swatches cambian el acabado.

## Casos límite

- **Modelo con 1 variante** → sin fila de thumbnails (igual que hoy).
- **Filtro de color activo** → tarjeta por defecto en ese color; thumbnails con todos.
- **Variantes con precio distinto** → se muestra el de la variante seleccionada.
- **Variante sin imagen** → placeholder (lo gestiona `Tile`).

## Fuera de alcance

- Preferir un acabado "canónico" (p. ej. Blanco) como `default` — de momento la
  primera variante.
- Animaciones/transiciones al cambiar de acabado.
