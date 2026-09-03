import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import App from "@/App";
import { installCSRFDefaults } from "@/utils/csrfDefaults";

// SEC-002: install X-Requested-With + credentials on every axios /
// fetch call BEFORE any component mounts so the very first request
// (typically ``/api/auth/me`` on load) carries the CSRF header.
installCSRFDefaults();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
