# Buscador Inteligente Roca — PoC

Monorepo con dos partes independientes:

```
roca-buscador/
├── backend/     API FastAPI (Python)  -> se despliega en Railway
│   └── data/    products.json, relations.json  (generados)
├── frontend/    React + Vite (TypeScript) -> se despliega en Vercel
└── tools/       build_data.py  (genera los JSON desde el Excel + CSV)
```

## Datos
Fuentes (no se suben al repo): `ROCA_productos_definitivo.xlsx` (15.408 productos, precio RRP)
y `relations.csv` (54.945 relaciones: compatible / optional / included / sparepart).
Para regenerar los JSON:
```bash
python tools/build_data.py
```

## Arranque local
```bash
# Backend
cd backend && python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt && uvicorn main:app --reload --port 8000

# Frontend (otra terminal)
cd frontend && npm install && npm run dev
```
Frontend en http://localhost:5173 · Backend en http://localhost:8000

## Despliegue
- **Backend → Railway**: New Project → Deploy from repo → Root Directory `backend`.
- **Frontend → Vercel**: Import Project → Root Directory `frontend` → variable `VITE_API_URL` = URL de Railway.

## Roadmap (crecer)
- Interpretacion de lenguaje natural con Claude en `/search`.
- Embeddings en memoria para busqueda semantica.
- Filtros por facetas (categoria, acabado, precio) e imagenes de producto.
