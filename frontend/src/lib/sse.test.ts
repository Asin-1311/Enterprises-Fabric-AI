import { SSEParser } from "./sse";

test("parses complete events and ignores comments", () => {
  const p = new SSEParser();
  const out = p.push('event: progress\ndata: {"step":"retrieving"}\n\n: ping\n\nevent: token\ndata: {"text":"Hi "}\n\n');
  expect(out).toEqual([
    { event: "progress", data: '{"step":"retrieving"}' },
    { event: "token", data: '{"text":"Hi "}' },
  ]);
});

test("keeps events split across network chunks", () => {
  const p = new SSEParser();
  expect(p.push("event: fin")).toEqual([]);
  expect(p.push('al\r\ndata: {"status":')).toEqual([]);
  expect(p.push('"answered"}\r\n\r\n')).toEqual([{ event: "final", data: '{"status":"answered"}' }]);
});

test("flush returns a last event without a trailing blank line", () => {
  const p = new SSEParser();
  expect(p.push('event: error\ndata: {"message":"x"}')).toEqual([]);
  expect(p.flush()).toEqual([{ event: "error", data: '{"message":"x"}' }]);
  expect(p.flush()).toEqual([]);
});
