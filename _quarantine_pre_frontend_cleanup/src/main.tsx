// Inactive production entrypoint.
// Vercel builds the active app from `frontend/`, so feature work should target
// `frontend/src/main.tsx` unless the deployment root changes.
import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import "./index.css";

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
