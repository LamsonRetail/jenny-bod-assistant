"use server";

import { revalidatePath } from "next/cache";
import { sb } from "@/lib/db";

export async function saveConfig(formData: FormData): Promise<void> {
  const key = String(formData.get("key") || "").trim();
  const raw = String(formData.get("value") || "");
  const description = String(formData.get("description") || "");
  if (!key) return;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return; // JSON sai — không lưu
  }
  await sb().from("configs").upsert(
    { key, value, description, updated_at: new Date().toISOString() },
    { onConflict: "key" }
  );
  revalidatePath("/configs");
}

export async function deleteConfig(formData: FormData): Promise<void> {
  const key = String(formData.get("key") || "");
  if (key) await sb().from("configs").delete().eq("key", key);
  revalidatePath("/configs");
}
