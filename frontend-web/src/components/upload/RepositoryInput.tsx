"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

export interface RepositoryInputProps {
  repository: string;
  onRepositoryChange: (value: string) => void;
  branch: string;
  onBranchChange: (value: string) => void;
}

// SSH-ключ здесь сознательно не запрашивается: доступ к репозиторию идёт
// через SSH-agent, уже настроенный на машине с `pko serve`
// (backend/pko/web/app.py), а не через ключ, переданный с этой формы.
export function RepositoryInput({
  repository,
  onRepositoryChange,
  branch,
  onBranchChange,
}: RepositoryInputProps) {
  return (
    <div className="flex flex-col gap-3 sm:flex-row">
      <div className="flex-1 flex flex-col gap-2">
        <Label htmlFor="repository">Репозиторий</Label>
        <Input
          id="repository"
          placeholder="git@host:project/repo.git или локальный путь"
          value={repository}
          onChange={(e) => onRepositoryChange(e.target.value)}
          required
        />
      </div>
      <div className="sm:w-40 flex flex-col gap-2">
        <Label htmlFor="branch">Ветка</Label>
        <Input
          id="branch"
          placeholder="по умолчанию"
          value={branch}
          onChange={(e) => onBranchChange(e.target.value)}
        />
      </div>
    </div>
  );
}
