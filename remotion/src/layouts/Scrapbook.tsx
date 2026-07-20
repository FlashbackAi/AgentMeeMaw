import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { LayoutProps } from "../theme";
import { pop, ramp } from "../anim";
import { Grain, LightLeak } from "../FX";

// Polaroids toss in from below with rotation overshoot + gentle sway, each with
// a strip of tape; the caption is "written on" and a doodle star pops. Playful.
const Polaroid: React.FC<{
  image: string; rest: number; top: string; left: string; t: number; frame: number; phase: number;
}> = ({ image, rest, top, left, t, frame, phase }) => {
  const sway = Math.sin((frame + phase) / 26) * 1.4;
  const rotate = interpolate(t, [0, 1], [rest + 26, rest]) + sway;
  const y = interpolate(t, [0, 1], [560, 0]);
  return (
    <div style={{ position: "absolute", top, left, transform: `translateY(${y}px) rotate(${rotate}deg)`, background: "#fff", padding: "18px 18px 52px", boxShadow: "0 22px 50px rgba(0,0,0,.34)" }}>
      <div style={{ position: "absolute", top: -14, left: "38%", width: 90, height: 30, background: "rgba(224,206,150,0.7)", transform: "rotate(-6deg)", boxShadow: "0 2px 6px rgba(0,0,0,.15)" }} />
      <Img src={staticFile(image)} style={{ width: 430, height: 330, objectFit: "cover", display: "block" }} />
    </div>
  );
};

export const Scrapbook: React.FC<LayoutProps> = ({ text, image, image2, recipe }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t1 = pop(frame, fps, 0, 11);
  const t2 = pop(frame, fps, 9, 11);
  const write = ramp(frame, 24, 46);
  const star = pop(frame, fps, 30, 9);
  return (
    <AbsoluteFill style={{ backgroundColor: "#efe6d3" }}>
      <LightLeak hue="#ffd27a" strength={0.14} />
      <Polaroid image={image} rest={-7} top={"12%"} left={"7%"} t={t1} frame={frame} phase={0} />
      <Polaroid image={image2 ?? image} rest={6} top={"40%"} left={"39%"} t={t2} frame={frame} phase={40} />
      <span
        style={{
          position: "absolute", bottom: "10%", left: "11%", transform: "rotate(-4deg)",
          fontFamily: recipe.fonts.script_family ?? "Caveat", fontSize: 96, color: "#2a4d69",
          clipPath: `inset(0 ${(1 - write) * 100}% -10% 0)`,
        }}
      >
        {text}
      </span>
      <span
        style={{
          position: "absolute", top: "8%", right: "12%", fontSize: 90, color: "#e8552e",
          transform: `scale(${star}) rotate(${interpolate(star, [0, 1], [-40, 0])}deg)`, transformOrigin: "center",
        }}
      >
        ★
      </span>
      <Grain opacity={0.06} />
    </AbsoluteFill>
  );
};
