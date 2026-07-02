// Reescala una foto en el navegador antes de subirla: máx. 1024px de lado, JPEG 0.85.
// (~1.5MB de móvil -> ~150KB; el modelo reescala de todos modos, no se pierde señal útil)
export async function downscalePhoto(
  file: File, maxSide = 1024, quality = 0.85
): Promise<Blob> {
  const bmp = await createImageBitmap(file);
  const scale = Math.min(1, maxSide / Math.max(bmp.width, bmp.height));
  if (scale === 1 && file.type === "image/jpeg") { bmp.close(); return file; }
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(bmp.width * scale);
  canvas.height = Math.round(bmp.height * scale);
  canvas.getContext("2d")!.drawImage(bmp, 0, 0, canvas.width, canvas.height);
  bmp.close();
  return await new Promise((resolve, reject) =>
    canvas.toBlob(
      (b) => (b ? resolve(b) : reject(new Error("No se pudo procesar la foto"))),
      "image/jpeg", quality,
    )
  );
}
