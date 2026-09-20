/** React Router (Data Mode). Routes are lazy-loaded per feature. */
import { lazy } from "react";
import { createBrowserRouter } from "react-router";
import { RootLayout } from "./root-layout";
import { RouteErrorBoundary } from "./error-boundary";

const InboxPage = lazy(() => import("@/features/inbox/inbox-page"));
const WorkbenchPage = lazy(() => import("@/features/video-workbench/workbench-page"));
const SearchPage = lazy(() => import("@/features/search/search-page"));
const ConflictsPage = lazy(() => import("@/features/conflicts/conflicts-page"));
const ReportsPage = lazy(() => import("@/features/reports/reports-page"));
const CollectPage = lazy(() => import("@/features/collect/collect-page"));

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <InboxPage /> },
      { path: "videos/:videoId", element: <WorkbenchPage /> },
      { path: "search", element: <SearchPage /> },
      { path: "conflicts", element: <ConflictsPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "collect", element: <CollectPage /> },
    ],
  },
]);
