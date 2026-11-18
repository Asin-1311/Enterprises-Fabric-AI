// Groups the sidebar History by when each question was asked (in the viewer's local time).

import type { HistoryItem } from "../types";

export interface HistoryGroup {
  label: string;
  items: HistoryItem[];
}

const DAY = 24 * 60 * 60 * 1000;

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();
}

/** "Today", "Yesterday", "Previous 7 days", "Previous 30 days", "Older" (empty groups left out). */
export function groupHistory(items: HistoryItem[], now: Date = new Date()): HistoryGroup[] {
  const today = startOfDay(now);
  const labelFor = (askedAt: string): string => {
    const day = startOfDay(new Date(askedAt));
    if (day >= today) return "Today";
    if (day >= today - DAY) return "Yesterday";
    if (day >= today - 7 * DAY) return "Previous 7 days";
    if (day >= today - 30 * DAY) return "Previous 30 days";
    return "Older";
  };
  const groups: HistoryGroup[] = [];
  for (const item of items) {  // items arrive newest first, so groups stay in order
    const label = labelFor(item.asked_at);
    const last = groups[groups.length - 1];
    if (last && last.label === label) last.items.push(item);
    else groups.push({ label, items: [item] });
  }
  return groups;
}
