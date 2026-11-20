import { FOLLOW_UP_MS, REPHRASE_MS, SignalSender, signalForNextQuestion, similarity } from "./signals";

const PREVIOUS = { answerId: 7, question: "Within how many days must the final audit file be assembled?", answeredAt: 1_000_000 };

test("a reworded question soon after is a rephrase; another question is a follow-up", () => {
  const soon = PREVIOUS.answeredAt + 30_000;
  expect(signalForNextQuestion(PREVIOUS, "In how many days must we assemble the final audit file?", soon))
    .toEqual({ answerId: 7, kind: "rephrase" });
  expect(signalForNextQuestion(PREVIOUS, "Which NPA checks are on the bank checklist?", soon))
    .toEqual({ answerId: 7, kind: "follow_up" });
});

test("timing: a similar question after the rephrase window is a follow-up; after 5 minutes, nothing", () => {
  const similar = "In how many days must we assemble the final audit file?";
  expect(signalForNextQuestion(PREVIOUS, similar, PREVIOUS.answeredAt + REPHRASE_MS + 1)?.kind).toBe("follow_up");
  expect(signalForNextQuestion(PREVIOUS, similar, PREVIOUS.answeredAt + FOLLOW_UP_MS + 1)).toBeNull();
  expect(signalForNextQuestion(null, similar, PREVIOUS.answeredAt)).toBeNull();
});

test("similarity is the share of shared words", () => {
  expect(similarity("final audit file", "Final AUDIT file?")).toBe(1);
  expect(similarity("final audit file", "the final audit file")).toBe(0.75);
  expect(similarity("final audit file", "bank checklist")).toBe(0);
  expect(similarity("", "anything")).toBe(0);
});

test("each kind is sent once per answer", () => {
  const post = vi.fn(async () => undefined);
  const sender = new SignalSender(post);
  sender.send(7, "citation_click");
  sender.send(7, "citation_click");
  sender.send(7, "copy");
  sender.send(8, "citation_click");
  expect(post.mock.calls).toEqual([[7, "citation_click"], [7, "copy"], [8, "citation_click"]]);
});
