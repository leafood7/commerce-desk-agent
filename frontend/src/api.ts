export async function api(path: string, token = "", body?: unknown, extra: Record<string,string> = {}) {
  const form = body instanceof FormData;
  const response = await fetch("/api/v1" + path, {
    method: body === undefined ? "GET" : "POST",
    headers: { ...(body !== undefined && !form ? { "Content-Type":"application/json" } : {}),
      ...(token ? { Authorization:"Bearer " + token } : {}), ...extra },
    ...(body !== undefined ? { body: form ? body : JSON.stringify(body) } : {})
  });
  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    const messages: Record<number,string> = {
      401: path === "/auth/login" ? "商家、账号或密码不正确。" : "登录已失效，请重新登录。",
      403: "当前账号没有此项权限。", 404: "未找到当前商家的订单或记录",
      429: path === "/auth/login" ? "登录尝试过多，请稍后再试。" : "当前待处理任务较多，请稍后重试；已有流程仍保留。",
      413: "文件为空或超过 20 MB 限制。", 422: "请检查输入内容与文件格式。"
    };
    throw new Error(messages[response.status] || (typeof error.detail === "string" ?
      error.detail : "服务暂不可用，请稍后再试。"));
  }
  return response.status === 204 ? null : response.json();
}

class StreamAccessError extends Error {}

export async function watchRun(id: string, token: string, onEvent: (event: any) => void, signal?: AbortSignal) {
  let cursor = 0;
  for (let attempt = 0; attempt < 3; attempt++) {
    try {
      const response = await fetch("/api/v1/runs/" + id + "/events", {
        signal,
        headers: { Authorization: "Bearer " + token,
          ...(cursor ? { "Last-Event-ID": String(cursor) } : {}) }
      });
      if (response.status === 401 || response.status === 403) {
        throw new StreamAccessError("登录已失效或无权读取，请重新登录。");
      }
      if (!response.ok || !response.body) throw new Error("无法连接处理进度。");
      const reader = response.body.getReader(), decoder = new TextDecoder();
      let buffer = "";
      try {
        while (true) {
          const next = await reader.read();
          buffer += decoder.decode(next.value, { stream: !next.done });
          buffer = buffer.replace(/\r\n/g, "\n");
          if (buffer.length > 262144) throw new Error("处理进度数据超出限制，请重新打开流程。");
          let end: number;
          while ((end = buffer.indexOf("\n\n")) >= 0) {
            const block = buffer.slice(0, end); buffer = buffer.slice(end + 2);
            if (block.split("\n").some(line => line.trim() === "event: authentication_required")) {
              throw new StreamAccessError("登录已失效，请重新登录。");
            }
            const lines = block.split("\n").filter(row => row.startsWith("data:"))
              .map(row => row.slice(5).replace(/^ /, ""));
            if (!lines.length) continue;
            const event = JSON.parse(lines.join("\n"));
            if (!Number.isSafeInteger(event.id) || event.id < 1 || typeof event.type !== "string") {
              throw new Error("处理进度格式异常，请重新打开流程。");
            }
            if (event.id <= cursor) continue;
            cursor = event.id; onEvent(event);
          }
          if (next.done) break;
        }
      } finally {
        await reader.cancel().catch(() => {});
        reader.releaseLock();
      }
      // An early EOF is a lost subscription, not completion of the business run.
      const current = await api("/runs/" + id, token);
      if (!["queued", "running"].includes(current.status)) return;
      if (attempt === 2) throw new Error("进度连接已中断，流程仍保留，请重新打开核实状态。");
    } catch (error) {
      if (signal?.aborted || error instanceof StreamAccessError || attempt === 2) throw error;
    }
    await new Promise(resolve => setTimeout(resolve, 250 * (attempt + 1)));
  }
}
