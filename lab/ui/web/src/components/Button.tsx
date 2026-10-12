import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import type { ComponentProps } from "react";
import { cn } from "../lib/utils";

// shadcn/ui Button, in the design's tokens.
const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-lg text-sm font-semibold transition-colors disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-accent text-on-accent font-bold hover:opacity-90",
        soft: "bg-accent-soft text-accent-fg border border-accent-line",
        outline: "border border-track bg-transparent text-fg-2 hover:text-fg",
        ghost: "text-fg-2 hover:bg-surface-2",
      },
      size: { default: "min-h-9 px-3", sm: "min-h-8 px-2.5 text-xs", lg: "min-h-11 px-4" },
    },
    defaultVariants: { variant: "outline", size: "default" },
  },
);

export function Button({ className, variant, size, asChild = false, ...props }:
  ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}
