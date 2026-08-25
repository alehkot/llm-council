import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import App from "./App.jsx";
import ErrorBoundary from "./components/ErrorBoundary.jsx";
import { ThemeProvider } from "./contexts/ThemeContext.jsx";

createRoot(document.getElementById("root")).render(
	<StrictMode>
		<ErrorBoundary>
			<ThemeProvider defaultTheme="system">
				<App />
			</ThemeProvider>
		</ErrorBoundary>
	</StrictMode>,
);
