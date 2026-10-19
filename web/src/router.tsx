import { lazy, Suspense, type ReactNode } from "react";
import { createBrowserRouter } from "react-router";
import { Shell } from "./components/layout/Shell";
import Landing from "./pages/Landing";
import { Skeleton } from "./components/ui/Skeleton";

// Dashboard pages are split out so the landing page ships less JavaScript.
const Leaderboard = lazy(() => import("./pages/Leaderboard"));
const SignIn = lazy(() => import("./pages/SignIn"));
const NotFound = lazy(() => import("./pages/NotFound"));
const Stub = lazy(() => import("./pages/Stub"));

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
 * Routes (docs/WEB.md "Pages"). Pages not built yet render <Stub>, which states what the page will hold.
 * The page author replaces each Stub with a real page under src/pages/.
 */
export const router = createBrowserRouter([
  {
    element: <Shell />,
    children: [
      { path: "/", element: <Landing /> },
      { path: "/leaderboard", element: <L><Leaderboard /></L> },
      { path: "/providers/:id", element: <L><Stub page="provider" /></L> },
      { path: "/compare", element: <L><Stub page="compare" /></L> },
      { path: "/picker", element: <L><Stub page="picker" /></L> },
      { path: "/clips", element: <L><Stub page="clips" /></L> },
      { path: "/methodology", element: <L><Stub page="methodology" /></L> },
      { path: "/requests", element: <L><Stub page="requests" /></L> },
      { path: "/changelog", element: <L><Stub page="changelog" /></L> },
      { path: "/account", element: <L><Stub page="account" /></L> },
      { path: "/docs/api", element: <L><Stub page="api" /></L> },
      { path: "/sign-in", element: <L><SignIn /></L> },
      { path: "*", element: <L><NotFound /></L> },
    ],
  },
]);
