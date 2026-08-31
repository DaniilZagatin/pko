import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { ProductView } from "@/components/product/ProductView";

export default async function ProductPage(props: PageProps<"/products/[id]">) {
  const { id } = await props.params;
  return (
    <div className="flex-1 px-4 py-10">
      <div className="mx-auto flex w-full max-w-4xl flex-col gap-6">
        <Link
          href="/"
          className="inline-flex w-fit items-center gap-1.5 text-sm text-muted-foreground transition-colors hover:text-foreground"
        >
          <ArrowLeft className="size-4" aria-hidden="true" />
          Новый анализ
        </Link>
        <ProductView key={id} productId={id} />
      </div>
    </div>
  );
}
