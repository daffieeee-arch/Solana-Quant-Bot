import * as ToggleGroup from "@radix-ui/react-toggle-group";

/** Segmented control (Radix ToggleGroup, shadcn style): one value always selected. */
export function Segmented<T extends string>({ value, options, onChange, label }: {
  value: T; options: { value: T; label: string }[]; onChange: (v: T) => void; label: string;
}) {
  return (
    <ToggleGroup.Root type="single" value={value} aria-label={label} onValueChange={(v) => v && onChange(v as T)}
      className="inline-flex gap-1 p-[3px] rounded-[10px] bg-surface-3">
      {options.map((o) => (
        <ToggleGroup.Item key={o.value} value={o.value}
          className="min-h-8 px-2.5 rounded-lg text-xs font-semibold text-muted data-[state=on]:bg-accent data-[state=on]:text-on-accent data-[state=on]:font-bold">
          {o.label}
        </ToggleGroup.Item>
      ))}
    </ToggleGroup.Root>
  );
}
