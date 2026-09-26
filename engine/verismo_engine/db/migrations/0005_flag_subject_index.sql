-- 0005_flag_subject_index: batched flag upserts look flags up by subject.
CREATE INDEX ix_flags_subject ON flags(subject_id, last_run_id);
