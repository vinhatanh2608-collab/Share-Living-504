export async function compressReceipt(file: File): Promise<Blob> {
  if (!["image/jpeg", "image/png", "image/webp"].includes(file.type) || file.size > 20_000_000)
    throw new Error("Choose a JPEG, PNG, or WebP receipt under 20 MB.");
  const image = await createImageBitmap(file);
  const canvas = document.createElement("canvas");
  try {
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("Image processing is unavailable.");
    let scale = Math.min(1, 1500 / Math.max(image.width, image.height));
    for (let i = 0; i < 6; i++, scale *= 0.78) {
      canvas.width = Math.max(1, Math.round(image.width * scale));
      canvas.height = Math.max(1, Math.round(image.height * scale));
      ctx.fillStyle = "#fff";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(image, 0, 0, canvas.width, canvas.height);
      for (const quality of [0.88, 0.74, 0.60]) {
        const blob = await new Promise<Blob | null>(resolve => canvas.toBlob(resolve, "image/jpeg", quality));
        if (blob && blob.size < 300_000) return blob;
      }
    }
    throw new Error("Could not compress the receipt below 300 KB.");
  } finally {
    image.close();
    canvas.width = canvas.height = 1;
  }
}
