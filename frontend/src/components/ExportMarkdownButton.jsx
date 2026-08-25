import { useCallback, useState } from "react";
import { api } from "../api";
import { Button } from "./ui/button";

export default function ExportMarkdownButton({
	conversationId,
	messageIndex,
	disabled = false,
	variant = "outline",
	size = "sm",
	className,
	onError,
}) {
	const [isExporting, setIsExporting] = useState(false);

	const handleExport = useCallback(async () => {
		if (
			!conversationId ||
			messageIndex === undefined ||
			messageIndex === null
		) {
			return;
		}

		if (onError) {
			onError(null);
		}
		setIsExporting(true);
		try {
			const { blob, filename } = await api.exportMarkdown(
				conversationId,
				messageIndex,
			);
			const url = URL.createObjectURL(blob);
			const link = document.createElement("a");
			link.href = url;
			link.download = filename;
			document.body.appendChild(link);
			link.click();
			link.remove();
			URL.revokeObjectURL(url);
		} catch (error) {
			console.error("Failed to export markdown:", error);
			if (onError) {
				onError(error?.message || "Failed to export markdown");
			}
		} finally {
			setIsExporting(false);
		}
	}, [conversationId, messageIndex, onError]);

	return (
		<Button
			variant={variant}
			size={size}
			onClick={handleExport}
			disabled={disabled || isExporting}
			className={className}
		>
			{isExporting ? "Exporting…" : "Export Markdown"}
		</Button>
	);
}
