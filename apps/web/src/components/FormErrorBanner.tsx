"use client";

import {
  type FormEvent,
  type FormHTMLAttributes,
  useCallback,
  useRef,
  useState,
} from "react";

type FormErrorBannerProps = Omit<
  FormHTMLAttributes<HTMLFormElement>,
  "onInvalid" | "onSubmit"
> & {
  formId?: string;
  emptyMessage?: string;
};

type FieldError = {
  field: string;
  message: string;
};

type InvalidFieldElement = HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement;

function readInvalidMessage(element: InvalidFieldElement): FieldError | null {
  const message = element.validationMessage;
  if (!message) {
    return null;
  }
  const label =
    element.getAttribute("aria-label") ??
    element.getAttribute("name") ??
    element.tagName.toLowerCase();
  return { field: label, message };
}

function isInvalidField(
  element: Element | null,
): element is InvalidFieldElement {
  if (!element) {
    return false;
  }
  return (
    element instanceof HTMLInputElement ||
    element instanceof HTMLTextAreaElement ||
    element instanceof HTMLSelectElement
  );
}

function findInvalidField(form: HTMLFormElement | null): InvalidFieldElement | null {
  if (!form) {
    return null;
  }
  if (form.checkValidity()) {
    return null;
  }
  const candidates = form.querySelectorAll<
    HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement
  >("input, textarea, select");
  for (const candidate of Array.from(candidates)) {
    if (!candidate.checkValidity()) {
      return candidate;
    }
  }
  return null;
}

export function FormErrorBanner({
  children,
  emptyMessage,
  formId,
  ...rest
}: FormErrorBannerProps) {
  const [error, setError] = useState<FieldError | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);

  const setFormRef = useCallback((node: HTMLFormElement | null) => {
    formRef.current = node;
  }, []);

  const handleInvalid = useCallback(
    (event: FormEvent<HTMLFormElement>) => {
      const nativeEvent = event.nativeEvent as unknown as Event;
      const form =
        event.currentTarget instanceof HTMLFormElement
          ? event.currentTarget
          : formRef.current;

      const nativeTarget = nativeEvent.target;
      const candidate = isInvalidField(nativeTarget as Element | null)
        ? (nativeTarget as InvalidFieldElement)
        : null;
      const fallback =
        candidate ?? findInvalidField(form);

      const next = fallback ? readInvalidMessage(fallback) : null;
      if (next) {
        setError(next);
        if (fallback) {
          fallback.focus({ preventScroll: true });
        }
      }
    },
    [],
  );

  const handleSubmit = useCallback(() => {
    setError(null);
  }, []);

  return (
    <>
      {error ? (
        <div
          aria-live="assertive"
          className="pixel-form-error"
          role="alert"
        >
          <span aria-hidden="true" className="pixel-form-error-light" />
          <div className="pixel-form-error-body">
            <strong>{emptyMessage ?? "请检查表单字段"}</strong>
            <span>{error.message}</span>
          </div>
          <button
            aria-label="关闭错误"
            className="pixel-form-error-close"
            onClick={() => setError(null)}
            type="button"
          >
            ×
          </button>
        </div>
      ) : null}
      <form
        {...rest}
        id={formId}
        noValidate={false}
        onInvalid={handleInvalid}
        onSubmit={handleSubmit}
        ref={setFormRef}
      >
        {children}
      </form>
    </>
  );
}