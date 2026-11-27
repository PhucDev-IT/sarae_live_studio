// Match the video parameters shown by the dev branch's media library.
export function durationLabel(seconds: number | null | undefined): string | null {
  if (seconds == null || !Number.isFinite(seconds) || seconds <= 0) return null
  return `${Math.floor(seconds / 60)}:${String(Math.floor(seconds % 60)).padStart(2, '0')}`
}

export function aspectLabel(width: number | null, height: number | null): string | null {
  if (!width || !height || width <= 0 || height <= 0) return null
  const ratio = width / height
  for (const [w, h] of [[9, 16], [16, 9], [1, 1], [4, 3], [3, 4], [21, 9], [9, 21]]) {
    if (Math.abs(ratio - w / h) / (w / h) < 0.02) return `${w}:${h}`
  }
  let a = width
  let b = height
  while (b) { const remainder = a % b; a = b; b = remainder }
  return `${width / a}:${height / a}`
}

export function dimensionsLabel(width: number | null, height: number | null): string | null {
  return width && height ? `${width} × ${height}` : null
}
