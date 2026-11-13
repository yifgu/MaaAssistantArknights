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

`battle_connect`, `battle_attach(stage_name="main_01-07")`, `battle_capture_evidence`, `battle_observe` 순서로 호출합니다. 스테이지 ID는 실제 화면과 일치해야 합니다. 맵 지정은 호출자가 제공하며 자동 인식이 아닙니다. `battle_get_map`은 정적 타일과 두 가지 화면 투영을 반환합니다. 알 수 없는 이름은 null입니다. `battle_identify`는 상세 화면을 열어 인식하며 일시 정지를 요청합니다. 이후 스크린샷을 확인하고 `battle_set_paused(paused=false)`로 명시적으로 재개하세요.

수동 정지를 인계하려면 `battle_identify`의 엄격한 boolean 인수 `request.adopt_manual_pause=true`가 필요하며 기본값은 false입니다. 재개를 허용하지는 않습니다. Core는 명확한 정지 증거를 요구합니다. 실행 버튼이 없다는 이유만으로 정지로 판단하지 않고 입장 제목 전환도 인계하지 않습니다. 정지 상태에서 카드를 확인하고 부분 OCR에서도 `pause_verified`, `safe_state`, 같은 프레임의 `snapshot` / `image_path`와 PNG를 반환합니다. 저장 실패는 `evidence_error`에 기록하며 취소나 worker 장애로 최종 증거를 얻지 못해도 자동 재개하지 않습니다.

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

`action`은 `deploy`, `retreat`, `skill`, `wait`이며 배치에는 `location`과 `direction`도 필요합니다. 수동 정지 재개에는 명시적 `adopt_manual_pause=true`와 새 정지 증거가 필요합니다. 호출당 행동은 최대 하나이며 경계 프레임을 분석한 다음 정지를 요청하고 새 이미지와 `pause_verified`를 반환합니다. 취소 시 Core 정리를 기다리지만 worker 장애 시 정지가 미확인일 수 있습니다. `battle_step.timeout_ms` 기본값은 10000, 상한은 30000이며 호출자의 예산을 임의 연장하지 않습니다.

`captured_at_ms`는 이미지 획득 직후, 분석 전에 기록한 호스트의 Unix 밀리초 시각으로 기기의 렌더링 시각이 아닙니다. `capture_time_snapshot`은 `capture_image_path`(행동 경계), `return_time_snapshot`은 `return_image_path`(이후 정지 확인)와 연결됩니다. MCP 이미지는 기존처럼 반환 시점 프레임, 행동 경계 프레임 순서입니다. `observation_id`는 획득 순서가 아닌 분석 순서대로 증가하므로 행동 경계의 ID가 더 커도 시각은 더 이를 수 있습니다. 시스템 시계 조정은 시간 차이에 영향을 주며 이 시각은 실행 시간의 엄격한 상한이 아닙니다. 동일 작업 재전송은 원래 시각과 경로를 보존하고 새 캡처에는 자체 획득 시각을 기록합니다.

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

`battle_evaluate_placement(session_id, placements)`는 후보 전체 진형을 읽기 전용으로 검사합니다. 각 항목에 `operator_game_id`, `location`, `direction`을 넣고 정예 단계는 설정에서 가져오거나 `assumed_elite`로 가정을 명시합니다. 기본 범위, 기하학적 아군 커버, 정적 배치 호환성과 타일 충돌을 반환합니다. 정예 단계/범위를 모르면 직군 기본값을 대입하지 않습니다. 실제 점유, 치료 대상 자격, 생존이나 최적성을 검증하지 않고 스킬, 모듈, 재능과 특수 지형 효과도 포함하지 않습니다. 실시간 적 추적은 계속 미지원입니다.

## 장면 증거, 준비 관측 및 제한된 조작

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

유효한 최신 결과만 기존의 명시적 재확인 attach에 사용할 수 있습니다. 새 관측은 이전 연결을 무효화하며 화면/편성 변경, 증거 저장 실패, 중단된 스캔은 유효한 연결을 만들지 않습니다. `decision_context.preparation.observed_fields`는 네이티브 관측을 입력된 구성과 분리해 유지하고 충돌을 기록하지만 어느 쪽도 덮어쓰지 않습니다. 이는 전투 전 측정이지 계정/스테이지/구성의 지속 검증이나 실시간 전투 측정이 아닙니다. 기존 리소스를 재사용하는 인식기는 합성 Debug Core 회귀가 있지만 **실기기 검증은 아닙니다**. 실제 화면 배치와 선택 표시 호환성은 로컬에서 확인해야 합니다.

### 오프라인 유닛 정보와 소유 개체

`battle_get_unit_info(game_id)`는 기기 연결이나 정보 provider 없이 번들 `resource/unit_info.json`에서 `unit`, `source`, `resource_sha256`, `scope`를 반환합니다. TOKEN/TRAP/오퍼레이터 분류, 효과, 기본 속성 키 프레임과 범위 ID, 스킬 참조/정의, 재능 blackboard, 조건부 `tokenKey`/`overrideTokenKey` ID 관계를 보존합니다. 정적 표의 값은 현장 카드 `cost`/`available`/`cooling`이나 실제 장착 상태 측정이 아닙니다. 사용자 설정으로 시각 ID의 모호함을 해결하지 않습니다.

`base_range_definitions`는 `base_range_coordinate_system`에 명시된 게임 상대 행/열 좌표를 보존하며 시각적으로 측정한 MAA 타일이 아닙니다.

능력 필드는 `value=true|false|null`, `source`, `reason`, `game_data_value`, `live_verified`입니다. 검토된 adapter 값은 제한된 상호작용 계약이며 엔진 정책이나 실기기 합격의 증거가 아닙니다. `execution_support`는 입력 시도만 가능한 작업과 미지원 작업을 별도로 표시합니다. unknown은 허가가 아닙니다. 첫 소환 adapter는 메딕프로브 `token_10000_silent_healrb`의 일반 드래그 및 `direction="None"`입니다. 다른 TOKEN/TRAP은 미확인으로 거부하며 동일 타일 공존과 소환물 스킬은 지원하지 않습니다.

`battle_step(action="deploy", unit_id=..., card_id=...)`를 사용합니다. `game_id`는 종류, `card_id`는 관측 카드, `instance:<operation_id>`는 소유 배치 시도입니다. 이름의 유일성보다 구체적 카드 선택을 먼저 하므로 동명 카드를 구별하지만 게임 ID는 유일해야 합니다. 재개 후 신원, 레이아웃, 외관, 가용성 및 30초 신선도를 재검사합니다. `--allow-summon` 생략은 지원 adapter를 허용하며 이전 옵션은 경고와 함께 명시적 제한으로 유지합니다. 미지원 상호작용을 허가하지 않습니다. 네이티브 `allowed_summons=[]`는 모든 소환을 제외하고 필드 생략만 지원 카드를 활성화합니다.

C++ 타입으로 presence, 위치 증거 및 작업 상태를 분리합니다. 입력 및 비용/재배치 상태 변화는 배치 확인이 아닙니다. 변경형 유한 상세 검사는 두 프레임의 이름/아이콘 증거를 기록하지만 이번 개체의 존재나 선택 타일을 증명하지 않습니다. 미확인 개체는 요청 타일을 예약합니다. `battle_step(action="retreat", unit_id="instance:...")`는 능력을 먼저 확인하고 동종/이웃 개체의 모호함을 거부한 뒤 신원 및 긍정적인 퇴각 버튼을 확인합니다. **실제 선택 타일 anchor 및 명확한 이탈 규칙이 아직 없으므로 anchor 미확인 시 퇴각 버튼을 누르지 않으며 미확인 점유를 해제하지 않습니다.** 버튼 미검출은 `selection_unverified`로, 영구 퇴각 불가 추정이 아닙니다. 명시적 false만 `not_retreatable`입니다. 수동 관찰은 클릭/재개하지 않고 시간이나 단일 프레임 미검출로 소멸을 판정하지 않습니다. 검증된 종결 화면은 이탈을 기록할 수 있습니다. 재전송은 저널/증거를 보존하고 재입력하지 않으며 재시작/중도 접속 복원은 지원하지 않습니다.

긍정적인 상세 프레임은 선택한 종류의 존재(`selected_kind_present`)만 보여 주며 이번 배치 성공은 증명하지 않습니다. 타일 anchor 없이는 소유 개체 `presence`가 unknown입니다. 퇴각 아이콘 일치는 `retreat_button_detected`이지 활성화 증명이 아니며 가용성 규칙이 없어 `retreat_button_available`은 null입니다.

이전 `battle_deploy`는 선택적 `card_id`를 받으며 이전 배치/퇴각/스킬 진입점도 동일 유한 사전 검사 및 일시정지 정리를 사용합니다. 시작 시 실행 중 전투가 필요하고 능력/개체/위치 검사를 우회할 수 없습니다. 선택 타일 anchor 없는 일반 오퍼레이터 스킬/퇴각도 미검증 이력으로 실행하지 않고 거부합니다.

TL/TL-EX는 `act55side_*` ID를 사용합니다(예: `act55side_ex01`, `TL-EX-1` 아님). ｢自爆榴弹｣(폭발하는 스테이지 장치) `trap_341_dsjtzd`는 오프라인 조회 가능하며 기본 비용 20, 재배치 20초, 범위 `x-4`, 나열된 스킬 없음, 재능 blackboard 기절 5초입니다. **재배치 시간은 폭발 카운트다운이 아닙니다.** 원본 표는 드래그/퇴각 정책을 증명하지 않습니다. 폭발, 확실한 소멸 및 翻板 변화의 검증된 실제 이미지 규칙이 없어 동적 overlay는 unknown/unsupported이고 TL 배치는 `dynamic_terrain_unknown`입니다. 예상 시간으로 정적 지도를 바꾸지 않으며 TL 완전 루프 또는 실기기 합격을 주장하지 않습니다.

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

`step`은 유닛 인수 없는 `args.action="wait"`와 `args.duration_ms`도 지원합니다. 실행 중 `AsstSetTaskParams`에 `{"session_id":"…","cancel_command_id":"…"}`를 보낼 수 있습니다. 해당 명령의 취소 신호만 설정하며 기기 입력이나 두 번째 결과를 만들지 않습니다. 늦은 취소는 다음 명령에 영향을 주지 않고 원래 콜백으로 정리 결과를 반환합니다. MCP는 원래 `operation_id`로 저장하고 중복을 방지합니다.
