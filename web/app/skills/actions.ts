"use server";

import { revalidatePath } from "next/cache";
import { sb } from "@/lib/db";

export async function saveSkill(formData: FormData) {
  const id = String(formData.get("id") || "");
  const row = {
    name: String(formData.get("name") || "").trim(),
    description: String(formData.get("description") || "").trim(),
    content_md: String(formData.get("content_md") || ""),
    enabled: formData.get("enabled") === "on",
    updated_at: new Date().toISOString(),
  };
  if (!row.name || !row.description) return;
  const db = sb();
  if (id) {
    await db.from("skills").update(row).eq("id", id);
  } else {
    await db.from("skills").insert(row);
  }
  revalidatePath("/skills");
}

export async function deleteSkill(formData: FormData) {
  const id = String(formData.get("id") || "");
  if (id) await sb().from("skills").delete().eq("id", id);
  revalidatePath("/skills");
}

export async function toggleSkill(formData: FormData) {
  const id = String(formData.get("id") || "");
  const enabled = formData.get("to") === "true";
  if (id) await sb().from("skills").update({ enabled }).eq("id", id);
  revalidatePath("/skills");
}
