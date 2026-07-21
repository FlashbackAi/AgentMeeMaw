import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { LayoutProps } from "../theme";
import { pop, ramp } from "../anim";
import { Grain, LightLeak } from "../FX";

// The caption inks itself onto ruled letter paper; a small painted photo is
// tucked under a strip of tape up top. Intimate — the message register.
export const LetterNote: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const photoIn = pop(frame, fps, 4, 13);
  const write = ramp(frame, 20, 74);
  const flourish = ramp(frame, 76, 96);
  return (
    <AbsoluteFill style={{ backgroundColor: "#f6eedd" }}>
      <div style={{ position: "absolute", inset: 0, background: "repeating-linear-gradient(to bottom, transparent 0 86px, rgba(122,96,58,0.14) 86px 88px)" }} />
      <div style={{ position: "absolute", left: "9%", top: 0, bottom: 0, width: 3, background: "rgba(196,90,74,0.35)" }} />
      <LightLeak hue="#ffd9a0" strength={0.1} />
      <div
        style={{
          position: "absolute", top: "8%", left: "16%",
          transform: `translateY(${(1 - photoIn) * -420}px) rotate(-4deg)`,
          background: "#fff", padding: "16px 16px 42px", boxShadow: "0 18px 42px rgba(0,0,0,.22)",
        }}
      >
        <div style={{ position: "absolute", top: -14, left: "34%", width: 96, height: 30, background: "rgba(224,206,150,0.7)", transform: "rotate(5deg)", boxShadow: "0 2px 6px rgba(0,0,0,.15)" }} />
        <Img src={staticFile(image)} style={{ width: 560, height: 430, objectFit: "cover", display: "block" }} />
      </div>
      <div style={{ position: "absolute", left: "14%", right: "9%", top: "56%" }}>
        <span
          style={{
            display: "inline-block",
            fontFamily: recipe.fonts.script_family ?? "Caveat", fontSize: 84, lineHeight: 1.45,
            color: "#3c3020", clipPath: `inset(0 ${(1 - write) * 100}% -10% 0)`,
          }}
        >
          {text}
        </span>
        <div style={{ height: 6, width: `${flourish * 42}%`, marginTop: 18, borderRadius: 3, background: recipe.ink.accent ?? "#e8552e", opacity: 0.8 }} />
      </div>
      <Grain opacity={0.05} />
    </AbsoluteFill>
  );
};
