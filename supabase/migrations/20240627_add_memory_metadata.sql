-- enable pgvector once
create extension if not exists vector;

alter table public."Carves"
  add column summary_snippet   text,
  add column importance        real          default 0.5,
  add column last_used         timestamptz   default now(),
  add column usage_count       int           default 0,
  add column embedding         vector(1536),
  add column emotag            text,
  add column persona_tag       text,
  add column type              text          default 'episodic',
  add column immutable         boolean       default false,
  add column source_ids        uuid[],
  add column created_at        timestamptz   default now(),
  add column synthesis_metadata jsonb,
  add column theme_tags        text[];

-- prevent deletes (safety guard)
create policy "No delete on carves"
on public."Carves"
for delete using (false);
