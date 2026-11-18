import { clsx } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs) {
	return twMerge(clsx(inputs));
}

export function formatModelName(model) {
	return model?.split("/").slice(1).join("/") || model || "unknown";
}

export function parseTimestamp(value) {
	// The backend stores UTC date-times without a timezone suffix.
	const offsetless = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?$/;
	if (typeof value === "string" && offsetless.test(value)) {
		return new Date(`${value}Z`);
	}
	return new Date(value);
}
