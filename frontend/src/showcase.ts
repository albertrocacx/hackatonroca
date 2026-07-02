/**
 * Escaparate de espera de "Diseña tu baño": selección fija de catálogo que se
 * muestra mientras se genera el render, después de los productos de la cesta
 * (con 1-2 productos en la cesta el carrusel se repetiría sin parar).
 *
 * 20 productos de ~10 áreas, curados a mano (títulos limpios, con foto y PVPR).
 * Generado consultando /search y /products/{sku} del backend el 2026-07-02.
 */

export interface ShowcaseProduct {
  sku: string;
  title: string;
  category: string | null;
  collection: string | null;
  finish: string | null;
  price_rrp: number;
  image: string;
}

export const SHOWCASE_PRODUCTS: ShowcaseProduct[] = [
  {
    "sku": "A357475000",
    "title": "Bidé The Gap Square suspendido",
    "category": "Bidés",
    "collection": "The Gap|Senso Square DE",
    "finish": "Blanco",
    "price_rrp": 160,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1633220001/Product%20Pictures/THE_GAP_A357475000.jpg"
  },
  {
    "sku": "A327C10000",
    "title": "Lavabo Optica de sobre encimera",
    "category": "Lavabos",
    "collection": "Optica",
    "finish": "Blanco",
    "price_rrp": 76.4,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1633226211/Product%20Pictures/A327C10000.jpg"
  },
  {
    "sku": "A346244000",
    "title": "Inodoro Meridian suspendido compacto",
    "category": "Inodoros",
    "collection": "Meridian",
    "finish": "Blanco",
    "price_rrp": 263,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1707729588/Product%20Pictures/004_12019_00_MERIDIAN_A346244000.jpg"
  },
  {
    "sku": "A803151S01",
    "title": "In-Wash® Ona - Smart toilet Roca Rimless® con funciones de lavado y secado",
    "category": "Smart Toilets|Inodoros",
    "collection": "Ona",
    "finish": "Blanco",
    "price_rrp": 1548,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1705661153/Product%20Pictures/ROCA_TRAD_ONA_A803151S01_TVM_A403_00.jpg"
  },
  {
    "sku": "A803105001",
    "title": "In-Wash® Insignia con In-Tank® - Smart toilet adosado a pared Roca Rimless® Vortex con tanque integrado y funciones premium de lavado y secado",
    "category": "Smart Toilets|Inodoros",
    "collection": "In-Wash® Insignia",
    "finish": "Blanco",
    "price_rrp": 3363,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1690265850/Product%20Pictures/ROCA_TRAD_IW_INSIGNIA_A803105001.jpg"
  },
  {
    "sku": "A5A358GC00",
    "title": "Grifería empotrable para lavabo",
    "category": "Grifería",
    "collection": "Monodin-N",
    "finish": "Cromado",
    "price_rrp": 227,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1759384972/Product%20Pictures/ROCA_TRAD_MONODIN_A5A358GC00_TVM_D930_00.jpg"
  },
  {
    "sku": "A5A2080C00",
    "title": "T - SQUARE - Columna de ducha termostática",
    "category": "Duchas",
    "collection": "Even-T",
    "finish": "Cromado",
    "price_rrp": 661,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1633213968/Product%20Pictures/5A2080C00_square4.jpg"
  },
  {
    "sku": "A5A2C18C00",
    "title": "PLUS - Columna de ducha termostática con altura regulable",
    "category": "Duchas",
    "collection": "Victoria (fau)",
    "finish": null,
    "price_rrp": 492,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1633222564/Product%20Pictures/VICTORIA_A5A2C18C00_004_10357_00.jpg"
  },
  {
    "sku": "AP9013E838401100",
    "title": "Plato de ducha extraplano de STONEX®",
    "category": "Platos de ducha",
    "collection": "Pyros",
    "finish": "Blanco",
    "price_rrp": 370,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1646912667/Product%20Pictures/ROCA_TRAD_PYROS_1400_1200_1000_10.jpg"
  },
  {
    "sku": "A248722000",
    "title": "Bañera de STONEX® con desagüe click-clack y sifón",
    "category": "Bañeras",
    "collection": "Ohtake",
    "finish": "Blanco",
    "price_rrp": 2398,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1756713496/Product%20Pictures/ROCA_TRAD_OHTAKE_A248722000_TVM_E123_00.jpg"
  },
  {
    "sku": "A248798000",
    "title": "Bañera acrílica one-piece con faldón integrado y juego de desagüe",
    "category": "Bañeras",
    "collection": "Noya",
    "finish": "Blanco brillo",
    "price_rrp": 1550,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1739361739/Product%20Pictures/ROCA_TRAD_NOYA_A248798000_TVM_D966_00.jpg"
  },
  {
    "sku": "A852486529",
    "title": "Unik - mueble base de dos cajones y lavabo de porcelana",
    "category": "Muebles de baño",
    "collection": "Tenue",
    "finish": "Blanco mate",
    "price_rrp": 600,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1676460884/Product%20Pictures/ROCA_TRAD_TENUE_A851934529_TVM_A466_00.jpg"
  },
  {
    "sku": "A852026558",
    "title": "Unik - mueble base de dos cajones y lavabo",
    "category": "Muebles de baño",
    "collection": "Tura",
    "finish": "Blanco roto",
    "price_rrp": 765,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1685355319/Product%20Pictures/ROCA_TRAD_TURA_A852026558_TVM_C081_00.jpg"
  },
  {
    "sku": "AM1973847D0012NMN",
    "title": "DF - Hoja fija ducha",
    "category": "Mamparas",
    "collection": "Victoria Plus",
    "finish": "Cristal transparente / Perfil plata brillo",
    "price_rrp": 318,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1679041508/Product%20Pictures/ROCA_Victoria_DF_ROCA_Victoria_AM1973847D0012NMN_Plata_brillo.jpg"
  },
  {
    "sku": "A851834509",
    "title": "Pack -mueble base de dos cajones, lavabo y espejo con luz LED",
    "category": "Muebles de baño",
    "collection": "Aleyda",
    "finish": "Blanco Mate",
    "price_rrp": 565,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1652973224/Product%20Pictures/ROCA_TRAD_ALEYDA_A851834509_004_15696_00.jpg"
  },
  {
    "sku": "A815504NB0",
    "title": "Toallero de agua",
    "category": "Accesorios",
    "collection": "Victoria (acc)",
    "finish": "Negro mate",
    "price_rrp": 202,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1736517876/Product%20Pictures/ROCA_TRAD_VICTORIA_A815504NB0_TVM_E141_00.jpg"
  },
  {
    "sku": "A816641C00",
    "title": "Toallero eléctrico 500W",
    "category": "Accesorios",
    "collection": "Victoria (acc)",
    "finish": "Cromado",
    "price_rrp": 475,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1736517880/Product%20Pictures/ROCA_TRAD_VICTORIA_A816641NB0_TVM_E147_00.jpg"
  },
  {
    "sku": "A812402000",
    "title": "Ambient - Espejo circular con luz perimetral. Temperatura de color: 3000K",
    "category": "Espejos e iluminación",
    "collection": "Luna",
    "finish": null,
    "price_rrp": 302,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1646900010/Product%20Pictures/ROCA_TRAD_004_15446_00%20LUNA%20AMBIENT%20A812402000.jpg"
  },
  {
    "sku": "A5A3411C00",
    "title": "Mezclador monomando para lavabo con caño alto, desagüe click-clack y cuerpo liso",
    "category": "Grifería",
    "collection": "Lanta",
    "finish": "Cromado",
    "price_rrp": 331,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1633211377/Product%20Pictures/5A3411C00.jpg"
  },
  {
    "sku": "A3270A0000",
    "title": "Lavabo Carmen con un orificio para grifería",
    "category": "Lavabos",
    "collection": "Carmen",
    "finish": "Blanco",
    "price_rrp": 422,
    "image": "https://res.cloudinary.com/roca-dam/image/upload/t_Download_72_dpi/v1633213200/Product%20Pictures/004_07329_00_CARMEN_Carmen.jpg"
  }
];
