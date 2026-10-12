import NumberFlow, { type Format } from "@number-flow/react";
import { useEffect, useRef, useState } from "react";
import { cn } from "../lib/utils";

/** A number in JetBrains Mono that rolls to its new value (NumberFlow) and flashes green or red when it changes. */
export function Num({ value, format, prefix, suffix, signed = false, className }: {
  value: number | null | undefined; format?: Format; prefix?: string; suffix?: string; signed?: boolean; className?: string;
}) {
  const prev = useRef<number | null | undefined>(value);
  const [flash, setFlash] = useState<"" | "flash-up" | "flash-down">("");
  useEffect(() => {
    if (prev.current != null && value != null && value !== prev.current) {
      setFlash(value > prev.current ? "flash-up" : "flash-down");
      const t = setTimeout(() => setFlash(""), 900);
      prev.current = value;
      return () => clearTimeout(t);
    }
    prev.current = value;
  }, [value]);
  if (value == null || !Number.isFinite(value)) return <span className={cn("num", className)}>–</span>;
  return (
    <span className={cn("num inline-block", flash, className)}>
      <NumberFlow value={value} locales="nl-NL" prefix={prefix} suffix={suffix}
        format={{ ...(signed ? { signDisplay: "exceptZero" } : {}), ...format }} />
    </span>
  );
}

/** ▲ / ▼ with a signed percentage, green or red on a soft background (exchange style). */
export function Change({ value, digits = 1, unit = "%", className }: { value: number | null | undefined; digits?: number; unit?: string; className?: string }) {
  if (value == null || !Number.isFinite(value)) return <span className={cn("num text-muted", className)}>–</span>;
  const up = value > 0, down = value < 0;
  return (
    <span className={cn("num inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[13px] font-semibold",
      up ? "bg-buy-soft text-buy" : down ? "bg-sell-soft text-sell" : "bg-surface-2 text-fg-2", className)}>
      {up ? "▲" : down ? "▼" : "•"} <Num value={Math.abs(value)} format={{ minimumFractionDigits: digits, maximumFractionDigits: digits }} suffix={unit} />
    </span>
  );
}
