import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { LayoutProps } from "../theme";
import { pop, ramp } from "../anim";
import { Grain, LightLeak } from "../FX";

// The scene lands as a tilted vintage postcard — stamp in the corner, a
// circular postmark rolling over it, and the wish handwritten beneath.
export const Postcard: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cardIn = pop(frame, fps, 2, 12);
  const mark = pop(frame, fps, 16, 10);
  const write = ramp(frame, 26, 68);
  const sway = Math.sin(frame / 30) * 0.8;
  const accent = recipe.ink.accent ?? "#e8552e";
  return (
    <AbsoluteFill style={{ backgroundColor: "#eee4cf" }}>
      <LightLeak hue="#ffd27a" strength={0.13} />
      <div
        style={{
          position: "absolute", left: "50%", top: "12%", width: "80%",
          transform: `translateX(-50%) translateY(${(1 - cardIn) * 620}px) rotate(${-3 + sway}deg)`,
          background: "#fdfaf2", padding: 22, boxShadow: "0 26px 60px rgba(0,0,0,.3)",
        }}
      >
        <Img src={staticFile(image)} style={{ width: "100%", height: 980, objectFit: "cover", display: "block" }} />
        <div
          style={{
            position: "absolute", top: 44, right: 44, width: 130, height: 160, padding: 8,
            background: accent, border: "10px dashed #fdfaf2", boxShadow: "0 4px 12px rgba(0,0,0,.2)",
            transform: "rotate(4deg)",
          }}
        />
        <div
          style={{
            position: "absolute", top: 30, right: 120, width: 190, height: 190, borderRadius: "50%",
            border: "5px solid rgba(60,48,32,0.55)", opacity: mark * 0.8,
            transform: `rotate(-14deg) scale(${0.8 + mark * 0.2})`,
            display: "flex", alignItems: "center", justifyContent: "center",
          }}
        >
          <span style={{ fontFamily: recipe.fonts.eyebrow_family ?? "EB Garamond", fontSize: 22, letterSpacing: 4, color: "rgba(60,48,32,0.6)", textTransform: "uppercase", textAlign: "center" }}>
            with love
          </span>
        </div>
      </div>
      <div style={{ position: "absolute", left: "12%", right: "10%", bottom: "9%" }}>
        <span
          style={{
            display: "inline-block",
            fontFamily: recipe.fonts.script_family ?? "Caveat", fontSize: 92, lineHeight: 1.3,
            color: "#2a4d69", transform: "rotate(-2deg)",
            clipPath: `inset(0 ${(1 - write) * 100}% -12% 0)`,
          }}
        >
          {text}
        </span>
      </div>
      <Grain opacity={0.05} />
    </AbsoluteFill>
  );
};
