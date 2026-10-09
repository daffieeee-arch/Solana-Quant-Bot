const nf0 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 1 });
const nf2 = new Intl.NumberFormat("nl-NL", { maximumFractionDigits: 2 });

export const n0 = (v: unknown) => (v == null ? "–" : nf0.format(Number(v)));
export const n1 = (v: unknown) => (v == null ? "–" : nf1.format(Number(v)));
export const sol = (v: unknown) => {
  if (v == null) return "–";
  const x = Number(v);
  return (Math.abs(x) >= 100 ? nf0 : Math.abs(x) >= 1 ? nf1 : nf2).format(x) + " SOL";
};
export const mln = (v: unknown) => (v == null ? "–" : nf1.format(Number(v) / 1e6) + " mln");
export const pct = (a: number, b: number) => (b ? nf1.format((100 * a) / b) + "%" : "–");
export const shortAddr = (a: string | null | undefined) => (!a ? "–" : a.length > 12 ? `${a.slice(0, 4)}…${a.slice(-4)}` : a);

const dayFmt = new Intl.DateTimeFormat("nl-NL", { weekday: "short", day: "numeric", month: "short", timeZone: "UTC" });
const tsFmt = new Intl.DateTimeFormat("nl-NL", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", timeZone: "UTC" });
const timeFmt = new Intl.DateTimeFormat("nl-NL", { hour: "2-digit", minute: "2-digit", second: "2-digit", timeZone: "UTC" });

export const day = (d: string) => dayFmt.format(new Date(d.slice(0, 10) + "T00:00:00Z"));
export const isoTime = (s: string | null | undefined) => (!s ? "–" : tsFmt.format(new Date(s.endsWith("Z") ? s : s + "Z")));
export const unixTime = (t: number | null | undefined) => (t == null ? "–" : tsFmt.format(new Date(t * 1000)));
export const unixClock = (t: number | null | undefined) => (t == null ? "–" : timeFmt.format(new Date(t * 1000)));

/** Creator-supplied strings: trim control characters and cap length; React escapes the rest. */
export const label = (s: string | null | undefined, max = 40) => {
  if (!s) return "";
  const clean = s.replace(/[\u0000-\u001f\u007f-\u009f​-‏‪-‮⁦-⁩]/g, "").trim();
  return clean.length > max ? clean.slice(0, max - 1) + "…" : clean;
};
