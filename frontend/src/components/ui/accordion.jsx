import * as React from "react";
import * as AccordionPrimitive from "@radix-ui/react-accordion";

import { cn } from "../../lib/utils";

const Accordion = AccordionPrimitive.Root;

const AccordionItem = React.forwardRef(({ className, ...props }, ref) => (
	<AccordionPrimitive.Item
		ref={ref}
		className={cn("rounded-lg border border-border/60 bg-card", className)}
		{...props}
	/>
));
AccordionItem.displayName = AccordionPrimitive.Item.displayName;

const AccordionTrigger = React.forwardRef(
	({ className, children, ...props }, ref) => (
		<AccordionPrimitive.Header className="flex">
			<AccordionPrimitive.Trigger
				ref={ref}
				className={cn(
					"flex flex-1 items-center justify-between gap-3 px-4 py-3 text-left text-sm font-semibold transition hover:text-foreground/80 [&[data-state=open]>svg]:rotate-180",
					className,
				)}
				{...props}
			>
				{children}
				<svg
					aria-hidden="true"
					viewBox="0 0 24 24"
					className="h-4 w-4 transition-transform"
					fill="none"
					stroke="currentColor"
					strokeWidth="2"
					strokeLinecap="round"
					strokeLinejoin="round"
				>
					<polyline points="6 9 12 15 18 9" />
				</svg>
			</AccordionPrimitive.Trigger>
		</AccordionPrimitive.Header>
	),
);
AccordionTrigger.displayName = AccordionPrimitive.Trigger.displayName;

const AccordionContent = React.forwardRef(
	({ className, children, ...props }, ref) => (
		<AccordionPrimitive.Content
			ref={ref}
			className={cn(
				"overflow-hidden data-[state=open]:animate-accordion-down data-[state=closed]:animate-accordion-up motion-reduce:animate-none",
				className,
			)}
			{...props}
		>
			<div className="px-4 pb-4 pt-1">{children}</div>
		</AccordionPrimitive.Content>
	),
);
AccordionContent.displayName = AccordionPrimitive.Content.displayName;

export { Accordion, AccordionItem, AccordionTrigger, AccordionContent };
