"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { Button } from "@/components/ui/button";
import { createAnalysis, createProduct, ApiError } from "@/lib/api";
import type { ProductSelection } from "@/lib/types";
import { FileDropzone } from "./FileDropzone";
import { ProductPicker } from "./ProductPicker";
import { RepositoryInput } from "./RepositoryInput";

// Репозиторий и файлы проекта — два независимых необязательных источника
// evidence, не выбор одного из вариантов: можно указать репозиторий, можно
// загрузить файлы (в том числе ZIP — распаковывается на бэкенде), можно и
// то, и другое сразу — тогда файлы дополняют репозиторий, а не подменяют его
// (backend/pko/web/analyses.py::create_analysis). Оба можно оставить
// пустыми — это не ошибка: агент получает пустой снимок материалов и сам
// решает по каждому пункту плана, что писать в вердикт.
export function ProjectUploadForm() {
  const router = useRouter();
  const [presentation, setPresentation] = useState<File[]>([]);
  const [repository, setRepository] = useState("");
  const [branch, setBranch] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [productSelection, setProductSelection] = useState<ProductSelection>({ mode: "none" });
  const [error, setError] = useState<{ message: string; hint: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const canSubmit =
    presentation.length > 0 && !submitting &&
    (productSelection.mode !== "new" || productSelection.name.trim().length > 0);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!canSubmit) return;
    setSubmitting(true);
    setError(null);
    try {
      let productId = "";
      if (productSelection.mode === "existing") {
        productId = productSelection.productId;
      } else if (productSelection.mode === "new") {
        productId = (await createProduct(productSelection.name.trim())).id;
      }
      const { analysis_id } = await createAnalysis(
        presentation[0], repository, branch, files, productId
      );
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
        <FileDropzone
          files={presentation}
          onChange={setPresentation}
          accept=".pptx"
          label="Перетащите презентацию сюда"
          hint=".pptx или выберите файл"
        />
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-2">
          Репозиторий <span className="font-normal text-muted-foreground">(необязательно)</span>
        </h2>
        <RepositoryInput
          repository={repository}
          onRepositoryChange={setRepository}
          branch={branch}
          onBranchChange={setBranch}
        />
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-2">
          Файлы проекта <span className="font-normal text-muted-foreground">(необязательно)</span>
        </h2>
        <FileDropzone
          files={files}
          onChange={setFiles}
          multiple
          label="Перетащите файлы сюда"
          hint="код, ZIP-архив, метрики, отчёты — что угодно, чем можно подтвердить готовность"
        />
      </div>
      <div>
        <h2 className="text-sm font-semibold mb-2">
          Продукт <span className="font-normal text-muted-foreground">(необязательно)</span>
        </h2>
        <ProductPicker selection={productSelection} onChange={setProductSelection} />
      </div>
      {error && (
        <div className="rounded-lg bg-destructive/10 text-destructive text-sm p-3 whitespace-pre-wrap">
          {error.message}
          {error.hint ? `\n${error.hint}` : ""}
        </div>
      )}
      <Button type="submit" disabled={!canSubmit} className="self-start">
        {submitting ? "Отправляем…" : "Начать анализ"}
      </Button>
    </form>
  );
}
