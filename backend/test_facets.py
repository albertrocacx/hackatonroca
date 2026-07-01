"""Pruebas de las facetas dinamicas de /search (llaman a search() directamente)."""
import main

Q = "lavabo"


def test_response_incluye_facets():
    r = main.search(q=Q)
    assert set(r["facets"]) == {"category", "collection", "finish", "price", "dims"}
    assert set(r["facets"]["dims"]) == {"length", "width", "height"}
    assert r["total"] > 0


def test_contadores_coinciden_con_filtrar_a_mano():
    r = main.search(q=Q)
    top = r["facets"]["category"][0]
    r2 = main.search(q=Q, category=[top["value"]])
    # el total al filtrar por esa categoria == su contador en la faceta
    assert r2["total"] == top["count"]


def test_leave_one_out_mantiene_las_demas_opciones():
    r = main.search(q=Q)
    cats = [f["value"] for f in r["facets"]["category"]]
    assert len(cats) >= 2
    # al fijar una categoria, la faceta categoria sigue mostrando las demas
    r2 = main.search(q=Q, category=[cats[0]])
    cats2 = [f["value"] for f in r2["facets"]["category"]]
    assert cats[1] in cats2


def test_or_dentro_de_faceta():
    r = main.search(q=Q)
    a, b = r["facets"]["category"][0]["value"], r["facets"]["category"][1]["value"]
    ra = main.search(q=Q, category=[a])["total"]
    rb = main.search(q=Q, category=[b])["total"]
    rab = main.search(q=Q, category=[a, b])["total"]
    assert rab == ra + rb        # categorias disjuntas -> union = suma


def test_and_entre_facetas():
    r = main.search(q=Q)
    cat = r["facets"]["category"][0]["value"]
    solo_cat = main.search(q=Q, category=[cat])["total"]
    fin = main.search(q=Q, category=[cat])["facets"]["finish"]
    if fin:
        con_color = main.search(q=Q, category=[cat], finish=[fin[0]["value"]])["total"]
        assert con_color <= solo_cat      # anadir otra faceta solo puede reducir


def test_rango_precio_filtra_y_excluye_nulos():
    r = main.search(q=Q, max_price=100)
    for it in r["results"]:
        assert it["price_rrp"] is not None and it["price_rrp"] <= 100


def test_bounds_son_numericos():
    dims = main.search(q=Q)["facets"]["dims"]["length"]
    assert dims is None or (isinstance(dims["min"], (int, float)) and dims["min"] <= dims["max"])
