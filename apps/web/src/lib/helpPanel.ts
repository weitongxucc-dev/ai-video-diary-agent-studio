"use client";

type Listener = (open: boolean) => void;

let isOpen = false;
const listeners = new Set<Listener>();

function notify() {
  for (const listener of listeners) {
    listener(isOpen);
  }
}

export function isHelpPanelOpen(): boolean {
  return isOpen;
}

export function openHelpPanel(): void {
  if (!isOpen) {
    isOpen = true;
    notify();
  }
}

export function closeHelpPanel(): void {
  if (isOpen) {
    isOpen = false;
    notify();
  }
}

export function toggleHelpPanel(): void {
  isOpen = !isOpen;
  notify();
}

export function subscribeHelpPanel(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}