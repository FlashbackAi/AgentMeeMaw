import React from "react";
import { AbsoluteFill, Img, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { Grain, LightLeak, Vignette } from "../FX";

// Two cream paper layers tear apart to reveal the scene full-bleed between
// them; the caption rides the bottom sheet in quiet serif.
const TOP_TEAR =
  "polygon(0 0, 100% 0, 100% 86%, 94% 90%, 87% 86%, 79% 91%, 71% 87%, 62% 92%, 53% 87%, 44% 91%, 36% 86%, 27% 91%, 19% 87%, 10% 92%, 4% 88%, 0 91%)";
const BOTTOM_TEAR =
  "polygon(0 12%, 6% 8%, 14% 13%, 23% 8%, 31% 12%, 40% 7%, 49% 12%, 58% 8%, 67% 13%, 76% 8%, 84% 12%, 92% 8%, 100% 11%, 100% 100%, 0 100%)";

export const TornReveal: React.FC<LayoutProps> = ({ text, image, recipe }) => {
  const frame = useCurrentFrame();
  const tear = ramp(frame, 4, 34);
  const caption = ramp(frame, 30, 52);
  return (
    <AbsoluteFill style={{ backgroundColor: "#111" }}>
      <Img
        src={staticFile(image)}
        style={{ width: "100%", height: "100%", objectFit: "cover", transform: `scale(${drift(frame, 1.12, 0.0007)})` }}
      />
      <LightLeak hue="#ffb45a" strength={0.12} />
      <div
        style={{
          position: "absolute", left: 0, right: 0, top: 0, height: "34%",
          background: "#f3ead7", clipPath: TOP_TEAR,
          transform: `translateY(${tear * -78}%)`,
          boxShadow: "0 18px 40px rgba(0,0,0,.35)",
        }}
      />
      <div
        style={{
          position: "absolute", left: 0, right: 0, bottom: 0, height: "40%",
          background: "#f3ead7", clipPath: BOTTOM_TEAR,
          transform: `translateY(${tear * 52}%)`,
          display: "flex", alignItems: "flex-end", justifyContent: "center",
          paddingBottom: "6%",
        }}
      >
        <span
          style={{
            fontFamily: recipe.fonts.main_family, fontStyle: "italic", fontSize: 58,
            color: recipe.ink.main_fill, textAlign: "center", padding: "0 9%",
            opacity: caption, transform: `translateY(${(1 - caption) * 40}px)`,
          }}
        >
          {text}
        </span>
      </div>
      <Vignette strength={0.35} />
      <Grain opacity={0.06} />
    </AbsoluteFill>
  );
};
