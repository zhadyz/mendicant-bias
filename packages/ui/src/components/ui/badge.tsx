"use client";

import { cn } from "@/lib/utils";
import { cva, type VariantProps } from "class-variance-authority";
import { type ReactNode } from "react";

const badgeVariants = cva(
  "inline-flex items-center gap-1.5 font-mono text-[10px] font-semibold uppercase tracking-wider px-2.5 py-0.5 rounded-sm",
  {
    variants: {
      variant: {
        default: "border border-border bg-muted/50 text-muted-foreground",
        cyan: "border border-cyan/30 bg-cyan/10 text-cyan",
        success: "border border-success/30 bg-success/10 text-success",
        destructive: "border border-destructive/30 bg-destructive/10 text-destructive",
        warning: "border border-yellow-500/30 bg-yellow-500/10 text-yellow-400",
      },
    },
    defaultVariants: {
      variant: "default",
    },
  }
);

interface BadgeProps extends VariantProps<typeof badgeVariants> {
  children: ReactNode;
  className?: string;
  pulse?: boolean;
}

export function Badge({ children, variant, className, pulse }: BadgeProps) {
  return (
    <span className={cn(badgeVariants({ variant }), className)}>
      {pulse && (
        <span className="relative flex h-1.5 w-1.5">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-current opacity-60" />
          <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-current" />
        </span>
      )}
      {children}
    </span>
  );
}
