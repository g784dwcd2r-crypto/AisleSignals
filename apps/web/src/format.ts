export const label = (value: string) =>
  value
    .toLowerCase()
    .split("_")
    .map((s) => s[0]?.toUpperCase() + s.slice(1))
    .join(" ");
export function money(cents: number | null) {
  return cents === null
    ? "Not established"
    : new Intl.NumberFormat("en-IE", {
        style: "currency",
        currency: "EUR",
      }).format(cents / 100);
}
export function date(value: string | null, full = false) {
  if (!value) return "Not available";
  const d = new Date(value);
  if (Number.isNaN(d.valueOf())) return "Not available";
  return new Intl.DateTimeFormat("en-IE", {
    timeZone: "Europe/Dublin",
    day: "2-digit",
    month: "short",
    ...(full ? { year: "numeric" } : {}),
    hour: "2-digit",
    minute: "2-digit",
  }).format(d);
}
export function cents(value: string): number | null {
  if (value.trim() === "") return null;
  if (!/^\d+(\.\d{1,2})?$/.test(value))
    throw new Error(
      "Enter a non-negative euro amount with up to two decimal places.",
    );
  const [whole, fraction = ""] = value.split(".");
  const result = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  if (!Number.isSafeInteger(result))
    throw new Error("The amount is too large.");
  return result;
}
export function euroInput(value: number | null) {
  return value === null ? "" : (value / 100).toFixed(2);
}
export function liveCandidates<
  T extends { historical: boolean; status: string },
>(candidates: T[]) {
  return candidates.filter(
    (c) => !c.historical && (c.status === "NEW" || c.status === "ACKNOWLEDGED"),
  );
}
export function getAlertCount(
  candidates: { historical: boolean; status: string }[],
) {
  return candidates.filter((c) => !c.historical && c.status === "NEW").length;
}
