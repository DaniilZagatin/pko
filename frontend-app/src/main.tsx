import { createRoot } from "react-dom/client";
import { Dashboard, type JourneyItem } from "./Dashboard";
import "./styles.css";

declare global {
  interface Window {
    __JOURNEY_ITEMS__?: JourneyItem[];
  }
}

const root = document.getElementById("journey-root");
if (root) {
  createRoot(root).render(<Dashboard items={window.__JOURNEY_ITEMS__ ?? []} />);
}
