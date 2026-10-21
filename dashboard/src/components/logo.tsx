import { cn } from "@/lib/utils";

export function Mark({ className }: { className?: string }): React.ReactElement {
  return (
    <svg viewBox="0 0 32 32" className={cn("shrink-0", className)} aria-hidden="true" fill="none">
      <path
        d="M6.5 7.25 16 25.75 25.5 7.25"
        stroke="currentColor"
        strokeWidth="3.75"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </svg>
  );
}
