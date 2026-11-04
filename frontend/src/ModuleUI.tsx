import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export function ModulePageHeader({
  icon: Icon,
  eyebrow,
  title,
  description,
  actions,
}: {
  icon: LucideIcon;
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="module-page-header">
      <span className="module-page-icon"><Icon size={24} aria-hidden="true" /></span>
      <div className="module-page-heading-copy">
        <span className="module-page-eyebrow">{eyebrow}</span>
        <h2>{title}</h2>
        <p>{description}</p>
      </div>
      {actions && <div className="module-page-actions">{actions}</div>}
    </div>
  );
}
