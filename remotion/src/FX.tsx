import React, { useId } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";

// Soft warm light-leak that drifts across the frame. Cinematic, alive.
export const LightLeak: React.FC<{ hue?: string; strength?: number }> = ({ hue = "#ff8a3d", strength = 0.2 }) => {
  const frame = useCurrentFrame();
  const x = 50 + Math.sin(frame / 42) * 28;
  const y = 32 + Math.cos(frame / 58) * 18;
  const op = strength + Math.sin(frame / 30) * 0.05;
  return (
    <AbsoluteFill
      style={{
        background: `radial-gradient(42% 38% at ${x}% ${y}%, ${hue}, transparent 70%)`,
        mixBlendMode: "screen",
        opacity: Math.max(0, op),
        pointerEvents: "none",
      }}
    />
  );
};

// Filmic grain via animated fractal noise. Subtle overlay for painterly texture.
export const Grain: React.FC<{ opacity?: number }> = ({ opacity = 0.08 }) => {
  const frame = useCurrentFrame();
  const id = useId().replace(/:/g, "");
  const seed = Math.floor(frame / 2) % 60;
  return (
    <AbsoluteFill style={{ opacity, mixBlendMode: "overlay", pointerEvents: "none" }}>
      <svg width="100%" height="100%">
        <filter id={`grain-${id}`}>
          <feTurbulence type="fractalNoise" baseFrequency="0.9" numOctaves="2" seed={seed} />
        </filter>
        <rect width="100%" height="100%" filter={`url(#grain-${id})`} />
      </svg>
    </AbsoluteFill>
  );
};

// Cinematic dark-edge vignette.
export const Vignette: React.FC<{ strength?: number }> = ({ strength = 0.5 }) => (
  <AbsoluteFill
    style={{
      background: `radial-gradient(120% 80% at 50% 45%, transparent 55%, rgba(0,0,0,${strength}))`,
      pointerEvents: "none",
    }}
  />
);

// Floating dust motes drifting upward. Deterministic (no RNG) so renders are stable.
export const Motes: React.FC<{ n?: number }> = ({ n = 16 }) => {
  const frame = useCurrentFrame();
  return (
    <AbsoluteFill style={{ pointerEvents: "none" }}>
      {Array.from({ length: n }).map((_, i) => {
        const seed = (i * 37) % 100;
        const baseX = (seed * 1.7) % 100;
        const y = (110 + (seed * 13) % 100 - frame * (0.18 + (i % 5) * 0.04)) % 115;
        const s = 2 + (i % 3);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: `${baseX + Math.sin((frame + seed) / 40) * 3}%`,
              top: `${y}%`,
              width: s,
              height: s,
              borderRadius: "50%",
              background: "rgba(255,240,205,0.75)",
              filter: "blur(0.5px)",
              opacity: 0.45,
            }}
          />
        );
      })}
    </AbsoluteFill>
  );
};
