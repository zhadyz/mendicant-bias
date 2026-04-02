"use client";

import { cn } from "@/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import { type ButtonHTMLAttributes, type ReactNode } from "react";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 font-mono text-xs font-semibold uppercase tracking-widest transition-all duration-200 disabled:pointer-events-none disabled:opacity-40",
  {
    variants: {
      variant: {
        primary: [
          "border border-cyan/40 bg-cyan/10 text-cyan",
          "hover:bg-cyan/20 hover:border-cyan/60 hover:shadow-[0_0_16px_oklch(0.78_0.14_195_/_20%)]",
          "active:bg-cyan/30",
        ].join(" "),
        ghost: [
          "border border-transparent text-muted-foreground",
          "hover:border-border hover:text-foreground hover:bg-muted/50",
        ].join(" "),
        destructive: [
          "border border-destructive/40 bg-destructive/10 text-destructive",
          "hover:bg-destructive/20 hover:border-destructive/60",
        ].join(" "),
        outline: [
          "border border-border text-foreground",
          "hover:border-cyan/40 hover:text-cyan hover:bg-cyan/5",
        ].join(" "),
      },
      size: {
        sm: "h-7 px-3 text-[10px]",
        md: "h-9 px-5 text-xs",
        lg: "h-11 px-8 text-xs",
      },
    },
    defaultVariants: {
      variant: "primary",
      size: "md",
    },
  }
);

interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  children: ReactNode;
}

export function Button({ children, variant, size, className, ...props }: ButtonProps) {
  return (
    <button className={cn(buttonVariants({ variant, size }), className)} {...props}>
      {children}
    </button>
  );
}
