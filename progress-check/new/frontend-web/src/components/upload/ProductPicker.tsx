"use client";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useProducts } from "@/hooks/useProducts";
import type { ProductSelection } from "@/lib/types";

const NEW_PRODUCT_VALUE = "__new__";

export interface ProductPickerProps {
  selection: ProductSelection;
  onChange: (selection: ProductSelection) => void;
}

// Продукт выбирается пользователем явно, не матчится автоматически по
// репозиторию (см. lib/types.ts::ProductSelection) — здесь только выбор из
// уже существующих или создание нового; сам продукт создаётся на сервере
// только при отправке формы (ProjectUploadForm.tsx), не здесь.
export function ProductPicker({ selection, onChange }: ProductPickerProps) {
  const { data: products = [], isError: loadFailed } = useProducts();

  const selectValue =
    selection.mode === "existing" ? selection.productId
    : selection.mode === "new" ? NEW_PRODUCT_VALUE
    : "";

  return (
    <div className="flex flex-col gap-2">
      <Label htmlFor="product">Продукт</Label>
      <select
        id="product"
        value={selectValue}
        onChange={(e) => {
          const value = e.target.value;
          if (value === NEW_PRODUCT_VALUE) onChange({ mode: "new", name: "" });
          else if (value === "") onChange({ mode: "none" });
          else onChange({ mode: "existing", productId: value });
        }}
        className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none transition-colors focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50"
      >
        <option value="">Без сохранения в историю</option>
        {products.map((product) => (
          <option key={product.id} value={product.id}>
            {product.name}
          </option>
        ))}
        <option value={NEW_PRODUCT_VALUE}>+ Новый продукт…</option>
      </select>
      {selection.mode === "new" && (
        <Input
          autoFocus
          placeholder="Название продукта"
          value={selection.name}
          onChange={(e) => onChange({ mode: "new", name: e.target.value })}
        />
      )}
      {loadFailed && (
        <p className="text-xs text-destructive">Не удалось загрузить список продуктов.</p>
      )}
      <p className="text-xs text-muted-foreground">
        Привяжите проверку к продукту, чтобы позже сравнить прогресс с предыдущей.
      </p>
    </div>
  );
}
