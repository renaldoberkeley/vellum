import { describe, expect, it } from "vitest";

import { parseSseMessageEvent } from "./api";

describe("SSE payload parsing", () => {
  it("parses output_truncated error payloads with machine-readable code", () => {
    const payload = parseSseMessageEvent(
      'data: {"type":"error","code":"output_truncated","detail":"incomplete"}',
    );

    expect(payload).not.toBeNull();
    expect(payload?.type).toBe("error");
    expect(payload?.code).toBe("output_truncated");
    expect(payload?.detail).toBe("incomplete");
  });

  it("parses done payload metadata", () => {
    const payload = parseSseMessageEvent(
      'data: {"type":"done","document_id":4,"base_version":3,"truncated":false}',
    );

    expect(payload).not.toBeNull();
    expect(payload?.type).toBe("done");
    expect(payload?.document_id).toBe(4);
    expect(payload?.base_version).toBe(3);
  });
});
