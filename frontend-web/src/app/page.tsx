import { ProjectUploadForm } from "@/components/upload/ProjectUploadForm";

export default function Home() {
  return (
    <div className="flex flex-1 items-center justify-center px-4 py-16">
      <div className="w-full max-w-xl flex flex-col gap-6">
        <div className="text-center">
          <h1 className="text-2xl font-bold">Оценка готовности проекта</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            Загрузите презентацию и подключите репозиторий
          </p>
        </div>
        <ProjectUploadForm />
      </div>
    </div>
  );
}
