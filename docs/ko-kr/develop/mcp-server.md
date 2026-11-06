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

실제 Debug Core의 작업 등록, 잘못된 인수 거부, 콜백, 맵 투영 확인도 기기 없이 실행할 수 있습니다.

```bash
MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install" \
  uv run --project src/MaaMcpServer pytest src/MaaMcpServer/tests/test_native.py -q
```

PowerShell에서는 먼저 `$env:MAA_MCP_NATIVE_CORE="$PWD/build-mcp/install"`을 설정한 뒤 같은 pytest 명령을 실행합니다. 리소스 경로가 다르면 그 부모를 `MAA_MCP_RESOURCE_DIR`로 지정합니다. 환경 변수가 없으면 이 테스트는 명시적으로 건너뜁니다. 오프라인 통과가 실제 게임 동작의 검증을 의미하지는 않습니다.

Windows 소스 Debug 빌드도 먼저 `tools/PrepareMcpRuntime.ps1 -CoreDir build-mcp/install`을 실행해야 하며 요구 사항은 Debug 패키지와 같습니다.

## 실행 및 클라이언트 설정

동일 기기를 조작하는 다른 MAA/GUI 작업을 중지하고 고정 맵 전투에 수동으로 진입하세요. 첫 버전은 계약 선택, 스테이지 진입, 승리 판정, 다단계 카메라 이동, 연결 전에 배치된 오퍼레이터 복원을 지원하지 않습니다.

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

`outcome_unknown`은 MCP `isError=true`로 반환됩니다. `helper_returned=true`여도 실제 게임 내 성공을 뜻하지 않습니다. 새 스크린샷으로 확인하고 무작정 재시도하지 마세요. 위치 출처는 `action_history_unverified`, 적 상태는 null이며 모델의 직접 상태 덮어쓰기는 지원하지 않습니다. 일시 정지 요청은 정지 확인과 다르며 재개는 이 서버가 요청한 정지에만 허용됩니다.

`timeout_ms`는 1–30000입니다. 시간 초과나 취소 시 worker를 종료하고 세션을 무효화하며 알 수 없는 결과를 저장합니다. `battle_close`는 제어를 중단하지만 **게임을 일시 정지하거나 종료하지 않습니다**. `battle_get_operation`으로 저장된 결과를 조회합니다. 로그와 `operations.sqlite3`는 `--user-dir`에 있으며 전투별로 최근 명시적 캡처 128장을 보관합니다. 오래된 세션, 로그, 데이터베이스는 주기적으로 직접 정리하세요.

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

## Core 프로토콜

`AsstAppendTask(handle, "BattleControl", {"stage_name":"main_01-07","session_id":"unique-id"})`로 초기화하고 `AsstStart`를 호출한 뒤 `SubTaskExtraInfo`의 `what="BattleControlReady"`(프로토콜 버전 1)를 기다립니다. `AsstSetTaskParams`에 `session_id`, `command_id`, `action`, `args`, `timeout_ms`를 전달하고 `what="BattleControlResult"`를 기다립니다. 한 번에 명령 하나만 허용하므로 콜백을 받은 뒤 다음 명령을 전송하세요.

행동 이름은 `observe`, `capture`, `map`, `identify`, `deploy`, `retreat`, `skill`, `set_paused`입니다. 인수 갱신은 큐에 추가만 하며 기기 조작은 Core 작업 스레드에서 실행합니다. C ABI만으로는 하위 I/O의 반환 기한을 보장할 수 없어 네이티브 호출자는 외부 watchdog과 중복 방지를 구현해야 합니다. MCP 서버는 프로세스 수준 watchdog을 제공합니다. 행동 후 자동 성공 확인, 반복 실험, 최적화는 후속 구현 범위입니다.
