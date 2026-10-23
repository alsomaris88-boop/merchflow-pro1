"use client";

import { useEffect } from "react";
import { announceDesignsChanged, isSavedDesign } from "./designs-store";
import { P2P_EVENT } from "./p2p-client";

/** Mounted once above module navigation. Server jobs and their durable outbox
 * are independent of this connection; EventSource resumes via Last-Event-ID.
 */
export function useDesignEvents() {
  useEffect(() => {
    const events = new EventSource("/api/bridge/events");
    events.onmessage = (message) => {
      try {
        const event = JSON.parse(message.data);
        if (event.type === "design.saved" && isSavedDesign(event.design)) announceDesignsChanged(event.design);
        if (["sync", "designs.changed"].includes(event.type)) announceDesignsChanged();
        if (["sync", "p2p.updated"].includes(event.type)) {
          window.dispatchEvent(new CustomEvent(P2P_EVENT, { detail: event.batchId }));
        }
      } catch { /* malformed events never become saved designs; polling reconciles */ }
    };
    return () => events.close();
  }, []);
}
