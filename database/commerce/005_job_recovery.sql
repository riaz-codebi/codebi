BEGIN;
SET LOCAL lock_timeout='5s';
-- Retry scheduling lives in Python; this only widens the integrity bound.
ALTER TABLE commerce.jobs DROP CONSTRAINT jobs_foundation_check_3;
ALTER TABLE commerce.jobs ADD CONSTRAINT jobs_foundation_check_3
    CHECK (attempts>=0 AND max_attempts BETWEEN 1 AND 20);
COMMENT ON CONSTRAINT jobs_foundation_check_3 ON commerce.jobs IS 'Bounded job recovery v2: maximum 20 attempts';
COMMIT;
