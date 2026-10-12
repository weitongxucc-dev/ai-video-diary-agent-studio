"use client";

import { AppSelect, AppSelectOption } from "@/components/AppSelect";
import { useEffect, useRef, useState } from "react";
import type { BusinessObjectKind, MaterialBusinessObject } from "@/lib/types";
import styles from "./MaterialLibraryControls.module.css";

// 类型与 lib/types.ts 共用同一份，避免组件和真实接口各自漂移。
export type { BusinessObjectKind };
export type BusinessObject = MaterialBusinessObject;

export type MaterialBusinessObjectsProps = {
  /** 当前素材。切换素材时本组件的草稿会整体重置，迟到响应不会写回已切换的素材。 */
  assetId: string | null;
  /**
   * 当前素材的关联是否已经读成功。读成功之前整块不开放选择/新增/保存：还没读
   * 回来的空数组不等于「这条素材没有关联」，照着保存会把旧关联覆盖掉。
   */
  loaded: boolean;
  /** 读取失败的提示。只有重新读取能把不确定变成确定，所以它配的是重试，不是关闭。 */
  readError?: string | null;
  onRetryRead?: () => void;
  /** 当前工作区已有的名称条目，由父页读取冻结接口后传入。 */
  objects: readonly BusinessObject[];
  /** 该素材当前已保存的名称条目 ID。 */
  selectedIds: readonly string[];
  onCreate: (kind: BusinessObjectKind, name: string) => Promise<BusinessObject>;
  onSave: (assetId: string, businessObjectIds: string[]) => Promise<BusinessObject[]>;
  pending?: "create" | "save" | null;
  error?: string | null;
  onDismissError?: () => void;
};

const kindOptions: ReadonlyArray<{ value: BusinessObjectKind; label: string }> = [
  { value: "product", label: "产品" },
  { value: "dish", label: "菜品" },
  { value: "person", label: "人物" },
];

function kindLabel(kind: BusinessObjectKind) {
  return kindOptions.find(option => option.value === kind)?.label ?? kind;
}

function sameIds(left: readonly string[], right: readonly string[]) {
  if (left.length !== right.length) return false;
  const a = [...left].sort();
  const b = [...right].sort();
  return a.every((value, index) => value === b[index]);
}

function failureMessage(cause: unknown, fallback: string) {
  if (!(cause instanceof Error)) return fallback;
  return cause.message.trim() || fallback;
}

type DraftState = {
  assetId: string | null;
  ids: string[];
  name: string;
  kind: BusinessObjectKind;
  saved: string | null;
};

function draftFor(assetId: string | null, selectedIds: readonly string[]): DraftState {
  return { assetId, ids: [...selectedIds], name: "", kind: "product", saved: null };
}

export function MaterialBusinessObjects({
  assetId,
  loaded,
  readError = null,
  onRetryRead,
  objects,
  selectedIds,
  onCreate,
  onSave,
  pending = null,
  error = null,
  onDismissError,
}: MaterialBusinessObjectsProps) {
  const [draftState, setDraftState] = useState<DraftState>(() => draftFor(assetId, selectedIds));
  const [localBusy, setLocalBusy] = useState<{ assetId: string; action: "create" | "save" } | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const inFlight = useRef(new Set<string>());
  const assetIdRef = useRef(assetId);
  assetIdRef.current = assetId;
  const localBusyRef = useRef(localBusy);
  localBusyRef.current = localBusy;
  const lastPropIds = useRef(selectedIds);

  // 切换素材时整体重置草稿：名称、类型、未保存的选择和失败提示都不带到下一个素材。
  let draft = draftState;
  if (draft.assetId !== assetId) {
    draft = draftFor(assetId, selectedIds);
    setDraftState(draft);
    setActionError(null);
  }

  // 父页给出的已保存关联变化时跟随；用户仍有未保存选择则不覆盖。
  useEffect(() => {
    const previous = lastPropIds.current;
    if (sameIds(previous, selectedIds)) return;
    lastPropIds.current = selectedIds;
    setDraftState(current => {
      if (current.assetId !== assetId || !sameIds(current.ids, previous)) return current;
      return { ...current, ids: [...selectedIds], saved: null };
    });
  }, [selectedIds, assetId]);

  const creating = pending === "create" || (localBusy?.assetId === assetId && localBusy.action === "create");
  const saving = pending === "save" || (localBusy?.assetId === assetId && localBusy.action === "save");
  const busy = creating || saving;
  // 没有素材时整块不可操作，不让用户先把名称和选择准备好却无处保存。
  const ready = Boolean(assetId);
  // 关联读成功之前一律不开放：界面上暂时空着的选择可能只是「还没读到」，
  // 不是「这条素材没有关联」，放开就等于让用户拿未知状态覆盖已知状态。
  const canOperate = ready && loaded;
  const trimmedName = draft.name.trim();
  const canCreate = canOperate && !busy && trimmedName.length > 0;
  const canSave = canOperate && !busy && !sameIds(draft.ids, selectedIds);

  function toggle(id: string, checked: boolean) {
    setActionError(null);
    setDraftState(current => ({
      ...current,
      saved: null,
      ids: checked
        ? current.ids.includes(id)
          ? current.ids
          : [...current.ids, id]
        : current.ids.filter(value => value !== id),
    }));
  }

  async function handleCreate() {
    if (!assetId || !canCreate) return;
    const target = assetId;
    const key = `create:${target}`;
    if (inFlight.current.has(key)) return;
    inFlight.current.add(key);
    setLocalBusy({ assetId: target, action: "create" });
    setActionError(null);
    try {
      const created = await onCreate(draft.kind, trimmedName);
      if (assetIdRef.current !== target) return;
      setDraftState(current =>
        current.assetId === target
          ? { ...current, name: "", ids: current.ids.includes(created.id) ? current.ids : [...current.ids, created.id] }
          : current,
      );
    } catch (cause) {
      if (assetIdRef.current !== target) return;
      setActionError(`${failureMessage(cause, "名称条目新增失败")}。可重试新增或取消。`);
    } finally {
      inFlight.current.delete(key);
      const current = localBusyRef.current;
      if (current?.assetId === target && current.action === "create") setLocalBusy(null);
    }
  }

  async function handleSave() {
    if (!assetId || !canSave) return;
    const target = assetId;
    const key = `save:${target}`;
    if (inFlight.current.has(key)) return;
    const ids = [...draft.ids];
    inFlight.current.add(key);
    setLocalBusy({ assetId: target, action: "save" });
    setActionError(null);
    setDraftState(current => (current.assetId === target ? { ...current, saved: null } : current));
    try {
      const saved = await onSave(target, ids);
      if (assetIdRef.current !== target) return;
      const savedIds = saved.map(object => object.id);
      setDraftState(current =>
        current.assetId === target ? { ...current, ids: savedIds, saved: `已保存 ${savedIds.length} 条名称关联。` } : current,
      );
    } catch (cause) {
      if (assetIdRef.current !== target) return;
      setActionError(`${failureMessage(cause, "名称关联保存失败")}。已保留当前选择，可重试保存或取消。`);
    } finally {
      inFlight.current.delete(key);
      const current = localBusyRef.current;
      if (current?.assetId === target && current.action === "save") setLocalBusy(null);
    }
  }

  function handleCancel() {
    setActionError(null);
    setDraftState(current => ({ ...draftFor(current.assetId, selectedIds), saved: null }));
  }

  return (
    <section className={styles.notesEditor} aria-label="素材业务对象">
      <strong>业务名称条目</strong>
      <p>选择或新增产品、菜品、人物名称，保存到当前素材。</p>
      <p>人物名称用于素材分类。</p>

      {!assetId ? <p role="status">先选择一条素材，再维护它的名称条目。</p> : null}
      {assetId && !loaded ? (
        readError ? (
          <div role="alert">
            <p className={styles.tagError}>{readError}</p>
            {onRetryRead ? <button onClick={onRetryRead} type="button">重新读取</button> : null}
          </div>
        ) : (
          <p role="status">正在读取这条素材已保存的名称关联…</p>
        )
      ) : null}

      {error ? (
        <div role="alert">
          <p className={styles.tagError}>{error}</p>
          {onDismissError ? <button onClick={onDismissError} type="button">关闭提示</button> : null}
        </div>
      ) : null}
      {actionError ? <p className={styles.tagError} role="alert">{actionError}</p> : null}
      {draft.saved ? <p role="status">{draft.saved}</p> : null}

      {objects.length === 0 ? (
        <p>当前工作区还没有名称条目，可以在下面新增一个。</p>
      ) : (
        <div className={styles.notesSelection} role="group" aria-label="已有名称条目">
          {objects.map(object => (
            <label key={object.id}>
              <input
                checked={draft.ids.includes(object.id)}
                disabled={!canOperate || busy}
                onChange={event => toggle(object.id, event.target.checked)}
                type="checkbox"
              />
              <span>{object.name}</span>
              <small>{kindLabel(object.kind)}</small>
            </label>
          ))}
        </div>
      )}

      <label>
        类型
        <AppSelect
          disabled={!canOperate || busy}
          onValueChange={newValue => setDraftState(current => ({ ...current, kind: newValue as BusinessObjectKind }))}
          value={draft.kind}
        >
          {kindOptions.map(option => (
            <AppSelectOption key={option.value} value={option.value}>{option.label}</AppSelectOption>
          ))}
        </AppSelect>
      </label>
      <label>
        名称
        <input
          className={styles.intakeLinkInput}
          disabled={!canOperate || busy}
          onChange={event => setDraftState(current => ({ ...current, name: event.target.value, saved: null }))}
          placeholder="例如：红烧肉"
          value={draft.name}
        />
      </label>
      <button disabled={!canCreate} onClick={() => void handleCreate()} type="button">新增并选中</button>
      <button disabled={!canSave} onClick={() => void handleSave()} type="button">保存关联到当前素材</button>
      <button disabled={!canOperate || busy} onClick={handleCancel} type="button">取消</button>
    </section>
  );
}