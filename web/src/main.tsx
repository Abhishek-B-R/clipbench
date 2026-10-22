import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { RouterProvider } from "react-router/dom";
import { LazyMotion } from "motion/react";
import { router } from "./router";
import "./styles/index.css";

// One canonical address: the site Worker also answers on its workers.dev origin, so a visit there is sent
// to the custom domain. Through the custom domain the browser always sees that domain, so this cannot loop.
const CANONICAL_HOST = "clipbench.abhishekbr.com";
if (location.hostname.endsWith(".workers.dev")) {
  location.replace(`https://${CANONICAL_HOST}${location.pathname}${location.search}${location.hash}`);
}

const loadMotionFeatures = () => import("./lib/motionFeatures").then((m) => m.default);

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Components use the slim `m` element; the animation features load as a separate chunk. */}
    <LazyMotion features={loadMotionFeatures}>
      <RouterProvider router={router} />
    </LazyMotion>
  </StrictMode>,
);
