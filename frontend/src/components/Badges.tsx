import * as Tooltip from "@radix-ui/react-tooltip";
import { Gauge, History, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";

/** A small badge that explains itself on hover or keyboard focus (Radix Tooltip). */
function Badge({ tone, icon, label, explanation }: {
  tone: string;
  icon: ReactNode;
  label: string;
  explanation: string;
}) {
  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>
        <span tabIndex={0}
          className={`inline-flex cursor-help items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${tone}`}>
          {icon} {label}
        </span>
      </Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content sideOffset={6}
          className="z-50 max-w-xs rounded-md bg-slate-900 px-3 py-2 text-xs leading-snug text-white shadow-lg">
          {explanation}
          <Tooltip.Arrow className="fill-slate-900" />
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}

export function ReducedCheckingBadge() {
  return (
    <Badge tone="bg-orange-100 text-orange-800" icon={<Gauge size={13} aria-hidden />}
      label="Reduced checking"
      explanation="The system was under load, so this low-risk answer was not verified claim by claim and has no confidence score. Check the cited sources before relying on it." />
  );
}

export function SupersededBadge() {
  return (
    <Badge tone="bg-amber-100 text-amber-800" icon={<History size={13} aria-hidden />}
      label="Superseded policy involved: current version cited"
      explanation="An older version of a cited policy or circular exists. The answer relies on the current version and names both effective dates." />
  );
}

/** Marks a claim the verifier accepted but the NLI model found contradicted by its source. */
export function CheckThisPoint() {
  return (
    <Badge tone="bg-amber-50 text-amber-800 ring-1 ring-amber-200"
      icon={<TriangleAlert size={12} aria-hidden />} label="check this point"
      explanation="Two independent checks disagreed about this statement: read the cited source before relying on it." />
  );
}
