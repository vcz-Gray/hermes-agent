# Gling soft-delete rollout 후속 수정 계획

## 목표
FE #1710 / BE #1117의 soft-delete rollout을 실제 운영 계약에 맞게 정렬한다.

## 전제
1. `deletedAt`가 **없는 경우**와 **`null`인 경우**를 코드상 구분하지 않는 경로에서는 둘 다 **정상 노출(active)** 로 간주한다.
2. FE 회차 목록 정합성은 반드시 맞춘다. 단, 기존 `private`, `publishedAt`, 기타 공개 조건은 유지한 채 **원래 노출돼야 하는 데이터만** 보이게 한다.
3. 문제 발생 시 실행 가능한 **원복 방법**을 문서/절차 수준이 아니라 실제 실행 단위로 제시한다.

---

## 트랙 1. deletedAt 계약 정렬 계획

### 목적
현재 helper / read-path / rollout 문서 / QA 기준이 `deletedAt: null`을 어떻게 해석하는지 일치시키기.

### 해야 할 일
1. **계약 기준 고정**
   - active 판정:
     - `deletedAt` 없음 -> active
     - `deletedAt: null` -> active
   - deleted 판정:
     - `deletedAt`이 숫자(ms epoch) -> deleted

2. **영향 helper 재정의**
   - backend:
     - `src/common/utils/visibility-query.ts`
     - `src/common/utils/public-visibility.ts`
   - 목표:
     - 현재 `{ deletedAt: { $exists: false } }` 기반 로직이 있으면
     - `없음 OR null` 을 active로 보는 공용 조건으로 통일

3. **blast radius 재점검**
   - 최소 재검토 경로:
     - `src/routes/sitemap/sitemap.model.ts`
     - `src/routes/search/search.model.ts`
     - `src/routes/event/event.model.ts`
     - `src/routes/banner/banner.model.ts`
     - `src/routes/novel/novel.model.ts`
     - `src/routes/chapter/chapter.model.ts`
     - `src/routes/admin/admin.model.ts`
   - 확인 포인트:
     - public/admin/search/list/count/detail 모두 `없음 OR null` active로 동작하는지
     - showDeleted=true일 때 숫자 deletedAt만 잡는지

4. **FE 공개 가드 정렬**
   - `src/common/utils/public-series-visibility.ts`
   - `src/pages/novel/detail/[_id].tsx`
   - `src/pages/novel/viewer/[chapterId].tsx`
   - 목표:
     - sitemap / public gate가 backend active 계약과 같은 의미를 쓰게 만들기

5. **문서/검증 기준 정정**
   - `docs/soft-delete-rollout.md`
   - `docs/soft-delete-restore-runbook.md`
   - 테스트 파일:
     - `tests/soft-delete-read-path.test.ts`
   - 목표:
     - `legacyNull = 이상치`라는 전제를 제거하거나, 실제 계약에 맞게 재기술

### 구현 원칙
- broad migration으로 `null`을 강제로 없애는 방향부터 가정하지 않는다.
- 먼저 **read contract**를 바로잡고, 그 다음 migration 필요 여부를 다시 판단한다.

---

## 트랙 2. FE 회차 목록 정합성 수정 계획

### 목적
회차 목록, 총 개수, 탭 숫자, 페이지 수가 모두 같은 visibility 계약을 따르도록 정렬.

### 현재 문제 축
- 목록은 FE 후처리 필터 또는 일부 BE 응답 기준으로 hidden 처리됨
- 총 개수/탭 숫자는 원본 `chapterCnt`를 그대로 사용함
- 결과적으로 마지막 페이지 빈 상태, 페이지 튕김, 숫자/목록 불일치 발생 가능

### 수정 방향
1. **정합성 기준 고정**
   - 페이지네이션에 쓰는 totalCount는
     - “실제 사용자에게 노출되는 chapter row 집합” 기준이어야 함
   - 단, 아래 기존 조건 유지:
     - `setting.isPrivate`
     - `chapterInfo.publishedAt`
     - 기타 현재 chapter list API가 갖는 visibility 조건

2. **소스 오브 트루스 재정의**
   - FE에서 목록만 필터링하고 count는 원본값 쓰는 구조를 줄인다.
   - 가능하면 BE 응답에서
     - filtered list
     - filtered totalCount
     를 같은 계약으로 내려주는 방향 우선 검토.

3. **주요 수정 후보 경로**
   - FE:
     - `src/features/admin/service/fetchers.ts`
     - `src/pages/studio/series-management/chapter-notice/[seriesId].tsx`
     - `src/features/studio/components/series-content-management-panel.tsx`
     - 관련 query/types 파일
   - BE:
     - admin chapter list/count를 내려주는 route/model/service
     - chapter list aggregate/count logic

4. **검증 시나리오**
   - 공개 회차 + 비공개 회차 + 미래 발행 회차 + soft-deleted 회차가 섞인 작품 준비
   - 확인 항목:
     1. 첫 페이지 목록 개수
     2. 총 개수
     3. 탭 숫자
     4. 마지막 페이지 진입 여부
     5. 마지막 페이지에서 삭제 직후 fallback 동작
   - 기대값:
     - “원래 노출돼야 하는 회차”만 보임
     - 숫자/페이지 수가 실제 목록과 일치

### 구현 원칙
- 단순히 totalCount를 줄이는 게 아니라,
- **기존 공개 조건(private, publishedAt 등)을 유지한 채** 일치시켜야 함.

---

## 트랙 3. 원복 방안 수립 계획

### 목적
문제 발생 시 “restore tool이 있으니 괜찮다” 수준이 아니라, 실제 운영자가 바로 실행할 수 있는 원복 절차를 제시.

### 원복을 두 층으로 분리

#### A. 코드 원복
1. FE PR #1710 revert 가능 단위 정리
2. BE PR #1117 revert 가능 단위 정리
3. 함께 revert해야 하는 조합 정의
   - helper만 revert
   - FE 가드만 revert
   - rollout script 적용 전/후 각각 revert 전략

#### B. 데이터 원복
1. **apply 이전**
   - 코드만 롤백하면 되는지
   - 배포 순서 역전 시 어떤 read-path가 깨지는지 문서화

2. **apply 이후**
   - 자동 완전 원복은 불가하다는 전제 명시
   - 필요한 것:
     - apply 실행 로그 보관
     - 대상 `_id` 집합 보관
     - Mongo snapshot/backup 기준 복구 절차
     - creator/account/comment 도메인별 수동복구 조건

3. **도메인별 원복성 분류표 작성**
   - account: 자동 restore 불가
   - creator: partial restore 가능, 원본 nickname/linkInfo 필요
   - series: deletedAt 해제 가능, workStatus 재지정 필요
   - comment: tombstone이면 원문 백업 없이는 완전복구 불가
   - secondary domain: 기본 자동복구 대상 아님

### 산출물
1. **배포 전 원복 체크리스트**
   - snapshot 확보 여부
   - apply 로그 저장 위치
   - 승인자/실행자/maintenance window
2. **배포 후 장애 시나리오별 대응표**
   - 정상 콘텐츠 과차단
   - admin 목록 불일치
   - creator 삭제 중 partial failure
   - apply 후 visibility 이상
3. **실행 명령 예시 포함 runbook 개정안**

---

## 실행 순서 제안
1. 트랙 1 계약 정렬 설계 확정
2. 트랙 2 FE/BE 정합성 수정 설계 확정
3. 트랙 1, 2 구현
4. build/test/실QA
5. 트랙 3 원복 runbook 확정
6. 최종 Go / No-Go 재판정

---

## 최종 판정 기준
다음 3개가 충족돼야 Go 검토 가능:
1. `deletedAt` active/deleted 계약이 FE/BE/helper/doc/test에서 일치
2. 회차 목록 숫자/목록/페이지네이션이 기존 공개 조건 유지한 채 정합
3. apply 전/후 각각에 대한 원복 절차가 실제 실행 가능한 형태로 문서화
