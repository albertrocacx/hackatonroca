"""Pruebas de búsqueda por imagen: fusión multi-foto, /search/image y cruce con texto."""
import pytest

import image_search


def test_fuse_same_media_sobre_total_de_fotos():
    fused = image_search.fuse_same([{"A": 0.8, "B": 0.6}, {"A": 0.7}])
    assert fused["A"] == pytest.approx(0.75)   # (0.8 + 0.7) / 2
    assert fused["B"] == pytest.approx(0.30)   # (0.6 + 0.0) / 2 — ausente cuenta 0
    assert fused["A"] > fused["B"]


def test_fuse_same_una_foto_es_identidad():
    fused = image_search.fuse_same([{"A": 0.9, "B": 0.5}])
    assert fused == {"A": 0.9, "B": 0.5}


def test_query_images_reintenta_fallos_de_transporte(monkeypatch):
    calls = {"n": 0}

    def flaky(b, top_k=50):
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("transporte")
        return {"A": 0.9}

    monkeypatch.setattr(image_search, "query_image", flaky)
    monkeypatch.setattr(image_search.time, "sleep", lambda s: None)
    assert image_search.query_images([b"x"]) == [{"A": 0.9}]
    assert calls["n"] == 2


def test_query_images_no_reintenta_endpoint_error(monkeypatch):
    def bad(b, top_k=50):
        raise image_search.EndpointError("foto ilegible")

    monkeypatch.setattr(image_search, "query_image", bad)
    with pytest.raises(image_search.EndpointError):
        image_search.query_images([b"x"])


# ---------------------------------------------------------------- /search/image
import io

import requests as requests_lib
from fastapi.testclient import TestClient

import main

client = TestClient(main.app)


def _two_models():
    """Dos (modelo, sku, producto) de modelos distintos del catálogo real."""
    out, seen = [], set()
    for p in main.PRODUCTS:
        m = p.get("model")
        if m and m not in seen:
            seen.add(m)
            out.append((m, p["sku"], p))
        if len(out) == 2:
            return out
    raise RuntimeError("catálogo sin 2 modelos")


def _post(monkeypatch, rankings, n_photos=1, q="", mode="same"):
    monkeypatch.setattr(image_search, "ready", lambda: True)
    monkeypatch.setattr(image_search, "query_images", lambda blobs, top_k=50: rankings)
    files = [("images", (f"f{i}.jpg", io.BytesIO(b"xx"), "image/jpeg"))
             for i in range(n_photos)]
    return client.post("/search/image", files=files, data={"q": q, "mode": mode})


def test_same_ordena_por_score_y_default_es_mejor_sku(monkeypatch):
    (m1, sku1, _), (m2, sku2, _) = _two_models()
    r = _post(monkeypatch, [{sku1: 0.9, sku2: 0.5}, {sku1: 0.8, sku2: 0.7}], n_photos=2)
    assert r.status_code == 200
    body = r.json()
    assert body["facets"] is None
    models = [c["model"] for c in body["results"]]
    assert models[:2] == [m1, m2]                      # 0.85 > 0.6
    card = body["results"][0]
    assert card["variants"][card["default"]]["sku"] == sku1


def test_distinct_un_ranking_por_foto(monkeypatch):
    (m1, sku1, _), (m2, sku2, _) = _two_models()
    r = _post(monkeypatch, [{sku1: 0.9}, {sku2: 0.8}], n_photos=2, mode="distinct")
    assert r.status_code == 200
    groups = r.json()["groups"]
    assert [g["photo"] for g in groups] == [1, 2]
    assert groups[0]["results"][0]["model"] == m1
    assert groups[1]["results"][0]["model"] == m2


def _pair_con_token_distintivo():
    """(sku1, sku2, token): token aparece en el blob de sku2 y NO en el de sku1."""
    pairs = _two_models()
    for _, sku2, p2 in [pairs[1], pairs[0]]:
        other = pairs[0] if sku2 == pairs[1][1] else pairs[1]
        blob_other = main.SEARCH_INDEX[other[1]]
        for tok in (p2.get("title") or "").lower().split():
            if len(tok) >= 4 and tok not in blob_other:
                return other[1], sku2, tok
    pytest.skip("no hay token distintivo entre los dos primeros modelos")


def test_texto_filtra_candidatos_sin_reordenar(monkeypatch):
    sku1, sku2, token = _pair_con_token_distintivo()
    m2 = main.BY_SKU[sku2]["model"]
    r = _post(monkeypatch, [{sku1: 0.9, sku2: 0.5}], q=token)
    models = [c["model"] for c in r.json()["results"]]
    assert m2 in models
    assert main.BY_SKU[sku1]["model"] not in models


def test_texto_sin_matches_devuelve_cero(monkeypatch):
    (_, sku1, _), _ = _two_models()
    r = _post(monkeypatch, [{sku1: 0.9}], q="zzzznoexiste")
    assert r.status_code == 200
    assert r.json()["total"] == 0


def test_sin_api_key_503(monkeypatch):
    monkeypatch.setattr(image_search, "ready", lambda: False)
    files = [("images", ("f.jpg", io.BytesIO(b"xx"), "image/jpeg"))]
    assert client.post("/search/image", files=files).status_code == 503


def test_endpoint_caido_502(monkeypatch):
    monkeypatch.setattr(image_search, "ready", lambda: True)

    def boom(blobs, top_k=50):
        raise requests_lib.ConnectionError("down")

    monkeypatch.setattr(image_search, "query_images", boom)
    files = [("images", ("f.jpg", io.BytesIO(b"xx"), "image/jpeg"))]
    assert client.post("/search/image", files=files).status_code == 502


def test_demasiadas_fotos_400(monkeypatch):
    monkeypatch.setattr(image_search, "ready", lambda: True)
    files = [("images", (f"f{i}.jpg", io.BytesIO(b"xx"), "image/jpeg")) for i in range(7)]
    assert client.post("/search/image", files=files).status_code == 400


def test_health_expone_image_ready():
    r = client.get("/health")
    assert "image_ready" in r.json()
