import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { KineticWords } from "../Kinetic";
import { useFit } from "../fit";
import { Grain, LightLeak, Motes, Vignette } from "../FX";

// Art fills the frame with a slow cinematic push; light leak, drifting motes,
// and a scrim; the italic caption rises in low. The closing.
export const FullbleedCaption: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const scrim = ramp(frame, 6, 30);
  const pan = interpolate(frame, [0, 120], [0, -18]);
  const size = useFit({
    text,
    font: (s) => `italic 400 ${s}px "${recipe.fonts.main_family}", serif`,
    maxWidth: 896 * 0.84,
    maxHeight: 1600 * 0.34,
    maxSize: 60,
    minSize: 28,
    lineHeight: 1.2,
  });
  return (
    <AbsoluteFill style={{ backgroundColor: "#000" }}>
      <Img
        src={staticFile(image)}
        style={{ width: "100%", height: "114%", objectFit: "cover", transform: `translateY(${pan}px) scale(${drift(frame, 1.14, 0.0006)})` }}
      />
      <LightLeak hue="#ffb45a" strength={0.16} />
      <Motes n={16} />
      <div style={{ position: "absolute", inset: 0, background: "linear-gradient(to top, rgba(0,0,0,.6), rgba(0,0,0,0) 42%)", opacity: scrim }} />
      <div style={{ position: "absolute", left: "8%", right: "8%", bottom: "8%" }}>
        <KineticWords
          text={text}
          inline
          delay={12}
          stagger={3}
          up={64}
          style={{ color: "#fff", fontFamily: recipe.fonts.main_family, fontStyle: "italic", fontSize: size, lineHeight: 1.2, textShadow: "0 2px 22px rgba(0,0,0,.6)" }}
        />
      </div>
      <Vignette strength={0.4} />
      <Grain opacity={0.06} />
    </AbsoluteFill>
  );
};
