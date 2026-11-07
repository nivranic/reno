import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { flushSync } from "react-dom";
import { RouterProvider } from "react-router";
import { Providers } from "./app/providers";
import { router } from "./app/router";
import "./styles/global.css";

async function bootstrap() {
  // Mock mode (`npm run dev:mock`): intercept the network layer with MSW so
  // the very same pages + api client run against fixtures, not the backend.
  if (import.meta.env.MODE === "mock") {
    const { worker } = await import("./mocks/browser");
    await worker.start({ onUnhandledRequest: "bypass" });
  }
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <Providers>
        <RouterProvider router={router} flushSync={(update) => { flushSync(update); return undefined; }} />
      </Providers>
    </StrictMode>,
  );
}

void bootstrap();
