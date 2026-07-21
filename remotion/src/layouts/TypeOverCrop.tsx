import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { KineticWords } from "../Kinetic";
import { Grain, LightLeak, Vignette } from "../FX";

// Giant kinetic headline over a hard-pushing crop. Words punch up behind a
// mask; a highlight underline wipes in beneath the payoff line.
export const TypeOverCrop: React.FC<LayoutProps> = ({ text, display, image, recipe }) => {
  const frame = useCurrentFrame();
  const title = display || text;
  const words = title.toUpperCase().split(" ");
  const land = 5 * (words.length - 1) + 22;
  const float = interpolate(frame, [0, 120], [0, -22]);
  const underline = ramp(frame, land, land + 16);
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <Img
        src={staticFile(image)}
        style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${drift(frame, 1.16, 0.0011)})`, filter: "brightness(0.6)" }}
      />
      <div style={{ position: "absolute", inset: 0, background: "linear-gradient(to top, rgba(0,0,0,.45), rgba(0,0,0,0) 55%)" }} />
      <LightLeak hue="#ff7a2d" strength={0.14} />
      <AbsoluteFill style={{ display: "flex", flexDirection: "column", justifyContent: "center", padding: "0 7%", transform: `translateY(${float}px)` }}>
        <KineticWords
          text={title.toUpperCase()}
          stagger={5}
          up={130}
          scaleFrom={1.25}
          style={{ color: "#fff", fontFamily: recipe.fonts.display_family ?? "Nunito", fontWeight: 900, fontSize: 132, lineHeight: 0.92, textTransform: "uppercase", letterSpacing: -3, textShadow: "0 6px 30px rgba(0,0,0,.4)" }}
        />
        <div style={{ height: 16, width: `${underline * 58}%`, marginTop: 22, borderRadius: 8, background: recipe.ink.accent ?? "#e8552e" }} />
      </AbsoluteFill>
      <Vignette strength={0.55} />
      <Grain />
    </AbsoluteFill>
  );
};
