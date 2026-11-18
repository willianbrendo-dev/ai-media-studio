"use server";

import { cookies } from "next/headers";

import { getModel, parseSettings } from "./catalog";
import type { GenerationPlane } from "./catalog/types";
import {
  MissingCredentialsError,
  PLATFORM_KEY_COOKIE,
  PLATFORM_KEY_COOKIE_OPTIONS,
  decodeCredentials,
  encodeCredentials,
  parseCredentialInput,
} from "./credentials";
import { getLocalGenerationStatus, submitLocalGeneration } from "./local-engine";
import { createPlatformClient } from "./platform";
import type { StatusResult } from "./platform";
import { toPlatform } from "./to-platform";

export async function savePlatformCredentials(data: unknown) {
  const { apiKey } = parseCredentialInput(data);
  const jar = await cookies();
  jar.set(PLATFORM_KEY_COOKIE, encodeCredentials(apiKey), PLATFORM_KEY_COOKIE_OPTIONS);
}

export async function clearPlatformCredentials() {
  const jar = await cookies();
  jar.set(PLATFORM_KEY_COOKIE, "", { ...PLATFORM_KEY_COOKIE_OPTIONS, maxAge: 0 });
}

export async function hasPlatformCredentials() {
  if ((await readStoredCredentials()) !== null) return true;
  // Built-in VPS Studio Engine works out of the box when external HF_API_BASE_URL is not configured.
  return !process.env.HF_API_BASE_URL?.trim();
}

export async function submitGeneration(plane: GenerationPlane) {
  const model = getModel(plane.model);
  const parsed: GenerationPlane = {
    ...plane,
    settings: parseSettings(model, plane.settings),
  };
  const baseUrl = process.env.HF_API_BASE_URL?.trim();
  const stored = await readStoredCredentials();
  if (baseUrl && stored && !stored.apiKey.startsWith("vps-cascade:") && !stored.apiKey.startsWith("demo-")) {
    const { path, body } = toPlatform(parsed);
    return createPlatformClient({ ...stored, baseUrl }).submit(path, body);
  }
  return submitLocalGeneration(parsed, model.surface, model.label);
}

export async function getGenerationStatuses(data: unknown): Promise<StatusResult[]> {
  const requestIds = parseRequestIds(data);
  const localIds = requestIds.filter((id) => id.startsWith("ohf_"));
  const remoteIds = requestIds.filter((id) => !id.startsWith("ohf_"));

  const localResults: StatusResult[] = await Promise.all(
    localIds.map(async (requestId): Promise<StatusResult> => {
      try {
        return { requestId, status: await getLocalGenerationStatus(requestId) };
      } catch (caught) {
        return { requestId, error: caught instanceof Error ? caught.message : String(caught) };
      }
    }),
  );

  if (remoteIds.length === 0) return localResults;

  const client = createPlatformClient(await readCredentials());
  const remoteResults: StatusResult[] = await Promise.all(
    remoteIds.map(async (requestId): Promise<StatusResult> => {
      try {
        return { requestId, status: await client.status(requestId) };
      } catch (caught) {
        return { requestId, error: caught instanceof Error ? caught.message : String(caught) };
      }
    }),
  );

  return [...localResults, ...remoteResults];
}

async function readStoredCredentials() {
  const jar = await cookies();
  return decodeCredentials(jar.get(PLATFORM_KEY_COOKIE)?.value);
}

async function readCredentials() {
  const stored = await readStoredCredentials();
  if (!stored) throw new MissingCredentialsError();
  const baseUrl = process.env.HF_API_BASE_URL;
  if (!baseUrl) throw new Error("Missing HF_API_BASE_URL");
  return { ...stored, baseUrl };
}

function parseRequestIds(data: unknown): string[] {
  const payload = asObject(data, "Invalid status payload");
  const requestIds = payload.requestIds;
  if (!Array.isArray(requestIds) || requestIds.length === 0) {
    throw new Error("Invalid request ids");
  }
  return requestIds.map((requestId) => {
    if (typeof requestId !== "string" || !requestId) throw new Error("Invalid request id");
    return requestId;
  });
}

function asObject(data: unknown, message: string): Record<string, unknown> {
  if (data === null || typeof data !== "object" || Array.isArray(data)) throw new Error(message);
  return data as Record<string, unknown>;
}
