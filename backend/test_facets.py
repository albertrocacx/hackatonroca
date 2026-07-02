"""Pruebas de /search: facetas dinámicas + agrupación por modelo (tarjetas-modelo)."""
import main

Q = "lavabo"
UNIK = "851545..."   # modelo con varias variantes de acabado


def test_response_incluye_facets_y_cards():
    r = main.search(q=Q)
    assert set(r["facets"]) == {"category", "collection", "finish", "color", "price", "dims"}
    assert r["total"] > 0
    c = r["results"][0]
    assert "model" in c and isinstance(c["variants"], list) and c["variants"]
    assert 0 <= c["default"] < len(c["variants"])


def test_total_cuenta_modelos():
    r = main.search(q=Q, limit=5000)
    models = {c["model"] for c in r["results"]}
    assert r["total"] == len(models)          # una tarjeta por modelo


def test_agrupacion_unik():
    r = main.search(q="Unik", limit=5000)
    cards = [c for c in r["results"] if c["model"] == UNIK]
    assert len(cards) == 1                     # las 4 variantes en UNA tarjeta
    skus = {v["sku"] for v in cards[0]["variants"]}
    assert {"A851545402", "A851545434"} <= skus


def test_contador_categoria_coincide_con_total():
    r = main.search(q=Q)
    top = r["facets"]["category"][0]
    r2 = main.search(q=Q, category=[top["value"]])
    assert r2["total"] == top["count"]         # total (modelos) == contador (modelos)


def test_leave_one_out_mantiene_opciones():
    r = main.search(q=Q)
    cats = [f["value"] for f in r["facets"]["category"]]
    assert len(cats) >= 2
    r2 = main.search(q=Q, category=[cats[0]])
    assert cats[1] in [f["value"] for f in r2["facets"]["category"]]


def test_default_respeta_filtro_color():
    r = main.search(q="Unik", limit=5000)
    card = next(c for c in r["results"] if c["model"] == UNIK)
    color = card["variants"][0]["finish"]
    r2 = main.search(q="Unik", finish=[color], limit=5000)
    card2 = next(c for c in r2["results"] if c["model"] == UNIK)
    assert card2["variants"][card2["default"]]["finish"] == color
    assert len(card2["variants"]) >= 1         # sigue listando acabados (Q2·A)


def test_rango_precio_excluye_nulos():
    r = main.search(q=Q, max_price=100, limit=5000)
    for c in r["results"]:
        for v in c["variants"]:
            assert v["price_rrp"] is None or v["price_rrp"] <= 100


def test_detail_incluye_variants():
    d = main.product_detail("A851545402")
    assert "variants" in d and any(v["sku"] == "A851545434" for v in d["variants"])
