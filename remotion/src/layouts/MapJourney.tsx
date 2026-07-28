import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { LayoutProps } from "../theme";
import { pop, ramp } from "../anim";
import { useFit } from "../fit";
import { Grain, LightLeak, Vignette } from "../FX";

// A dotted route draws itself across parchment to the scene, which pops in
// like a pinned photograph; the caption arrives in script. Wandering, warm.
const PATH = "M 120 1560 C 320 1380, 180 1120, 430 1000 S 820 830, 700 620 S 520 420, 760 320";
const PATH_LEN = 1520; // approx; only the dash animation cares

export const MapJourney: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const draw = ramp(frame, 4, 60);
  const photo = pop(frame, fps, 40, 12);
  const write = ramp(frame, 52, 92);
  const accent = recipe.ink.accent ?? "#c2503b";
  const size = useFit({
    text,
    font: (s) => `400 ${s}px "${recipe.fonts.script_family ?? "Caveat"}", cursive`,
    maxWidth: 896 * 0.8,
    maxHeight: 1600 * 0.3,
    maxSize: 86,
    minSize: 32,
    lineHeight: 1.35,
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#e9d9b6" }}>
      <div style={{ position: "absolute", inset: 0, background: "radial-gradient(90% 70% at 50% 40%, transparent 55%, rgba(130,96,50,.28))" }} />
      <LightLeak hue="#ffe3ad" strength={0.1} />
      <svg viewBox="0 0 1080 1920" style={{ position: "absolute", inset: 0, width: "100%", height: "100%" }}>
        <path
          d={PATH}
          fill="none" stroke={accent} strokeWidth={10} strokeLinecap="round"
          strokeDasharray="2 44"
          strokeDashoffset={0}
          pathLength={PATH_LEN}
          style={{ clipPath: `inset(${(1 - draw) * 100}% 0 0 0)` }}
        />
        <circle cx={120} cy={1560} r={16} fill={accent} />
      </svg>
      <div
        style={{
          position: "absolute", top: "8%", left: "50%",
          transform: `translateX(-58%) rotate(3deg) scale(${0.7 + photo * 0.3})`, opacity: photo,
          background: "#fdfaf2", padding: "18px 18px 52px", boxShadow: "0 22px 50px rgba(0,0,0,.3)",
        }}
      >
        <div style={{ position: "absolute", top: -16, left: "44%", width: 26, height: 26, borderRadius: "50%", background: accent, boxShadow: "0 4px 8px rgba(0,0,0,.3)" }} />
        <Img src={staticFile(image)} style={{ width: 620, height: 480, objectFit: "cover", display: "block" }} />
      </div>
      <div style={{ position: "absolute", left: "10%", right: "8%", bottom: "7%" }}>
        <span
          style={{
            display: "inline-block",
            fontFamily: recipe.fonts.script_family ?? "Caveat", fontSize: size, lineHeight: 1.35,
            color: "#4a3822", transform: "rotate(-1.5deg)",
            clipPath: `inset(0 ${(1 - write) * 100}% -12% 0)`,
          }}
        >
          {text}
        </span>
      </div>
      <Vignette strength={0.25} />
      <Grain opacity={0.06} />
    </AbsoluteFill>
  );
};
