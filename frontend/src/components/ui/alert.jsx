import * as React from "react";

import { cn } from "../../lib/utils";

const Alert = React.forwardRef(({ className, variant, ...props }, ref) => (
	<div
		ref={ref}
		role="alert"
		className={cn(
			"relative w-full rounded-lg border px-4 py-3 text-sm shadow-sm",
			variant === "destructive"
				? "border-destructive/40 bg-destructive/10 text-destructive"
				: "border-border/60 bg-card text-card-foreground",
			className,
		)}
		{...props}
	/>
));
Alert.displayName = "Alert";

const AlertTitle = React.forwardRef(({ className, ...props }, ref) => (
	<h5
		ref={ref}
		className={cn("mb-1 font-semibold leading-none tracking-tight", className)}
		{...props}
	/>
));
AlertTitle.displayName = "AlertTitle";

const AlertDescription = React.forwardRef(({ className, ...props }, ref) => (
	<div
		ref={ref}
		className={cn("text-sm text-muted-foreground", className)}
		{...props}
	/>
));
AlertDescription.displayName = "AlertDescription";

export { Alert, AlertTitle, AlertDescription };
