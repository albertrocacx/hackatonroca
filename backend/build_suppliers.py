"""
Genera data/suppliers.json a partir del export oficial de puntos de venta Roca
(Roca_ES_POS_*.xlsx, hoja LOCATION).

Datos REALES de la red de distribuidores Roca en España (861 POS geolocalizados).
Se excluyen a propósito los campos sensibles del export (CIF y emails de contacto
internos): se conserva solo lo que muestra el localizador público de tiendas
(nombre, dirección, teléfono, web, coordenadas, si tiene exposición).

Uso: python build_suppliers.py [ruta_al_xlsx]   ->   data/suppliers.json
"""
import glob, json, os, sys
import openpyxl

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
REPO = os.path.dirname(HERE)   # .../hackatonroca

# provincia por prefijo de código postal (2 primeros dígitos)
CP_PROV = {
    "01": "Álava", "02": "Albacete", "03": "Alicante", "04": "Almería", "05": "Ávila",
    "06": "Badajoz", "07": "Illes Balears", "08": "Barcelona", "09": "Burgos",
    "10": "Cáceres", "11": "Cádiz", "12": "Castellón", "13": "Ciudad Real", "14": "Córdoba",
    "15": "A Coruña", "16": "Cuenca", "17": "Girona", "18": "Granada", "19": "Guadalajara",
    "20": "Gipuzkoa", "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León",
    "25": "Lleida", "26": "La Rioja", "27": "Lugo", "28": "Madrid", "29": "Málaga",
    "30": "Murcia", "31": "Navarra", "32": "Ourense", "33": "Asturias", "34": "Palencia",
    "35": "Las Palmas", "36": "Pontevedra", "37": "Salamanca", "38": "Santa Cruz de Tenerife",
    "39": "Cantabria", "40": "Segovia", "41": "Sevilla", "42": "Soria", "43": "Tarragona",
    "44": "Teruel", "45": "Toledo", "46": "Valencia", "47": "Valladolid", "48": "Bizkaia",
    "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}

CATEGORY_LABEL = {"WITH_EXPOSITION": "Con exposición", "WITHOUT_EXPOSITION": "Sin exposición"}

# índices de columna en la hoja LOCATION (ver cabecera del export)
C_ID, C_AREA, C_CAT = 1, 2, 4
C_ZIP, C_CODE, C_COMPANY = 9, 10, 12
NAME_PAIRS = [(13, 14), (15, 16), (17, 18), (19, 20), (21, 22)]   # (NAME, LOCALE)
ADDR_PAIRS = [(27, 28), (29, 30)]                                  # (ADDRESS, LOCALE)
C_LNG, C_LAT, C_PHONE, C_WEB = 7, 8, 35, 43


def _es(row, pairs):
    """Valor con LOCALE es_ES entre pares (valor, locale); si no, el primero no vacío."""
    first = None
    for vi, li in pairs:
        v = row[vi]
        if v in (None, ""):
            continue
        if row[li] == "es_ES":
            return str(v).strip()
        if first is None:
            first = str(v).strip()
    return first


def build(xlsx):
    wb = openpyxl.load_workbook(xlsx, read_only=True, data_only=True)
    ws = wb["LOCATION"]
    out = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row[C_ID]:
            continue
        lat, lon = row[C_LAT], row[C_LNG]
        if lat in (None, "") or lon in (None, ""):
            continue
        name = (str(row[C_COMPANY]).strip() if row[C_COMPANY] else None) or _es(row, NAME_PAIRS)
        if not name:
            continue
        zip_ = str(row[C_ZIP]).strip() if row[C_ZIP] else ""
        cat = row[C_CAT]
        web = str(row[C_WEB]).strip() if row[C_WEB] else None
        if web and not web.startswith(("http://", "https://")):
            web = "https://" + web
        out.append({
            "id": str(row[C_ID]),
            "name": name,
            "address": _es(row, ADDR_PAIRS),
            "city": str(row[C_AREA]).strip() if row[C_AREA] else None,
            "province": CP_PROV.get(zip_[:2]),
            "postal_code": zip_,
            "phone": str(row[C_PHONE]).strip() if row[C_PHONE] else None,
            "web": web,
            "lat": round(float(lat), 6),
            "lon": round(float(lon), 6),
            "exposition": cat == "WITH_EXPOSITION",
            "category": CATEGORY_LABEL.get(cat),
        })

    dst = os.path.join(DATA, "suppliers.json")
    with open(dst, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    exp = sum(1 for s in out if s["exposition"])
    print(f"escritos {len(out)} puntos de venta ({exp} con exposición) -> {dst}")


if __name__ == "__main__":
    if len(sys.argv) > 1:
        xlsx = sys.argv[1]
    else:
        hits = glob.glob(os.path.join(REPO, "Roca_ES_POS_*.xlsx"))
        if not hits:
            sys.exit("No encuentro Roca_ES_POS_*.xlsx en la raíz del repo; pásalo como argumento.")
        xlsx = max(hits)   # el más reciente por nombre (lleva fecha)
    build(xlsx)
