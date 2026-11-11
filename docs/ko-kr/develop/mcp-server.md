---
icon: mdi:robot
order: 20
---

# 전투 MCP Server

`src/MaaMcpServer`는 별도 worker 프로세스에서 MaaCore를 실행하는 로컬 stdio MCP 서버입니다. Python 3.11+, uv, 현재 브랜치에서 빌드한 Core가 필요합니다. 이전 버전에는 `BattleControl` 작업이 없습니다. 실험적인 전투 제어 기능이며 위기협약 자동 최적화 도구가 아닙니다.

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

자신의 저장소에서 **Actions → Release Pipeline → Run workflow**를 열고 MCP 브랜치를 선택한 뒤 `mcp_only`를 켜고 `mcp_configuration`을 선택합니다.

- `RelWithDebInfo`: 기본 전체 실행 패키지입니다. Python, uv, ADB, Visual Studio를 별도로 설치할 필요가 없습니다.
- `Debug`: 개발 및 테스트용입니다. Linux는 바로 실행할 수 있지만 Windows는 재배포할 수 없는 Debug CRT를 로컬에서 준비해야 합니다.

두 설정 모두 먼저 Debug Core를 빌드하고 전체 테스트를 실행한 다음 선택한 구성을 패키징합니다. GUI 빌드나 Release 게시는 하지 않습니다.

`mcp_platform`의 기본값은 `all`입니다. `win-x64` 또는 `linux-x64`를 선택하면 필요한 플랫폼만 빌드하여 runner 중복 사용을 피할 수 있습니다.

```bash
gh workflow run ci.yml --ref <branch> -f mcp_only=true -f mcp_configuration=RelWithDebInfo
```

관련 PR도 같은 `Battle MCP` 작업을 실행합니다. `.github/workflows/mcp-build.yml`(**Battle MCP Bundle**)을 일반 및 Nightly 파이프라인이 공유합니다. 기본 브랜치에 병합되기 전에는 기존 Release Pipeline에서 수동 실행할 수 있습니다. 수동 MCP 빌드는 별도 동시 실행 그룹을 사용하므로 같은 브랜치의 PR 빌드에 의해 취소되지 않습니다.

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

개체별 식별 기능을 구현하기 전까지 소환물과 장치 배치는 명시적으로 거부합니다. 이름이 같은 여러 유닛을 하나의 오퍼레이터처럼 조작해서는 안 됩니다.

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

`battle_connect`, `battle_attach(stage_name="main_01-07")`, `battle_capture_evidence`, `battle_observe` 순서로 호출합니다. 스테이지 ID는 실제 화면과 일치해야 합니다. 맵 지정은 호출자가 제공하며 자동 인식이 아닙니다. `battle_get_map`은 정적 타일과 두 가지 화면 투영을 반환합니다. 알 수 없는 이름은 null입니다. `battle_identify`는 상세 화면을 열어 인식하며 일시 정지를 요청합니다. 이후 스크린샷을 확인하고 `battle_set_paused(paused=false)`로 명시적으로 재개하세요.

수동 일시 정지된 전투에 새 세션을 연결했다면 `battle_identify`에 `request.adopt_manual_pause=true`를 지정해 재개 없이 정지 상태 인계를 명시적으로 허용할 수 있습니다. 엄격한 boolean 형식이며 기본값은 false입니다. 생략하면 서버가 관리하지 않는 정지는 계속 거부됩니다. Core는 먼저 전투 화면으로 인식되고 실행 중 일시 정지 버튼이 보이지 않는지 확인하며, 인식할 수 없는 화면은 인계하지 않습니다. 카드는 정지 상태에서만 확인하고 OCR이 불완전하거나 요청이 취소되어도 자동 재개하지 않습니다. 처리가 완료되면 부분 인식에서도 `pause_verified`, `safe_state`, 최종 동일 프레임의 `snapshot` / `image_path`와 PNG를 반환합니다. 저장 실패는 `evidence_error`로 명시하며 취소나 worker 장애 시 최종 증거를 반환하지 못할 수 있습니다. 초기 인식에 외부 ADB 탭은 필요하지 않습니다.

배치 요청 예시입니다. 세션과 관측된 유닛 ID를 바꾸세요. 도구 인수에는 `request` 객체가 있습니다.

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

`action`은 `deploy`, `retreat`, `skill` 또는 아래의 `wait`입니다. 배치에는 `location`과 `direction`도 필요합니다. 수동 일시 정지는 기본적으로 인계하지 않습니다. 재개하려면 `adopt_manual_pause=true`와 새로운 스크린샷으로 확인된 일시 정지가 필요합니다. 호출당 행동은 최대 하나입니다. 행동 경계 화면을 캡처하고 일시 정지를 요청한 뒤, 새로운 화면과 `pause_verified`를 반환합니다. 취소 시 Core에 알리고 정리 단계가 끝날 때까지 기다립니다. worker 자체가 실패하면 일시 정지 상태를 알 수 없다고 명시합니다. `battle_step`의 `timeout_ms` 상한은 10000입니다.

`captured_at_ms`는 이미지 획득 직후, 분석 전에 기록한 호스트의 Unix 밀리초 시각으로 기기의 렌더링 시각이 아닙니다. `capture_time_snapshot`은 `capture_image_path`(행동 경계), `return_time_snapshot`은 `return_image_path`(이후 정지 확인)와 연결됩니다. MCP 이미지는 기존처럼 반환 시점 프레임, 행동 경계 프레임 순서입니다. `observation_id`는 획득 순서가 아닌 분석 순서대로 증가하므로 행동 경계의 ID가 더 커도 시각은 더 이를 수 있습니다. 시스템 시계 조정은 시간 차이에 영향을 주며 이 시각은 실행 시간의 엄격한 상한이 아닙니다. 동일 작업 재전송은 원래 시각과 경로를 보존하고 새 캡처에는 자체 획득 시각을 기록합니다.

`battle_identify`는 `identified_count`, `unresolved_count`, `ambiguous_count`와 카드별 `identity_reason`을 반환합니다. 이름이 빈 캐시 항목은 더 이상 상세 OCR을 억제하지 않습니다. 다른 행동이 `outcome_unknown`을 반환하면 MCP `isError=true`이며 `helper_returned=true` 또는 `action_returned=true`도 게임 내 성공을 증명하지 않습니다. 새 증거를 확인하고 무작정 재시도하지 마세요. 위치 출처는 `action_history_unverified`, 적 상태는 null이며 모델의 직접 상태 덮어쓰기는 지원하지 않습니다. 별도의 일시 정지 요청은 정지 확인이 아니며 재개는 이 서버가 요청하거나 명시적으로 인계한 정지에만 허용됩니다.

일반 명령의 `timeout_ms`는 1–30000이고 `battle_step`은 최대 10000입니다. 일반 명령 시간 초과나 취소 시 worker를 종료하고 세션을 무효화하며 알 수 없는 결과를 저장합니다. step 취소 시 준비된 일시 정지 정리가 끝날 때까지 기다리지만, worker 장애로 일시 정지를 확인하지 못할 수 있습니다. `battle_close`는 일반 명령을 중단하며 step 실행 중에는 일시 정지 정리가 끝날 때까지 기다린 뒤 닫습니다. 단, 이 작업 자체가 게임을 일시 정지하거나 종료하지는 않습니다. `battle_get_operation`으로 저장된 결과를 조회합니다. 로그와 `operations.sqlite3`는 `--user-dir`에 있으며 전투별로 최근 명시적 캡처 128장을 보관합니다. 오래된 세션, 로그, 데이터베이스는 주기적으로 직접 정리하세요.

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

`battle_evaluate_placement(session_id, placements)`는 후보 전체 진형을 읽기 전용으로 검사합니다. 각 항목에 `operator_game_id`, `location`, `direction`을 넣고 정예 단계는 설정에서 가져오거나 `assumed_elite`로 가정을 명시합니다. 기본 범위, 기하학적 아군 커버, 정적 배치 호환성과 타일 충돌을 반환합니다. 정예 단계/범위를 모르면 직군 기본값을 대입하지 않습니다. 실제 점유, 치료 대상 자격, 생존이나 최적성을 검증하지 않고 스킬, 모듈, 재능과 특수 지형 효과도 포함하지 않습니다. 소환물/장치 제어와 실시간 적 추적은 계속 미지원입니다.

## Core 프로토콜

`AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})`로 초기화하고 `AsstStart`를 호출한 뒤 `SubTaskExtraInfo`의 `what="BattleControlReady"`(프로토콜 버전 1)를 기다립니다. `AsstSetTaskParams`에 `session_id`, `command_id`, `action`, `args`, `timeout_ms`를 전달하고 `what="BattleControlResult"`를 기다립니다. 한 번에 명령 하나만 허용하므로 콜백을 받은 뒤 다음 명령을 전송하세요.

행동 이름은 `observe`, `capture`, `map`, `identify`, `deploy`, `retreat`, `skill`, `set_paused`, `step`입니다. `step`은 사전 검사된 전술 행동 하나를 받고 즉시 캡처한 뒤 일시 정지를 요청하고, 확인 후 새 일시 정지 화면을 반환합니다. 기기 I/O 또는 MCP 프로세스 자체가 실패하는 경우에는 일시 정지를 보장할 수 없습니다. 인수 갱신은 큐에 추가만 하며 기기 조작은 Core 작업 스레드에서 실행합니다. C ABI만으로는 하위 I/O의 반환 기한을 보장할 수 없어 네이티브 호출자는 외부 watchdog과 중복 방지를 구현해야 합니다. MCP 서버는 프로세스 수준 watchdog을 제공합니다. 반복 실험과 최적화는 후속 구현 범위입니다.

`identify`는 선택적인 엄격한 boolean 인수 `args.adopt_manual_pause`(기본값 false)를 받습니다. 명시적 인계는 인식된 정지를 관리 대상으로 삼을 뿐 정지를 전환하거나 해당 인식 작업의 전투 재개를 허용하지 않습니다.

`step`은 유닛 인수 없는 `args.action="wait"`와 `args.duration_ms`도 지원합니다. 실행 중 `AsstSetTaskParams`에 `{"session_id":"…","cancel_command_id":"…"}`를 보낼 수 있습니다. 해당 명령의 취소 신호만 설정하며 기기 입력이나 두 번째 결과를 만들지 않습니다. 늦은 취소는 다음 명령에 영향을 주지 않고 원래 콜백으로 정리 결과를 반환합니다. MCP는 원래 `operation_id`로 저장하고 중복을 방지합니다.
