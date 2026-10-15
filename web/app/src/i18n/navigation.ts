/** Arrow keys follow the visual order of RTL tab lists; time controls stay LTR. */
export function readingOrderKey(key: string, locale: string): string {
  if (locale !== "ar") return key;
  return key === "ArrowLeft" ? "ArrowRight" : key === "ArrowRight" ? "ArrowLeft" : key;
}
