# NextPlay 数据契约 v1

对应开发文档第 4 节与 5.1 节，供 A/B/C/D 使用同一组固定样本联调。
JSON Schema 使用 Draft 2020-12；校验器只加载仓库内文件，无远程引用。

## 入口与边界

| 文件 / 定义 | 数据 |
| --- | --- |
| `schema.json#/$defs/match` | 现有 `metadata.json`：球队、球员、种子、生成器、策略参数、球场、分节与方向 |
| `schema.json#/$defs/event` | `events.jsonl`：16 类事件，按类型要求动作、参与者、结果等字段 |
| `schema.json#/$defs/tracking` / `state` / `preview` | 帧、统计快照、60 秒预览 |
| `schema.json#/$defs/match_player` / `roster_player` / `squads` | 比赛名单、原始阵容中的匿名球员、完整阵容文件 |
| `schema.json#/$defs/sofascore_profile` | 来源属性，外场与门将雷达分开；未知值为 `null` |
| `contracts/schema.json#/$defs/match` / `snapshot` | 带 `data_version` 的比赛封装、带哈希的固定快照 |
| `contracts/schema.json#/$defs/simulated_profile` | `kind: simulated` 的假设画像 |
| `contracts/schema.json#/$defs/analysis_request` / `analysis_response` | 文档中的分析请求和响应 |

根目录 Schema 的根仍匹配 tracking/event/state 三种 JSONL 记录；其他类型按 `$defs` 选择。
`$defs/player` 保留旧的 tracking 五元组含义，球员资料使用 `match_player`。
生成器的 `metadata.schema_version: 1.0.0`、已有文件、球队与球员数量及数值均未迁移。
新封装的版本为 `schema_version: contract-1`，通过 `make_match()` / `make_snapshot()` 构造。
比赛封装中的 `metadata.seed` 与快照顶层 `seed` 都是生成种子。

本次提供契约、校验器、封装函数和固定样本。现有 `server.py`、`outcome.compare()`、
`agents.py` 与浏览器还使用旧接口；响应示例不代表线上 API 已完成接线。
B/C/D 后续需把计算、Agent 与前端接入这些字段及检查，特别是候选集校验与模型失败降级。
Schema 不替代自然语言审查，不能判断解释中的每个数字是否真实有据，也不能自动检查文本中的成功百分比。

## 单位、标识、时间和缺失值

- 比赛球员 ID 沿用 `H01..H14` / `A01..A14`，球队为 `HOME` / `AWAY`。
  文档中的 `HOME-08` 是示意；本仓库无需改写成该格式。ID 只在一场比赛内唯一，跨比赛必须携带 `match_id`。
  阵容 ID（如 `ARS-01`）和球衣号码是另外两个字段，不能互换。
- 球场固定 105×68 米，左上角原点，x 向右、y 向下、z 向上；速度为米/秒。
  tracking.players 为 `[player_id,x,y,vx,vy]`，ball 为 `[x,y,z,vx,vy,vz]`。
- `t_ms` 是比赛累计毫秒，不包含中场休息；`period_ms = t_ms - period.start_ms`。
  现有比赛每半场 45+5 分钟，分节边界帧以记录中的 `period` 为准。
  事件 `period_ms` 对应事件结束时刻；`start_ms <= end_ms <= available_at_ms`。
  回放只能展示 `available_at_ms <= 当前回放时刻` 的事件结果。
- `periods[].home_attacks` 给出主队方向；快照 `attacking_direction` 明确两队的相反方向。
  不按画面左右、球员朝向或运动速度猜测进攻方向。
- 模拟画像必须包含 `profile_version: sim-1`、`kind: simulated`、`preferred_foot`、`weak_foot`。
  惯用脚为 `left/right/both/null`；弱脚采用本契约明确约定的 **1–5 整数或 null**（文档未指定量表）。
  `null` 表示未知，不能自动补成右脚或默认能力值；应标注 `preferred_foot_not_considered` 并回退。
- SofaScore 是来源观测，不是模拟画像；ATT/DEF/CRE/TEC/TAC 与 SAV/ANT/TAC/BAL/AER 不混用。
  五维为 0–100 或 null，身高单位 cm、体重 kg。未知重量保留 null。
  `complete` 要求五维、身高和惯用脚齐全，不要求体重；`partial` 保留未知项。
  `needs_review/unmatched` 可只含 schema_version 与 status。来源冲突保留双方值。
  模拟画像不会直接使用来源画像替代，修改模拟参数也不能回写来源数据。

## 版本与哈希

`contracts/model.py` 是参考实现，消费者优先使用服务端返回的 ID，无需自行实现散列。

1. `data_version = "gen-" + sha256(metadata 的规范 JSON)[:16]`。
   它绑定种子、生成器版本、动作策略参数及名单属性；是生成输入元数据的内容版本，
   **不是所有输出文件的总校验和**。生成算法或其他未写入 metadata 的依赖改变时必须升级 generator / policy revision。
2. `snapshot_id = sha256(除 snapshot_id 外的完整快照封装)[:16]`。
   包含数据版本、比赛、种子、时间、方向与当前帧，不含问题、实际动作、事件结果或未来帧。
3. `candidate_set_id = outcome-<版本>:<16位哈希>`。
   哈希输入为 `{snapshot_id, data_version, profile, candidates, evidence}`，
   candidates 去除 `is_actual`。画像内容改变（即使 profile_version 没变）、候选或证据改变均失效；
   仅揭示实际动作不改变 ID。计算器修改必须升级前缀版本。

规范 JSON 使用 Python `json.dumps(sort_keys=True, ensure_ascii=True, separators=(',', ':'), allow_nan=False)`，
先将整数值 float（包括 -0.0）规范为 int；UTF-8 编码，数组顺序保留。这是项目的编码约定，非 RFC 8785。
禁止 NaN/Infinity。固定样本含参考哈希；旧 Agent 内部的快照哈希不能与本契约混用。

## 分析响应与跨记录规则

模式沿用现有 `fan/analyst`，语言为 `en/zh`。
候选类型为 `pass/carry/shot/hold`；action_id 的前缀必须与 type 一致。
合法候选的 rank 从 1 连续、唯一；不合法候选 rank 为 null 且不能推演。
目前仅合法传球允许 `rollout_supported: true`，其余三类必须为 false。
候选只能公开文字机会、风险和排序；Schema 拒绝 `value_gain`、`scenario_weight` 等额外字段。
数值评分仍可留在计算器内部，不能直接把内部候选对象当成公共候选返回。

所有状态均返回文档中的完整响应字段：

| status | candidates / evidence | explanation |
| --- | --- | --- |
| ready | 非空 | 有解释对象 |
| partial | 非空 | 可为 null |
| model_unavailable | 保留非空计算结果 | null |
| unknown / error | 可为空 | null |

模型未调用时 agent_trace 可以为空；实际调用记录 coordinator/attack/defense/reviewer、tool、ms、ok，禁止放入密钥。
结构校验后调用 `check_exchange(request,response,snapshot,metadata)` 检查：

- 快照哈希、数据版本、时间、方向、种子、当前持球人和活动名单一致；
- 候选集 ID 绑定当前画像与计算结果；不一致应丢弃 Agent 回答并提示重试；
- action_id/evidence_id 唯一，所有证据引用存在，rank 连续；
- 传球接球人是同队的在场球员，最多一个实际动作，解释 mode 与请求一致。

结构/引用校验无法证明模型从未读取未来数据。调用方必须只向比较器传当前 frame + metadata；
实际动作和未来事件只进入独立的回放展示路径。对未找到比赛/快照的 HTTP 错误可直接返回传输层错误，
不要伪造有效 snapshot_id 来满足分析响应。

## 固定案例与验证

`tests/fixtures/contracts/match.json` 与 `case-a/b/c/` 是可直接读取的固定样本。
来源是 seed=20261019 的本地合成单场，源决策分别为 d000001 / d000322 / d000029。

| 案例 | 固定条件 |
| --- | --- |
| A | 看起来空位的接球人离最近防守者 >5 米，但防守者先到传球线路；至少一种假设推演发生拦截 |
| B | 实际选择射门，同时存在合法传球候选，可以展示传球推演 |
| C | 持球人 3 米内有压迫；实际回传，向前线路被封，回传线路的到达时间差更好 |

每个案例有 `snapshot.json`、`request.json`、`response.example.json`、`context.json`。
context 只供显示实际动作/选择问题，不属于计算输入。
response.example 是从当前计算器确定性投影出的公共字段示例，使用 model_unavailable，
明确没有调用模型；不伪造 Agent trace，也不声称已有真实模型回答。
案例测试检查实际几何条件和传球推演，不能用案例筛选结果评估模型的总体准确率。

```sh
npm ci                  # 仅开发校验依赖；应用运行仍只需 Python 标准库
npm run test:contract   # 字段/错误状态/固定哈希/错配/引用/案例条件/短程种子复现
npm test                # 包含 tests/test_contract.py
npm run validate:schema # data 与 data/league 中全部现存的核心数据记录

# 单文件，或自定义数据目录：
node scripts/validate_contracts.mjs --file contract.snapshot tests/fixtures/contracts/case-a/snapshot.json
node scripts/validate_contracts.mjs --dataset data/league-validated data/realism-sample

# 需要本地完整 decisions.jsonl；只在刻意升级固定案例时重导出并审查 diff：
python3 -m contracts.export_fixtures
```

全量校验覆盖 metadata、events、states、现存完整 tracking、decisions 内的 snapshot、preview、squads。
报告中的 data.tracking 包含决策快照，decision_snapshots 是其中子集；preview 按完整对象计数。
完整 tracking 与 decisions 是可选生成缓存，缺失时不冒充已经验证；events/states 缺失则失败。
赛后报告、榜单、压缩演示打包等衍生展示文件不属于这份核心 Schema；
它们的聚合与轨迹/事件一致性继续由既有 `validate.py`、`validate_league.py` 和各模块测试负责。
固定快照不依赖被 gitignore 的大文件，fresh clone 可执行契约测试。
