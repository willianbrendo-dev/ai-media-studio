export async function uploadMedia(file: File): Promise<{ url: string }> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch("/app/higgsfield/api/upload", {
    method: "POST",
    body: form,
  });
  if (!res.ok) {
    throw new Error("Failed to upload media");
  }
  const data = (await res.json()) as { url?: unknown };
  if (typeof data.url !== "string" || !data.url) {
    throw new Error("Upload did not return a URL");
  }
  return { url: data.url };
}
