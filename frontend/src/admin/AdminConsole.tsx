import * as Tabs from "@radix-ui/react-tabs";
import { ArrowLeft, Settings } from "lucide-react";
import { Link } from "react-router";
import type { User } from "../types";
import { ClassificationPanel } from "./ClassificationPanel";
import { PerformancePanel } from "./PerformancePanel";
import { SourcesPanel } from "./SourcesPanel";
import { TracesPanel } from "./TracesPanel";
import { PromptsPanel, RoutingPanel } from "./VersionsPanel";

const TABS = [
  { value: "sources", label: "Sources", panel: <SourcesPanel /> },
  { value: "traces", label: "Questions & traces", panel: <TracesPanel /> },
  { value: "prompts", label: "Prompts", panel: <PromptsPanel /> },
  { value: "routing", label: "Routing policies", panel: <RoutingPanel /> },
  { value: "classification", label: "Classification", panel: <ClassificationPanel /> },
  { value: "performance", label: "Performance", panel: <PerformancePanel /> },
];

const TAB = "rounded-md px-3 py-1.5 text-sm font-medium text-slate-600 hover:bg-slate-100 data-[state=active]:bg-indigo-50 data-[state=active]:text-indigo-800";

/** The admin console (spec 7.10): only for users with the admin flag. The flag lets them
 * manage the system; it does not let them see more documents. */
export function AdminConsole({ user }: { user: User }) {
  if (!user.is_admin) {
    return (
      <main className="grid h-screen place-items-center bg-slate-50 p-6 text-center">
        <div>
          <p role="alert" className="text-sm text-slate-700">The admin console is for administrators only.</p>
          <Link to="/" className="mt-3 inline-block text-sm text-indigo-700 underline">Back to questions</Link>
        </div>
      </main>
    );
  }
  return (
    <main className="min-h-screen bg-slate-50 text-slate-900">
      <header className="flex items-center gap-3 border-b border-slate-200 bg-white px-6 py-4">
        <Settings size={20} className="text-indigo-600" aria-hidden />
        <h1 className="text-lg font-semibold">Admin console</h1>
        <span className="text-xs text-slate-500">Signed in as {user.name}. The admin flag grants no extra document access.</span>
        <Link to="/" className="ml-auto inline-flex items-center gap-1 text-sm text-indigo-700 hover:underline">
          <ArrowLeft size={14} aria-hidden /> Back to questions
        </Link>
      </header>
      <Tabs.Root defaultValue="sources" className="mx-auto max-w-6xl px-6 py-5">
        <Tabs.List aria-label="Admin sections" className="mb-5 flex flex-wrap gap-1">
          {TABS.map((t) => <Tabs.Trigger key={t.value} value={t.value} className={TAB}>{t.label}</Tabs.Trigger>)}
        </Tabs.List>
        {TABS.map((t) => <Tabs.Content key={t.value} value={t.value}>{t.panel}</Tabs.Content>)}
      </Tabs.Root>
    </main>
  );
}
