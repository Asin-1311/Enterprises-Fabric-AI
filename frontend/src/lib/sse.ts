// Incremental parser for Server-Sent Events.
//
// The browser's EventSource only supports GET, but POST /api/v1/query needs a POST body, so
// the response stream is read with fetch() and parsed here. Network chunks can end in the
// middle of a line or an event, so the parser keeps the unfinished part in a buffer.

export interface SSEMessage {
  event: string;
  data: string;
}

export class SSEParser {
  private buffer = "";

  /** Feed a decoded text chunk; returns every event completed by it. */
  push(chunk: string): SSEMessage[] {
    this.buffer += chunk.replace(/\r\n/g, "\n");
    const out: SSEMessage[] = [];
    let boundary = this.buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const block = this.buffer.slice(0, boundary);
      this.buffer = this.buffer.slice(boundary + 2);
      const message = parseBlock(block);
      if (message) out.push(message);
      boundary = this.buffer.indexOf("\n\n");
    }
    return out;
  }

  /** Call at end of stream: returns a final event that had no trailing blank line. */
  flush(): SSEMessage[] {
    const message = parseBlock(this.buffer);
    this.buffer = "";
    return message ? [message] : [];
  }
}

function parseBlock(block: string): SSEMessage | null {
  let event = "message";
  const data: string[] = [];
  for (const line of block.split("\n")) {
    if (!line || line.startsWith(":")) continue; // blank or comment (e.g. ": ping")
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    if (field === "event") event = value;
    else if (field === "data") data.push(value);
  }
  return data.length ? { event, data: data.join("\n") } : null;
}
