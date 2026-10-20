"use client";

import { Select } from "@base-ui/react/select";
import { Check, ChevronDown, ChevronUp } from "lucide-react";
import { Children, isValidElement, useRef, useState, type ReactNode } from "react";
import styles from "./AppSelect.module.css";

type OptionProps = { value: string; disabled?: boolean; title?: string; children: ReactNode };

// shadcn/ui Base Nova Select (MIT): https://ui.shadcn.com/r/styles/base-nova/select.json
// Reviewed copy: designs/yaohe-desktop-language-20261020/candidate-v2/src/registry/base-nova/ui/select.tsx
// Retain our form/Portal contract; text and the selected indicator use independent slots.
export function AppSelectOption({ value, disabled, title, children }: OptionProps) {
  return <Select.Item className={styles.item} data-slot="select-item" data-app-select-value={value} value={value} disabled={disabled} title={title}>
    <Select.ItemText className={styles.itemText} data-slot="select-item-text">{children}</Select.ItemText>
    <Select.ItemIndicator className={styles.indicator} data-slot="select-item-indicator"><Check size={16} strokeWidth={1.7} aria-hidden /></Select.ItemIndicator>
  </Select.Item>;
}

type AppSelectProps = {
  children: ReactNode;
  value?: string;
  defaultValue?: string;
  name?: string;
  id?: string;
  form?: string;
  disabled?: boolean;
  required?: boolean;
  className?: string;
  "aria-label"?: string;
  "aria-labelledby"?: string;
  onValueChange?: (value: string) => void;
};

/** The mature Select owns keyboard, focus, typeahead and its hidden form field. */
export function AppSelect({ children, value, defaultValue, name, id, form, disabled, required, className, onValueChange, ...aria }: AppSelectProps) {
  const trigger = useRef<HTMLButtonElement>(null);
  const [container, setContainer] = useState<HTMLElement | undefined>();
  const items = Children.toArray(children).filter(isValidElement<OptionProps>).map(option => ({ value: option.props.value, label: option.props.children, disabled: option.props.disabled }));
  return <Select.Root<string> items={items} value={value} defaultValue={defaultValue ?? items.find(item => !item.disabled)?.value ?? null} name={name} id={id} form={form} disabled={disabled} required={required}
    onValueChange={next => { if (next !== null) onValueChange?.(next); }}
    onOpenChange={open => { if (open) setContainer(trigger.current?.closest<HTMLElement>('dialog[open], [role="dialog"]') ?? undefined); }}>
    <Select.Trigger render={(props, state) => <button {...props} data-selected-value={state.value} />} ref={trigger} data-slot="select-trigger" data-app-select-trigger className={[styles.trigger, className].filter(Boolean).join(" ")} {...aria}>
      <Select.Value className={styles.value} data-slot="select-value" /><Select.Icon className={styles.icon} data-slot="select-icon"><ChevronDown size={16} strokeWidth={1.7} aria-hidden /></Select.Icon>
    </Select.Trigger>
    <Select.Portal container={container}>
      <Select.Positioner className={styles.positioner} alignItemWithTrigger={false} side="bottom" align="start" sideOffset={4}>
        <Select.Popup className={styles.popup} data-app-select-popup>
          <Select.ScrollUpArrow className={styles.scrollArrow}><ChevronUp size={16} strokeWidth={1.7} aria-hidden /></Select.ScrollUpArrow>
          <Select.List className={styles.list}>{children}</Select.List>
          <Select.ScrollDownArrow className={styles.scrollArrow}><ChevronDown size={16} strokeWidth={1.7} aria-hidden /></Select.ScrollDownArrow>
        </Select.Popup>
      </Select.Positioner>
    </Select.Portal>
  </Select.Root>;
}
