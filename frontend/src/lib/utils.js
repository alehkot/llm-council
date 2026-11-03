import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
	return twMerge(clsx(inputs));
}

export function formatModelName(model) {
	return model?.split("/").slice(1).join("/") || model || "unknown";
}
