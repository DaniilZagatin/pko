import { AnalysisView } from "@/components/analysis/AnalysisView";

export default async function AnalysisPage(props: PageProps<"/analysis/[id]">) {
  const { id } = await props.params;
  return (
    <div className="flex-1 px-4 py-10">
      <div className="mx-auto w-full max-w-4xl">
        <AnalysisView key={id} analysisId={id} />
      </div>
    </div>
  );
}
