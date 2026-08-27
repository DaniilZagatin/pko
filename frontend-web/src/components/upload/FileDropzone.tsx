"use client";

import { useId, useState } from "react";
import { cn } from "@/lib/utils";

export interface FileDropzoneProps {
  files: File[];
  onChange: (files: File[]) => void;
  accept?: string;
  multiple?: boolean;
  label: string;
  hint: string;
}

// Обычный <input type="file"> + drag-события — по объёму задачи отдельная
// библиотека (react-dropzone) не нужна. Один компонент на все три случая
// (презентация, ZIP, отдельные файлы проекта) — не три копии одного и того
// же дропзона с разницей только в accept/multiple.
export function FileDropzone({ files, onChange, accept, multiple, label, hint }: FileDropzoneProps) {
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
          const dropped = Array.from(e.dataTransfer.files);
          if (dropped.length) onChange(multiple ? dropped : [dropped[0]]);
        }}
        className={cn(
          "flex flex-col items-center justify-center gap-1 rounded-lg border border-dashed px-4 py-8 text-center text-sm cursor-pointer transition-colors",
          isDragOver ? "border-primary bg-accent" : "border-border hover:bg-muted"
        )}
      >
        {files.length > 0 ? (
          multiple ? (
            <span className="font-medium text-foreground">
              {files.length === 1 ? files[0].name : `Выбрано файлов: ${files.length}`}
            </span>
          ) : (
            <span className="font-medium text-foreground">{files[0].name}</span>
          )
        ) : (
          <>
            <span className="text-foreground">{label}</span>
            <span className="text-muted-foreground text-xs">{hint}</span>
          </>
        )}
      </label>
      <input
        id={inputId}
        type="file"
        accept={accept}
        multiple={multiple}
        className="sr-only"
        onChange={(e) => onChange(Array.from(e.target.files ?? []))}
      />
    </div>
  );
}
