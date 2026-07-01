"""
Genera data/suppliers.json: red de distribuidores/showrooms Roca con coordenadas.

DATOS DE DEMO. Las coordenadas de ciudad son geografia publica, pero los nombres,
direcciones y telefonos de tienda son FICTICIOS (PoC de hackathon). Sustituir por el
maestro real de puntos de venta Roca antes de cualquier uso con clientes.

Uso: python build_suppliers.py   ->   data/suppliers.json
"""
import json, os

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# (ciudad, provincia, lat, lon, tipo)  — lat/lon = centro aproximado de la ciudad.
# tipo: "gallery" (showroom oficial Roca) | "distribuidor" (punto de venta asociado)
CITIES = [
    ("Barcelona", "Barcelona", 41.3874, 2.1686, "gallery"),
    ("Madrid", "Madrid", 40.4168, -3.7038, "gallery"),
    ("Valencia", "Valencia", 39.4699, -0.3763, "gallery"),
    ("Sevilla", "Sevilla", 37.3891, -5.9845, "gallery"),
    ("Bilbao", "Bizkaia", 43.2630, -2.9350, "gallery"),
    ("Málaga", "Málaga", 36.7213, -4.4214, "distribuidor"),
    ("Zaragoza", "Zaragoza", 41.6488, -0.8891, "distribuidor"),
    ("Palma", "Illes Balears", 39.5696, 2.6502, "distribuidor"),
    ("A Coruña", "A Coruña", 43.3623, -8.4115, "distribuidor"),
    ("Vigo", "Pontevedra", 42.2406, -8.7207, "distribuidor"),
    ("Gijón", "Asturias", 43.5322, -5.6611, "distribuidor"),
    ("Granada", "Granada", 37.1773, -3.5986, "distribuidor"),
    ("Murcia", "Murcia", 37.9922, -1.1307, "distribuidor"),
    ("Alicante", "Alicante", 38.3452, -0.4810, "distribuidor"),
    ("Córdoba", "Córdoba", 37.8882, -4.7794, "distribuidor"),
    ("Valladolid", "Valladolid", 41.6523, -4.7245, "distribuidor"),
    ("San Sebastián", "Gipuzkoa", 43.3183, -1.9812, "distribuidor"),
    ("Pamplona", "Navarra", 42.8125, -1.6458, "distribuidor"),
    ("Santander", "Cantabria", 43.4623, -3.8099, "distribuidor"),
    ("Salamanca", "Salamanca", 40.9701, -5.6635, "distribuidor"),
    ("León", "León", 42.5987, -5.5671, "distribuidor"),
    ("Burgos", "Burgos", 42.3439, -3.6969, "distribuidor"),
    ("Toledo", "Toledo", 39.8628, -4.0273, "distribuidor"),
    ("Almería", "Almería", 36.8340, -2.4637, "distribuidor"),
    ("Cádiz", "Cádiz", 36.5271, -6.2886, "distribuidor"),
    ("Logroño", "La Rioja", 42.4627, -2.4449, "distribuidor"),
    ("Castellón", "Castellón", 39.9864, -0.0513, "distribuidor"),
    # Área metropolitana de Barcelona (densidad alta: RocaSalvatella/Roca son de aquí)
    ("L'Hospitalet de Llobregat", "Barcelona", 41.3596, 2.0999, "distribuidor"),
    ("Badalona", "Barcelona", 41.4500, 2.2474, "distribuidor"),
    ("Sabadell", "Barcelona", 41.5463, 2.1086, "distribuidor"),
    ("Terrassa", "Barcelona", 41.5615, 2.0084, "distribuidor"),
    ("Mataró", "Barcelona", 41.5388, 2.4449, "distribuidor"),
    ("Girona", "Girona", 41.9794, 2.8214, "distribuidor"),
    ("Tarragona", "Tarragona", 41.1189, 1.2445, "distribuidor"),
    ("Reus", "Tarragona", 41.1550, 1.1075, "distribuidor"),
    ("Lleida", "Lleida", 41.6176, 0.6200, "distribuidor"),
    ("Manresa", "Barcelona", 41.7230, 1.8265, "distribuidor"),
    # Área metropolitana de Madrid
    ("Getafe", "Madrid", 40.3082, -3.7326, "distribuidor"),
    ("Alcalá de Henares", "Madrid", 40.4818, -3.3644, "distribuidor"),
    ("Móstoles", "Madrid", 40.3223, -3.8649, "distribuidor"),
]

# nombres ficticios deterministas por ciudad (no son razones sociales reales)
STREETS = ["Av. Diagonal", "C/ Mayor", "Av. de la Constitución", "Ronda Litoral",
           "C/ Industria", "Pol. Ind. Les Comes", "Av. del Puerto", "C/ del Comercio",
           "Ctra. Nacional II, km 12", "C/ San Juan"]


def build():
    suppliers = []
    for i, (city, prov, lat, lon, kind) in enumerate(CITIES):
        if kind == "gallery":
            name = f"Roca {city} Gallery"
            phone_area = 800 + i
        else:
            name = f"Saneamientos {city}"
            phone_area = 900 + i
        street = STREETS[i % len(STREETS)]
        num = 10 + (i * 7) % 180
        suppliers.append({
            "id": f"sup-{i:03d}",
            "name": name,
            "type": kind,
            "address": f"{street}, {num}",
            "city": city,
            "province": prov,
            "postal_code": f"{(8000 + i * 137) % 52000:05d}",
            "phone": f"+34 {phone_area % 1000:03d} {100 + i:03d} {200 + i:03d}",
            "lat": round(lat, 5),
            "lon": round(lon, 5),
            # showrooms oficiales exponen todo el catálogo; los distribuidores, bajo pedido
            "official": kind == "gallery",
        })

    out = os.path.join(DATA, "suppliers.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(suppliers, f, ensure_ascii=False, indent=1)
    print(f"escritos {len(suppliers)} distribuidores -> {out}")


if __name__ == "__main__":
    build()
