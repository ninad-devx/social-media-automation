import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { AuthProvider } from "./hooks/useAuth";
import "./styles/global.css";
import "./styles/components.css";
import "./styles/layout.css";
import "./styles/pages.css";
import "./styles/ai.css";
import "./styles/settings.css";
import "./styles/auth.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* Wraps everything so the shell and the login screen share one session
        probe, and so a 401 anywhere can drop back to signed-out. */}
    <AuthProvider>
      <App />
    </AuthProvider>
  </StrictMode>,
);