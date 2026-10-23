"use client";

import { useState } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Shell, type ViewKey } from "@/components/shell";
import Dashboard from "@/components/dashboard";
import Generator from "@/components/generator";
import DesignsHistory from "@/components/designs-history";
import PixelToPixel from "@/components/modules/pixel-to-pixel";
import PrepareLab from "@/components/modules/prepare-lab";
import Trademark from "@/components/trademark";
import ListingHub from "@/components/listing-hub";
import TrendRadar from "@/components/trend-radar";
import Mockup from "@/components/mockup";
import Accounts from "@/components/accounts";
import Licensing from "@/components/licensing";

export default function Page() {
  const [view, setView] = useState<ViewKey>("dashboard");

  const render = () => {
    switch (view) {
      case "dashboard":
        return <Dashboard setView={setView} />;
      case "generator":
        return <Generator />;
      case "history":
        return <DesignsHistory setView={setView} />;
      case "recolor":
        return <PixelToPixel />;
      case "prepare":
        return <PrepareLab />;
      case "trademark":
        return <Trademark />;
      case "listing":
        return <ListingHub />;
      case "trends":
        return <TrendRadar />;
      case "mockup":
        return <Mockup />;
      case "accounts":
        return <Accounts />;
      case "license":
        return <Licensing />;
    }
  };

  return (
    <Shell view={view} setView={setView}>
      <AnimatePresence mode="wait">
        <motion.div
          key={view}
          initial={{ opacity: 0, y: 14, filter: "blur(6px)" }}
          animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
          exit={{ opacity: 0, y: -10, filter: "blur(6px)" }}
          transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
        >
          {render()}
        </motion.div>
      </AnimatePresence>
    </Shell>
  );
}
