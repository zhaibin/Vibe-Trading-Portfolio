import { useEffect, useRef } from "react";

export interface Notice {
  kind: "success" | "error";
  title: string;
  detail?: string;
  action?: { label: string; onClick: () => void };
}

export function StatusMessage({ notice }: { notice: Notice }) {
  const heading = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    heading.current?.focus();
  }, [notice]);

  return (
    <section
      className={`status-message status-${notice.kind}`}
      role={notice.kind === "error" ? "alert" : "status"}
      aria-live={notice.kind === "error" ? "assertive" : "polite"}
    >
      <h2 ref={heading} tabIndex={-1}>
        {notice.title}
      </h2>
      {notice.detail === undefined ? null : <p>{notice.detail}</p>}
      {notice.action === undefined ? null : (
        <button type="button" onClick={notice.action.onClick}>
          {notice.action.label}
        </button>
      )}
    </section>
  );
}
