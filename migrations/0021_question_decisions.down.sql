-- 0021_question_decisions.down.sql
DROP VIEW IF EXISTS active_question_decisions;
DROP INDEX IF EXISTS idx_question_decisions_lookup;
DROP INDEX IF EXISTS idx_question_decisions_active;
DROP TABLE IF EXISTS question_decisions;
