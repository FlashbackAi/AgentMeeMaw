import React from "react";
import { LayoutProps } from "../theme";
import { SplitDuotone } from "./SplitDuotone";
import { Scrapbook } from "./Scrapbook";
import { TypeOverCrop } from "./TypeOverCrop";
import { FullbleedCaption } from "./FullbleedCaption";
import { FramedHero } from "./FramedHero";

export const LAYOUTS: Record<string, React.FC<LayoutProps>> = {
  split_duotone: SplitDuotone,
  scrapbook: Scrapbook,
  type_over_crop: TypeOverCrop,
  fullbleed_caption: FullbleedCaption,
  framed_hero: FramedHero,
};

export const DEFAULT_LAYOUT = "framed_hero";
