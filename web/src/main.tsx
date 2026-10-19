import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";
import { LazyMotion } from "motion/react";
import { router } from "./router";
import { TooltipProvider } from "./components/ui/Tooltip";
import "./styles/index.css";

const loadMotionFeatures = () => import("./lib/motionFeatures").then((m) => m.default);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Components use the slim `m` element; the animation features load as a separate chunk. */}
    <LazyMotion features={loadMotionFeatures}>
      <TooltipProvider>
        <RouterProvider router={router} />
      </TooltipProvider>
    </LazyMotion>
  </StrictMode>,
);
