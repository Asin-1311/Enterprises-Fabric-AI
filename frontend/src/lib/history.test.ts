import { groupHistory } from "./history";
import type { HistoryItem } from "../types";

const item = (query_id: number, asked_at: string): HistoryItem =>
  ({ query_id, question: `q${query_id}`, status: "answered", asked_at });

test("groups newest-first items by local day, leaving out empty groups", () => {
  const now = new Date(2026, 10, 17, 15, 0); // 17 Nov 2026, 15:00 local time
  const local = (d: number, h = 10) => new Date(2026, 10, d, h, 0).toISOString();
  const groups = groupHistory([
    item(6, local(17, 9)), item(5, local(17, 0)), // today
    item(4, local(16, 23)),                       // yesterday
    item(3, local(11)),                           // 6 days ago
    item(2, local(1)),                            // 16 days ago
    item(1, new Date(2026, 5, 1).toISOString()), // June
  ], now);
  expect(groups.map((g) => [g.label, g.items.map((i) => i.query_id)])).toEqual([
    ["Today", [6, 5]], ["Yesterday", [4]], ["Previous 7 days", [3]],
    ["Previous 30 days", [2]], ["Older", [1]],
  ]);
  expect(groupHistory([], now)).toEqual([]);
});
