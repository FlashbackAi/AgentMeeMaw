import React from "react";
import { Composition } from "remotion";
import { Flashback } from "./Flashback";
import { FlashbackProps } from "./theme";

const DEFAULT_PROPS: FlashbackProps = {
  meta: { width: 896, height: 1600, fps: 30 },
  recipe: {
    fonts: {
      main_family: "Playfair Display",
      eyebrow_family: "EB Garamond",
      display_family: "Nunito",
      script_family: "Caveat",
    },
    ink: { main_fill: "#3a2c1c", eyebrow_fill: "#96764a", accent: "#e8552e" },
    pacing: { hold: 2.2, transition: 0.5 },
  },
  scenes: [],
};

function totalFrames(props: FlashbackProps): number {
  const fps = props.meta?.fps ?? 30;
  const hold = Math.max(1, Math.round((props.recipe?.pacing?.hold ?? 2.4) * fps));
  const trans = Math.max(1, Math.round((props.recipe?.pacing?.transition ?? 0.6) * fps));
  const n = props.scenes?.length ?? 1;
  return Math.max(1, n * hold - (n - 1) * trans);
}

export const RemotionRoot: React.FC = () => (
  <Composition
    id="Flashback"
    component={Flashback}
    durationInFrames={300}
    fps={30}
    width={896}
    height={1600}
    defaultProps={DEFAULT_PROPS}
    calculateMetadata={({ props }) => ({
      durationInFrames: totalFrames(props),
      fps: props.meta?.fps ?? 30,
      width: props.meta?.width ?? 896,
      height: props.meta?.height ?? 1600,
    })}
  />
);
