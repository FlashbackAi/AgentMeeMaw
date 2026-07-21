import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { KineticWords } from "../Kinetic";
import { Grain, LightLeak } from "../FX";

// Art one side; a bold colour block wipes in over the other, an accent rule
// draws down the seam, then the title rises word-by-word. "HOW WE MET".
export const SplitDuotone: React.FC<LayoutProps> = ({ text, display, image, recipe }) => {
  const frame = useCurrentFrame();
  const title = display || text;
  const blockIn = ramp(frame, 0, 20);
  const rule = ramp(frame, 16, 34);
  const parallax = interpolate(frame, [0, 90], [0, -26]);
  return (
    <AbsoluteFill style={{ backgroundColor: "#f3ead7" }}>
      <div style={{ position: "absolute", left: 0, top: 0, width: "56%", height: "100%", overflow: "hidden" }}>
        <Img
          src={staticFile(image)}
          style={{ width: "118%", height: "100%", objectFit: "cover", transform: `translateX(${parallax}px) scale(${drift(frame, 1.1)})` }}
        />
        <LightLeak hue="#fff0c8" strength={0.12} />
      </div>
      <div
        style={{
          position: "absolute", right: 0, top: 0, width: "46%", height: "100%",
          backgroundColor: recipe.ink.accent ?? "#e8552e",
          transform: `translateX(${(1 - blockIn) * 100}%)`,
          display: "flex", flexDirection: "column", justifyContent: "center", padding: "0 9%",
        }}
      >
        <div style={{ position: "absolute", left: 0, top: "50%", width: 8, height: `${rule * 46}%`, transform: "translateY(-50%)", background: "rgba(255,255,255,0.85)" }} />
        <div style={{ transform: `translateY(${interpolate(frame, [0, 120], [0, -10])}px)` }}>
          <span style={{ display: "block", fontFamily: recipe.fonts.eyebrow_family ?? "EB Garamond", color: "rgba(255,255,255,0.85)", letterSpacing: 6, fontSize: 26, textTransform: "uppercase", opacity: ramp(frame, 20, 34), marginBottom: 14 }}>
            Chapter One
          </span>
          <KineticWords
            text={title.toUpperCase()}
            delay={12}
            stagger={5}
            style={{ color: "#fff", fontFamily: recipe.fonts.display_family ?? "Nunito", fontWeight: 800, fontSize: 96, lineHeight: 0.98, letterSpacing: -1 }}
          />
        </div>
      </div>
      <Grain opacity={0.05} />
    </AbsoluteFill>
  );
};
