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
