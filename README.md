<!-- markdownlint-disable -->

<div align="center">

<img alt="LOGO" src="./docs/.vuepress/public/images/maa-logo_512x512.png" width="256" height="256" />

# MAA + Battle MCP

<br>
<div>
    <img alt="C++" src="https://img.shields.io/badge/C++-20-%2300599C?logo=cplusplus">
    <img alt="platform" src="https://img.shields.io/badge/platform-Windows%20%7C%20Linux%20%7C%20macOS-blueviolet">
</div>
<div>
    <img alt="license" src="https://img.shields.io/github/license/MaaAssistantArknights/MaaAssistantArknights">
    <img alt="commit" src="https://img.shields.io/github/commit-activity/m/MaaAssistantArknights/MaaAssistantArknights?color=%23ff69b4">
</div>
<div>
    <img alt="stars" src="https://img.shields.io/github/stars/MaaAssistantArknights/MaaAssistantArknights?style=social">
    <img alt="GitHub all releases" src="https://img.shields.io/github/downloads/MaaAssistantArknights/MaaAssistantArknights/total?style=social">
</div>
<div>
    <a href="https://deepwiki.com/MaaAssistantArknights/MaaAssistantArknights"><img src="https://img.shields.io/badge/-Ask_DeepWiki-blue?logo=readthedocs&logoColor=white&labelColor=black" alt="Ask DeepWiki"></a>
</div>
    <a href="https://trendshift.io/repositories/979" target="_blank"><img src="https://trendshift.io/api/badge/repositories/979" alt="MaaAssistantArknights%2FMaaAssistantArknights | Trendshift" width="250" height="55" style="width: 250px; height: 55px;" /></a>
<div>
</div>
<br>

<!-- markdownlint-restore -->

[简体中文](https://docs.maa.plus/zh-cn/) | [繁體中文](https://docs.maa.plus/zh-tw/) | [English](https://docs.maa.plus/en-us/) | [日本語](https://docs.maa.plus/ja-jp/) | [한국어](https://docs.maa.plus/ko-kr/)

MAA 的意思是 MAA Assistant Arknights

一款明日方舟游戏小助手

基于图像识别技术，一键完成全部日常任务！

绝赞更新中 ✿✿ヽ(°▽°)ノ✿

</div>

## 本仓库与上游的区别

本仓库是 [MaaAssistantArknights/MaaAssistantArknights](https://github.com/MaaAssistantArknights/MaaAssistantArknights) 的实验性分叉，保留上游 MAA 的日常自动化能力，额外提供 **Maa Battle MCP Server**。它让支持 MCP 的客户端通过本地 stdio 调用 MaaCore，观察战斗、读取截图与地图、识别编队，并执行有界的战术操作；不是预先编写的自动战斗作业，也不内置大模型或自动通关策略。

新增实现包括 [Python MCP 服务](src/MaaMcpServer)、[原生 BattleControl 任务](src/MaaCore/Task/Interface/BattleControlTask.cpp)、识别资源、可选情报适配器，以及 [独立 MCP 打包与测试流水线](.github/workflows/mcp-build.yml)。**必须配套使用本仓库同一提交的 Core、资源和 Python 服务；上游发行版没有这些新增接口。**

### MCP 能力与接口

默认 `--tool-profile minimal` 仅注册十个工具：`battle_status`、`battle_connect`、`battle_attach`、`battle_observe`、`battle_inspect_preparation`、`battle_identify`、`battle_step`、`battle_get_unit_info`、`battle_get_operation`、`battle_close`。仅配置 `--intel-config` 时额外注册 `intel_query`；无配置不暴露空壳。需要下表中的旧入口、独立暂停／恢复、媒体或几何扩展时，显式使用 `--tool-profile full`（27 个基础工具，配置情报后 28 个），旧客户端须刷新 `tools/list`。

minimal 响应在顶层提供单一 canonical `battle_decision`，不重复展开旧部署／场上列表和相同投影；操作、安全、未知值／原因及证据引用仍保留，原始审计结果不从日志删除。统一只读入口 `battle_observe` 默认不附图，`include_image=true` 读取同一原帧，`include_map=true` 返回结构化地图；`duration_ms=100..8000` 仅在输入已阻塞时复用只读终局／暂停采集，受超时限制，不隐式识别或输入。`evidence[].resource_uri` 指向 `image/png` MCP resource `battle-evidence://{session_id}/{operation_id}/{observation_id}`，只读取得操作已记录的原 PNG，不接受任意文件路径或重新截图。full 保留原有响应形状与旧入口附图行为。

两种配置均支持 `battle_get_operation(..., detail="full")` 按需返回原始持久化结构化结果，包括完整动作边界／返回快照及各自决策，另附证据引用；不重做观测、不发送输入、不改写日志，也不默认附图。`detail="summary"` 默认遵循当前配置，minimal 精简、full 保持完整，无需为读取边界证据重启或切换 full。full 的 pause／speed step 会正确附上原生 `image_path` PNG，相同路径仅附一次。

战前 `host_fallback.items[].retrieval` 按字段提供对应的原帧 `resource_uri`；minimal 不推荐未注册工具，full 保留兼容工具入口。历史读取使用同样的回读提示投影，原始事实、完整快照与 journal 记录不改。

下表涵盖 minimal 与 full 的完整能力，非默认工具仅在 full 提供：

| 能力 | MCP 工具 | 行为与边界 |
| --- | --- | --- |
| 连接与诊断 | `battle_status`、`battle_connect`、`battle_close` | 连接预配置设备，报告构建身份、Core 版本、资源哈希及会话状态；关闭会等待有界操作收尾，但本身不暂停或退出游戏。 |
| 固定地图绑定 | `battle_attach`、`battle_get_map` | 以唯一 stage/level ID 绑定手动开始的战斗，返回静态格子与屏幕投影；拒绝歧义 ID，不自动识别或进入关卡。 |
| 同帧观察与截图 | `battle_observe`、`battle_capture_evidence` | 返回 `battle_decision` v1 的局面分域、逐字段证据／新鲜度、生命值差分与原生事件游标，并保留同帧 PNG；未知状态不伪装为完整战场。 |
| 离线单位资料 | `battle_get_unit_info(game_id)` | 不需要连接设备、预先列举召唤 ID 或配置情报服务，读取随包提供的 TOKEN／TRAP 分类、效果、基础属性关键帧、技能／天赋、召唤 ID 关联、能力适配与固定来源版本；不是本场实际属性测量。 |
| 战前编队 | `battle_observe_preparation`、`battle_inspect_preparation`、`battle_invalidate_preparation` | 普通观察只读姓名；批量检查有界打开详情、查看技能列表，读取身份、选中 S1/S2/S3、精英阶段与等级，返回逐字段证据和页面恢复状态。不选择干员、切换技能或开始战斗。 |
| 主动身份识别 | `battle_identify` | 在暂停状态查看部署卡详情，报告已识别、未解决与歧义数量；接管手动暂停必须显式授权，识别本身不恢复战斗。 |
| 有界战术动作与等待 | `battle_step` | 按 action 判别的 Schema 导出各分支必填／禁止字段；一次执行 `deploy`、`retreat`、`skill`、`wait`、只暂停并验证的 `pause`，或仅已验证暂停时可用的 `speed`（1x/2x）。保留动作边界与返回原帧，验证暂停或终局；等待为 100–8000 ms 主机时间，支持取消与关闭收尾，不等同于精确游戏时间。 |
| 独立动作与暂停控制 | `battle_deploy`、`battle_retreat`、`battle_use_skill`、`battle_set_paused`、`battle_set_speed` | 提供受保护的部署、撤退、技能与暂停请求；1x/2x 调速只在已验证暂停时执行和回读，不自动恢复。战术闭环优先使用 `battle_step`。 |
| 终局证据与恢复 | `battle_collect_terminal` | 只读取证，区分胜利、失败、仍在战斗与未知；暂停不确定可由连续两帧新证据恢复，终局／安全隔离不能因此解除。 |
| 操作持久化 | `battle_get_operation` | 只读查询 `operations.sqlite3`；默认 `detail="summary"`，`detail="full"` 按需取原始前后结构化证据。动作重放时相同 `operation_id` 与相同参数只返回原结果，改参数拒绝，不重复发送输入。 |
| 历史原帧回读 | `battle-evidence://…` resource；full 的 `battle_get_evidence_frame` | 按会话、操作和观测 ID 只读取得操作已记录的单张原始 PNG，包括战前详情；不重新截图、不操作设备，不接受任意文件路径。 |
| 有界原生录像 | `battle_record_clip`、`battle_get_media`、`battle_get_media_frames` | 原生采样与时间清单、无损 PNG 关键帧及通过回读验证后才提供的 AVI/MJPEG；MCP 通过资源和 ImageContent 返回，不自动恢复暂停，不上传第三方。 |
| 本地视频导入 | `battle_import_video` | 只允许启动配置导入目录中的文件名，复制并校验哈希；在独立、无设备连接的 Core worker 中限时解码，拒绝路径逃逸、链接、设备和播放列表。 |
| 实验性视频转作业 | `battle_analyze_video` | 独立 Core 的 `VideoRecognition` 仅生成近似作业候选，不执行；完整上下文是调用者声明，原生仍须识别编队和关卡。短战斗片段优先交给多模态宿主看关键帧。 |
| 静态情报与阵型几何 | 条件扩展 `intel_query`；full 的 `battle_evaluate_placement` | 可选查询关卡、干员与敌人资料，支持完整关卡分页及坐标转换；几何复用 Core 公共实现，评估基础范围、静态部署兼容性与候选占格冲突，不在 Python 复制投影，不追踪实时敌人或优化战术。 |

`--intel-config` 可接入独立的 Intelligence／PRTS stdio 服务，或经过哈希校验的本地 raw 关卡与敌人快照；外部程序和数据不随运行包捆绑，也不自动下载。`--loadout-config` 可提供本场练度／技能配置，来源与原生观察分开记录，冲突显式报告，用户填写或确认不等于现场视觉验证。

战前批量检查保留原始 `game_ids`，按静态实体职业筛选普通干员后唯一化身份，并返回筛选依据；不会用战前名单消除战斗现场的身份歧义。详情打开／返回均在原期限内用新帧有界验证，过渡／未知页只读等待，不重复点击头像；已识别错误成员或编队改变立即停止，只有身份匹配的已识别详情页允许受保护返回。`evidence_frames.purpose` 是采样用途，`page` 是实际识别结果；原始错误与 `recovery_error` 分别保留。仍在原编队且名单未变时不发送返回。失败／中断／证据写入失败不建立有效绑定；合成回归不等于真实 OCR、点击或页面布局已验收。

战前姓名识别失败时，在编队专用姓名 ROI 中复用 OperBox 底层的 `OperNameAnalyzer` 分割能力，不套用整套 OperBox 翻页或 14／16 卡布局，也不导入其等级／精英／潜能默认值。`host_fallback` 按槽位和字段列出未知原因、原始候选及原帧回读引用，供多模态宿主兜底；宿主判断不自动变成原生事实或解锁输入。唯一普通非替身单位的冷却且本会话未记录撤退输入时，可以发出需宿主复核的 `suspected_defeat` 提示；离场未验证会明确标 `departure_verified=false`，不改在场状态或释放占格，外部操作和机制原因仍可能存在。

录像最多 8 秒、24 帧，采样间隔不少于 200 ms，原始帧缓冲不超过 64 MiB，单份发布录像不超过 32 MiB；保留最多 8 份媒体／256 MiB，活动读取和分析会锁定其媒体。可选 `battle_step(wait).recording` 只复用已经明确授权的等待采样，编码在暂停收尾后进行；编码／配额失败不跳过收尾。独立只读录像不发输入，运行中的游戏仍会推进。AVI 的呈现 FPS 不是游戏速度，帧的主机采集时间／单调偏移才是依据；导入帧只有媒体 PTS，不冒充实时采集时间。

MCP SDK 没有 VideoContent：可读取的视频资源最多 16 MiB，超限明确不给可读链接，但仍可回读最多 4 张／合计 16 MiB 的 PNG 关键帧及做原生离线分析。录像是稀疏采样，不保证连续动画。`--media-import-dir` 默认指向 `--user-dir/media-import`；导入上限为 128 MiB、10 分钟、1920×1080、18000 帧，并检查实际解码结果和期限。实验分析最多保留 10 个片段、每段 3 张方向样本并释放已处理样本，图像缓冲限于 128 MiB（不含已加载模型）；原有识别可能猜测身份并将消失写为 Retreat，不能当作击倒或完整战场事实。操作重放不重录或重建已过期媒体，历史读取不解除隔离；日志／操作记录与既有截图另按原规则保留。

PRTS 适配器请求 stdio 双通道，分别保留上游结构化数据与内容；不修复上游未替换 token、缺失文本或不完整条件。干员查询实际使用 `get_operator_basic_info(action=skills)`，不代表完整基础资料／天赋或本账号练度；PRTS 不支持完整关卡地图、路线、波次，也不自动拼接 raw 数据。

### 安装与接入 Battle MCP

`dev-v2` 上匹配现有构建路径的推送（包括 PR 合并）自动运行 **Battle MCP Bundle**，默认生成 Windows/Linux x64 的 `RelWithDebInfo` 包，不再触发全量 Release Pipeline。纯文档改动不触发 MCP 构建；其它文档与验证工作流、PR 全量构建、`v*` 标签发布和手动全量构建保持原有触发规则。

在 [本仓库 Actions](https://github.com/yifgu/MaaAssistantArknights/actions) 的 **Battle MCP Bundle → Run workflow** 中也可选择包含 MCP 的分支，按需设置 `platform` 为 `win-x64` 或 `linux-x64`。默认 `RelWithDebInfo` 完整包包含 Python、ADB、Core、资源和依赖，无需本地安装 uv 或重新编译；从成功运行的 Artifacts 下载 `MAAMcp-<platform>-RelWithDebInfo` 并解压内部压缩包。macOS 当前需源码构建，没有对应的完整 MCP 运行包。

Windows 使用 `run-mcp.cmd --check`，Linux 使用 `./run-mcp.sh --check` 检查运行环境；检查不连接设备。随后用支持 MCP 的客户端启动包内 `run_mcp.py`，或通过源码安装的 `maa-battle-mcp` 入口启动。服务使用 **stdio，不提供网页或 HTTP 服务**；设备地址、资源目录和可写状态目录由受信任的启动配置指定。

典型调用流程：

1. 停止同设备的其他 MAA／GUI 控制器，调用 `battle_connect`。
2. 如需战前资料，手动打开编队页后显式授权有输入的 `battle_inspect_preparation`；编队、技能、账号或关卡变化后使用新操作重新采集。full 另有只读 `battle_observe_preparation` 和 `battle_invalidate_preparation`。
3. 手动开始固定地图战斗，调用 `battle_attach` 并核对关卡与截图；使用战前快照时须显式重新确认编队未变。
4. 用 `battle_observe` 检查状态，按需开启 `include_image`／`include_map` 或读取原图 resource，再调用 `battle_identify` 或 `battle_step`；每次检查返回证据、暂停状态与错误，不盲目重试。
5. 需要静态情报或阵型评估时保持游戏暂停；结束后调用 `battle_close`，不要将关闭服务视为游戏已暂停。

编译、客户端 JSON 配置、参数、可选情报配置和测试方法见本仓库的五语言指南：[简体中文](docs/zh-cn/develop/mcp-server.md) | [繁體中文](docs/zh-tw/develop/mcp-server.md) | [English](docs/en-us/develop/mcp-server.md) | [日本語](docs/ja-jp/develop/mcp-server.md) | [한국어](docs/ko-kr/develop/mcp-server.md)。

### 安全边界与当前限制

- 实验性功能，仅支持调用者确认的固定地图；不自动进入关卡、选合约、消费资源、移动镜头、重开作战或寻找最优策略，也不能重建中途接入的完整场上身份。
- 召唤卡不再要求预先枚举 ID；已识别的医疗探机标准适配可凭新鲜 `card_id` 部署，同名多卡按具体卡片选择，不按姓名取第一张。旧 `--allow-summon` 保留为显式限制，不能授权未知交互。其他 TOKEN／TRAP 默认未知并拒绝部署。
- `instance:<operation_id>` 分别记录输入状态、在场证据与位置证据；原生血条增加全部候选格、冲突、朝向与请求实例候选，不按首个匹配落格，候选不授权输入。缺少选中身份绑定格及撤退按钮 enabled 的正向规则，未建立 `VerifiedTileAnchor`；未确认实例继续占格，技能／撤退仍拒绝，完整部署／撤退闭环尚未完成，也不能自主开技能。旧独立入口使用同一安全检查，不可绕过。
- TL／TL-EX 不再整关拒绝部署：已支持的固定地面按原有类型检查，战壕只放行在裸露／翻板覆盖两种情况下均可部署的普通远程干员；其他动态交互仍拒绝。翻板覆盖时的眩晕等效果由宿主判断，不宣称 TL 实机闭环已完成。｢自爆榴弹｣（`trap_341_dsjtzd`）仍仅能离线查询，再部署 20 秒不是爆炸倒计时。
- `battle_get_map` 现在只读采集并返回同帧 PNG；它与观察／截图／动作快照中的 `dynamic_overlay` 报告各格相对本会话首次有效截图的像素是否变化，保留基准图供宿主二次识别。首次截图不等于关卡初始状态；积雪、翻板、单位、特效和截图噪声都可能造成变化。该结果不识别具体状态，不改变静态地图，不授权部署／撤退或释放未知占格。
- `action_returned`／`helper_returned` 为 true 不证明游戏动作生效；`outcome_unknown` 必须结合截图人工或客户端复核。设备 I/O 卡死、断连或 worker 崩溃时无法保证暂停，进程隔离与 watchdog 不等于设备级硬期限。
- 暂停使用正向中英文证据，支持严格同帧分框中文；1X 使用 OCR／模板证据，未知或冲突时不调速。终局仅覆盖已配置的有限中英文标题，包括受上下文与几何约束的分行 `MISSION`／`ACCOMPLISHED`；黑屏、击杀数达到总数、离开战斗或等待成功都不代表胜利。
- `暂停` 缺字证据只有在限定 ROI、高置信 OCR、同帧战斗 HUD 与正向继续三角形共同成立时才接受；仍须连续两次新采集恢复软隔离，硬隔离不解除。普通单位的显式／隐式选卡共享恢复前后身份、布局、头像相关性与 30 秒新鲜度检查；召唤和重复卡仍须具体 `card_id`。
- `life`／`life_delta` 来自正向 HUD 数字证据和可信端点差分，缺失不作 0，生命减少不冒充漏怪原因。冷却卡与已拥有实例离场分开；没有已验证拥有关系和格子锚点时不释放占格。原生血条检测只给未关联矩形，不给 HP 比例；SP、弹药、技能阶段／持续时间、治疗启用、敌人位置／波次／路线仍缺少可信运行时测量，完整战斗决策数据尚未完成。
- 宿主可按稳定事件 ID 消费局面变化和告警，不必为已结构化的字段逐帧读图；未知、异常和审计仍保留同帧 PNG。可选重识别不进入恢复／暂停收尾窗口，预算不足时明确跳过。干员知识仍由 `battle_get_unit_info`／可选 `intel_query` 提供，不在每次观测中隐式查询或展开；配置只提供证据、冲突和稳定 ID。
- 战前识别失败、助战详情或无法确认的字段保持未知；扫描 `complete` 只覆盖检测到的槽位，不证明没有漏检。静态情报、外部配置和几何覆盖不代表当前实战数据或技能效果。
- CI 使用实际 Debug Core 与合成截图／本地传输验证接口和安全行为，不代表所有游戏画面、语言或设备已实机验收。Windows Debug CRT 不可再分发，完整运行包与开发自测包的区别见指南。

### 本仓库已合并 PR

以下汇总截至 2026-11-13 本仓库全部 12 个 PR，均已合并；能力描述以上面的当前接口与限制为准，不把各次合成测试当作实机验收。

| PR | 相对于上游的增量 |
| --- | --- |
| [#1](https://github.com/yifgu/MaaAssistantArknights/pull/1) | 新增持久 BattleControl 会话、本地 stdio MCP、可选情报适配、回归测试与五语言指南。 |
| [#2](https://github.com/yifgu/MaaAssistantArknights/pull/2) | 隔离 worker 的 stdio，增加启动阶段诊断、watchdog、地图唯一 ID 校验和构建／资源身份。 |
| [#3](https://github.com/yifgu/MaaAssistantArknights/pull/3) | 修复身份解析，增加识别统计、暂停验证及单动作有界 `battle_step`。 |
| [#4](https://github.com/yifgu/MaaAssistantArknights/pull/4) | 支持显式接管手动暂停，将截图时间戳与实际采集帧绑定。 |
| [#5](https://github.com/yifgu/MaaAssistantArknights/pull/5) | 增加可取消等待、终局取证、完整关卡情报、loadout 来源记录与只读阵型评估。 |
| [#6](https://github.com/yifgu/MaaAssistantArknights/pull/6) | 扩展场景仲裁、只读终局采集、战前观察、raw 静态情报、白名单召唤物部署与受控调速。 |
| [#7](https://github.com/yifgu/MaaAssistantArknights/pull/7) | 区分可恢复的暂停不确定与不可恢复的安全隔离，增加两帧恢复与同帧诊断。 |
| [#8](https://github.com/yifgu/MaaAssistantArknights/pull/8) | 支持英文 `PAUSE` 或中文 ｢暂停中｣ 任一严格匹配，不采用模糊前缀。 |
| [#9](https://github.com/yifgu/MaaAssistantArknights/pull/9) | 按已观测慢帧成本调整暂停验证预算，仍受命令期限约束。 |
| [#10](https://github.com/yifgu/MaaAssistantArknights/pull/10) | 保守识别同帧分行胜利标题，保留逐词证据、拒绝原因与胜败冲突保护。 |
| [#11](https://github.com/yifgu/MaaAssistantArknights/pull/11) | 新增有界战前编队批量检查、实际选中技能／练度识别及有序技能 ID 映射。 |
| [#12](https://github.com/yifgu/MaaAssistantArknights/pull/12) | 支持严格几何约束的中文暂停分框证据，并增加 1X 模板识别路径。 |

## 上游 MAA 下载与安装

以下官网与 Releases 链接提供上游原版，不包含本仓库新增的 Battle MCP；MCP 请按上面的本仓库安装说明获取。

请阅读 [文档](https://docs.maa.plus/zh-cn/manual/newbie.html) 后前往 [官网](https://maa.plus) 或 [Releases](https://github.com/MaaAssistantArknights/MaaAssistantArknights/releases) 下载，并参考 [新手上路](https://docs.maa.plus/zh-cn/manual/newbie.html) 进行安装。

## 亮点功能

- 理智作战，掉落识别及上传 [企鹅物流](https://penguin-stats.cn/)，[一图流](https://ark.yituliu.cn/)
- 智能基建换班，自动计算干员效率，支持跨设施组合；同时也支持 [自定义排班](https://docs.maa.plus/zh-cn/protocol/base-scheduling-schema.html)
- 自动公招，可选使用加急许可，一次全部刷完！公招数据自动上传 [企鹅物流](https://penguin-stats.cn/result/stage/recruit/recruit)，[一图流](https://ark.yituliu.cn/survey/maarecruitdata)
- 支持手动识别公招界面，方便对高星公招做出选择 ~~（你的这个高资回费出的是推王呢还是推王呢）~~
- 支持识别干员列表，统计已有和未有干员及潜能，并在公招识别显示
- 支持识别养成材料，并导出至 [企鹅物流刷图规划](https://penguin-stats.cn/planner)、[明日方舟工具箱](https://arkntools.app/#/material)、[ARK-NIGHTS 干员培养表](https://ark-nights.com/settings)
- 访问好友、收取信用及购物、领取日常奖励等，一键全日常自动长草
- 肉鸽全自动刷源石锭和等级，自动烧水和凹直升，智能识别干员及练度
- 选择作业 JSON 文件，自动抄作业， [视频演示](https://www.bilibili.com/video/BV1H841177Fk/)
- 支持 C, Python, Java, Rust, Golang, Java HTTP, Rust HTTP 等多种接口，方便集成调用，自定义你的 MAA！

<!-- markdownlint-disable -->

<details><summary>话不多说，看图！</summary>

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./docs/.vuepress/public/images/zh-cn/readme/1-dark.png">
  <img alt="zh1" src="./docs/.vuepress/public/images/zh-cn/readme/1-light.png">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./docs/.vuepress/public/images/zh-cn/readme/2-dark.png">
  <img alt="zh2" src="./docs/.vuepress/public/images/zh-cn/readme/2-light.png">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./docs/.vuepress/public/images/zh-cn/readme/3-dark.png">
  <img alt="zh3" src="./docs/.vuepress/public/images/zh-cn/readme/3-light.png">
</picture>
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="./docs/.vuepress/public/images/zh-cn/readme/4-dark.png">
  <img alt="zh4" src="./docs/.vuepress/public/images/zh-cn/readme/4-light.png">
</picture>

</details>

<!-- markdownlint-restore -->

## 使用说明

### 功能介绍

请参阅 [用户手册](https://docs.maa.plus/zh-cn/manual/)。

### 外服支持

目前国际服（美服）、日服、韩服、繁中服的绝大部分功能均已支持。但由于外服用户较少及项目人手不足，很多功能并没有进行全面的测试，所以请自行体验。  
若您遇到了 Bug，或对某个功能有强需求，欢迎在 [Issues](https://github.com/MaaAssistantArknights/MaaAssistantArknights/issues) 和 [讨论区](https://github.com/MaaAssistantArknights/MaaAssistantArknights/discussions) 催更；或加入我们一起建设 MAA！请参阅 [外服适配教程](https://docs.maa.plus/zh-cn/develop/overseas-client-adaptation.html)

### CLI 支持

MAA 支持命令行界面（CLI）操作，支持 Linux，macOS 和 Windows，可用于自动化脚本或在无图形界面的服务器上使用。请参阅 [CLI 使用指南](https://docs.maa.plus/zh-cn/manual/cli/)

## 加入我们

### 主要关联项目

- 全新框架：[MaaFramework](https://github.com/MaaXYZ/MaaFramework)
- [作业站](https://prts.plus) 前端：[zoot-plus-frontend](https://github.com/ZOOT-Plus/zoot-plus-frontend)
- [作业站](https://prts.plus) 后端：[ZootPlusBackend](https://github.com/ZOOT-Plus/ZootPlusBackend)
- [官网](https://maa.plus)：[前端](https://github.com/MaaAssistantArknights/maa-website)
- 深度学习：[MaaAI](https://github.com/MaaAssistantArknights/MaaAI)

### 多语言 (i18n)

MAA 以中文（简体）为第一语言，翻译词条均以中文（简体）为准。

### 参与开发

请参阅 [开发指南](https://docs.maa.plus/zh-cn/develop/development.html)。

### API

- [Battle MCP 接口（本仓库新增）](src/MaaMcpServer)：[配置、能力与协议](docs/zh-cn/develop/mcp-server.md)
- [C 接口](include/AsstCaller.h)：[集成示例](src/Cpp/main.cpp)
- [Python 接口](src/Python/asst/asst.py)：[集成示例](src/Python/sample.py)
- [Golang 接口](src/Golang)：[集成示例](src/Golang/maa/maa.go)
- [Dart 接口](src/Dart)
- [Java 接口](src/Java/src/main/java/com/iguigui/maaj/easySample/MaaCore.java)：[集成示例](src/Java/src/main/java/com/iguigui/maaj/easySample/MaaJavaSample.java)
- [Java HTTP 接口](src/Java/Readme.md)
- [Rust 接口](src/Rust/src/maa_sys)：[HTTP 接口](src/Rust)
- [TypeScript 接口](https://github.com/MaaAssistantArknights/MaaX/tree/main/packages/main/coreLoader)
- [Woolang 接口](src/Woolang/maa.wo)：[集成示例](src/Woolang/demo.wo)
- [集成文档](https://docs.maa.plus/zh-cn/protocol/integration.html)
- [回调消息协议](https://docs.maa.plus/zh-cn/protocol/callback-schema.html)
- [任务流程协议](https://docs.maa.plus/zh-cn/protocol/task-schema.html)
- [自动战斗协议](https://docs.maa.plus/zh-cn/protocol/copilot-schema.html)

### 外服适配

请参阅 [外服适配教程](https://docs.maa.plus/zh-cn/develop/overseas-client-adaptation.html)，对于国服已支持的功能，绝大部分的外服适配工作仅需要截图 + 简单的 JSON 修改即可。

### Issue bot

请参阅 [Issue Bot 使用方法](https://docs.maa.plus/zh-cn/develop/issue-bot-usage.html)

## 致谢

### 开源库

- 图像识别库：[opencv](https://github.com/opencv/opencv.git)
- ~~文字识别库：[chineseocr_lite](https://github.com/DayBreak-u/chineseocr_lite.git)~~
- 文字识别库：[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)
- 深度学习部署库：[FastDeploy](https://github.com/PaddlePaddle/FastDeploy)
- 机器学习加速器：[onnxruntime](https://github.com/microsoft/onnxruntime)
- ~~关卡掉落识别：[企鹅物流识别](https://github.com/penguin-statistics/recognizer)~~
- 地图格子识别：[Arknights-Tile-Pos](https://github.com/yuanyan3060/Arknights-Tile-Pos)
- C++ JSON 库：[meojson](https://github.com/MistEO/meojson.git)
- C++ 运算符解析器：[calculator](https://github.com/kimwalisch/calculator)
- ~~C++ base64 编解码：[cpp-base64](https://github.com/ReneNyffenegger/cpp-base64)~~
- C++ 解压压缩库：[zlib](https://github.com/madler/zlib)
- C++ Gzip 封装：[gzip-hpp](https://github.com/mapbox/gzip-hpp)
- 安卓触控事件器：[Minitouch](https://github.com/DeviceFarmer/minitouch)
- 安卓触控事件器：[MaaTouch](https://github.com/MaaAssistantArknights/MaaTouch)
- WPF MVVM 框架：[Stylet](https://github.com/canton7/Stylet)
- WPF 控件库：[HandyControl](https://github.com/HandyOrg/HandyControl) -> [HandyControls](https://github.com/ghost1372/HandyControls)
- C# 日志：[Serilog](https://github.com/serilog/serilog)
- C# JSON 库：[Newtonsoft.Json](https://github.com/JamesNK/Newtonsoft.Json) & [System.Text.Json](https://github.com/dotnet/runtime)
- ~~下载器：[aria2](https://github.com/aria2/aria2)~~

### 数据源

- ~~公开招募数据：[明日方舟工具箱](https://www.bigfun.cn/tools/aktools/hr)~~
- ~~干员及基建数据：[PRTS Wiki](http://prts.wiki/)~~
- 关卡数据：[企鹅物流数据统计](https://penguin-stats.cn/)
- 游戏数据及资源：[明日方舟客户端素材](https://github.com/yuanyan3060/ArknightsGameResource)
- 游戏数据：[《明日方舟》Yostar游戏数据](https://github.com/ArknightsAssets/ArknightsGamedata)

### 贡献/参与者

感谢所有参与到开发/测试中的朋友们，是大家的帮助让 MAA 越来越好！ (\*´▽｀)ノノ

[![Contributors](https://contributors-img.web.app/image?repo=MaaAssistantArknights/MaaAssistantArknights&max=105&columns=15)](https://github.com/MaaAssistantArknights/MaaAssistantArknights/graphs/contributors)

## 声明

- 本软件使用 [GNU Affero General Public License v3.0 only](https://spdx.org/licenses/AGPL-3.0-only.html) 开源，并附带额外 [用户协议](https://github.com/MaaAssistantArknights/MaaAssistantArknights/blob/dev-v2/terms-of-service.md)。
- 本软件 logo 并非使用 AGPL 3.0 协议开源，[耗毛](https://weibo.com/u/3251357314)、vie 两位画师及软件全体开发者保留所有权利。不得以 AGPL 3.0 协议已授权为由在未经授权的情况下使用本软件 logo，不得在未经授权的情况下将本软件 logo 用于任何商业用途。
- 本软件开源、免费，仅供学习交流使用。若您遇到商家使用本软件进行代练并收费，可能是设备与时间等费用，产生的问题及后果与本软件无关。

### DirectML 支持说明

本软件支持 GPU 加速功能，其在 Windows 平台上依赖于 Microsoft 提供的独立组件 [DirectML](https://learn.microsoft.com/en-us/windows/ai/directml/)。DirectML 并非本项目的开源部分，也不受 AGPL 3.0 的约束。为方便用户，我们随安装包附带了一个未经修改的 DirectML.dll 文件。如果您无需 GPU 加速功能，可安全删除该 DLL 文件，软件的核心功能仍可正常运行。

## 广告

用户交流 QQ 群：[MAA 使用 & 粥游交流 QQ 群](https://api.maa.plus/MaaAssistantArknights/api/qqgroup/index.html)  
Discord 服务器: [邀请链接](https://discord.gg/23DfZ9uA4V)  
用户交流 TG 群：[Telegram 群](https://t.me/+Mgc2Zngr-hs3ZjU1)  
自动战斗 JSON 作业分享：[prts.plus](https://prts.plus)  
Bilibili 直播间：[MrEO 直播间](https://live.bilibili.com/2808861) 直播敲代码 & [MAA-Official 直播间](https://live.bilibili.com/27548877) 游戏/杂谈

技术群（舟无关、禁水）：[内卷地狱！(QQ 群)](https://jq.qq.com/?_wv=1027&k=ypbzXcA2)  
开发者群：[QQ 群](https://jq.qq.com/?_wv=1027&k=JM9oCk3C)

如果觉得软件对你有帮助，帮忙点个 Star 吧！~（网页最上方右上角的小星星），这就是对我们最大的支持了！
