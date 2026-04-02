"use client";

import { cn } from "@/lib/utils";
import { type ReactNode } from "react";

interface CardProps {
  children: ReactNode;
  className?: string;
  glow?: boolean;
  onClick?: () => void;
}

export function Card({ children, className, glow = false, onClick }: CardProps) {
  return (
    <div
      onClick={onClick}
      className={cn(
        "forerunner-panel rounded-sm p-5 transition-all duration-300",
        glow && "forerunner-border",
        onClick && "cursor-pointer hover:border-cyan/30 hover:shadow-[0_0_20px_oklch(0.78_0.14_195_/_12%)]",
        className
      )}
    >
      {children}
    </div>
  );
}

interface CardHeaderProps {
  children: ReactNode;
  className?: string;
}

export function CardHeader({ children, className }: CardHeaderProps) {
  return (
    <div className={cn("mb-3 flex items-center gap-3", className)}>
      {children}
    </div>
  );
}

interface CardTitleProps {
  children: ReactNode;
  className?: string;
}

export function CardTitle({ children, className }: CardTitleProps) {
  return (
    <h3 className={cn("text-sm font-semibold uppercase tracking-wider text-cyan", className)}>
      {children}
    </h3>
  );
}

interface CardContentProps {
  children: ReactNode;
  className?: string;
}

export function CardContent({ children, className }: CardContentProps) {
  return <div className={cn("text-sm text-muted-foreground", className)}>{children}</div>;
}
