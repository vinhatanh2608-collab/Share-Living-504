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
export async function receiptMime(blob: Blob) {
  const bytes = new Uint8Array(await blob.slice(0, 12).arrayBuffer());
  if (bytes[0] === 255 && bytes[1] === 216 && bytes[2] === 255) return "image/jpeg";
  if ([137, 80, 78, 71, 13, 10, 26, 10].every((value, index) => value === bytes[index])) return "image/png";
  const signature = String.fromCharCode(...bytes);
  if (signature.startsWith("RIFF") && signature.slice(8, 12) === "WEBP") return "image/webp";
}

