"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { createAnalysis, ApiError } from "@/lib/api";
import { PresentationDropzone } from "./PresentationDropzone";
import { RepositoryInput } from "./RepositoryInput";

export function ProjectUploadForm() {
  const router = useRouter();
  const [presentation, setPresentation] = useState<File | null>(null);
  const [repository, setRepository] = useState("");
  const [branch, setBranch] = useState("");
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!presentation || !repository.trim()) return;
    setSubmitting(true);
    setError(null);
    try {
      const { analysis_id } = await createAnalysis(presentation, repository, branch);
      router.push(`/analysis/${analysis_id}`);
    } catch (err) {
      setError(err instanceof ApiError ? { message: err.message, hint: err.hint } : {
        message: "Не удалось отправить запрос.", hint: String(err),
      });
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 rounded-xl border border-border bg-card p-6">
      <div>
        <h2 className="text-sm font-semibold mb-2">Презентация</h2>
        <PresentationDropzone file={presentation} onChange={setPresentation} />
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-2">Репозиторий</h2>
        <RepositoryInput
          repository={repository}
          onRepositoryChange={setRepository}
          branch={branch}
          onBranchChange={setBranch}
        />
      </div>
      {error && (
        <div className="rounded-lg bg-destructive/10 text-destructive text-sm p-3 whitespace-pre-wrap">
          {error.message}
          {error.hint ? `\n${error.hint}` : ""}
        </div>
      )}
      <Button type="submit" disabled={!presentation || !repository.trim() || submitting} className="self-start">
        {submitting ? "Отправляем…" : "Начать анализ"}
      </Button>
    </form>
  );
}
