import { useState } from "react";
import {
  activatePrompt, activateRoutingPolicy, listPrompts, listRoutingPolicies, replayState,
} from "./adminApi";
import { runAction, useLoad } from "./useLoad";
import { cell, Loading, Notice, Pill, smallButton, Table, when, type Message } from "./ui";

const REPLAY_TONE = { passed: "green", failed: "red", "not required": "slate", none: "amber" } as const;

/** The button for a version that is not active: a rollback if it served before, an
 * activation if its replay passed; otherwise nothing (the server would refuse it). */
function actionFor(v: { is_active: boolean; activated_at: string | null; eval_report: unknown }) {
  if (v.is_active) return null;
  if (replayState(v.eval_report as never) === "passed") return "Activate";
  if (v.activated_at) return "Roll back";
  return null;
}

/** Prompt versions per agent: replay result, activate, roll back. */
export function PromptsPanel() {
  const { data, error, reload } = useLoad(listPrompts);
  const [message, setMessage] = useState<Message>(null);
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <Notice message={message} />
      <Table label="Prompt versions" head={["Agent", "Version", "Created by", "Replay", "Active since", ""]}>
        {data.map((v) => {
          const action = actionFor(v);
          const replay = replayState(v.eval_report);
          return (
            <tr key={v.id}>
              <td className={`${cell} font-medium`}>{v.agent}</td>
              <td className={cell}>v{v.version} {v.is_active && <Pill tone="green">active</Pill>}</td>
              <td className={cell}>{v.created_by}</td>
              <td className={cell}><Pill tone={REPLAY_TONE[replay]}>{replay}</Pill></td>
              <td className={cell}>{v.is_active ? when(v.activated_at) : "—"}</td>
              <td className={`${cell} text-right`}>
                {action && (
                  <button type="button" className={smallButton} aria-label={`${action} ${v.agent} v${v.version}`}
                    onClick={() => void runAction(() => activatePrompt(v.id),
                      `${v.agent} v${v.version} is now active (${action === "Roll back" ? "rolled back" : "activated"}).`,
                      setMessage, reload)}>
                    {action}
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </Table>
    </div>
  );
}

/** Routing-policy versions: replay result, activate, roll back. */
export function RoutingPanel() {
  const { data, error, reload } = useLoad(listRoutingPolicies);
  const [message, setMessage] = useState<Message>(null);
  if (!data) return <Loading error={error} />;
  return (
    <div>
      <Notice message={message} />
      <Table label="Routing policy versions" head={["Version", "Kind", "Created by", "Replay", "Active since", ""]}>
        {data.map((p) => {
          const action = actionFor(p);
          const replay = replayState(p.eval_report);
          return (
            <tr key={p.id}>
              <td className={cell}>v{p.version} {p.is_active && <Pill tone="green">active</Pill>}</td>
              <td className={cell}>{String(p.weights.kind ?? "—")}</td>
              <td className={cell}>{p.created_by}</td>
              <td className={cell}><Pill tone={REPLAY_TONE[replay]}>{replay}</Pill></td>
              <td className={cell}>{p.is_active ? when(p.activated_at) : "—"}</td>
              <td className={`${cell} text-right`}>
                {action && (
                  <button type="button" className={smallButton} aria-label={`${action} routing policy v${p.version}`}
                    onClick={() => void runAction(() => activateRoutingPolicy(p.version),
                      `Routing policy v${p.version} is now active.`, setMessage, reload)}>
                    {action}
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </Table>
    </div>
  );
}
