-- New Supabase projects don't grant API roles access to new tables. Give it to the server key only;
-- anon / authenticated keep no access (RLS is also on with no policies).
grant usage on schema public to service_role;
grant select, insert, update, delete on public.sources, public.bids, public.analyses, public.notifications to service_role;
grant usage, select on all sequences in schema public to service_role;
