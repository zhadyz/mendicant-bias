"use client";

import { useRef, useState, useEffect } from "react";
import { useMahoragaWheel } from "@/lib/useMahoragaWheel";
import { cn } from "@/lib/utils";

interface MahoragaWheelProps {
  spinTrigger: number;
  className?: string;
  size?: number;
}

export function MahoragaWheel({
  spinTrigger,
  className,
  size = 280,
}: MahoragaWheelProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const { isSpinning } = useMahoragaWheel(canvasRef, spinTrigger);
  const [showAdapted, setShowAdapted] = useState(false);

  useEffect(() => {
    if (spinTrigger === 0) return;
    setShowAdapted(true);
    const timer = setTimeout(() => setShowAdapted(false), 2500);
    return () => clearTimeout(timer);
  }, [spinTrigger]);

  return (
    <div
      className={cn(
        "forerunner-panel relative flex flex-col items-center justify-center rounded-sm p-4",
        className,
      )}
    >
      <canvas
        ref={canvasRef}
        style={{ width: size, height: size }}
        className="block"
      />
      <div
        className={cn(
          "mt-2 font-mono text-xs uppercase tracking-[0.3em] transition-opacity duration-500",
          showAdapted || isSpinning
            ? "text-cyan opacity-100"
            : "text-muted-foreground/30 opacity-100",
        )}
      >
        {showAdapted || isSpinning ? "ADAPTED" : "MAHORAGA"}
      </div>
    </div>
  );
}
