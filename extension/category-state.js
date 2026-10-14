// Local review completion is separate from remote synchronization.
export const categoryNeedsWork=z=>z.operation_edit?.draft?!!z.operation_edit.error:z.category_draft?z.category_draft.valid!==true:!z.transfer&&!z.categories?.length;
export const zenRowsById=state=>new Map((state.day_review||[]).flatMap(d=>d.zen_rows||[]).map(z=>[z.id,z]));
export const linkedIds=b=>b.group?.zen_ids||(b.split_ids?.length?b.split_ids:[b.linked_id]);
export function bankNeedsCategory(b,zen){
  const parts=linkedIds(b).map(id=>zen.get(id));
  if(parts.length&&parts.every(Boolean))return parts.some(categoryNeedsWork);
  return !!b.linked&&!b.linked.transfer&&(!b.linked.categories?.length||b.linked.missing_category);
}
