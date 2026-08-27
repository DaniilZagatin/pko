"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/utils";

export interface PresentationDropzoneProps {
  file: File | null;
  onChange: (file: File | null) => void;
}

// Обычный <input type="file"> + drag-события — по объёму задачи отдельная
// библиотека (react-dropzone) не нужна.
export function PresentationDropzone({ file, onChange }: PresentationDropzoneProps) {
  const inputId = useId();
  const [isDragOver, setIsDragOver] = useState(false);

  return (
    <div>
      <label
        htmlFor={inputId}
        onDragOver={(e) => {
          e.preventDefault();
          setIsDragOver(true);
        }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setIsDragOver(false);
          const dropped = e.dataTransfer.files[0];
          if (dropped) onChange(dropped);
        }}
        className={cn(
          "flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-8 text-center text-sm cursor-pointer transition-colors",
          isDragOver ? "border-primary bg-accent" : "border-border hover:bg-muted"
        )}
      >
        {file ? (
          <span className="font-medium text-foreground">{file.name}</span>
        ) : (
          <>
            <span className="text-foreground">Перетащите презентацию сюда</span>
            <span className="text-muted-foreground text-xs">.pptx или выберите файл</span>
          </>
        )}
      </label>
      <input
        id={inputId}
        type="file"
        accept=".pptx"
        className="sr-only"
        onChange={(e) => onChange(e.target.files?.[0] ?? null)}
      />
    </div>
  );
}
