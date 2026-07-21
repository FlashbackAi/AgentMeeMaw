import React from "react";
import { AbsoluteFill, Img, interpolate, staticFile, useCurrentFrame } from "remotion";
import { LayoutProps } from "../theme";
import { drift, ramp } from "../anim";
import { Grain, LightLeak, Vignette } from "../FX";

// A vertical film strip slides slowly upward through two painted frames,
// sprocket holes down both edges; the caption sits below like a frame label.
const Sprockets: React.FC<{ side: "left" | "right" }> = ({ side }) => (
  <div
    style={{
      position: "absolute", top: 0, bottom: 0, [side]: 12, width: 40,
      background: "repeating-linear-gradient(to bottom, transparent 0 26px, rgba(245,240,228,0.92) 26px 62px, transparent 62px 88px)",
      backgroundClip: "content-box", padding: "0 6px", borderRadius: 4,
    }}
  />
);

export const Filmstrip: React.FC<LayoutProps> = ({ text, image, image2, recipe }) => {
  const frame = useCurrentFrame();
  const slideY = interpolate(frame, [0, 150], [40, -110]);
  const label = ramp(frame, 18, 40);
  return (
    <AbsoluteFill style={{ backgroundColor: "#171310" }}>
      <LightLeak hue="#ff9a4d" strength={0.1} />
      <div
        style={{
          position: "absolute", left: "50%", top: "50%", width: "72%", height: "128%",
          transform: `translate(-50%, -50%) translateY(${slideY}px) rotate(1.5deg)`,
          background: "#0c0a08", padding: "36px 64px", boxShadow: "0 30px 80px rgba(0,0,0,.55)",
        }}
      >
        <Sprockets side="left" />
        <Sprockets side="right" />
        {[image, image2 ?? image].map((img, i) => (
          <div key={i} style={{ margin: "26px 0", overflow: "hidden" }}>
            <Img
              src={staticFile(img)}
              style={{ width: "100%", height: 760, objectFit: "cover", display: "block", transform: `scale(${drift(frame, 1.05, 0.0005)})`, filter: "saturate(0.92)" }}
            />
          </div>
        ))}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: "5.5%", textAlign: "center", opacity: label }}>
        <span style={{ fontFamily: recipe.fonts.eyebrow_family ?? "EB Garamond", color: "rgba(245,240,228,0.9)", letterSpacing: 8, fontSize: 34, textTransform: "uppercase" }}>
          {text}
        </span>
      </div>
      <Vignette strength={0.5} />
      <Grain opacity={0.09} />
    </AbsoluteFill>
  );
};
