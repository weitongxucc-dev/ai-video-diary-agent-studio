import { chooseSelectValue, inspectSelectOptions } from "./select-interaction";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { expect, it, vi } from "vitest";
import { useRef, useState } from "react";
import {
  MaterialBusinessObjects,
  type BusinessObject,
  type BusinessObjectKind,
} from "@/components/MaterialBusinessObjects";

const seedObjects: BusinessObject[] = [
  { id: "obj-product", kind: "product", name: "曜核一体机" },
  { id: "obj-dish", kind: "dish", name: "红烧肉" },
  { id: "obj-person", kind: "person", name: "老张" },
];

function Harness({
  assetId,
  objects: initialObjects = seedObjects,
  initialIds = [],
  pending = null,
  error = null,
  loaded = true,
  readError = null,
  onRetryRead,
  onCreate: onCreateOverride,
  onSave: onSaveOverride,
}: {
  assetId: string | null;
  objects?: BusinessObject[];
  initialIds?: string[];
  pending?: "create" | "save" | null;
  error?: string | null;
  loaded?: boolean;
  readError?: string | null;
  onRetryRead?: () => void;
  onCreate?: (kind: BusinessObjectKind, name: string) => Promise<BusinessObject>;
  onSave?: (assetId: string, ids: string[]) => Promise<BusinessObject[]>;
}) {
  const [objects, setObjects] = useState(initialObjects);
  // 真实父页按素材分别保存关联；这里用同样的形状，换素材时读到该素材自己的已保存值。
  const [savedByAsset, setSavedByAsset] = useState<Record<string, string[]>>({});
  const objectsRef = useRef(objects);
  objectsRef.current = objects;
  const ids = assetId === null ? [] : savedByAsset[assetId] ?? initialIds;

  const defaultCreate = async (kind: BusinessObjectKind, name: string) => {
    const created: BusinessObject = { id: `obj-new-${objectsRef.current.length}`, kind, name };
    setObjects(current => [...current, created]);
    return created;
  };

  const defaultSave = async (savedAssetId: string, next: string[]) => {
    const latest = objectsRef.current;
    setSavedByAsset(current => ({ ...current, [savedAssetId]: next }));
    return next
      .map(id => latest.find(object => object.id === id))
      .filter((object): object is BusinessObject => Boolean(object));
  };

  return (
    <MaterialBusinessObjects
      assetId={assetId}
      error={error}
      loaded={loaded}
      objects={objects}
      onRetryRead={onRetryRead}
      readError={readError}
      onCreate={onCreateOverride ?? defaultCreate}
      onSave={onSaveOverride ?? defaultSave}
      pending={pending}
      selectedIds={ids}
    />
  );
}

function checkbox(name: string) {
  return screen.getByRole("checkbox", { name: new RegExp(name) });
}

it("adds a name entry on an empty list, selects it and saves it explicitly", async () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId="asset-1" objects={[]} onSave={onSave} />);
  expect(screen.getByText("当前工作区还没有名称条目，可以在下面新增一个。")).toBeInTheDocument();
  expect(onSave).not.toHaveBeenCalled();

  await chooseSelectValue(screen.getByLabelText("类型"), "dish");
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "红烧肉" } });
  fireEvent.click(screen.getByRole("button", { name: "新增并选中" }));

  await screen.findByRole("checkbox", { name: /红烧肉/ });
  // 新增只是进草稿，没有写素材。
  expect(onSave).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "保存关联到当前素材" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][0]).toBe("asset-1");
  expect(onSave.mock.calls[0][1]).toEqual(["obj-new-0"]);
});

it("never persists a selection until the explicit save is pressed", async () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId="asset-1" onSave={onSave} />);

  fireEvent.click(checkbox("曜核一体机"));
  expect(checkbox("曜核一体机")).toBeChecked();
  expect(onSave).not.toHaveBeenCalled();

  fireEvent.click(screen.getByRole("button", { name: "保存关联到当前素材" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));
  expect(onSave.mock.calls[0][1]).toEqual(["obj-product"]);
});

it("restores the saved selection on cancel without persisting anything", async () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId="asset-1" initialIds={["obj-product"]} onSave={onSave} />);
  expect(checkbox("曜核一体机")).toBeChecked();

  fireEvent.click(checkbox("红烧肉"));
  expect(checkbox("红烧肉")).toBeChecked();
  fireEvent.click(screen.getByRole("button", { name: "取消" }));

  expect(checkbox("红烧肉")).not.toBeChecked();
  expect(checkbox("曜核一体机")).toBeChecked();
  expect(onSave).not.toHaveBeenCalled();
  expect(screen.getByRole("button", { name: "保存关联到当前素材" })).toBeDisabled();
});

it("saves an explicit empty selection to clear the existing association", async () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId="asset-1" initialIds={["obj-product"]} onSave={onSave} />);

  fireEvent.click(checkbox("曜核一体机"));
  fireEvent.click(screen.getByRole("button", { name: "保存关联到当前素材" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledWith("asset-1", []));
});

it("keeps the draft and stays operable when the save fails, then succeeds on retry", async () => {
  const onSave = vi
    .fn()
    .mockRejectedValueOnce(new Error("外接盘暂不可写"))
    .mockResolvedValueOnce([{ id: "obj-dish", kind: "dish", name: "红烧肉" }]);
  render(<Harness assetId="asset-1" onSave={onSave} />);

  fireEvent.click(checkbox("红烧肉"));
  fireEvent.click(screen.getByRole("button", { name: "保存关联到当前素材" }));

  await screen.findByText(/外接盘暂不可写/);
  expect(screen.getByText(/已保留当前选择/)).toBeInTheDocument();
  // 失败不伪装成功：没有“已保存”提示，草稿仍在。
  expect(screen.queryByText(/已保存 \d+ 条名称关联/)).not.toBeInTheDocument();
  expect(checkbox("红烧肉")).toBeChecked();
  const retry = screen.getByRole("button", { name: "保存关联到当前素材" });
  expect(retry).toBeEnabled();
  expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();

  fireEvent.click(retry);
  await screen.findByText("已保存 1 条名称关联。");
  expect(onSave).toHaveBeenCalledTimes(2);
});

it("keeps the draft after a failed create and allows a retry", async () => {
  const onCreate = vi.fn().mockRejectedValue(new Error("名称重复"));
  render(<Harness assetId="asset-1" onCreate={onCreate} />);

  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "红烧肉" } });
  fireEvent.click(screen.getByRole("button", { name: "新增并选中" }));

  await screen.findByText(/名称重复/);
  expect(screen.getByLabelText("名称")).toHaveValue("红烧肉");
  expect(screen.getByRole("button", { name: "新增并选中" })).toBeEnabled();
});

it("blocks duplicate submissions while a save is pending", () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId="asset-1" onSave={onSave} pending="save" />);

  expect(screen.getByRole("button", { name: "保存关联到当前素材" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "新增并选中" })).toBeDisabled();
  expect(checkbox("曜核一体机")).toBeDisabled();
});

it("does not bind a late create response to the material selected afterwards", async () => {
  const create: { release?: (object: BusinessObject) => void } = {};
  const onCreate = vi.fn().mockImplementation(
    () => new Promise<BusinessObject>(resolve => {
      create.release = resolve;
    }),
  );
  const { rerender } = render(<Harness assetId="asset-a" initialIds={["obj-product"]} onCreate={onCreate} />);

  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "半成品" } });
  fireEvent.click(screen.getByRole("button", { name: "新增并选中" }));
  await waitFor(() => expect(onCreate).toHaveBeenCalledTimes(1));

  rerender(<Harness assetId="asset-b" initialIds={["obj-dish"]} onCreate={onCreate} />);
  // 草稿整体属于新素材：输入框清空，选择回到 asset-b 已保存的关联。
  expect(screen.getByLabelText("名称")).toHaveValue("");
  expect(checkbox("红烧肉")).toBeChecked();
  expect(checkbox("曜核一体机")).not.toBeChecked();

  create.release?.({ id: "obj-late", kind: "product", name: "半成品" });
  await waitFor(() => expect(screen.getByLabelText("名称")).toHaveValue(""));

  // 迟到响应没有把新条目写进 asset-b 的草稿，也没有报错。
  expect(screen.queryByRole("checkbox", { name: /半成品/ })).not.toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(checkbox("红烧肉")).toBeChecked();

  // 旧素材的请求结束也不能把 asset-b 锁住。
  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "清汤" } });
  expect(screen.getByRole("button", { name: "新增并选中" })).toBeEnabled();
  expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
});

it("does not report a save result for the previous material", async () => {
  const save: { release?: (objects: BusinessObject[]) => void } = {};
  const onSave = vi.fn().mockImplementation(
    () => new Promise<BusinessObject[]>(resolve => {
      save.release = resolve;
    }),
  );
  const { rerender } = render(<Harness assetId="asset-a" initialIds={["obj-product"]} onSave={onSave} />);

  fireEvent.click(checkbox("红烧肉"));
  fireEvent.click(screen.getByRole("button", { name: "保存关联到当前素材" }));
  await waitFor(() => expect(onSave).toHaveBeenCalledTimes(1));

  rerender(<Harness assetId="asset-b" initialIds={["obj-person"]} onSave={onSave} />);
  save.release?.([{ id: "obj-dish", kind: "dish", name: "红烧肉" }]);
  await waitFor(() => expect(screen.getByRole("button", { name: "取消" })).toBeEnabled());

  expect(screen.queryByText("已保存 1 条名称关联。")).not.toBeInTheDocument();
  expect(checkbox("老张")).toBeChecked();
  expect(checkbox("红烧肉")).not.toBeChecked();
});

it("offers only product, dish and person with plain wording", async () => {
  render(<Harness assetId="asset-1" />);

  await inspectSelectOptions(screen.getByLabelText("类型"),()=>{ const options = screen.getAllByRole("option");
  expect(options.map(option => option.getAttribute("data-app-select-value"))).toEqual(["product", "dish", "person"]);
  expect(options.map(option => option.textContent)).toEqual(["产品", "菜品", "人物"]); });

  expect(screen.getByText("选择或新增产品、菜品、人物名称，保存到当前素材。")).toBeInTheDocument();
  expect(screen.getByText("人物名称用于素材分类。")).toBeInTheDocument();
  // 协议边界由代码保证，不整段放进产品UI。
  expect(screen.queryByText(/口型|出镜形象|人工纠正|导入批次/)).not.toBeInTheDocument();
});

it("disables the whole editor while no material is selected", () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId={null} onSave={onSave} />);

  expect(screen.getByLabelText("类型")).toBeDisabled();
  expect(screen.getByLabelText("名称")).toBeDisabled();
  expect(checkbox("曜核一体机")).toBeDisabled();
  expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
  expect(onSave).not.toHaveBeenCalled();
});

it("does not allow cancel while a create is in flight", () => {
  const { rerender } = render(<Harness assetId="asset-1" initialIds={["obj-dish"]} pending="create" />);

  expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();

  rerender(<Harness assetId="asset-1" initialIds={["obj-dish"]} pending={null} />);
  expect(screen.getByRole("button", { name: "取消" })).toBeEnabled();
});

it("does not carry a failure message over to the next material", async () => {
  const onCreate = vi.fn().mockRejectedValue(new Error("名称重复"));
  const { rerender } = render(<Harness assetId="asset-a" onCreate={onCreate} />);

  fireEvent.change(screen.getByLabelText("名称"), { target: { value: "红烧肉" } });
  fireEvent.click(screen.getByRole("button", { name: "新增并选中" }));
  await screen.findByText(/名称重复/);

  rerender(<Harness assetId="asset-b" onCreate={onCreate} />);
  expect(screen.queryByText(/名称重复/)).not.toBeInTheDocument();
});

it("surfaces a parent error and does not fake a save", async () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId="asset-1" error="工作区未就绪" onSave={onSave} />);

  expect(screen.getByRole("alert")).toHaveTextContent("工作区未就绪");
  expect(screen.queryByText(/已保存/)).not.toBeInTheDocument();
  expect(onSave).not.toHaveBeenCalled();
});

it("asks for a material before allowing any association work", () => {
  const onSave = vi.fn().mockResolvedValue([]);
  render(<Harness assetId={null} onSave={onSave} />);

  expect(screen.getByText("先选择一条素材，再维护它的名称条目。")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "新增并选中" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存关联到当前素材" })).toBeDisabled();
  expect(onSave).not.toHaveBeenCalled();
});

it("keeps the whole block closed until the associations are known, and offers a real retry on failure", async () => {
  const onRetryRead = vi.fn();
  const { rerender } = render(<Harness assetId="asset-1" initialIds={["obj-dish"]} loaded={false} />);

  // 还没读回来：空选择只是未知，不能当成「没有关联」去保存。
  expect(screen.getByText("正在读取这条素材已保存的名称关联…")).toBeInTheDocument();
  expect(checkbox("红烧肉")).toBeDisabled();
  expect(screen.getByLabelText("名称")).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存关联到当前素材" })).toBeDisabled();
  expect(screen.queryByText("名称关联读取失败。")).not.toBeInTheDocument();

  rerender(<Harness assetId="asset-1" initialIds={["obj-dish"]} loaded={false} readError="名称关联读取失败。" onRetryRead={onRetryRead} />);
  // 读取失败就明确说出来，并给真正的重读入口，而不是只让用户把错误关掉。
  expect(screen.getByText("名称关联读取失败。")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "重新读取" }));
  expect(onRetryRead).toHaveBeenCalledTimes(1);
  expect(checkbox("红烧肉")).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存关联到当前素材" })).toBeDisabled();

  rerender(<Harness assetId="asset-1" initialIds={["obj-dish"]} />);
  await waitFor(() => expect(checkbox("红烧肉")).toBeChecked());
  expect(checkbox("红烧肉")).toBeEnabled();
});
