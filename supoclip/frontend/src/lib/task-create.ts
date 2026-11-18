const DUPLICATE_SOURCE_CODE = "DUPLICATE_SOURCE";

export function taskPathFromCreate(body: {
  task_id?: string;
  duplicate?: boolean;
  code?: string;
}): string {
  const id = body.task_id ?? "";
  const prefix = typeof window !== "undefined" && window.location.pathname.startsWith("/app/supoclip") ? "/app/supoclip" : "";
  if (body.duplicate || body.code === DUPLICATE_SOURCE_CODE) {
    return `${prefix}/tasks/${id}?already=1`;
  }
  return `${prefix}/tasks/${id}`;
}
