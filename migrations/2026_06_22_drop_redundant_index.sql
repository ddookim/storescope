-- D+20 B2: api_keys.key_hash 중복 인덱스 정리
--
-- 현재 상태 (psql \d api_keys):
--   "api_keys_key_hash_key" UNIQUE CONSTRAINT, btree (key_hash)  ← UNIQUE → implicit btree
--   "idx_api_keys_hash"     btree (key_hash)                    ← 명시 btree (중복)
--
-- 영향:
--   - write 시 두 인덱스 동시 update (2x I/O)
--   - storage 낭비 (Neon free 3GB → 미세하지만 추가 누적)
--   - planner는 UNIQUE 인덱스 우선 사용 → idx_api_keys_hash 는 dead weight
--
-- 멱등 안전: IF EXISTS — 이미 drop된 환경에서 재실행 OK.

DROP INDEX IF EXISTS idx_api_keys_hash;
