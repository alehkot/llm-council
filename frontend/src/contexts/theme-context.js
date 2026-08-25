import { createContext, useContext } from "react";

const ThemeContext = createContext(undefined);

function useTheme() {
	const context = useContext(ThemeContext);
	if (context === undefined) {
		throw new Error("useTheme must be used within a ThemeProvider");
	}
	return context;
}

export { ThemeContext, useTheme };
