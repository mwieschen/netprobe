// Formatierung: Anzeige in Europe/Berlin, Werte kommen als UTC-ISO-Strings.

const TZ = "Europe/Berlin";

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString("de-DE", {
    timeZone: TZ,
    dateStyle: "short",
    timeStyle: "medium",
  });
}

export function fmtTime(iso: string): string {
  return new Date(iso).toLocaleTimeString("de-DE", { timeZone: TZ });
}

export function fmtNum(v: number | null | undefined, digits = 1, unit = ""): string {
  if (v === null || v === undefined) return "–";
  return `${v.toFixed(digits)}${unit ? " " + unit : ""}`;
}

export function fmtBytes(bytes: number | null | undefined): string {
  if (bytes === null || bytes === undefined) return "–";
  const units = ["B", "KB", "MB", "GB"];
  let v = bytes;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  return `${v.toFixed(1)} ${units[i]}`;
}
