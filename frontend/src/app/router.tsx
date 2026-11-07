/** React Router (Data Mode). Routes are lazy-loaded per feature. */
import { lazy } from "react";
import { createBrowserRouter } from "react-router";
import { RootLayout } from "./root-layout";
import { RouteErrorBoundary } from "./error-boundary";

const InboxPage = lazy(() => import("@/features/inbox/inbox-page"));
const WorkbenchPage = lazy(() => import("@/features/video-workbench/workbench-page"));
const SearchPage = lazy(() => import("@/features/search/search-page"));
const ComparePage = lazy(() => import("@/features/compare/compare-page"));
const AskPage = lazy(() => import("@/features/ask/ask-page"));
const ConflictsPage = lazy(() => import("@/features/conflicts/conflicts-page"));
const ReconciliationPage = lazy(() => import("@/features/reconciliation/reconciliation-page"));
const ReportsPage = lazy(() => import("@/features/reports/reports-page"));
const CollectPage = lazy(() => import("@/features/collect/collect-page"));
const ProjectPage = lazy(() => import("@/features/project/project-page"));
const DecisionsPage = lazy(() => import("@/features/decisions/decisions-page"));
const DecisionDetailPage = lazy(() => import("@/features/decisions/decision-detail-page"));
const SourcesPage = lazy(() => import("@/features/sources/sources-page"));
const ChecksPage = lazy(() => import("@/features/checks/checks-page"));
const CheckDetailPage = lazy(() => import("@/features/checks/check-detail-page"));

export const router = createBrowserRouter([
  {
    path: "/",
    element: <RootLayout />,
    errorElement: <RouteErrorBoundary />,
    children: [
      { index: true, element: <InboxPage /> },
      { path: "videos/:videoId", element: <WorkbenchPage /> },
      { path: "search", element: <SearchPage /> },
      { path: "compare", element: <ComparePage /> },
      { path: "ask", element: <AskPage /> },
      { path: "conflicts", element: <ConflictsPage /> },
      { path: "reconciliation", element: <ReconciliationPage /> },
      { path: "reports", element: <ReportsPage /> },
      { path: "collect", element: <CollectPage /> },
      { path: "project", element: <ProjectPage /> },
      { path: "decisions", element: <DecisionsPage /> },
      { path: "decisions/:cardId", element: <DecisionDetailPage /> },
      { path: "sources", element: <SourcesPage /> },
      { path: "checks", element: <ChecksPage /> },
      { path: "checks/:instanceId", element: <CheckDetailPage /> },
    ],
  },
]);
