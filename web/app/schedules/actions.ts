"use server";

import { revalidatePath } from "next/cache";
import { sb } from "@/lib/db";

export async function saveSchedule(formData: FormData): Promise<void> {
  const id = String(formData.get("id") || "");
  const row = {
    name: String(formData.get("name") || "").trim(),
    cron: String(formData.get("cron") || "").trim(),
    prompt: String(formData.get("prompt") || ""),
    channel: String(formData.get("channel") || "lark"),
    chat_id: String(formData.get("chat_id") || "").trim(),
    enabled: formData.get("enabled") === "on",
  };
  if (!row.name || !row.cron || !row.prompt) return;
  const db = sb();
  if (id) await db.from("scheduled_tasks").update(row).eq("id", id);
  else await db.from("scheduled_tasks").insert(row);
  revalidatePath("/schedules");
}

export async function deleteSchedule(formData: FormData): Promise<void> {
  const id = String(formData.get("id") || "");
  if (id) await sb().from("scheduled_tasks").delete().eq("id", id);
  revalidatePath("/schedules");
}

export async function toggleSchedule(formData: FormData): Promise<void> {
  const id = String(formData.get("id") || "");
  const enabled = formData.get("to") === "true";
  if (id) await sb().from("scheduled_tasks").update({ enabled }).eq("id", id);
  revalidatePath("/schedules");
}
