# Backend — Buscador Roca (PoC)

API FastAPI. Carga `data/products.json` + `data/relations.json` en memoria.

## Ejecutar en local
```bash
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows;  source .venv/bin/activate en Mac/Linux
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```
Prueba: http://localhost:8000/health · http://localhost:8000/search?q=lavabo

## Endpoints
- `GET /health`
- `GET /search?q=<texto>&limit=30&include_spare=false`
- `GET /products/{sku}` → ficha + relaciones (compatible/optional/included/sparepart)
- `GET /recommend/{sku}?intent=complete_solution|alternatives|components`

## Regenerar datos
```bash
python ../tools/build_data.py
```

## Deploy (Railway)
- Root Directory: `backend`
- Start command (se detecta del Procfile): `uvicorn main:app --host 0.0.0.0 --port $PORT`
