import { useState } from "react";
import type { SourceRecord } from "@/lib/personal-api";
import { Button } from "@/components/ui/button";
import { SourceRecordDialog } from "./source-record-dialog";
import { usePersonalProject } from "@/features/project/use-personal-project";

/** Keep the enclosing decision/check draft mounted while editing a source.
 * Successful source edits never imply selecting it or updating a saved ref. */
export function SourceEditorButton({ source, disabled, onSaved, label }: { source?: SourceRecord; disabled?: boolean; onSaved?: (source: SourceRecord) => void; label?: string }) {
  const access = usePersonalProject();
  if (!access.writable) return <Button size="sm" variant="outline" disabled>{label ?? (source ? "维护此依据" : "新建人工依据")}</Button>;
  return <WritableSourceEditorButton key={access.scope} source={source} disabled={disabled} onSaved={onSaved} label={label} />;
}
function WritableSourceEditorButton({ source, disabled, onSaved, label }: { source?: SourceRecord; disabled?: boolean; onSaved?: (source: SourceRecord) => void; label?: string }) {
  const [open, setOpen] = useState(false);
  // Closed editors release their mutation callbacks. Reopening restores the
  // shared draft and original request instead of retaining an outdated copy.
  return <span className="inline-block"><Button size="sm" variant="outline" disabled={disabled} onClick={() => setOpen(true)}>{label ?? (source ? "维护此依据" : "新建人工依据")}</Button>
    {open && <SourceRecordDialog key={source?.source_id ?? "new"} source={source} open onOpenChange={setOpen} onSaved={(saved) => { setOpen(false); onSaved?.(saved); }} />}
  </span>;
}
