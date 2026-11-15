---
icon: mdi:robot
order: 20
---

# 전투 MCP Server

`src/MaaMcpServer`는 별도 worker 프로세스에서 MaaCore를 실행하는 로컬 stdio MCP 서버입니다. Python 3.11+, uv, 현재 브랜치에서 빌드한 Core가 필요합니다. 이전 버전에는 `BattleControl` 작업이 없습니다. 실험적인 전투 제어 기능이며 위기협약 자동 최적화 도구가 아닙니다.

## 도구 프로필과 응답 계약

`--tool-profile minimal|full`의 기본값은 `minimal`입니다. Python에서는 `create_server(service, intelligence=None, tool_profile="minimal")`을 사용합니다. 기본 `tools/list`는 다음 10개 도구만 포함합니다.

- 연결과 수명 주기: `battle_status`, `battle_connect`, `battle_attach`, `battle_close`.
- 읽기 전용 증거와 자료: `battle_observe`, `battle_get_unit_info`, `battle_get_operation`.
- 명시적인 장치 입력: `battle_inspect_preparation`, `battle_identify`, `battle_step`.

`intel_query`는 `--intel-config`가 설정된 경우에만 등록하며 두 프로필 모두에 도구 하나를 추가합니다. provider 없는 빈 도구는 노출하지 않습니다. `full`은 기존 비정보 도구 27개를 노출하며 이전 행동, 독립적인 정지/재개, 전투 전 보조, 미디어와 기하 평가를 포함합니다. 정보 설정 시 총 28개입니다. 기존 확장을 사용하는 클라이언트는 `--tool-profile full`을 추가하고 `tools/list`를 새로 조회해야 합니다. 읽기와 장치 입력을 만능 action 도구에 섞지 않습니다.

`battle_observe(session_id, include_image=false, include_map=false, duration_ms=0, operation_id=null, timeout_ms=10000)`가 통합 수동 관측 진입점입니다. 암묵적인 식별, 정지, 재개 또는 입력은 하지 않습니다. 일반 관측은 동일 프레임 PNG를 저장하지만 기본적으로 첨부하지 않습니다. `include_image=true`는 같은 원본을 첨부하고, `include_map=true`는 추가 캡처 없이 구조화된 정적 맵을 반환하며 기본 맵은 구조화된 참조입니다. `duration_ms=100..8000`은 읽기 전용 `finalize`를 재사용하며 **세션 입력이 차단된 상태에서만** 가능합니다. `timeout_ms`(1..30000)를 초과할 수 없으며 실행 중인 게임을 정지하지 않습니다. 작업 ID 생략 시 생성한 ID를 `command_id`로 반환하고 명시된 ID는 영구 재생 규칙을 따릅니다.

minimal 응답은 **최상위**에 canonical `battle_decision` 하나를 제공하며 반환 시점, 일반 관측, 행동 경계 순으로 스냅샷을 선택합니다. 중첩 스냅샷의 메타데이터, 안전, 작업 결과와 증거 참조는 유지하지만 `battle_decision`, `deployment`, `battlefield`를 반복하지 않습니다. 미확인 값과 이유는 생략하지 않습니다. 응답 투영일 뿐 `operations.sqlite3`의 원본 감사 결과를 삭제하지 않습니다. full은 기존 중첩/원시 형태와 이전 진입점의 이미지 첨부를 유지하고 minimal은 기본적으로 PNG를 첨부하지 않습니다.

구조화된 행동 경계 증거가 필요하면 두 프로필 모두에서 `battle_get_operation(session_id, operation_id, detail="full")`을 호출할 수 있습니다. 저장된 결과를 바꾸지 않고 완전한 `capture_time_snapshot`/`return_time_snapshot`, 각각의 구조화된 판단과 증거 참조를 반환합니다. 새 관측, 입력 또는 기록 재작성은 하지 않습니다. 기본 `detail="summary"`는 선택한 프로필을 따라 minimal에서는 요약, full에서는 호환성을 위해 여전히 완전한 결과를 반환합니다. full 작업 상세는 PNG를 첨부하지 않으며 프로필 전환도 필요 없습니다. full의 pause/speed step은 네이티브 `image_path` PNG도 첨부하고 같은 이미지 경로는 한 번만 첨부합니다.

보관된 작업 프레임은 `image/png` MCP resource `battle-evidence://{session_id}/{operation_id}/{observation_id}`로 읽습니다. 응답에 `evidence[].resource_uri`와 링크를 제공합니다. 새 캡처가 아닌 기록된 원본 PNG를 반환하며 종료 후에도 보존 기간 안에서는 조회할 수 있습니다. 작업에 연결된 ID만 허용하고 임의 경로는 받지 않습니다. full의 `battle_get_evidence_frame`도 유지합니다. 만료는 명시적인 오류이며 합성하거나 다시 캡처하지 않습니다.

`battle_step.request`는 action으로 구분하는 `oneOf` Schema이며 각 분기의 필수 필드와 `additionalProperties=false`를 노출합니다. `deploy`는 `unit_id`, `location`, `direction`, `retreat`/`skill`은 `unit_id`, `wait`는 `duration_ms`가 필수입니다. `pause`는 유닛, 위치, 방향, 시간을 받지 않고 정지 후 검증만 하며 전투를 진행하지 않습니다. `speed`는 정수 `multiplier=1|2`가 필수이며 검증된 정지 상태에서만 가능합니다. `recording`은 wait 전용이고 시간/기한/프레임 관계도 입력 전에 검사합니다. 수동 정지 인계는 여전히 명시적 `adopt_manual_pause=true`가 필요합니다. 독립적인 `battle_set_paused(paused=false)`는 full 전용이며 유한 step이 아닙니다.

## 구조화된 전투 판단 뷰

일반 네이티브 관측도 동일 프레임 PNG를 저장합니다. 체력 바 `field_units`는 모든 후보 타일(`candidate_locations`), 충돌/투영 상태, 방향 증거와 `requested_instance_candidates`를 반환하며 첫 일치로 위치를 확정하지 않습니다. `instance_association`, 개체별 `spatial_evidence`와 `action_verification`은 부족한 조건을 유지합니다. 후보는 검증된 식별/타일 연결이 아니므로 `instance_id`, `game_id`, `location`은 미확인이고 `authorizes_input=false`입니다. 선택된 식별을 타일에 연결하는 양성 규칙과 퇴각 버튼 enabled 상태 규칙이 없어 `VerifiedTileAnchor`는 확립되지 않았습니다. 스킬/퇴각은 계속 거부되며 자율 스킬 실행이나 배치/퇴각 루프 완성을 주장하지 않습니다.

스냅샷에 `battle_decision.schema_version=1`을 추가하며 네이티브 프로토콜은 1을 유지합니다. 영역은 `safety`, `progress`, `deployment`, `allies`, `terrain`, `enemies`, `mechanics`, `events`, `coverage`, `inspection`, `alerts`입니다. 값마다 출처, 검증 상태, 이유, 관측 ID, 획득 시각, 최신성을 보존합니다. 미지원 상태는 빈 전장을 뜻하지 않으며 `allies.action_history`는 현재 존재 상태와 독립입니다.

- `life_evidence`는 양성 HUD anchor 및 점수 ≥ 0.9인 전체 문자열 숫자 OCR을 사용합니다. `life_delta_evidence`는 신뢰 가능한 양 끝 증거, 단조 시계 비교 간격, 읽지 못한 관측 수를 보존합니다. 누락은 0이 아니며 차이는 순간 누수·적 ID·기믹 원인이 아닙니다. 네이티브 이벤트는 128개와 안정된 `event_id`, `sequence`, `event_cursor`, `events_retained_from`을 유지합니다. ID 중복을 제거하고 커서 누락을 확인하세요. 호스트 투영과 작업 재생은 네이티브 상태를 진행하지 않습니다.
- `observed_card_cooling`은 패배 또는 소유 개체 이탈이 아닙니다. 이전에 검증한 소유 관계/타일 anchor, 유일한 일반 비대체 개체, 연속 두 번의 새 양성 쿨다운 관측이 필요합니다. 누락·모호함·충돌 시 증거를 초기화하며 미확인 개체의 예약은 유지합니다. `field_units`는 관측 내부의 미연결 체력 바 사각형/점수만 제공하며 HP 비율이나 점유 증명이 아닙니다.
- 요청 위치의 준비 완료 후보는 활성화·단계·SP·탄약·지속 시간·저지·비행·치유 상태를 증명하지 않습니다. 이 영역과 현장 적 위치/웨이브/경로는 미완성이며 연결된 전투의 런타임 상세 검사기도 없습니다. 선택적 검출/분류는 공개 또는 최종 증거 프레임에서 정지 정리 후 실행하고 재개 대기/정지 확인 구간에 넣지 않습니다. 예산 부족은 명시하며 미확인·이상·감사에는 동일 프레임 PNG를 이전 스킬 진입점에서도 유지합니다.
- 글자가 빠진 `暂停`는 제한된 중국어 ROI, 기존 ≥ 0.9 임계값, 동일 프레임의 양성 전투 HUD와 오른쪽 재개 삼각형이 모두 있고 실행/종료 충돌이 없을 때만 인정합니다. 성공 시에도 원문·사각형·점수·규칙을 유지합니다. 연속 두 번의 새 증거는 소프트 정지 불확실성만 해제하며 하드 격리를 해제하거나 입력을 재생하지 않습니다.
- 일반 개체의 명시/암시 카드 경로는 재개 전후 같은 검증기로 획득 후 30초 이내, 식별, 순서/기하, 유효한 아바타 정규화 상관 ≥ 0.95를 확인합니다. 균일한 음영이나 작은 변화는 허용할 수 있으나 교체·재정렬·오래되거나 모호한 증거는 거부합니다. 다른 카드의 식별 정보 개선만은 재정렬이 아닙니다. 소환/중복 카드는 구체적인 `card_id`가 필요합니다.
- 전투 전 레벨은 양성 LV 표식 바로 아래의 중앙 영역에서 읽으며 오른쪽 정예 아이콘 영역으로 이동하지 않습니다. 낮은 점수·불완전·범위 밖 숫자는 미확인입니다. 편성 식별은 공간 일대일 대응으로 같은 행 Y 흔들림을 허용합니다. 선택 스킬은 별도 증거 fingerprint를 보존하며 편성 이름 불변이 스킬 불변을 증명하지 않습니다. `effective_configuration`은 전투 전/사용자 증거와 미해결 충돌을 유지하고 정적 최대 육성치를 선택하지 않습니다.
- 오퍼레이터 지식은 기존 `battle_get_unit_info` 또는 설정된 `intel_query`로만 조회합니다. `knowledge_refs`는 게임/스킬 ID 참조만 제공하며 자동 조회나 정적 공식으로 런타임 관측을 대신하지 않습니다. 합성 Debug 테스트는 원본 이미지, Windows 실기기 또는 전체 전투 검증이 아닙니다.

## 전투 전 인식 대안과 호스트 검토

편성 이름의 정상 인식 경로를 유지하며 실패할 때만 편성 전용 이름 ROI에서 OperBox 하위 `OperNameAnalyzer`의 분할을 재사용합니다. 원시 후보, 점수, 식별 매핑 근거를 보존합니다. OperBox 탐색이나 14/16장 배치는 사용하지 않으며 기본 레벨 1·정예 0·잠재 1을 관측값으로 가져오지 않습니다.

전투 전 결과의 `host_fallback.required`와 슬롯/필드별 `items`는 이유, 사용 가능한 원시 후보, ROI, 증거 및 `retrieval` 참조를 제공합니다. 미확인 스킬·레벨·식별은 그대로 미확인입니다. 멀티모달 호스트 검토용일 뿐 해석을 네이티브 사실로 자동 승격하거나 안전 잠금 변경·추가 탐색을 수행하지 않습니다.

필드별 `items[].retrieval.resource_uri`는 기록된 원본 PNG 리소스 참조를 재사용합니다. minimal은 등록되지 않은 full 전용 도구를 안내하지 않으며 full은 호환 도구와 리소스를 함께 유지합니다. 기록된 프레임 참조가 없으면 minimal은 `available=false`와 null 리소스 URI를 반환합니다. `detail="full"`을 포함한 과거 작업 조회에도 같은 투영을 적용하며 조회 메타데이터만 조정하고 영구 사실이나 원본 기록은 변경하지 않습니다.

원본 프레임 resource 또는 full의 `battle_get_evidence_frame(session_id, operation_id, observation_id)`(ImageContent)는 기록된 작업의 원본 PNG 한 장을 반환합니다. 편성 복귀 이미지뿐 아니라 과거 전투 전 상세 프레임도 조회합니다. 기록된 작업 ID만 허용하며 미등록/모호한 프레임, 다른 세션 경로, 심볼릭 링크 이탈 및 16 MiB 초과 이미지는 거부합니다. 종료 후에도 기록과 파일이 남으면 조회할 수 있습니다. 새 캡처나 장치 입력 없이 `frame_is_live=false`를 반환합니다.

네이티브 `suspected_defeat`는 중복 제거된 **호스트 검토 단서**이며 엔진의 격퇴 이벤트가 아닙니다. 현재 세션에 퇴각 입력 기록이 없고 유일한 일반 비대체 종류의 배치 기록이 쿨다운이면 소유 관계/이탈 미검증 상태에서도 제시할 수 있습니다. `departure_verified=false`, 원인 미상, 신뢰도 null, `record_coverage="this_session_only"` 및 다른 가능성을 명시합니다. 중복/소환/대체 모호함이나 종료 정리는 단서를 만들지 않습니다. 존재 상태 변경·타일 해제·입력 허가에 사용하지 않으며 외부/세션 이전 입력과 기믹도 가능한 원인입니다.

## 로컬 미디어와 실험적 동영상 후보

아래 독립 미디어 도구는 full 전용입니다. 명시적으로 허용한 wait recording과 리소스 링크는 minimal에서도 가능합니다.

`battle_record_clip(request)`는 전투 `session_id`, 영구 `operation_id`, `duration_ms`(100–8000, 기본 2000), `interval_ms`(200–2000, 기본 500), `max_frames`(2–24, 기본 24), 일반 `timeout_ms`를 받습니다. 불가능한 시간/간격/프레임 조합은 사전 거부합니다. **입력을 보내지 않고** 정지/재개하지 않습니다. 실행 중 게임은 계속 진행되며 마지막 스냅샷/PNG는 실제 획득 프레임과 대응합니다.

명시적으로 허용한 `battle_step(action="wait", duration_ms=2000, ...)`에만 `recording: {"interval_ms": 500, "max_frames": 8}`을 추가합니다. 기존 대기 캡처를 재사용하며 PNG/동영상 인코딩은 정지 정리 후 실행합니다. 미디어 실패가 정리를 생략하거나 입력을 허용하지 않습니다. 취소/닫기는 부분/불명 결과를 명시하며 필요하면 자신의 worker만 종료합니다. 같은 작업 ID는 원래 artifact 의도/결과를 재사용하고 부분/만료 미디어도 다시 녹화하지 않습니다.

저장 공간 예약 실패는 장치 명령 전송 전에 `media_preflight_failed`로 거부하며, 녹화 옵션을 버리고 전투를 진행하지 않습니다. 동작 시작 후 미디어 저장에 실패해도 안전한 마무리를 유지한 뒤 완전한 성공 대신 `media_unavailable`/`media_incomplete`를 반환합니다. 입력과 일시 정지 결과는 별도로 보존하므로 무조건 재실행하지 마세요.

`media`에는 `artifact_id`, 상태, 원본 PNG 참조/해시, `video` 메타데이터가 있습니다. 네이티브 OpenCV는 **AVI/MJPEG**만 시도하며 디코딩 재검증 후에만 재생 가능으로 표시합니다. MP4 인코더나 Python Pillow/OpenCV 런타임은 필요하지 않습니다. 희소 샘플이므로 연속 애니메이션을 보장하지 않고 `rendered_fps`는 표시용이며 게임 속도/시뮬레이션 시간이 아닙니다. 라이브 프레임의 근거는 `captured_at_ms`와 획득 시작 `offset_monotonic_ms`입니다. 가져온 프레임은 `media_pts_ms`(디코더 PTS 또는 명시한 프레임 번호/FPS 계산)만 가지며 라이브 획득 시간을 만들지 않습니다.

`battle_get_media(artifact_id)`는 현재 보관/리소스 가용성을, `battle_get_media_frames(artifact_id, frame_ids=[...])`는 최대 4장·합계 16 MiB의 PNG ImageContent를 반환합니다. 새 캡처, 지식 조회, 과거 이미지에 의한 현장 상태 변경/격리 해제는 없습니다. SDK에는 VideoContent가 없으므로 작은 AVI는 `battle-media://<id>/video.avi` 바이너리 리소스로 읽으며 리소스 응답에 실제 동영상 MIME을, manifest에 codec을 제공합니다. 가져온 동영상은 해당 형식의 확장자를 사용합니다. 바이너리 읽기는 16 MiB 한도입니다. 큰 원본은 `resource_retrievable=false`이고 읽을 수 없는 URI를 제시하지 않지만 키프레임과 네이티브 분석은 유지합니다. 영구 작업 결과는 당시 가용성이므로 옛 링크를 쓰기 전에 `battle_get_media`로 확인하세요. 만료는 명시적으로 실패하고 다시 만들지 않습니다.

녹화 원시 프레임 버퍼는 64 MiB, 게시 파일은 32 MiB, 보관은 8개 artifact/256 MiB 한도이며 프로세스 간 읽기/분석 중에는 pin합니다. 로그/작업 기록과 일반/중요 전투 이미지는 기존의 별도 규칙을 따릅니다. `--media-import-dir /safe/local/imports`(기본 `<user-dir>/media-import`)에 직접 놓은 파일을 `battle_import_video(request={"operation_id": "...", "import_name": "full.avi", "declared_context": "full_battle", "timeout_ms": 30000})`로 가져옵니다. basename과 AVI/MP4/MOV/MKV 서명만 허용하며 링크, 경로 이탈, 장치, 재생 목록, URL은 거부하고 소유한 불변 복사본/해시를 만듭니다. 연결 없는 별도 Core가 전투 잠금을 오래 잡지 않고 실제 크기/프레임 수/기한을 확인합니다. 한도는 128 MiB, 10분, 1920×1080, 18000프레임, 보고 FPS 240입니다. 미지원 디코더/부분 I/O 실패를 명시하며 모든 패키지의 codec 지원을 가정하지 않습니다.

`battle_analyze_video(request={"operation_id": "...", "artifact_id": "...", "timeout_ms": 120000})`의 범위는 **experimental_video_to_copilot**이며 일반 전장 인식/실행이 아닙니다. 완전히 디코딩되고 호출자가 전체 문맥을 선언한 가져오기만 기존 `VideoRecognition`으로 넘기되 선언을 사실로 여기지 않고 Core가 편성/스테이지를 확인합니다. 짧은 전투 클립은 동영상/키프레임으로 호스트에게 유용하며 누락 문맥을 지원한다고 꾸미지 않습니다. 별도 무연결 worker/handle과 격리 출력 디렉터리가 입력 ID/해시, 진단/경고가 있는 읽기 전용 copilot 후보를 반환합니다. 식별을 추측하거나 사라짐을 Retreat로 바꾸는 근사이며 후보 실행/현장 상태 변경은 없습니다. 무거운 오프라인 작업은 프로세스 전체에서 하나만 허용하고 자신의 worker만 종료합니다. 최대 10 clip·clip당 3 방향 샘플 및 완료 샘플 해제로 정규화 이미지 버퍼를 128 MiB 이내(로드된 모델 제외)로 제한하며 watchdog은 최대 120초입니다.

네이티브 프로토콜 1에 `BattleControl` 읽기 전용 `record`, 대기 `recording`, 서버가 발급한 32자리 hex artifact ID를 추가합니다. 일반 빌드의 `BattleMedia` Core task는 격리 사용자 디렉터리에 생성한 `input.avi|mp4|mov|mkv`만 디코딩하고 `BattleMediaResult`를 반환합니다. DebugTask 전용이 아닙니다. Debug 합성 codec/전송 테스트는 Windows·실기기·원본 동영상 검증이 아닙니다.

## 빌드 및 설치

저장소 루트에서 실행합니다. [Linux 빌드 가이드](./linux-tutorial.md) 또는 해당 플랫폼 가이드에 따라 컴파일러와 CMake를 준비하세요. Windows에서는 Visual Studio 개발자 터미널을 사용합니다.

```bash
git submodule update --init --recursive src/MaaUtils
python tools/maadeps-download.py
cmake --preset linux-x64 -B build-mcp -DBUILD_WPF_GUI=OFF -DINSTALL_RESOURCE=ON
cmake --build build-mcp --config Debug --target MaaCore -j 2
cmake --install build-mcp --config Debug --prefix "$PWD/build-mcp/install"
uv sync --project src/MaaMcpServer
```

Windows x64에서는 `linux-x64`를 `windows-x64`로, macOS에서는 `macos-arm64` 또는 `macos-x64`로 바꿉니다. 플랫폼 간 빌드 디렉터리를 공유하지 마세요. 설치 경로에는 Core, 런타임 의존성, `resource/`가 함께 있어야 합니다. `--resource-dir`로 `resource/`의 부모 디렉터리를 따로 지정할 수 있습니다.

## GitHub Actions 산출물 사용

기존 빌드 경로와 일치하는 `dev-v2` push(PR 병합 포함)는 전체 Release Pipeline 대신 **Battle MCP Bundle**을 자동 실행하며, 기본적으로 Windows/Linux x64 `RelWithDebInfo` 패키지를 생성합니다. 문서만 변경한 경우 MCP 빌드를 실행하지 않습니다. 다른 문서 및 검증 워크플로, PR 전체 빌드, `v*` 태그 릴리스와 수동 전체 빌드는 기존 실행 조건을 유지합니다.

수동으로 빌드하려면 자신의 저장소에서 **Actions → Battle MCP Bundle → Run workflow**를 열고 MCP 브랜치와 `configuration`을 선택합니다.

- `RelWithDebInfo`: 기본 전체 실행 패키지입니다. Python, uv, ADB, Visual Studio를 별도로 설치할 필요가 없습니다.
- `Debug`: 개발 및 테스트용입니다. Linux는 바로 실행할 수 있지만 Windows는 재배포할 수 없는 Debug CRT를 로컬에서 준비해야 합니다.

두 설정 모두 전체 Debug Core 테스트를 통과해야 완성된 패키지를 업로드합니다. Python 환경과 순수 Python 테스트, Debug Core, 독립 빌드에 필요한 `RelWithDebInfo` Core는 별도 작업에서 병렬 실행하며, Debug 설정에서는 Core를 한 번만 컴파일합니다. 이 워크플로는 GUI 빌드나 Release 게시를 하지 않습니다.

`platform`의 기본값은 `all`입니다. `win-x64` 또는 `linux-x64`를 선택하면 필요한 플랫폼만 빌드하여 runner 중복 사용을 피할 수 있습니다.

```bash
gh workflow run mcp-build.yml --ref <branch> -f configuration=RelWithDebInfo -f platform=all
```

관련 PR은 계속 Release Pipeline을 통해 같은 `Battle MCP` 작업을 실행합니다. `.github/workflows/mcp-build.yml`은 일반 및 Nightly 파이프라인에서 계속 공유합니다. 기존 **Release Pipeline → Run workflow**도 사용할 수 있습니다. `mcp_only`를 켜고 `mcp_configuration`과 `mcp_platform`을 선택하거나, 전체 빌드가 필요하면 `mcp_only`를 끄세요. 수동 MCP 빌드는 같은 브랜치의 PR 빌드에 의해 취소되지 않습니다.

전체 Release Pipeline의 `RelWithDebInfo` MCP 작업은 GUI, CLI 또는 다른 플랫폼을 기다리지 않고 같은 실행에서 해당 플랫폼의 x64 네이티브 런타임을 재사용합니다. 독립 빌드, `mcp_only`, Nightly는 Core를 직접 빌드합니다. 재사용 전에 소스 커밋, 플랫폼, 설정, 컴파일 옵션, 의존성과 Core 버전을 확인하며 `BUILD-INFO.json`에 소스 커밋과 실제 Core 버전을 함께 기록합니다. MCP의 ADB ControlUnit은 MaaFramework `v5.13.0`으로 고정하며 Linux 릴리스의 latest 버전과 섞지 않습니다. 테스트는 소요 시간과 JUnit 보고서를 출력하고 실패 시 진단 산출물을 보관합니다. `MCP-intermediate-*`와 `MAARuntime-*`는 단기 내부 산출물이며 실행 가능한 MCP 완성 패키지가 아닙니다.

성공한 실행의 **Artifacts**에서 `MAAMcp-win-x64-RelWithDebInfo` 또는 `MAAMcp-linux-x64-RelWithDebInfo`(Debug 선택 시 `-Debug`)를 다운로드하고 내부 `.zip` 또는 `.tar.gz`를 풉니다. `core/`(Core, 네이티브 의존성, 리소스, ADB ControlUnit), `python/`(독립 Python 및 잠긴 전체 실행/테스트 의존성), `adb/`, 시작 스크립트, 소스와 테스트, wheel/sdist, 의존성 라이선스, 소스 커밋과 실행 URL이 있는 `BUILD-INFO.json` 및 SHA-256을 제공합니다. Windows 실행 패키지에는 재배포 가능한 VC++ CRT도 포함됩니다. 저장소 복제, 재빌드, 온라인 Python 패키지 설치가 필요 없습니다. Linux는 호환 x64 OS와 시스템 glibc가 필요하며 컨테이너나 운영체제 자체는 포함하지 않습니다.

Windows x64에서는 다운로드 디렉터리에서 실행하세요:

```powershell
Expand-Archive .\MAAMcp-win-x64-RelWithDebInfo.zip .\mcp-test
Set-Location .\mcp-test
.\run-mcp.cmd --check
.\run-mcp.cmd --address 127.0.0.1:5555
```

Linux x64에서는 다운로드 디렉터리에서 실행합니다:

```bash
mkdir mcp-test
tar -xzf MAAMcp-linux-x64-RelWithDebInfo.tar.gz -C mcp-test
cd mcp-test
./run-mcp.sh --check
./run-mcp.sh --address 127.0.0.1:5555
```

`--check`는 내장 ADB, Python 의존성, Core 및 리소스 로딩을 확인하며 기기에 연결하지 않습니다. 일반 실행은 MCP 입력을 기다리는 stdio 서비스이며 웹 페이지가 없습니다. 클라이언트의 `command`에는 내장 `python/python.exe`(Windows) 또는 `python/bin/python3`(Linux), `args`에는 `["-I","-B","/absolute/mcp-test/run_mcp.py","--address","127.0.0.1:5555"]`를 지정합니다. 모든 경로는 절대 경로여야 합니다. 상태는 기본적으로 패키지의 `state/`에 저장하며 `--user-dir`로 다른 쓰기 가능한 디렉터리를 지정할 수 있습니다.

전체 오프라인 테스트는 Debug 패키지의 `run-mcp.cmd --self-test` 또는 `./run-mcp.sh --self-test`로 실행하며 기기에 연결하지 않습니다. Windows에서는 Visual Studio 2026/Build Tools의 C++ 데스크톱 워크로드와 Windows SDK를 설치하고 먼저 `powershell -NoProfile -ExecutionPolicy Bypass -File tools/PrepareMcpRuntime.ps1 -CoreDir core`를 실행합니다. **Debug CRT는 재배포할 수 없고 일반 VC++ Redistributable로 대체할 수 없습니다.** 스크립트는 로컬 개발 DLL만 복사하며 수정된 디렉터리는 재배포하면 안 됩니다. CI는 별도 Debug 디렉터리에서 테스트하고 산출물에서 Debug CRT를 제외합니다. `RelWithDebInfo`는 비활성화된 단언으로 인한 오해를 방지하기 위해 `--self-test`를 거부합니다.

실제 게임에서는 아래 연결 및 맵 확인 절차가 필요합니다. 이전 Core/리소스나 다른 커밋의 테스트를 섞지 마세요. 외부 정보 프로그램과 데이터 스냅샷은 선택적 플러그인이며 별도 설정이 필요합니다. 게임, 에뮬레이터, 자동 클라우드 모델 호출은 포함되지 않습니다.

Windows Debug와 MaaFramework Release ControlUnit은 ABI가 호환되지 않으므로 Debug 패키지에서 해당 선택적 DLL을 제외하고 `--touch-mode MaaFwAdb`를 거부합니다. 기본 `maatouch`, `adb`, `minitouch` 또는 전체 `RelWithDebInfo` 패키지를 사용하세요.

## 로컬 테스트

오프라인 단위 테스트, 실제 MCP stdio 연결, 모의 정보 서버 테스트에는 기기가 필요하지 않습니다.

```bash
uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests -q
uv run --project src/MaaMcpServer ruff check src/MaaMcpServer
uv run --project src/MaaMcpServer ruff format --check src/MaaMcpServer
```

실제 Debug Core의 작업 등록, 잘못된 인수 거부, 콜백, 맵 투영 확인도 기기 없이 실행할 수 있습니다. 합성 화면 테스트는 ADB 대신 로컬 모의 전송으로 실제 Core를 구동하여 정지 상태 인식, 취소, 시각 순서, PNG 대응 관계, 재전송을 확인합니다.

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest \
  src/MaaMcpServer/tests/test_native.py src/MaaMcpServer/tests/test_battle_control.py -q
```

PowerShell에서는 먼저 `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`을 설정한 뒤 같은 pytest 명령을 실행합니다. 리소스 경로가 다르면 그 부모를 `MAA_MCP_RESOURCE_DIR`로 지정합니다. 환경 변수가 없으면 이 테스트는 명시적으로 건너뜁니다. 오프라인 통과가 실제 게임 동작의 검증을 의미하지는 않습니다.

Windows 소스 Debug 빌드도 먼저 `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install`을 실행해야 하며 요구 사항은 Debug 패키지와 같습니다.

worker는 MCP 입출력을 상속하지 않고 전용 stdin/stdout 파이프가 있는 별도 프로세스로 실행됩니다. stderr에 PID와 `worker_started`, `core_load`, `resource_load`, `core_create`, `ready` 및 요청 단계를 기록합니다. 시작·연결 시간 초과는 마지막 단계를 표시하며 전투 결과 불명으로 표시하지 않습니다. 시간 초과나 취소 시 worker를 종료하고 제어 잠금을 해제합니다. 전투 명령 중단은 계속 `outcome_unknown`으로 처리하며 자동 재시도하지 않습니다.

`battle_status`와 `battle_connect`는 `project_version`, `build`(인접한 `BUILD-INFO.json`), `resources`(설정된 리소스 루트와 맵 색인 SHA-256)를 별도로 반환합니다. 매니페스트나 색인이 없으면 해당 값은 null이며 MCP SDK 버전을 프로젝트 버전으로 사용하지 않습니다. 연결 성공 후에는 `core_version`도 반환합니다. 연결할 맵의 색인과 파일을 고유 stage/level ID로 확인하며 `TL-EX-1` 같은 표시 코드는 거부합니다. 누락 시 요청 ID와 리소스 루트를 명시하고 자동 다운로드나 대체 맵 연결을 하지 않습니다. 결과의 `map_source`에는 맵 파일 SHA-256도 포함되지만 화면 검증이나 버전 간 호환성을 보장하지 않습니다.

선택적 실기기 스모크 테스트는 `MAA_MCP_NATIVE_CORE`, `MAA_MCP_LIVE_ADDRESS`, `MAA_MCP_LIVE_STAGE`를 명시한 뒤 `uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_stdio.py -k real_device`로 실행합니다. 리소스, ADB, 연결 설정은 각각 `MAA_MCP_RESOURCE_DIR`, `MAA_MCP_ADB_PATH`, `MAA_MCP_CONNECTION_CONFIG`로 지정할 수 있습니다. 다른 제어기를 중지하고 고유 ID와 일치하는 고정 맵 전투에 수동으로 진입하세요. 테스트는 연결, 맵 바인딩, 캡처, 종료만 하며 전투 재개·배치·포기는 하지 않습니다. 조건이 부족하면 명시적으로 건너뜁니다. 모의 worker 오프라인 통과가 Windows 실기기 검증을 의미하지는 않습니다.

## 실행 및 클라이언트 설정

동일 기기를 조작하는 다른 MAA/GUI 작업을 중지하고 고정 맵 전투에 수동으로 진입하세요. 계약 선택, 스테이지 진입, 다단계 카메라 이동, 연결 전에 배치된 오퍼레이터 복원은 지원하지 않습니다. 종료 판정은 아래의 제한적인 제목 인식만 지원합니다.

동일한 `--user-dir`에는 프로세스 간 제어 잠금이 적용됩니다. 같은 기기는 항상 같은 디렉터리를 사용하세요. 다른 디렉터리의 서버나 외부 GUI가 기기를 조작하는 것은 막을 수 없습니다.

지원되는 소환 카드는 시작 시 ID 열거가 필요하지 않습니다. 미확인 상호작용은 거부하고 입력 상태, 존재 증거, 요청 위치를 분리합니다. 완전한 배치/퇴각 루프는 아직 미완성이고 실기기 검증도 되지 않았습니다. 자세한 내용은 아래를 참고하세요.

```bash
uv run --project src/MaaMcpServer maa-battle-mcp \
  --core-dir "$PWD/build-mcp/install" \
  --adb-path /absolute/path/to/adb \
  --address 127.0.0.1:5555 \
  --user-dir "$PWD/build-mcp/session"
```

프로세스는 MCP 클라이언트의 입력을 기다립니다. 웹 페이지를 제공하거나 자동으로 기기에 연결하지 않습니다. 클라이언트 설정의 경로는 절대 경로로 바꾸세요. Windows JSON 경로는 `/` 또는 이스케이프한 역슬래시를 사용합니다.

```json
{
  "mcpServers": {
    "maa-battle": {
      "command": "uv",
      "args": [
        "run", "--project", "/absolute/repo/src/MaaMcpServer",
        "maa-battle-mcp", "--core-dir", "/absolute/repo/build-mcp/install",
        "--adb-path", "/absolute/path/to/adb", "--address", "127.0.0.1:5555",
        "--user-dir", "/absolute/repo/build-mcp/session"
      ]
    }
  }
}
```

`battle_connect`, `battle_attach(stage_name="main_01-07")`, `battle_observe(session_id=..., include_image=true, include_map=true)` 순으로 호출합니다. 스테이지 ID는 실제 화면과 일치해야 하며 호출자가 지정하므로 자동 인식이 아닙니다. 미확인 이름은 null입니다. `battle_identify`는 상세 화면을 열고 정지를 요청하므로 증거를 확인한 뒤 유한 `battle_step`을 사용하세요. full의 `battle_get_map`은 정적 타일, 두 화면 투영과 실시간 픽셀 변화를 반환합니다.

`battle_get_map`의 MCP 이미지는 현재 PNG(`image_path`), 기준 PNG(`baseline_image_path`) 순서입니다. 경로가 같으면 한 장만 첨부하며 유효한 기준이 없으면 기준 이미지를 첨부하지 않습니다. 호스트가 Core 파일 시스템에 직접 접근하지 않고 재인식할 수 있습니다. 변화 필드와 제한은 아래에 설명합니다.

수동 정지를 인계하려면 `battle_identify`의 엄격한 boolean 인수 `request.adopt_manual_pause=true`가 필요하며 기본값은 false입니다. 재개를 허용하지는 않습니다. Core는 명확한 정지 증거를 요구합니다. 실행 버튼이 없다는 이유만으로 정지로 판단하지 않고 입장 제목 전환도 인계하지 않습니다. 정지 상태에서 카드를 확인하고 부분 OCR에서도 `pause_verified`, `safe_state`, 같은 프레임의 `snapshot` / `image_path`와 PNG를 반환합니다. 저장 실패는 `evidence_error`에 기록하며 취소나 worker 장애로 최종 증거를 얻지 못해도 자동 재개하지 않습니다.

full의 이전 진입점 `battle_deploy` 예시입니다. 세션과 관측된 유닛 ID를 바꾸세요. 도구 인수에는 `request` 객체가 있습니다.

```json
{
  "request": {
    "session_id": "replace-with-session-id",
    "operation_id": "deploy-001",
    "unit_id": "replace-with-observed-unit-id",
    "location": [4, 3],
    "direction": "Left",
    "timeout_ms": 10000
  }
}
```

`battle_retreat`와 `battle_use_skill`은 `location`, `direction`을 제외한 같은 형식입니다. 새로운 행동마다 새 `operation_id`를 사용합니다. 같은 요청을 재전송하면 저장된 결과만 반환하며 동일 ID의 인수가 달라지면 거부됩니다. 비용 부족이나 재배치 대기는 즉시 실패하며 무한히 기다리지 않습니다. 증거 도구는 같은 프레임의 PNG와 관측을 클라이언트의 멀티모달 모델에 반환합니다. 서버가 클라우드 모델을 자동 호출하지는 않습니다.

단일 전술 행동은 사전 검사, 필요한 재개, 행동, 직후 캡처, 일시 정지 검증을 하나의 Core 명령으로 처리하는 `battle_step`을 우선 사용하세요.

```json
{
  "request": {
    "session_id": "replace-with-session-id",
    "operation_id": "step-001",
    "action": "skill",
    "unit_id": "replace-with-observed-unit-id",
    "adopt_manual_pause": false,
    "timeout_ms": 10000
  }
}
```

`action`은 `deploy`, `retreat`, `skill`, `wait`, `pause`, `speed`이며 분기별 인수는 앞의 Schema를 따릅니다. 수동 정지 재개에는 명시적 `adopt_manual_pause=true`와 새 정지 증거가 필요합니다. 호출당 행동은 최대 하나이며 경계를 분석한 뒤 정지를 요청하고 새 증거와 `pause_verified`를 반환합니다. pause/speed는 재개하지 않습니다. 취소 시 Core 정리를 기다리지만 worker 장애 시 정지가 미확인일 수 있습니다. `battle_step.timeout_ms` 기본값은 10000, 상한은 30000이며 예산을 임의 연장하지 않습니다.

`captured_at_ms`는 이미지 획득 직후, 분석 전에 기록한 호스트의 Unix 밀리초 시각으로 기기의 렌더링 시각이 아닙니다. `capture_time_snapshot`은 `capture_image_path`(행동 경계), `return_time_snapshot`은 `return_image_path`(이후 정지 확인)와 연결됩니다. full의 MCP 이미지는 반환 시점, 행동 경계 순서이며 minimal은 원본 프레임 resource를 제공합니다. `observation_id`는 획득 순서가 아닌 분석 순서대로 증가하므로 행동 경계의 ID가 더 커도 시각은 더 이를 수 있습니다. 시스템 시계 조정은 시간 차이에 영향을 주며 이 시각은 실행 시간의 엄격한 상한이 아닙니다. 동일 작업 재전송은 원래 시각과 경로를 보존하고 새 캡처에는 자체 획득 시각을 기록합니다.

`battle_identify`는 `identified_count`, `unresolved_count`, `ambiguous_count`와 카드별 `identity_reason`을 반환합니다. 이름이 빈 캐시 항목은 더 이상 상세 OCR을 억제하지 않습니다. 다른 행동이 `outcome_unknown`을 반환하면 MCP `isError=true`이며 `helper_returned=true` 또는 `action_returned=true`도 게임 내 성공을 증명하지 않습니다. 새 증거를 확인하고 무작정 재시도하지 마세요. 위치 출처는 `action_history_unverified`, 적 상태는 null이며 모델의 직접 상태 덮어쓰기는 지원하지 않습니다. 별도의 일시 정지 요청은 정지 확인이 아니며 재개는 이 서버가 요청하거나 명시적으로 인계한 정지에만 허용됩니다.

일반 명령의 `timeout_ms` 상한은 30000이며 아래 전투 전 일괄 확인은 별도 상한을 사용합니다. 일반 명령 시간 초과/취소 시 worker를 종료하고 세션을 무효화하여 미확인 결과를 저장합니다. step은 정지 정리를 기다리지만 기기 장애 시 정지를 보장하지 않습니다. `battle_close`는 일반 명령을 중단하거나 step/전투 전 확인 정리를 기다리며 자체적으로 게임을 정지/종료하지 않습니다. `battle_get_operation`은 영구 결과를 읽고 저장 실패는 명시적으로 알리며 이 서비스의 후속 명령을 차단합니다. `--user-dir`에 로그와 `operations.sqlite3`를 저장하고 일반 이미지는 최근 128장, 중요 증거는 별도 보관하므로 직접 정리해야 합니다.

## 선택적 정보 서버

정보 서버와 데이터 스냅샷을 별도로 준비한 뒤 `--intel-config /absolute/intel.json`을 사용합니다. 이는 신뢰하는 로컬 시작 설정이며 모델이 임의 프로그램을 실행하는 인터페이스가 아닙니다.

```json
{
  "provider": "intelligence",
  "command": "uv",
  "args": ["run", "--project", "/absolute/arknights-mcp", "arknights-mcp", "serve", "--transport", "stdio"],
  "region": "cn"
}
```

PRTS는 `{"provider":"prts","command":"prts-mcp","region":"cn"}`으로 설정할 수 있습니다. 어댑터는 stdio와 구조화 출력을 요청합니다. `intel_query`는 `stage/operator/enemy`를 지원합니다. Intelligence는 게임 고유 ID를, PRTS는 스테이지 ID 또는 오퍼레이터/적의 중국어 이름을 사용합니다. Intelligence의 `routes_page`, `spawns_page`는 1부터 시작하며 페이지당 50개입니다. 출처, 제한, 페이지 정보를 보존하며 5분간 캐시합니다. PRTS 스테이지 조회는 적 정보만 반환하며 타일, 경로, 웨이브는 제공하지 않고 다른 지역 데이터로 조용히 대체하지도 않습니다. 정적 정보는 실시간 전황이 아니며 외부 맵을 자동 병합하거나 보정하지 않습니다.

PRTS 매핑은 `stage → get_stage_enemies(stage_id)`, `operator → get_operator_basic_info(name, action=skills)`, `enemy → get_enemy_info(name)`이며 `PRTS_TRANSPORT=stdio`, `PRTS_OUTPUT_CHANNEL=both`를 요청합니다. 상위 서비스의 `structuredContent`는 `data`에, `content`는 별도로 원형 보존합니다. 구조화 결과가 없으면 `data=null`이며 상위 `isError`를 성공으로 캐시하지 않습니다. 제공된 꺾쇠 내부 텍스트, blackboard와 조건 필드는 유지하지만 설명 token 치환, 상위에서 누락된 텍스트 복구, 잠재/특화/모듈 조건 추론은 하지 않습니다. `action=skills`는 완전한 기본 정보/재능 조회나 이 계정의 실제 구성 측정이 아닙니다. 다른 provider나 raw 데이터와 자동 결합하지 않습니다.

## 제한 시간 대기와 판단 자료

클라이언트에서 재개와 일시 정지를 따로 호출하지 않고 대기할 수 있습니다.

```json
{
  "request": {
    "session_id": "replace-with-session-id",
    "operation_id": "wait-001",
    "action": "wait",
    "duration_ms": 1000,
    "adopt_manual_pause": true,
    "timeout_ms": 10000
  }
}
```

`duration_ms`는 100–8000이며 `timeout_ms`에 최소 1000 ms의 여유가 있어야 합니다. 유닛, 위치, 방향 인수는 허용되지 않습니다. 시간은 게임 시뮬레이션 시간이 아닌 호스트 단조 시계 기준이며 캡처·분석 비용이 실제 소요 시간에 추가됩니다. Core는 진행 중 샘플을 수집하고 마지막 최대 8프레임과 경계/반환 프레임을 보관합니다. 취소나 종료 시 진행을 중단하고 정지를 시도합니다. 취소, 기한 초과, 증거 저장 실패, 정지 미확인은 정상 대기 성공이 아닙니다. 외부 일시 정지는 `wait_interrupted`를 반환하며 임의로 다시 재개하지 않습니다. 종료를 확인하지 못한 비전투 프레임을 관측하면 이후 일시 정지된 전투를 인식해도 `outcome_unknown`과 `session_input_blocked=true`를 반환합니다. 기기 I/O 정지나 프로세스 장애 시에는 정지가 불가능할 수 있습니다.

`terminal`은 `victory`, `defeat`, `unknown`, `still_in_battle`을 구분하며 인식 출처, 원시 일치 신뢰도와 `terminal_evidence_path`를 반환합니다. 현재 리소스는 명확한 중국어 간체/영어 성공·실패 제목만 포함하며 모든 언어와 화면의 실기기 지원을 보장하지 않습니다. 총 처치 수 도달, 검은 화면, 전투 이탈은 승리를 뜻하지 않고 대기 트랜잭션 성공도 승리가 아닙니다. 비전투 화면을 관측한 세션은 후속 입력을 거부하며 메뉴에서 정지 버튼을 누르지 않습니다. 이탈 증거는 일반 128장 순환 삭제에서 제외되므로 직접 정리해야 합니다. 사용자 증언은 이미지 증거와 분리하세요.

분리된 제목 보완은 조작 가능한 전투 화면을 이미 관측했고 현재도 전투 UI가 인식되며 실행 중 버튼이 보이지 않을 때만 위쪽의 완전 일치 단어 `MISSION`과 아래쪽의 `ACCOMPLISHED`를 인정합니다. 두 단어는 같은 프레임에서 각각 0.9 이상의 점수를 얻고 글자 영역 높이가 비슷하며 왼쪽 정렬된 인접 행이어야 합니다. 서로 다른 캡처의 조각은 합치지 않습니다. `terminal.split_victory`는 영역, 점수, 인정 또는 거부 이유를 보존하며 인정 시 출처는 `BattleControlVictorySplit`, 신뢰도는 두 점수 중 낮은 값입니다. 완전한 성공 제목과 분리된 성공 제목은 충돌하지 않지만 패배도 확인되면 unknown과 입력 격리를 유지합니다. 단독 `MISSION`이나 일반적인 `MISSION RESULTS`/行动结束는 승리 증거가 아닙니다. 합성 회귀는 실제 애니메이션 제목이나 독립적인 결과 화면의 인식을 보장하지 않습니다.

완전한 정적 조회는 `battle_attach(stage_name="act55side_ex01", mode="normal")`로 명시적으로 연결한 뒤 `intel_query(kind="stage", identifier="act55side_ex01", complete_stage=true, session_id="…")`를 호출합니다. 기존 provider를 재사용하고 페이지 캐시를 우회하며 최대 100회 요청/120초로 제한합니다. 지역, 스테이지, 모드, 페이지 간 출처 및 중복 ID를 검사합니다. 구조화 계약에는 `game_id`, `server`, `mode`, `provenance`, `routes_page`/`spawns_page`의 `page`, `has_more`(선택적 `total`), 최상위 `routes`/`spawns` 배열 또는 페이지 내 `items`가 필요합니다. 경로는 `route_id`, 생성 항목은 `spawn_id`, `enemy_game_id`, `route_id`, `count`를 사용합니다. 알 수 없는 provider 형식을 추측하지 않습니다.

`coordinate_system="public_row_col"`은 연결된 맵 높이로 `x=col, y=height-1-row`를 계산해 점에 `maa_location`을 추가하고 2차원 타일 배열 행 순서를 뒤집습니다. 원본 응답은 `pages`에 남습니다. `maa_grid`는 변환하지 않으며 다른 좌표계는 미확인입니다. 필드/페이지 누락이나 provider partial은 `partial`과 섹션별 `completeness`를 반환하며 누락을 빈 집합으로 바꾸지 않습니다. 식별/버전 충돌과 provider 실패는 오류입니다. `expires_at`은 Unix 초이며 만료를 명시합니다. 생략 시 최신 여부를 알 수 없고 5분 캐시는 최신 게임 데이터 보장이 아닙니다. 실제 provider 검증이 필요하며 모의 서비스 성공은 다섯 정보 범주의 완전성이나 동적 소환 개체 수를 증명하지 않습니다.

`--loadout-config /absolute/loadout.json`으로 현재 전투 구성을 제공합니다. 시작 시 읽으므로 수정 후에는 서비스를 재시작해야 합니다. 아래는 관측 결과가 아닌 예시입니다.

```json
{
  "region": "cn",
  "stage_id": "act55side_ex01",
  "mode": "normal",
  "confirmed_by_user": false,
  "operators": [
    {"operator_game_id": "char_4228_closur", "actual_elite": 2, "equipped_skill_id": null}
  ]
}
```

지역, 스테이지와 명시적 모드가 일치해야 합니다. `actual_level`, `potential`, `module_id`, `module_level`, `skill_level`, `mastery`도 선택적으로 입력하며 생략하면 unknown입니다. 스냅샷의 `decision_context`는 필드별 출처, 사용자 확인, 설정 해시를 별도로 보존하고 원시 관측을 덮어쓰거나 사용자 확인을 시각 검증으로 취급하지 않습니다. SP, 스킬 단계, 지속 시간과 재능은 여전히 미확인이며 모호한 game ID 후보의 첫 항목을 임의 선택하지 않습니다.

full의 `battle_evaluate_placement(session_id, placements)`는 Core 공통 기하 구현을 재사용하고 Python에 별도 투영 알고리즘을 두지 않으며 후보 전체 진형을 읽기 전용으로 검사합니다. 각 항목에 `operator_game_id`, `location`, `direction`을 넣고 정예 단계는 설정에서 가져오거나 `assumed_elite`로 가정을 명시합니다. 기본 범위, 기하학적 아군 커버, 정적 배치 호환성과 타일 충돌을 반환합니다. 정예 단계/범위를 모르면 직군 기본값을 대입하지 않습니다. 실제 점유, 치료 대상 자격, 생존이나 최적성을 검증하지 않고 스킬, 모듈, 재능과 특수 지형 효과도 포함하지 않습니다. 실시간 적 추적은 계속 미지원입니다.

## 장면 증거, 준비 관측 및 제한된 조작

minimal에서는 `battle_observe(duration_ms=..., ...)`로 유한 읽기 전용 복구 증거를 수집합니다. 아래 독립 캡처/종료/전투 전 보조 도구는 full 전용입니다.

`scene`은 `running / paused / transition / terminal / unknown`을 구분합니다. 모든 샘플에서 종료 증거를 확인하고 명확한 종료 증거를 잔존 전투 UI보다 우선합니다. 전투 확인 후 전투 내부의 `transition`은 `input_block_reason=pause_verification_pending`으로 입력을 막으며 곧바로 영구 종료 격리로 처리하지 않습니다. 복구하려면 명확한 일시 정지 증거가 있는 새 화면을 연속 두 번 캡처해야 하며 실행 중 또는 전환 프레임은 횟수를 초기화합니다. 실행 버튼이 없다는 사실만으로 일시 정지를 판정하지 않습니다. 정지 마무리 단계의 읽기 전용 확인 시간은 3000 ms와 관측된 최대 프레임 캡처·분석 시간의 두 배에 1000 ms를 더한 값 중 큰 값으로 정하여 두 프레임 확인과 폴링 시간을 확보합니다. 다만 항상 명령의 남은 기한을 상한으로 삼으며 취소되면 확인을 멈춥니다. 계속 불확실하면 입력 차단을 유지합니다.

`battle_observe`, `battle_capture_evidence`, `battle_collect_terminal(request)`은 입력 없이 복구 증거를 수집할 수 있습니다. `battle_collect_terminal.duration_ms`는 100–8000이며 `timeout_ms` 이하여야 합니다. `status=succeeded`는 수집 완료이지 정지 복구나 전투 승리가 아니므로 `pause_verified`, `session_input_blocked`, `terminal`을 확인하세요. 잠금 중 접수된 변경 명령은 해당 관측으로 복구되어도 실행되지 않습니다. 새 증거를 확인하고 새 작업을 요청하며 필요하면 수동 정지 인수를 명시적으로 허용해야 합니다. 과거 동작 재실행이나 결과 변경은 없습니다. 전투 이후 미확인/비전투 화면, 종료/충돌 증거, 속도 재확인 실패는 `input_block_reason=terminal_or_safety_quarantine`으로 영구 격리되며 정지 증거로 해제할 수 없습니다. `session_terminal`은 결과와 이미지 경로를 유지하며 검은 화면으로 확정 결과를 지우지 않고 승패 충돌은 unknown으로 유지합니다. 추가 Core 명령은 `set_speed`, `finalize`, `prepare`, `bind`입니다. 준비 작업은 `preparation_only=true`로 초기화하고 같은 컨트롤러로 연결합니다.

정지 인식은 먼저 `PAUSE`의 완전 일치를 요구합니다. 0.9에 도달하지 못하면 `[560,350,160,70]` 영역에서 중국 서버의 `暂停中`을 확인합니다. 중국어는 단일 상자의 완전한 단어 또는 한 번의 OCR에서 얻은 공간 순서상의 같은 행 조각 2~3개를 허용하며 각 조각에 0.9 이상을 요구합니다. 상자 간격과 겹침은 제한된 범위만 허용하고 누락 문자, 추가 접두사/접미사, 다른 행, 과도한 겹침 및 영역 밖 상자는 거부합니다. 서로 다른 프레임의 조각을 결합하지 않습니다. 단일 상자의 source는 `BattleControlPausedLocalized`, 분할 상자는 `BattleControlPausedLocalizedSplit`입니다. `pause_evidence`는 `fragments`, 전체 텍스트, 조각의 최저 `score`, 결합 영역, 프레임 ID/시각 및 `accepted`/`rejection_reason`을 보존합니다. `PAUSEX`는 대체 증거가 아닙니다. 실행 중 버튼과 종료 판정이 우선하며 격리 해제에는 여전히 연속 두 프레임이 필요하고 정지 소유권을 자동으로 인수하지 않습니다.

정지 인식 실패 시 `pause_diagnostics`는 동일 프레임 ID/시각, 설정 ROI, 요구 점수, 전처리 설정과 null이 가능한 일치 결과를 보존합니다. `localized.candidates`와 정식 중국어 규칙은 대상 텍스트 필터를 제외한 한 번의 OCR 수집을 공유하며 `candidate_source=same_frame_localized_pause_rule_ocr`로 표시합니다. `localized.fragment_rule`은 수락 여부와 거부 이유를 기록하며 규칙이 수락한 경우에만 정지 증거가 됩니다. 개별 후보만으로 증거가 되지는 않습니다. 영어 진단 후보는 여전히 별도의 필터 없는 OCR에서 수집되며 정식 일치를 대체하지 않습니다. null은 낮은 점수의 측정값이 아닙니다. 네이티브 기하 회귀는 보고된 겹침 상자를 다루고 합성 텍스트 회귀는 정식 OCR 경로를 사용하지만 게임 글꼴, 효과 가림 또는 다른 서버 지원을 검증하지는 않습니다. 배치 후 실제 OCR, 1x/2x 및 전체 전투 검증은 여전히 로컬 기기에서 해야 하며 속도 임계값은 변경하지 않았습니다.

`skill_ready`는 true 또는 null이며 분류기 부정을 확인된 쿨다운으로 취급하지 않습니다. `skill_evidence`는 동일 프레임 ID, 시각, ROI, 기준점, 분류 점수와 확률을 보존합니다. `probabilities` 순서는 `c, n, y`이며 `score`는 최상위 클래스의 점수로, 반드시 준비 클래스 `y`의 점수는 아닙니다. `y`가 최상위이고 0.9 이상일 때만 true를 보고합니다. 발동 단계/탄약/쿨다운은 미확인일 수 있고 점수는 보정된 정확도가 아닙니다. `asst.log`의 `Battle frame` / `Battle command result`, `battle-control/<session>/`의 PNG, `operations.sqlite3`를 보관하세요. 합성 회귀는 실제 OCR, ROI, 버튼 호환성 확인이 아니므로 로컬 재검증이 필요하지만 로컬 이미지를 클라우드 개발의 필수 입력으로 요구하지 않습니다.

연결 후 attach 전에 `battle_observe_preparation(stage_name, mode)`로 수동으로 연 편성 화면을 입력 없이 읽습니다. 이름, 후보 ID, 이름 영역과 증거를 반환하며 선택 스킬, 육성 상태, 지원 여부는 미확인이고 상세 화면을 열지 않습니다. 최신 결과를 `battle_attach(..., preparation_id=..., confirm_preparation_unchanged=true)`로 연결합니다. 이는 호출자의 재확인이지 지속적인 시각 검증이 아닙니다. 편성/스킬/스테이지/계정 변경 후 `battle_invalidate_preparation`을 호출하고 다시 수집하세요. 재수집, 연결 소비, 종료 후에는 이전 결과를 재사용할 수 없고 현장 식별 불일치는 `decision_context.preparation`에 충돌로 기록됩니다.

네이티브 일괄 확인은 별도의 **변경 도구** `battle_inspect_preparation`을 한 번 호출합니다.

```json
{
  "request": {
    "operation_id": "preparation-001",
    "stage_name": "main_01-07",
    "mode": "normal",
    "timeout_ms": 120000
  }
}
```

Core는 레벨 표시로 점유 슬롯을 찾고 이름을 읽지 못한 항목도 유지하며 레벨 OCR과 정예 1/2의 명확한 템플릿을 읽습니다. 이후 유일하게 식별된 일반 멤버의 초상화를 순서대로 열고 상세 화면의 이름을 확인한 다음 스킬 탭만 열어 횟수 제한 안에서 스크롤합니다. 완전하고 순서가 확인된 목록에서 선택 표시가 하나일 때만 `selected_skill=1|2|3`을 반환합니다. `equipped_skill_id`는 유일한 신원과 `battle_data.json`의 순서 있는 `skillIds`도 필요하며 정적 ID만으로 실제 장착을 판정하지 않습니다. 오퍼레이터 선택 카드, 스킬 선택지, 편성 확정, 시작 버튼은 누르지 않습니다. 지원 상세는 아직 건너뛰며 정예 템플릿 미검출은 null이고 **정예 0으로 추정하지 않습니다**. 레벨 실패도 1로 대체하지 않습니다. 빈 슬롯은 멤버가 아니며 레벨 표시 미검출은 편성 전체의 완전성을 증명하지 않습니다.

`snapshot.scan_status=complete|partial`, 멤버별 `fields`(값, 출처, 검증, 이유, ROI, 프레임 증거), `evidence_frames`, `formation_restored`, `roster_unchanged`, `preparation_valid`를 확인하세요. complete는 검출한 멤버만 포함하며 partial은 완전한 구성을 뜻하지 않습니다. PNG 경로와 캡처 시각은 원본 프레임에 대응하고 선택 스킬에는 프레임 간 스크롤 순서도 포함됩니다. 기본 기한은 120000 ms, **최대 180000 ms**로 일반 명령의 30000 ms와 다릅니다. 취소/닫기는 Core에 알리고 제한된 정리를 기다립니다. 이름이 일치하는 확인된 상세 화면에서만 원래 기한 내 뒤로 가기를 한 번 허용하며 미확인 화면은 무작정 닫지 않습니다. 작업자 실패 시 복구 상태는 미확인입니다. 같은 작업 ID에는 같은 인수만 사용하세요. 재요청은 원래 증거만 반환하며 화면 이동이나 무효 스냅샷 재활성화를 하지 않습니다. attach 전 영구 결과는 `battle_status.preparation_session_id`로 `battle_get_operation`에서 조회합니다.

상세 화면 열기와 복귀는 새 프레임으로 제한 검증하며 각 전환은 원래 기한 안에서 최대 3초, 새 스캔 전에는 정리용 5초를 확보합니다. 초상화는 한 번만 클릭하고 확인된 편성 화면에 남아 있으면 입력 없이 기다립니다. 다른 멤버나 미확인 화면에서는 스크롤, 확정, 뒤로 가기 권한 완화를 하지 않습니다. 새 증거로 원래 편성과 명단이 그대로임을 확인하면 뒤로 가기 입력을 보내지 않습니다. `evidence_frames.purpose`는 샘플링 목적이고 `page`는 실제 인식 결과이며 상세에는 `target_identity_verified`도 포함됩니다. 목적을 인식 증거로 취급하지 마세요. `error`는 이동/중단 원인을 유지하고 `recovery_error`는 복구 실패를 별도 보고합니다. 작업 `status`, 필드 완전성 `scan_status`, 연결 가능성 `preparation_valid`는 다릅니다. partial이 자동 무효는 아니며 complete도 미검출 슬롯이 없다는 증명이 아닙니다.

상세 화면 열기와 복귀 후 검증은 전환 중/미확인 프레임에서도 읽기 전용으로 제한 관측하며 첫 애니메이션 프레임만으로 실패하지 않습니다. 추가 입력이나 관측 시간 재시작은 하지 않습니다. 계속 미확인이면 같은 3초/원래 기한 안에 멈추며 다른 멤버나 편성 변경을 인식하면 즉시 멈춥니다. 취소는 새 상세 스캔을 중단하며 이미 보호된 뒤로 가기 이후의 읽기 전용 정리도 원래 기한 안에서만 수행합니다. 증거 저장 실패 후에는 폴링을 계속하지 않습니다. 미확인 프레임으로 스크롤, 확정, 뒤로 가기 입력을 허용하지 않습니다.

원래 동명 후보는 `game_ids`에 유지하며 정적 개체 직업으로 장치/소환물과 미확인 분류를 제외한 뒤 `operator_game_id`를 유일하게 결정합니다. `fields.identity`에는 `ordinary_operator_game_ids`, 매핑 출처와 이름 프레임 증거를 보존하며 일반 오퍼레이터 후보가 여러 개면 미확인입니다. 스킬 ID 미확인 이유는 `identity_unresolved`, `selected_skill_not_observed`, 슬롯 관측 후에만 보고하는 `skill_id_mapping_unavailable`로 구분하며 레벨, 정예 0, S1 기본값은 넣지 않습니다. 후속 필드 연결은 전투 자체 후보가 유일하고 전투 전 신원에 증거가 있을 때만 허용하며 편성 명단으로 현장 후보를 좁히거나 동명 장치를 일반 오퍼레이터와 연결하지 않습니다. 이름만 관측한 스냅샷은 구성 미확인을 유지합니다.

레벨 진단은 사용 가능한 `text`, `score`, `roi`와 원본 프레임 증거를 유지합니다. `fields.actual_level.roi_within_frame=false`이면 미확인으로 두며 잘린 영역을 완전한 레벨 OCR로 읽지 않습니다. 실제 글꼴/배율 보정이 완료되었다는 의미는 아닙니다.

유효한 최신 결과만 기존의 명시적 재확인 attach에 사용할 수 있습니다. 새 관측은 이전 연결을 무효화하며 화면/편성 변경, 증거 저장 실패, 중단된 스캔은 유효한 연결을 만들지 않습니다. `decision_context.preparation.observed_fields`는 네이티브 관측을 입력된 구성과 분리해 유지하고 충돌을 기록하지만 어느 쪽도 덮어쓰지 않습니다. 이는 전투 전 측정이지 계정/스테이지/구성의 지속 검증이나 실시간 전투 측정이 아닙니다. 기존 리소스를 재사용하는 인식기는 합성 Debug Core 회귀가 있지만 **실기기 검증은 아닙니다**. 실제 화면 배치와 선택 표시 호환성은 로컬에서 확인해야 합니다.

### 오프라인 유닛 정보와 소유 개체

`battle_get_unit_info(game_id)`는 기기 연결이나 정보 provider 없이 번들 `resource/unit_info.json`에서 `unit`, `source`, `resource_sha256`, `scope`를 반환합니다. TOKEN/TRAP/오퍼레이터 분류, 효과, 기본 속성 키 프레임과 범위 ID, 스킬 참조/정의, 재능 blackboard, 조건부 `tokenKey`/`overrideTokenKey` ID 관계를 보존합니다. 정적 표의 값은 현장 카드 `cost`/`available`/`cooling`이나 실제 장착 상태 측정이 아닙니다. 사용자 설정으로 시각 ID의 모호함을 해결하지 않습니다.

`base_range_definitions`는 `base_range_coordinate_system`에 명시된 게임 상대 행/열 좌표를 보존하며 시각적으로 측정한 MAA 타일이 아닙니다.

능력 필드는 `value=true|false|null`, `source`, `reason`, `game_data_value`, `live_verified`입니다. 검토된 adapter 값은 제한된 상호작용 계약이며 엔진 정책이나 실기기 합격의 증거가 아닙니다. `execution_support`는 입력 시도만 가능한 작업과 미지원 작업을 별도로 표시합니다. unknown은 허가가 아닙니다. 첫 소환 adapter는 메딕프로브 `token_10000_silent_healrb`의 일반 드래그 및 `direction="None"`입니다. 다른 TOKEN/TRAP은 미확인으로 거부하며 동일 타일 공존과 소환물 스킬은 지원하지 않습니다.

`battle_step(action="deploy", unit_id=..., card_id=...)`를 사용합니다. `game_id`는 종류, `card_id`는 관측 카드, `instance:<operation_id>`는 소유 배치 시도입니다. 이름의 유일성보다 구체적 카드 선택을 먼저 하므로 동명 카드를 구별하지만 게임 ID는 유일해야 합니다. 재개 후 신원, 레이아웃, 외관, 가용성 및 30초 신선도를 재검사합니다. `--allow-summon` 생략은 지원 adapter를 허용하며 이전 옵션은 경고와 함께 명시적 제한으로 유지합니다. 미지원 상호작용을 허가하지 않습니다. 네이티브 `allowed_summons=[]`는 모든 소환을 제외하고 필드 생략만 지원 카드를 활성화합니다.

C++ 타입으로 presence, 위치 증거 및 작업 상태를 분리합니다. 입력 및 비용/재배치 상태 변화는 배치 확인이 아닙니다. 변경형 유한 상세 검사는 두 프레임의 이름/아이콘 증거를 기록하지만 이번 개체의 존재나 선택 타일을 증명하지 않습니다. 미확인 개체는 요청 타일을 예약합니다. `battle_step(action="retreat", unit_id="instance:...")`는 능력을 먼저 확인하고 동종/이웃 개체의 모호함을 거부한 뒤 신원 및 긍정적인 퇴각 버튼을 확인합니다. **실제 선택 타일 anchor 및 명확한 이탈 규칙이 아직 없으므로 anchor 미확인 시 퇴각 버튼을 누르지 않으며 미확인 점유를 해제하지 않습니다.** 버튼 미검출은 `selection_unverified`로, 영구 퇴각 불가 추정이 아닙니다. 명시적 false만 `not_retreatable`입니다. 수동 관찰은 클릭/재개하지 않고 시간이나 단일 프레임 미검출로 소멸을 판정하지 않습니다. 검증된 종결 화면은 이탈을 기록할 수 있습니다. 재전송은 저널/증거를 보존하고 재입력하지 않으며 재시작/중도 접속 복원은 지원하지 않습니다.

긍정적인 상세 프레임은 선택한 종류의 존재(`selected_kind_present`)만 보여 주며 이번 배치 성공은 증명하지 않습니다. 타일 anchor 없이는 소유 개체 `presence`가 unknown입니다. 퇴각 아이콘 일치는 `retreat_button_detected`이지 활성화 증명이 아니며 가용성 규칙이 없어 `retreat_button_available`은 null입니다.

이전 `battle_deploy`는 선택적 `card_id`를 받으며 이전 배치/퇴각/스킬 진입점도 동일 유한 사전 검사 및 일시정지 정리를 사용합니다. 시작 시 실행 중 전투가 필요하고 능력/개체/위치 검사를 우회할 수 없습니다. 선택 타일 anchor 없는 일반 오퍼레이터 스킬/퇴각도 미검증 이력으로 실행하지 않고 거부합니다.

TL/TL-EX는 `act55side_*` ID를 사용합니다(예: `act55side_ex01`, `TL-EX-1` 아님). 도전 모드는 `act55side_ex01#f#`와 명시적인 `mode="challenge"`를 사용합니다. 이제 스테이지 전체의 배치를 거부하지 않고 대상 타일의 불변 규칙을 확인합니다. 지원되는 저지대 `tile_road`/`tile_fence_bound`/`tile_infection`은 기존 배치 유형 검사를 유지하며, `tile_trench`는 판자의 유무와 무관하게 배치 가능한 일반 원거리 오퍼레이터만 허용합니다. 판자로 덮인 참호에서는 기절할 수 있으므로 전술적 영향은 호스트가 판단해야 합니다. 참호의 근거리/소환물 배치와 미지원 동적 상호작용은 계속 `dynamic_terrain_unknown`을 반환합니다. 픽셀이 같아도 권한은 확대되지 않으며 카드, 점유, 능력, 인스턴스 검사는 유지됩니다. 완전한 전투 루프나 실기기 검증을 뜻하지 않습니다.

`battle_get_map`은 이제 읽기 전용으로 현재 PNG, `snapshot`, `map`을 반환하며 정적 `map.tiles`는 변경하지 않습니다. 관측/캡처/행동 스냅샷과 `map.dynamic_overlay`는 같은 프레임의 `observation_id`/`captured_at_ms`를 사용합니다. 최초의 조작 가능한 1280×720 전투 프레임을 고정 `baseline`으로 삼아 PNG 경로를 보존하고 세션 중 교체하지 않습니다. 이는 **최초 관측이지 게임 시작 상태가 아닙니다**. 중간 접속에서는 판자 변화나 적설이 이미 있을 수 있습니다. 첫 캡처 전 바인딩은 `status="not_observed"`입니다.

`dynamic_overlay.tiles`는 `location`, 고정 지도 투영의 `roi`/`screen_polygon`, `changed_from_baseline`을 제공합니다. true는 다각형 내부 픽셀 차이, false는 완전한 일치, null은 기준 생성 중 또는 이미지 범위 밖을 뜻합니다. 장면을 사용할 수 없으면 `status="unavailable"`, `tiles=null`이며 이전 결과를 재사용하지 않습니다. `exact_masked_pixels`에는 노이즈 허용치가 없고 `scene_changed_from_baseline`은 일시정지 표시 등의 장면 차이를 알립니다. 호스트가 기준 및 현재 이미지로 재확인해야 합니다. 판자, 적설, 유닛, 효과, UI, 캡처 노이즈 모두 변화 원인이며, 이미지가 같아도 가림이 없다는 뜻은 아닙니다. 지형, 기믹 상태, 시각적 덮개는 겹칠 수 있으므로 Core는 상태 목록을 추측하지 않으며 `semantic_state="unknown"`, `static_geometry_is_current_terrain=false`를 유지합니다. 차이는 배치 성공, 이탈, 선택 타일, 배치 가능성을 증명하지 않고 점유를 해제하지 않습니다.

｢自爆榴弹｣(폭발하는 스테이지 장치) `trap_341_dsjtzd`는 계속 오프라인 조회 가능하며 기본 비용 20, 재배치 20초, 범위 `x-4`, 나열된 스킬 없음, 재능 blackboard 기절 5초입니다. **재배치 시간은 폭발 카운트다운이 아닙니다.** 드래그/퇴각 지원은 추가하지 않으며 시간이나 픽셀 변화로 폭발/소멸을 추정하지 않습니다. 타일 변화 보고와 배치 불변 규칙은 판자 상태 인식이 아니며 실제 이미지 및 전체 전투 검증은 추후 필요합니다.

Debug `res_updater`를 빌드한 후 고정 버전의 로컬 캐릭터/스킬 표로 재생성합니다.

```bash
python tools/ResourceUpdater/generate_unit_info.py \
  --updater build-mcp/bin/Debug/res_updater \
  --input path/to/excel --output resource/unit_info.json
```

`tools/ResourceUpdater/unit_info_source.json`은 저장소, commit, 지역 및 캐릭터/승격 캐릭터/스킬/범위 네 입력 SHA-256을 고정하고 wrapper는 검증 후 단일 C++ generator를 호출합니다. 실행 중 다운로드/Wiki가 필요하지 않습니다. 생성 JSON은 한 레코드당 한 줄이며 Prettier에서 제외합니다. 일반 리소스 갱신은 입력 폴더에 `unit_info_source.json`이 있을 때만 보조 파일을 재생성합니다. 선언한 출처를 갱신하면 재생성/검증해야 하며 해시는 설치 게임 호환성을 증명하지 않습니다. 외부 raw 스테이지/적 스냅샷은 별도 설정이며 번들에 포함되지 않습니다.

`battle_set_speed(request)`은 `session_id`, `operation_id`, `multiplier=1|2`, `timeout_ms`를 받습니다. 명확히 정지된 상태에서만 속도를 읽고 필요하면 한 번 전환한 뒤 재확인하여 전후 증거를 반환합니다. 정지 소유권을 바꾸거나 자동 재개하지 않으며 2배속 중복 요청이 1배속으로 되돌리지 않습니다. 속도 미확인 시 클릭하지 않고 변경 후 검증 실패 시 입력을 잠급니다. 가속 판단은 전적으로 호스트가 합니다. 대기는 호스트 시간이며 관측한 캡처 비용으로 정리 예산을 확보하고 부족하면 재개를 거부합니다. `cleanup_reserve_ms`, `resumed_interval_upper_bound_ms`, `deadline_margin_ms`는 정확한 게임 시간 보장이 아닙니다.

1X는 0.9 이상의 OCR을 우선합니다. 부족하면 `BattleControlSpeedOneTemplate`이 같은 속도 영역에서 기존 `BattleSpeedUp.png`를 재사용하며 동일하게 0.9 이상을 요구합니다. `one_x_ocr_evidence`와 `one_x_template_evidence`는 개별 결과를, `one_x_evidence`는 채택 결과를 보존합니다. 2X는 여전히 `BattleSpeedUpCheck`를 사용하고 1X/2X 증거 충돌 시 unknown을 유지합니다. 2X가 인식되지 않았다는 이유만으로 1X를 추론하지 않습니다. 템플릿 회귀는 보고서의 게임 스크린샷 검증이 아닙니다.

## 고정 원본 스테이지 스냅샷

`--intel-config`의 `provider="raw"`는 신뢰할 수 있는 로컬 파일만 읽으며 자동 다운로드하지 않습니다. `root`(절대 디렉터리), `repository`, `commit`(40자리), `data_version`, `region`, `stages`, `enemy_database`를 설정합니다. `stages`는 stage ID를 `{"path":"relative/path","sha256":"64-character hash"}`로 매핑하며 `enemy_database`도 같은 구조입니다. 파일마다 64 MiB 제한과 SHA-256 검증을 적용하고 루트 밖 경로를 거부합니다.

연결된 세션에서 `intel_query(..., complete_stage=true)`를 사용합니다. 위쪽 원점 맵과 아래쪽 원점 경로를 따로 변환해 `maa_grid`를 출력하고 대기 체크포인트를 이동점으로 취급하지 않습니다. SPAWN action 하나당 레코드 하나를 만들며 count, delay, interval, 숨김 그룹, 차단 필드를 유지합니다. 적 레벨 상속, 정의 플래그, 스테이지 덮어쓰기를 처리하며 Key/Value와 key/value를 지원합니다. 미확인 기믹과 비 SPAWN actions를 빈 집합으로 바꾸지 않습니다. `mode_modifiers`는 난이도 mask만 선택하고 룬 해석 완료를 주장하지 않으며 `alignment=unknown`입니다. `mode=normal|challenge`는 난이도이며 연습/실전 구분이 아닙니다. 외부 provider에는 schema가 지원을 선언할 때만 mode를 전달하고 캐시도 mode별로 분리합니다. 해시와 버전 선언은 설치 게임 버전 일치나 독립 provider 호환성을 증명하지 않으며 외부 provider 데이터 패키지는 서비스에 포함해 재배포하지 않습니다.

위의 재배포하지 않는다는 설명은 외부 raw 스테이지/적 입력만 해당하며 앞서 설명한 오프라인 유닛 정보는 별도로 번들에 포함됩니다.

## Core 프로토콜

`AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})`로 초기화하고 `AsstStart`를 호출한 뒤 `SubTaskExtraInfo`의 `what="BattleControlReady"`(프로토콜 버전 1)를 기다립니다. `AsstSetTaskParams`에 `session_id`, `command_id`, `action`, `args`, `timeout_ms`를 전달하고 `what="BattleControlResult"`를 기다립니다. 한 번에 명령 하나만 허용하므로 콜백을 받은 뒤 다음 명령을 전송하세요.

행동 이름은 `observe`, `capture`, `map`, `identify`, `deploy`, `retreat`, `skill`, `set_paused`, `step`입니다. `step`은 사전 검사된 전술 행동 하나를 받고 즉시 캡처한 뒤 일시 정지를 요청하고, 확인 후 새 일시 정지 화면을 반환합니다. 기기 I/O 또는 MCP 프로세스 자체가 실패하는 경우에는 일시 정지를 보장할 수 없습니다. 인수 갱신은 큐에 추가만 하며 기기 조작은 Core 작업 스레드에서 실행합니다. C ABI만으로는 하위 I/O의 반환 기한을 보장할 수 없어 네이티브 호출자는 외부 watchdog과 중복 방지를 구현해야 합니다. MCP 서버는 프로세스 수준 watchdog을 제공합니다. 반복 실험과 최적화는 후속 구현 범위입니다.

`identify`는 선택적인 엄격한 boolean 인수 `args.adopt_manual_pause`(기본값 false)를 받습니다. 명시적 인계는 인식된 정지를 관리 대상으로 삼을 뿐 정지를 전환하거나 해당 인식 작업의 전투 재개를 허용하지 않습니다.

`step`은 유닛 없는 `args.action="wait"`와 `args.duration_ms`, 행동 인수 없는 `args.action="pause"`, 검증된 정지 전용 `args.action="speed"`와 `args.multiplier=1|2`도 지원합니다. 읽기 전용 `evaluate_placement`는 `placements` 배열을 받아 Core 기하를 재사용합니다. 실행 중 `AsstSetTaskParams`에 `{"session_id":"…","cancel_command_id":"…"}`를 보낼 수 있습니다. 해당 명령의 취소 신호만 설정하며 기기 입력이나 두 번째 결과를 만들지 않습니다. 늦은 취소는 다음 명령에 영향을 주지 않고 원래 콜백으로 정리 결과를 반환합니다. MCP는 원래 `operation_id`로 저장하고 중복을 방지합니다.
