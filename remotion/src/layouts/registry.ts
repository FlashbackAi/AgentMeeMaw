import React from "react";
import { LayoutProps } from "../theme";
import { SplitDuotone } from "./SplitDuotone";
import { Scrapbook } from "./Scrapbook";
import { TypeOverCrop } from "./TypeOverCrop";
import { FullbleedCaption } from "./FullbleedCaption";
import { FramedHero } from "./FramedHero";
import { LetterNote } from "./LetterNote";
import { Filmstrip } from "./Filmstrip";
import { Postcard } from "./Postcard";
import { WordMask } from "./WordMask";
import { TornReveal } from "./TornReveal";
import { GalleryWall } from "./GalleryWall";
import { Magazine } from "./Magazine";
import { MapJourney } from "./MapJourney";

export const LAYOUTS: Record<string, React.FC<LayoutProps>> = {
  split_duotone: SplitDuotone,
  scrapbook: Scrapbook,
  type_over_crop: TypeOverCrop,
  fullbleed_caption: FullbleedCaption,
  framed_hero: FramedHero,
  letter_note: LetterNote,
  filmstrip: Filmstrip,
  postcard: Postcard,
  word_mask: WordMask,
  torn_reveal: TornReveal,
  gallery_wall: GalleryWall,
  magazine: Magazine,
  map_journey: MapJourney,
};

export const DEFAULT_LAYOUT = "framed_hero";
