import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter } from "react-router";
import { Shell } from "./components/layout/Shell";
import Landing from "./pages/Landing";
import { Skeleton } from "./components/ui/Skeleton";
import { ACCOUNTS } from "./lib/flags";

// Dashboard pages are split out so the landing page ships less JavaScript.
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const SignIn = lazy(() => import("./pages/SignIn"));
const NotFound = lazy(() => import("./pages/NotFound"));
const Provider = lazy(() => import("./pages/Provider"));
const Compare = lazy(() => import("./pages/Compare"));
const Picker = lazy(() => import("./pages/Picker"));
const Clips = lazy(() => import("./pages/Clips"));
const Methodology = lazy(() => import("./pages/Methodology"));
const Requests = lazy(() => import("./pages/Requests"));
const Changelog = lazy(() => import("./pages/Changelog"));
const Account = lazy(() => import("./pages/Account"));
const ApiDocs = lazy(() => import("./pages/ApiDocs"));
const Lang = lazy(() => import("./pages/Lang"));
const Findings = lazy(() => import("./pages/Findings"));
const Streaming = lazy(() => import("./pages/Streaming"));
const Features = lazy(() => import("./pages/Features"));
const Early = lazy(() => import("./pages/Early"));
const Try = lazy(() => import("./pages/Try"));

function L({ children }: { children: ReactNode }) {
  return (
    <Suspense
      fallback={
        <div className="frame grid gap-3 py-16">
          <Skeleton className="h-10 w-72" />
          <Skeleton className="h-64 w-full" />
        </div>
      }
    >
      {children}
    </Suspense>
  );
}

/**
 * Routes (docs/WEB.md "Pages"). Every page but the landing is lazy-loaded.
 */
export const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { path: "/", element: <Landing /> },
      { path: "/leaderboard", element: <L><Leaderboard /></L> },
      { path: "/early", element: <L><Early /></L> },
      { path: "/try", element: <L><Try /></L> },
      { path: "/providers/:id", element: <L><Provider /></L> },
      { path: "/streaming", element: <L><Streaming /></L> },
      { path: "/features", element: <L><Features /></L> },
      { path: "/compare", element: <L><Compare /></L> },
      { path: "/picker", element: <L><Picker /></L> },
      { path: "/clips", element: <L><Clips /></L> },
      { path: "/methodology", element: <L><Methodology /></L> },
      { path: "/changelog", element: <L><Changelog /></L> },
      ...(ACCOUNTS
        ? [
            { path: "/account", element: <L><Account /></L> },
            { path: "/requests", element: <L><Requests /></L> },
            { path: "/sign-in", element: <L><SignIn /></L> },
          ]
        : []),
      { path: "/lang/:code", element: <L><Lang /></L> },
      { path: "/findings/:lang", element: <L><Findings /></L> },
      { path: "/findings/:lang/:slug", element: <L><Findings /></L> },
      { path: "/docs/api", element: <L><ApiDocs /></L> },
      { path: "*", element: <L><NotFound /></L> },
    ],
  },
]);
